"""
mmWave 레이더(TI IWR6843ISK) 기반 PC 신호 처리 및 호흡수(BR) 추정 5단계 파이프라인

담당: 김기은 (레이더 신호 처리 및 호흡 감지)
파이프라인 구조:
  1단계: 배경·노이즈 제거   - Static Clutter Removal (신호 MTI/평균 차감 + 3D ROI/SNR 필터) + DBSCAN 클러스터링
  2단계: 대상 추적 및 ROI   - 3D Kalman Filter (위치·속도 추적 및 호흡 측정 거리 구간 Range Bin 산출)
  3단계: 위상 추출 및 복원  - 복소 I/Q Arctangent(atan2) 복조 + Phase Unwrapping + 드리프트 제거
  4단계: 호흡 대역 필터링   - Median Filter (임펄스 노이즈 완화) + 0.1~0.5 Hz Butterworth Bandpass Filter
  5단계: 호흡수 산출·비교   - 주파수 영역 FFT(Zero-padding) vs 시간 영역 Peak Detection 비교 및 안정화

실행 예시:
  python breath_signal_pipeline.py               # 자체 시뮬레이션으로 FFT vs Peak Detection 오차 비교 실행
  python breath_signal_pipeline.py --gt-bpm 15   # 특정 호흡수(15 BPM) 시뮬레이션 검증
"""

from __future__ import annotations

import argparse
import math
from collections import deque
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import numpy as np
from scipy.signal import butter, detrend, find_peaks, medfilt, sosfiltfilt


# =====================================================================
# 1단계: 배경·노이즈 제거 (Static Clutter Removal + DBSCAN 클러스터링)
# =====================================================================

class StaticClutterRemover:
    """
    정적 클러터 제거기 (1D 복소 I/Q 또는 위상/거리 신호용)
    - 온라인(스트리밍): Exponential Moving Average(MTI 고역통과)로 벽·가구 등 고정 반사 성분 추정 및 차감
    - 배치(윈도우): 윈도우 평균 차감(Mean Subtraction)으로 정적 DC 성분 제거
    """

    def __init__(self, alpha: float = 0.97):
        self.alpha = alpha
        self._bg_estimate: Optional[complex | float] = None

    def reset(self) -> None:
        self._bg_estimate = None

    def update_sample(self, sample: complex | float) -> complex | float:
        """스트리밍 단일 샘플에 대한 IIR MTI 정적 클러터 제거"""
        if self._bg_estimate is None:
            self._bg_estimate = sample
            return sample - self._bg_estimate
        self._bg_estimate = self.alpha * self._bg_estimate + (1.0 - self.alpha) * sample
        return sample - self._bg_estimate

    @staticmethod
    def remove_window_mean(signal: np.ndarray) -> np.ndarray:
        """윈도우 단위 평균 차감 (복소 I/Q 및 실수 신호 모두 지원)"""
        arr = np.asarray(signal)
        if arr.size == 0:
            return arr
        return arr - np.mean(arr)


def dbscan_cluster(points_xyz: np.ndarray, eps: float = 0.45, min_samples: int = 3) -> np.ndarray:
    """
    순수 NumPy 기반 3D DBSCAN 클러스터링 (외부 scikit-learn 불필요)
    - 밀집된 사람 반사 포인트는 클러스터(0, 1, ...)로 묶고, 흩어진 잡음 점은 -1(노이즈)로 분류

    Args:
        points_xyz: (N, 3) 3D 좌표 배열 [x, y, z]
        eps: 이웃 탐색 반경 (m)
        min_samples: 핵심점(core point) 판정 최소 이웃 수

    Returns:
        labels: (N,) 정수 배열 (-1은 노이즈, 0 이상은 클러스터 ID)
    """
    n = len(points_xyz)
    if n == 0:
        return np.empty((0,), dtype=int)

    # 거리 행렬 계산 (실내 레이더 포인트 수는 보통 수십~수백 개라 O(N^2)로 수십 us 내 처리됨)
    diff = points_xyz[:, None, :] - points_xyz[None, :, :]
    dist_sq = np.sum(diff * diff, axis=2)
    eps_sq = eps * eps

    labels = np.full(n, -1, dtype=int)
    visited = np.zeros(n, dtype=bool)
    cluster_id = 0

    for idx in range(n):
        if visited[idx]:
            continue
        visited[idx] = True
        neighbors = np.where(dist_sq[idx] <= eps_sq)[0]
        if len(neighbors) < min_samples:
            labels[idx] = -1
            continue

        labels[idx] = cluster_id
        queue = deque(neighbors.tolist())
        while queue:
            nb = queue.popleft()
            if not visited[nb]:
                visited[nb] = True
                nb_neighbors = np.where(dist_sq[nb] <= eps_sq)[0]
                if len(nb_neighbors) >= min_samples:
                    queue.extend(nb_neighbors.tolist())
            if labels[nb] == -1:
                labels[nb] = cluster_id

        cluster_id += 1

    return labels


class PointCloudPreprocessor:
    """
    1단계: 3D 포인트 클라우드 정적 배경·잡음 제거 및 DBSCAN 사람 클러스터 분리
    """

    def __init__(
        self,
        roi: Optional[Dict[str, float]] = None,
        min_snr: float = 1.0,
        dbscan_eps: float = 0.50,
        dbscan_min_samples: int = 3,
        voxel_size: float = 0.25,
    ):
        self.roi = roi or {
            "x_min": -3.0,
            "x_max": 3.0,
            "y_min": 0.1,
            "y_max": 5.0,
            "z_min": -2.0,
            "z_max": 2.0,
        }
        self.min_snr = min_snr
        self.dbscan_eps = dbscan_eps
        self.dbscan_min_samples = dbscan_min_samples
        self.voxel_size = voxel_size
        # 빈 방 캘리브레이션 시 등록되는 정적 배경 복셀 집합 (벽·가구 등)
        self.static_bg_voxels: set[Tuple[int, int, int]] = set()

    def _to_voxel(self, xyz: np.ndarray) -> Tuple[int, int, int]:
        return (
            int(math.floor(xyz[0] / self.voxel_size)),
            int(math.floor(xyz[1] / self.voxel_size)),
            int(math.floor(xyz[2] / self.voxel_size)),
        )

    def register_static_background(self, empty_room_points: np.ndarray) -> int:
        """빈 방 상태에서 수집한 정적 반사점(벽·가구)을 배경 복셀로 등록"""
        if empty_room_points is None or len(empty_room_points) == 0:
            return 0
        pc = np.asarray(empty_room_points, dtype=float)
        for p in pc[:, :3]:
            self.static_bg_voxels.add(self._to_voxel(p))
        return len(self.static_bg_voxels)

    def filter_and_cluster(
        self,
        raw_point_cloud: Optional[np.ndarray],
        prev_target_pos: Optional[np.ndarray] = None,
    ) -> Tuple[np.ndarray, Optional[np.ndarray], Dict[str, int]]:
        """
        ROI/SNR 필터 -> 정적 배경 복셀 제거 -> DBSCAN 클러스터링 수행

        Returns:
            clean_pc: 노이즈가 제거된 유효 포인트 클라우드
            best_centroid: 대표 대상자 클러스터 중심 좌표 [x, y, z] (없으면 None)
            stats: 전처리 통계(raw_count, roi_count, clustered_count, num_clusters)
        """
        stats = {"raw_count": 0, "roi_count": 0, "clustered_count": 0, "num_clusters": 0}
        if raw_point_cloud is None or len(raw_point_cloud) == 0:
            return np.empty((0, 5)), None, stats

        pc = np.asarray(raw_point_cloud, dtype=float)
        if pc.ndim != 2 or pc.shape[1] < 3:
            return np.empty((0, 5)), None, stats

        stats["raw_count"] = len(pc)

        # (1) ROI 영역 및 SNR 필터링
        mask = (
            (pc[:, 0] >= self.roi["x_min"]) & (pc[:, 0] <= self.roi["x_max"]) &
            (pc[:, 1] >= self.roi["y_min"]) & (pc[:, 1] <= self.roi["y_max"]) &
            (pc[:, 2] >= self.roi["z_min"]) & (pc[:, 2] <= self.roi["z_max"])
        )
        if pc.shape[1] >= 5:
            mask &= pc[:, 4] >= self.min_snr
        pc = pc[mask]

        # (2) 캘리브레이션된 정적 배경 복셀 제거 (사람 정지 상태를 지우지 않도록 등록된 빈방 배경만 제거)
        if self.static_bg_voxels and len(pc) > 0:
            keep = [self._to_voxel(p[:3]) not in self.static_bg_voxels for p in pc]
            pc = pc[np.array(keep, dtype=bool)]

        stats["roi_count"] = len(pc)
        if len(pc) == 0:
            return np.empty((0, pc.shape[1])), None, stats

        # (3) DBSCAN 클러스터링으로 흩어진 잡음 점 제거
        labels = dbscan_cluster(pc[:, :3], eps=self.dbscan_eps, min_samples=self.dbscan_min_samples)
        unique_clusters = [c for c in np.unique(labels) if c >= 0]
        stats["num_clusters"] = len(unique_clusters)

        if not unique_clusters:
            # 포인트 수가 적어 DBSCAN이 형성되지 않은 경우 ROI 통과 점은 유지하되 중심점 None 반환
            return pc, None, stats

        valid_mask = labels >= 0
        clustered_pc = pc[valid_mask]
        stats["clustered_count"] = len(clustered_pc)

        # (4) 대표 사람 클러스터 선정 (이전 추적 위치 근접성 + SNR 가중치 + 포인트 수 종합)
        best_centroid = None
        best_score = -1e9
        for cid in unique_clusters:
            c_pts = pc[labels == cid]
            if c_pts.shape[1] >= 5 and np.sum(c_pts[:, 4]) > 0:
                weights = np.maximum(c_pts[:, 4], 0.1)
                centroid = np.average(c_pts[:, :3], axis=0, weights=weights)
                snr_bonus = float(np.mean(c_pts[:, 4])) * 0.2
            else:
                centroid = np.mean(c_pts[:, :3], axis=0)
                snr_bonus = 0.0

            score = float(len(c_pts)) + snr_bonus
            if prev_target_pos is not None:
                dist = float(np.linalg.norm(centroid - prev_target_pos))
                score -= dist * 2.5  # 이전 위치에서 멀리 튄 허상/선풍기 클러스터 감점

            if score > best_score:
                best_score = score
                best_centroid = centroid

        return clustered_pc, best_centroid, stats


# =====================================================================
# 2단계: 대상 추적 및 ROI(Range Bin) 선정 (3D Kalman Filter)
# =====================================================================

class KalmanTracker3D:
    """
    PC 이관 3D 선형 칼만 필터 (등속 모델: 상태 [x, y, z, vx, vy, vz])
    - 레이더 보드 내 GTRACK 추적 오류를 보완하기 위해 PC에서 대상자 위치·속도를 부드럽게 추적
    - 게이팅(Gating)으로 선풍기·커튼 등 순간 튐 측정치를 차단하고 호흡 측정용 Range Bin 계산
    """

    def __init__(
        self,
        dt: float = 0.09,
        process_noise: float = 0.08,
        measurement_noise: float = 0.15,
        gate_distance: float = 1.2,
        max_missed_frames: int = 25,
        range_resolution: float = 0.084,
    ):
        self.dt = dt
        self.process_noise = process_noise
        self.measurement_noise = measurement_noise
        self.gate_distance = gate_distance
        self.max_missed_frames = max_missed_frames
        self.range_resolution = range_resolution

        self.initialized = False
        self.missed_frames = 0
        self.track_id = 0

        self.x = np.zeros((6, 1), dtype=float)
        self.P = np.eye(6, dtype=float) * 0.5
        self.H = np.hstack([np.eye(3), np.zeros((3, 3))])
        self.R = np.eye(3, dtype=float) * (measurement_noise ** 2)

    def _build_F_Q(self, dt: float) -> Tuple[np.ndarray, np.ndarray]:
        F = np.eye(6, dtype=float)
        F[0, 3] = dt
        F[1, 4] = dt
        F[2, 5] = dt

        q = self.process_noise ** 2
        dt2 = dt * dt
        dt3 = dt2 * dt / 2.0
        dt4 = dt2 * dt2 / 4.0
        Q = np.zeros((6, 6), dtype=float)
        for i in range(3):
            Q[i, i] = dt4 * q
            Q[i, i + 3] = dt3 * q
            Q[i + 3, i] = dt3 * q
            Q[i + 3, i + 3] = dt2 * q
        return F, Q

    def reset(self) -> None:
        self.initialized = False
        self.missed_frames = 0
        self.x = np.zeros((6, 1), dtype=float)
        self.P = np.eye(6, dtype=float) * 0.5

    @property
    def position(self) -> Optional[np.ndarray]:
        return self.x[:3, 0].copy() if self.initialized else None

    def update(self, measurement_xyz: Optional[np.ndarray], dt: Optional[float] = None) -> Dict[str, object]:
        """한 프레임의 DBSCAN 중심 좌표(없으면 None)로 예측·갱신 수행"""
        step_dt = self.dt if (dt is None or dt <= 0) else dt
        F, Q = self._build_F_Q(step_dt)

        if not self.initialized:
            if measurement_xyz is None:
                return self._empty_state()
            self.x[:3, 0] = np.asarray(measurement_xyz, dtype=float)
            self.x[3:, 0] = 0.0
            self.P = np.eye(6, dtype=float) * 0.5
            self.initialized = True
            self.missed_frames = 0
            return self._current_state()

        # (1) Predict 단계
        self.x = F @ self.x
        self.P = F @ self.P @ F.T + Q

        # (2) Update 단계 (게이팅 포함)
        if measurement_xyz is not None:
            z = np.asarray(measurement_xyz, dtype=float).reshape(3, 1)
            innovation = z - (self.H @ self.x)
            dist = float(np.linalg.norm(innovation))

            if dist <= self.gate_distance:
                S = self.H @ self.P @ self.H.T + self.R
                K = self.P @ self.H.T @ np.linalg.inv(S)
                self.x = self.x + K @ innovation
                I = np.eye(6, dtype=float)
                self.P = (I - K @ self.H) @ self.P
                self.missed_frames = 0
            else:
                # 게이트 밖 이상치 측정(허상/선풍기 등)은 무시
                self.missed_frames += 1
        else:
            self.missed_frames += 1

        if self.missed_frames > self.max_missed_frames:
            self.reset()
            return self._empty_state()

        return self._current_state()

    def _empty_state(self) -> Dict[str, object]:
        return {
            "id": None,
            "x": 0.0,
            "y": 0.0,
            "z": 0.0,
            "speed": 0.0,
            "range_m": 0.0,
            "range_bin": 0,
            "is_moving": False,
        }

    def _current_state(self) -> Dict[str, object]:
        px, py, pz = float(self.x[0, 0]), float(self.x[1, 0]), float(self.x[2, 0])
        vx, vy, vz = float(self.x[3, 0]), float(self.x[4, 0]), float(self.x[5, 0])
        speed = float(math.sqrt(vx * vx + vy * vy + vz * vz))
        range_m = float(math.sqrt(px * px + py * py + pz * pz))
        range_bin = int(round(range_m / self.range_resolution)) if self.range_resolution > 0 else 0
        return {
            "id": self.track_id,
            "x": round(px, 2),
            "y": round(py, 2),
            "z": round(pz, 2),
            "speed": round(speed, 2),
            "range_m": round(range_m, 2),
            "range_bin": range_bin,
            "is_moving": bool(speed > 0.15),
        }


# =====================================================================
# 3~5단계: 위상 추출·언래핑 -> BPF 필터링 -> FFT vs Peak Detection 호흡수 산출
# =====================================================================

@dataclass
class BreathEstimationResult:
    """한 번의 호흡수(BR) 추정 결과 (FFT vs Peak Detection 동시 기록)"""
    br_fft: float = 0.0            # 주파수 영역 FFT(Zero-padding) 추정치 (BPM)
    br_peak: float = 0.0           # 시간 영역 Peak Detection 추정치 (BPM)
    br_final: float = 0.0          # 안정화 후 최종 채택 호흡수 (BPM)
    fft_snr: float = 0.0           # FFT 스펙트럼 피크 신뢰도(Peak/Mean 비율)
    peak_count: int = 0            # 윈도우 내 검출된 유효 호흡 피크 수
    fft_peak_diff: float = 0.0     # |br_fft - br_peak| (두 방식 간 차이)
    signal_energy: float = 0.0     # 호흡 파형 표준편차(무호흡/미감지 판별 보조)
    is_valid: bool = False         # 충분한 버퍼 및 유효 신호 여부
    filtered_wave: np.ndarray = field(default_factory=lambda: np.empty(0))


class BreathingSignalPipeline:
    """
    3~5단계 PC 호흡 신호 처리 파이프라인
      3단계: Static Clutter Removal(복소/DC 제거) -> atan2 위상 추출 -> Phase Unwrapping -> Detrend
      4단계: Median Filter(임펄스 노이즈 완화) -> 0.1~0.5 Hz Butterworth Bandpass Filter
      5단계: FFT(Zero-padding) & Peak Detection 동시 추정 -> Moving Average/Median 출력 안정화
    """

    def __init__(
        self,
        fs: float = 1.0 / 0.09,         # vital_signs_ISK_2m.cfg 기준 90ms 프레임 -> 11.111 Hz
        window_sec: float = 20.0,       # 분석 슬라이딩 윈도우 길이 (초)
        min_window_sec: float = 10.0,   # 최초 추정 시작 최소 데이터 길이 (초)
        lowcut_hz: float = 0.10,        # 6 BPM (호흡 대역 하한)
        highcut_hz: float = 0.50,       # 30 BPM (호흡 대역 상한)
        filter_order: int = 4,
        median_kernel: int = 5,
        smooth_window: int = 5,
    ):
        self.fs = float(fs)
        self.window_samples = int(round(window_sec * self.fs))
        self.min_samples = int(round(min_window_sec * self.fs))
        self.lowcut_hz = lowcut_hz
        self.highcut_hz = highcut_hz
        self.filter_order = filter_order
        self.median_kernel = median_kernel if median_kernel % 2 == 1 else median_kernel + 1

        # 4단계 Butterworth Bandpass Filter 계수 사전 설계 (수치 안정성이 높은 SOS 형식)
        nyq = 0.5 * self.fs
        if not (0.0 < lowcut_hz < highcut_hz < nyq):
            raise ValueError(f"유효하지 않은 필터 대역: [{lowcut_hz}, {highcut_hz}] Hz (Nyquist={nyq:.2f} Hz)")
        self.sos = butter(
            self.filter_order,
            [self.lowcut_hz, self.highcut_hz],
            btype="bandpass",
            fs=self.fs,
            output="sos",
        )

        # 실시간 스트리밍용 버퍼 (복소 I/Q 또는 실수 위상/파형 저장)
        self.raw_buffer: deque = deque(maxlen=self.window_samples)
        self.clutter_remover = StaticClutterRemover(alpha=0.97)
        self.br_fft_history: deque = deque(maxlen=smooth_window)
        self.br_peak_history: deque = deque(maxlen=smooth_window)
        self.br_final_history: deque = deque(maxlen=smooth_window)
        self._last_wave_chunk: Optional[np.ndarray] = None

    def reset(self) -> None:
        self.raw_buffer.clear()
        self.clutter_remover.reset()
        self.br_fft_history.clear()
        self.br_peak_history.clear()
        self.br_final_history.clear()
        self._last_wave_chunk = None

    def push_samples(self, samples: np.ndarray | List[float] | List[complex], deduplicate: bool = True) -> int:
        """
        새 샘플(복소 I/Q 또는 TI breathWaveform 15개 청크)을 슬라이딩 버퍼에 추가
        - TI 펌웨어가 동일 15샘플 버퍼를 중복 송신할 경우 deduplicate=True로 중복 누적 방지
        """
        arr = np.asarray(samples)
        if arr.size == 0:
            return 0
        if deduplicate and self._last_wave_chunk is not None and arr.shape == self._last_wave_chunk.shape:
            if np.allclose(arr, self._last_wave_chunk, atol=1e-7):
                return 0
        self._last_wave_chunk = arr.copy()
        self.raw_buffer.extend(arr.tolist())
        return int(arr.size)

    # -----------------------------------------------------------------
    # 3단계: Clutter Removal + 위상 추출(atan2) + Phase Unwrapping
    # -----------------------------------------------------------------
    def extract_and_unwrap_phase(self, signal: np.ndarray) -> np.ndarray:
        """
        복소 I/Q 신호 또는 실수 위상 신호에서 연속 위상 파형을 복원
        - 복소 I/Q 입력 시: 정적 클러터(평균 벡터) 차감 -> atan2 위상 추출 -> Phase Unwrapping -> 선형 드리프트 제거
        - 실수 위상/파형 입력 시: DC 오프셋 제거 -> Phase Unwrapping(±pi 점프 보정) -> 선형 드리프트 제거
        """
        arr = np.asarray(signal)
        if np.iscomplexobj(arr):
            # 정적 클러터 벡터 제거 후 위상 추출 (벽/가구 정적 반사 제거 핵심)
            clutter_removed = StaticClutterRemover.remove_window_mean(arr)
            wrapped_phase = np.angle(clutter_removed)  # atan2(Q, I)
            unwrapped = np.unwrap(wrapped_phase)
        else:
            real_sig = arr.astype(float)
            unwrapped = np.unwrap(real_sig)
            unwrapped = StaticClutterRemover.remove_window_mean(unwrapped)

        if len(unwrapped) >= 3:
            unwrapped = detrend(unwrapped, type="linear")
        return unwrapped

    # -----------------------------------------------------------------
    # 4단계: 임펄스 노이즈 완화(Median) + 0.1~0.5 Hz Butterworth BPF
    # -----------------------------------------------------------------
    def filter_breathing_band(self, unwrapped_phase: np.ndarray) -> np.ndarray:
        """Median Filter로 순간 움직임 스파이크를 억제하고 0.1~0.5 Hz 호흡 대역만 통과"""
        sig = np.asarray(unwrapped_phase, dtype=float)
        if len(sig) < max(self.median_kernel, 15):
            return sig

        # 순간적인 팔·몸 움직임으로 인한 임펄스 스파이크 완화
        if self.median_kernel >= 3 and len(sig) >= self.median_kernel:
            sig = medfilt(sig, kernel_size=self.median_kernel)

        # Zero-phase Butterworth Bandpass Filter (위상 지연 0)
        padlen = 3 * (2 * len(self.sos) + 1)
        if len(sig) <= padlen:
            return sig
        return sosfiltfilt(self.sos, sig)

    # -----------------------------------------------------------------
    # 5단계-A: 주파수 영역 호흡수 추정 (Hann Window + Zero-padding FFT)
    # -----------------------------------------------------------------
    def estimate_br_fft(self, filtered_wave: np.ndarray) -> Tuple[float, float]:
        """
        Hann 윈도우 + Zero-padding FFT + 2차 포물선 보간으로 호흡수(BPM) 추정

        Returns:
            (br_bpm, spectral_snr)
        """
        n = len(filtered_wave)
        if n < 16 or np.std(filtered_wave) < 1e-8:
            return 0.0, 0.0

        windowed = filtered_wave * np.hanning(n)
        # Zero-padding으로 주파수 해상도 확보 (4096pt @ 11.11Hz -> 0.0027Hz ≈ 0.16 BPM 간격)
        n_fft = max(4096, 1 << int(math.ceil(math.log2(n * 16))))
        spectrum = np.abs(np.fft.rfft(windowed, n=n_fft))
        freqs = np.fft.rfftfreq(n_fft, d=1.0 / self.fs)

        band_mask = (freqs >= self.lowcut_hz) & (freqs <= self.highcut_hz)
        band_freqs = freqs[band_mask]
        band_spec = spectrum[band_mask]
        if len(band_spec) == 0:
            return 0.0, 0.0

        peak_idx = int(np.argmax(band_spec))
        peak_val = float(band_spec[peak_idx])
        mean_val = float(np.mean(band_spec)) + 1e-12
        snr = peak_val / mean_val

        # 피크 주변 3점 포물선 보간(Parabolic Interpolation)으로 정밀도 향상
        peak_freq = float(band_freqs[peak_idx])
        if 0 < peak_idx < len(band_spec) - 1:
            alpha = band_spec[peak_idx - 1]
            beta = band_spec[peak_idx]
            gamma = band_spec[peak_idx + 1]
            denom = alpha - 2.0 * beta + gamma
            if abs(denom) > 1e-12:
                delta = 0.5 * (alpha - gamma) / denom
                df = band_freqs[1] - band_freqs[0]
                peak_freq = float(band_freqs[peak_idx] + np.clip(delta, -0.5, 0.5) * df)

        br_bpm = peak_freq * 60.0
        return round(br_bpm, 2), round(snr, 2)

    # -----------------------------------------------------------------
    # 5단계-B: 시간 영역 호흡수 추정 (Peak Detection)
    # -----------------------------------------------------------------
    def estimate_br_peak_detection(self, filtered_wave: np.ndarray) -> Tuple[float, int]:
        """
        시간 영역 피크 검출(scipy.signal.find_peaks) 기반 호흡수(BPM) 추정
        - 최소 피크 간격(1.8초 ≈ 33 BPM 상한)과 prominence 임계값으로 가짜 피크 차단
        - 피크 간격(IBI: Inter-Breath Interval)의 중앙값으로 호흡수 산출

        Returns:
            (br_bpm, num_peaks)
        """
        n = len(filtered_wave)
        std = float(np.std(filtered_wave))
        if n < 16 or std < 1e-8:
            return 0.0, 0

        norm_wave = (filtered_wave - np.mean(filtered_wave)) / std
        min_distance = max(1, int(round(self.fs * 1.8)))  # 30 BPM(2.0s) 대비 여유 1.8s
        peaks, _ = find_peaks(norm_wave, distance=min_distance, prominence=0.35)

        if len(peaks) < 2:
            return 0.0, int(len(peaks))

        intervals_sec = np.diff(peaks) / self.fs
        # 생리학적 유효 호흡 주기 [2.0초(30 BPM) ~ 10.0초(6 BPM)]만 채택
        valid_intervals = intervals_sec[(intervals_sec >= 1.9) & (intervals_sec <= 10.5)]
        if len(valid_intervals) == 0:
            return 0.0, int(len(peaks))

        median_period = float(np.median(valid_intervals))
        br_bpm = 60.0 / median_period
        return round(br_bpm, 2), int(len(peaks))

    # -----------------------------------------------------------------
    # 통합 실행: 윈도우 배열 직접 처리 또는 실시간 버퍼 처리
    # -----------------------------------------------------------------
    def process_window(self, signal_window: np.ndarray, is_moving: bool = False) -> BreathEstimationResult:
        """주어진 윈도우 신호에 3~5단계 파이프라인 전체를 적용"""
        arr = np.asarray(signal_window)
        if len(arr) < self.min_samples:
            return BreathEstimationResult(is_valid=False)

        # 3단계: Clutter Removal + 위상 추출/언래핑
        unwrapped = self.extract_and_unwrap_phase(arr)

        # 4단계: Median Filter + 0.1~0.5 Hz Butterworth BPF
        filtered = self.filter_breathing_band(unwrapped)
        sig_energy = float(np.std(filtered))

        # 움직임 구간(Motion Gating)에서는 이전 안정값 유지
        if is_moving:
            prev_final = float(np.median(self.br_final_history)) if self.br_final_history else 0.0
            prev_fft = float(np.median(self.br_fft_history)) if self.br_fft_history else 0.0
            prev_peak = float(np.median(self.br_peak_history)) if self.br_peak_history else 0.0
            return BreathEstimationResult(
                br_fft=round(prev_fft, 2),
                br_peak=round(prev_peak, 2),
                br_final=round(prev_final, 2),
                fft_peak_diff=round(abs(prev_fft - prev_peak), 2),
                signal_energy=round(sig_energy, 5),
                is_valid=prev_final > 0.0,
                filtered_wave=filtered,
            )

        # 5단계: FFT vs Peak Detection 동시 추정
        br_fft, fft_snr = self.estimate_br_fft(filtered)
        br_peak, peak_count = self.estimate_br_peak_detection(filtered)

        if 6.0 <= br_fft <= 30.0:
            self.br_fft_history.append(br_fft)
        if 6.0 <= br_peak <= 30.0:
            self.br_peak_history.append(br_peak)

        # 출력 안정화 (FFT 신뢰도가 높으면 FFT 우선, 두 방식이 근접하면 평균 결합)
        if br_fft > 0 and br_peak > 0:
            if abs(br_fft - br_peak) <= 2.5:
                combined = 0.6 * br_fft + 0.4 * br_peak
            else:
                combined = br_fft if fft_snr >= 3.0 else br_peak
        else:
            combined = br_fft if br_fft > 0 else br_peak

        if 6.0 <= combined <= 30.0:
            self.br_final_history.append(combined)

        if len(self.br_final_history) >= 3:
            br_final = float(np.median(self.br_final_history) * 0.6 + np.mean(self.br_final_history) * 0.4)
        elif self.br_final_history:
            br_final = float(np.mean(self.br_final_history))
        else:
            br_final = combined

        return BreathEstimationResult(
            br_fft=br_fft,
            br_peak=br_peak,
            br_final=round(br_final, 2),
            fft_snr=fft_snr,
            peak_count=peak_count,
            fft_peak_diff=round(abs(br_fft - br_peak), 2) if (br_fft > 0 and br_peak > 0) else 0.0,
            signal_energy=round(sig_energy, 5),
            is_valid=(br_final > 0.0),
            filtered_wave=filtered,
        )

    def process_current_buffer(self, is_moving: bool = False) -> BreathEstimationResult:
        """실시간 스트리밍 버퍼에 쌓인 데이터로 3~5단계 파이프라인 실행"""
        if len(self.raw_buffer) < self.min_samples:
            return BreathEstimationResult(is_valid=False)
        return self.process_window(np.asarray(list(self.raw_buffer)), is_moving=is_moving)


# =====================================================================
# 오차 비교기 (FFT vs Peak Detection vs TI 보드 출력 정량 비교)
# =====================================================================

class BRErrorComparator:
    """
    실제 호흡수(정답 BPM) 대비 FFT 방식과 Peak Detection 방식의 오차를 누적·비교
    - 작품 요약서 성능 목표: 호흡수 추정 오차 ±2회/분 이내 충족률 자동 계산
    """

    def __init__(self, target_error_bpm: float = 2.0):
        self.target_error_bpm = target_error_bpm
        self.records: List[Dict[str, float]] = []

    def add_record(
        self,
        br_fft: float,
        br_peak: float,
        br_final: float = 0.0,
        br_ti: Optional[float] = None,
        gt_bpm: Optional[float] = None,
    ) -> None:
        if br_fft <= 0 and br_peak <= 0:
            return
        rec: Dict[str, float] = {
            "br_fft": float(br_fft),
            "br_peak": float(br_peak),
            "br_final": float(br_final),
        }
        if br_ti is not None and br_ti > 0:
            rec["br_ti"] = float(br_ti)
        if gt_bpm is not None and gt_bpm > 0:
            rec["gt_bpm"] = float(gt_bpm)
        self.records.append(rec)

    def summarize(self) -> Dict[str, object]:
        if not self.records:
            return {"count": 0}

        fft_vals = np.array([r["br_fft"] for r in self.records if r["br_fft"] > 0], dtype=float)
        peak_vals = np.array([r["br_peak"] for r in self.records if r["br_peak"] > 0], dtype=float)
        both = [r for r in self.records if r["br_fft"] > 0 and r["br_peak"] > 0]
        agreement_diff = np.array([abs(r["br_fft"] - r["br_peak"]) for r in both], dtype=float)

        summary: Dict[str, object] = {
            "count": len(self.records),
            "fft_mean_bpm": round(float(np.mean(fft_vals)), 2) if len(fft_vals) else 0.0,
            "peak_mean_bpm": round(float(np.mean(peak_vals)), 2) if len(peak_vals) else 0.0,
            "fft_peak_mean_diff": round(float(np.mean(agreement_diff)), 2) if len(agreement_diff) else 0.0,
        }

        gt_records = [r for r in self.records if "gt_bpm" in r]
        if gt_records:
            for key, label in [("br_fft", "fft"), ("br_peak", "peak"), ("br_final", "final"), ("br_ti", "ti_board")]:
                errs = [abs(r[key] - r["gt_bpm"]) for r in gt_records if key in r and r[key] > 0]
                if not errs:
                    continue
                err_arr = np.array(errs, dtype=float)
                within_target = float(np.mean(err_arr <= self.target_error_bpm) * 100.0)
                summary[label] = {
                    "samples": len(err_arr),
                    "mae_bpm": round(float(np.mean(err_arr)), 3),
                    "rmse_bpm": round(float(np.sqrt(np.mean(err_arr ** 2))), 3),
                    "max_err_bpm": round(float(np.max(err_arr)), 3),
                    "within_2bpm_pct": round(within_target, 1),
                }

        return summary

    def format_report(self) -> str:
        s = self.summarize()
        if s.get("count", 0) == 0:
            return "비교할 유효 호흡수 측정 데이터가 없습니다."

        lines = [
            "=" * 68,
            f"📊 호흡수(BR) 추정 알고리즘 오차 비교 리포트 (유효 윈도우: {s['count']}개)",
            f"   - 평균 추정치: FFT={s['fft_mean_bpm']} BPM | Peak={s['peak_mean_bpm']} BPM (두 방식 평균 차이: {s['fft_peak_mean_diff']} BPM)",
        ]
        if any(k in s for k in ("fft", "peak", "final", "ti_board")):
            lines.append("-" * 68)
            lines.append(f"{'방식':<16} {'MAE(BPM)':>10} {'RMSE(BPM)':>10} {'MaxErr':>10} {'±2BPM충족률':>12}")
            lines.append("-" * 68)
            name_map = [
                ("fft", "FFT (주파수)"),
                ("peak", "Peak (시간)"),
                ("final", "Hybrid 안정화"),
                ("ti_board", "TI 보드 기본값"),
            ]
            for key, disp in name_map:
                if key in s:
                    m = s[key]
                    lines.append(
                        f"{disp:<16} {m['mae_bpm']:>10.3f} {m['rmse_bpm']:>10.3f} "
                        f"{m['max_err_bpm']:>10.3f} {m['within_2bpm_pct']:>11.1f}%"
                    )
        lines.append("=" * 68)
        return "\n".join(lines)


# =====================================================================
# 시뮬레이터 (레이더 미연결 시 PC에서 5단계 파이프라인 및 오차 비교 즉시 검증)
# =====================================================================

def simulate_60ghz_radar_session(
    gt_bpm: float = 15.0,
    duration_sec: float = 60.0,
    fs: float = 1.0 / 0.09,
    noise_std: float = 0.20,
    add_impulse_spikes: bool = True,
    seed: int = 42,
) -> Dict[str, object]:
    """
    TI IWR6843ISK (60.75 GHz, 파장 λ ≈ 4.94 mm) 물리 모델을 기반으로
    1~5단계 전체 파이프라인을 검증하는 시뮬레이션 생성 및 실행
      - 정적 벽/가구 클러터 복소 벡터 + 흉부 호흡 변위(±4mm) + 심박(±0.4mm, 72 BPM)
      - 3D 포인트 클라우드 (사람 클러스터 + 선풍기/잡음 점) -> DBSCAN + Kalman Tracking 검증
      - 가우시안 잡음 + 순간 몸 움직임 임펄스 스파이크 포함
    """
    rng = np.random.default_rng(seed)
    n_samples = int(round(duration_sec * fs))
    t = np.arange(n_samples) / fs

    # 물리 파라미터: 60.75 GHz -> λ = c / f ≈ 4.938 mm
    wavelength_mm = 299.792458 / 60.75
    f_breath = gt_bpm / 60.0
    f_heart = 1.2  # 72 BPM 심박 간섭 성분 (BPF로 제거되어야 함)

    # 흉부 변위 (mm): 호흡(진폭 2.0mm -> ±5.1 rad 위상 변화로 ±π 래핑 발생, Phase Unwrapping 검증) + 심박 + 느린 드리프트
    chest_disp_mm = (
        2.0 * np.sin(2.0 * np.pi * f_breath * t)
        + 0.30 * np.sin(2.0 * np.pi * f_heart * t + 0.7)
        + 0.03 * t  # 느린 자세 기울어짐 드리프트
    )

    # 복소 I/Q 레이더 반사 신호 = 강한 정적 클러터(벽/가구) + 대상 반사파 + 가우시안 노이즈
    phase_rad = (4.0 * np.pi / wavelength_mm) * chest_disp_mm
    static_clutter = 8.0 * np.exp(1j * 0.9)  # 정적 배경이 사람 반사보다 8배 큰 현실적 조건
    iq_signal = static_clutter + 1.0 * np.exp(1j * phase_rad)
    iq_signal += (rng.normal(0, noise_std, n_samples) + 1j * rng.normal(0, noise_std, n_samples))

    # 순간 임펄스 노이즈 (팔 움직임 등) 삽입
    if add_impulse_spikes:
        spike_indices = rng.choice(np.arange(15, n_samples - 15), size=max(3, int(duration_sec // 12)), replace=False)
        iq_signal[spike_indices] += rng.normal(0, 3.0, len(spike_indices)) + 1j * rng.normal(0, 3.0, len(spike_indices))

    # 1~2단계 (3D 포인트 클라우드 DBSCAN + Kalman Filter) 검증
    preprocessor = PointCloudPreprocessor(dbscan_eps=0.45, dbscan_min_samples=3)
    tracker = KalmanTracker3D(dt=1.0 / fs)
    true_pos = np.array([0.10, 1.50, 0.05])
    pos_errors = []

    # 3~5단계 (위상 언래핑 -> BPF -> FFT vs Peak Detection) 검증
    pipeline = BreathingSignalPipeline(fs=fs, window_sec=20.0, min_window_sec=12.0)
    comparator = BRErrorComparator(target_error_bpm=2.0)

    for i in range(n_samples):
        # 매 프레임 사람 주변 8~12개 포인트 + 흩어진 선풍기/잡음 포인트 2~3개 생성
        human_pts = np.column_stack([
            true_pos[0] + rng.normal(0, 0.12, 10),
            true_pos[1] + rng.normal(0, 0.12, 10),
            true_pos[2] + rng.normal(0, 0.15, 10),
            rng.normal(0, 0.02, 10),         # doppler
            rng.uniform(8.0, 20.0, 10),      # snr
        ])
        noise_pts = np.column_stack([
            rng.uniform(-2.5, 2.5, 3),
            rng.uniform(0.5, 4.5, 3),
            rng.uniform(-1.0, 1.5, 3),
            rng.uniform(-1.5, 1.5, 3),       # 선풍기 등 동적 노이즈 도플러
            rng.uniform(2.0, 6.0, 3),
        ])
        raw_pc = np.vstack([human_pts, noise_pts])

        _, centroid, _ = preprocessor.filter_and_cluster(raw_pc, prev_target_pos=tracker.position)
        track_state = tracker.update(centroid)
        if track_state["id"] is not None:
            est_pos = np.array([track_state["x"], track_state["y"], track_state["z"]])
            pos_errors.append(float(np.linalg.norm(est_pos - true_pos)))

        pipeline.push_samples([iq_signal[i]], deduplicate=False)

        # 15프레임(1.35초)마다 호흡수 추정 및 비교 기록 (TI Vital Signs 주기와 동일)
        if i >= pipeline.min_samples and (i % 15 == 0):
            res = pipeline.process_current_buffer(is_moving=bool(track_state["is_moving"]))
            if res.is_valid:
                comparator.add_record(
                    br_fft=res.br_fft,
                    br_peak=res.br_peak,
                    br_final=res.br_final,
                    gt_bpm=gt_bpm,
                )

    return {
        "gt_bpm": gt_bpm,
        "mean_pos_err_m": round(float(np.mean(pos_errors)), 4) if pos_errors else 0.0,
        "records": comparator.records,
        "summary": comparator.summarize(),
        "report": comparator.format_report(),
    }


def main():
    parser = argparse.ArgumentParser(description="5단계 mmWave 호흡 신호 처리 파이프라인 시뮬레이션 검증")
    parser.add_argument("--gt-bpm", type=float, nargs="+", default=[10.0, 15.0, 18.0, 24.0],
                        help="시뮬레이션할 실제 호흡수 목록 (기본: 10 15 18 24 BPM)")
    parser.add_argument("--duration", type=float, default=60.0, help="시나리오별 시뮬레이션 길이(초)")
    parser.add_argument("--noise", type=float, default=0.20, help="복소 I/Q 노이즈 표준편차")
    args = parser.parse_args()

    print("🔬 [5단계 mmWave 호흡 신호 처리 파이프라인 시뮬레이션 검증]")
    print("   파이프라인: Static Clutter Removal -> DBSCAN -> Kalman Filter -> Phase Unwrapping -> 0.1~0.5Hz BPF -> FFT vs Peak Detection\n")

    overall = BRErrorComparator(target_error_bpm=2.0)

    for idx, bpm in enumerate(args.gt_bpm):
        result = simulate_60ghz_radar_session(
            gt_bpm=bpm,
            duration_sec=args.duration,
            noise_std=args.noise,
            seed=42 + idx,
        )
        s = result["summary"]
        fft_m = s.get("fft", {})
        peak_m = s.get("peak", {})
        hyb_m = s.get("final", {})
        print(
            f"[시나리오 GT={bpm:4.1f} BPM] 3D 추적 오차={result['mean_pos_err_m']:.3f}m | "
            f"FFT MAE={fft_m.get('mae_bpm', 0):.3f} (±2BPM: {fft_m.get('within_2bpm_pct', 0):.0f}%) | "
            f"Peak MAE={peak_m.get('mae_bpm', 0):.3f} (±2BPM: {peak_m.get('within_2bpm_pct', 0):.0f}%) | "
            f"Hybrid MAE={hyb_m.get('mae_bpm', 0):.3f}"
        )

        # 각 시나리오의 모든 윈도우 추정치를 전체 비교기에 누적
        for rec in result["records"]:
            overall.add_record(
                br_fft=rec["br_fft"],
                br_peak=rec["br_peak"],
                br_final=rec["br_final"],
                gt_bpm=rec.get("gt_bpm"),
            )

    print("\n📌 전체 시나리오 종합 비교 (전체 슬라이딩 윈도우 집계):")
    print(overall.format_report())


if __name__ == "__main__":
    main()
