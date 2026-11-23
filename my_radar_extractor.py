import sys
import os
import csv
import time
import json
import inspect
import argparse
import threading
import requests
import numpy as np
from collections import deque

# 1. common 폴더 및 현재 폴더 경로 자동 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
common_dir = os.path.join(current_dir, 'common')
for p in (current_dir, common_dir):
    if p not in sys.path:
        sys.path.append(p)

from breath_signal_pipeline import (
    PointCloudPreprocessor,
    KalmanTracker3D,
    BreathingSignalPipeline,
    BreathEstimationResult,
    BRErrorComparator,
)

CLI_PORT = 'COM4'
DATA_PORT = 'COM3'
CLI_BAUD = 115200
DATA_BAUD = 921600
DEMO_NAME = 'Vital Signs with People Tracking'

DEFAULT_CFG_PATH = r'C:\capstone\radar_toolbox_4_20_00_07\source\ti\examples\Industrial_and_Personal_Electronics\Vital_Signs\Vital_Signs_With_People_Tracking\chirp_configs\vital_signs_ISK_2m.cfg'
LOCAL_CFG_PATH = os.path.join(current_dir, 'vital_signs_ISK_2m.cfg')
CFG_FILE_PATH = DEFAULT_CFG_PATH if os.path.exists(DEFAULT_CFG_PATH) else LOCAL_CFG_PATH

# =====================================================================
# [정현우 님(DB 서버) & 천채원 님(3D 웹 UI) 연동 설정]
# 서버 컴퓨터의 IP 주소와 포트 번호로 나중에 변경하세요!
# =====================================================================
DB_SERVER_URL_EVENT = "http://127.0.0.1:8000/api/v1/vital-events"      # 주의/응급 상세 로그 수신 주소
DB_SERVER_URL_SUMMARY = "http://127.0.0.1:8000/api/v1/vital-summary"   # 정상 요약 통계 수신 주소 (60초 주기)
REALTIME_SERVER_URL = "http://127.0.0.1:8000/api/v1/realtime-stream"   # 3D UI 실시간 스트리밍 수신 주소 (0.2초 주기)

SUMMARY_INTERVAL_SEC = 60       # 60초(1분)마다 정상 요약 통계 전송
REALTIME_INTERVAL_SEC = 0.2     # 0.2초마다 실시간 3D 위치 및 호흡 상태 전송 (화면 갱신 1초 이내 목표 달성)

# 로컬 백업 및 UI 연동 파일명 설정
EVENT_LOG_FILE = os.path.join(current_dir, "db_event_logs.jsonl")
SUMMARY_LOG_FILE = os.path.join(current_dir, "db_summary_stats.jsonl")
REALTIME_UI_FILE = os.path.join(current_dir, "realtime_ui_stream.json")
EXPERIMENT_LOG_DIR = os.path.join(current_dir, "experiment_logs")


def parse_frame_period_sec(cfg_path: str, default_sec: float = 0.09) -> float:
    """TI .cfg 파일의 frameCfg에서 프레임 주기(ms)를 읽어 초 단위로 반환"""
    try:
        with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as f:
            for line in f:
                stripped = line.strip()
                if stripped.startswith('frameCfg'):
                    parts = stripped.split()
                    if len(parts) >= 6:
                        period_ms = float(parts[5])
                        if period_ms > 0:
                            return period_ms / 1000.0
    except Exception:
        pass
    return default_sec


class ExperimentCSVLogger:
    """
    2차 멘토링 실험 일지 및 FFT vs Peak Detection 오프라인 재분석용 CSV 로거
    - 매 바이탈 패킷의 원시 breathWaveform(15샘플)과 각 알고리즘 추정치를 함께 저장
    """

    HEADER = [
        "timestamp",
        "frame_num",
        "label",
        "gt_bpm",
        "raw_points",
        "clustered_points",
        "track_x",
        "track_y",
        "track_z",
        "speed",
        "range_bin",
        "is_moving",
        "breath_dev",
        "ti_br",
        "pc_br_fft",
        "pc_br_peak",
        "pc_br_final",
        "fft_snr",
        "peak_count",
        "fft_peak_diff",
        "status",
        "waveform_json",
    ]

    def __init__(self, log_dir: str = EXPERIMENT_LOG_DIR, label: str = "exp", enabled: bool = True):
        self.enabled = enabled
        self.label = label
        self.filepath = None
        self._file = None
        self._writer = None

        if self.enabled:
            os.makedirs(log_dir, exist_ok=True)
            ts = time.strftime("%Y%m%d_%H%M%S")
            safe_label = "".join(c if (c.isalnum() or c in "-_") else "_" for c in label)
            self.filepath = os.path.join(log_dir, f"radar_{safe_label}_{ts}.csv")
            self._file = open(self.filepath, "w", newline="", encoding="utf-8-sig")
            self._writer = csv.writer(self._file)
            self._writer.writerow(self.HEADER)
            self._file.flush()

    def log_row(
        self,
        frame_num: int,
        gt_bpm: float | None,
        pc_stats: dict,
        target_info: dict,
        breath_dev: float,
        ti_br: float,
        est: BreathEstimationResult,
        status: str,
        waveform_chunk: np.ndarray | list | None,
    ) -> None:
        if not self.enabled or self._writer is None:
            return
        wave_list = np.round(np.asarray(waveform_chunk, dtype=float), 6).tolist() if waveform_chunk is not None else []
        self._writer.writerow([
            time.strftime("%Y-%m-%d %H:%M:%S"),
            frame_num,
            self.label,
            "" if gt_bpm is None else gt_bpm,
            pc_stats.get("raw_count", 0),
            pc_stats.get("clustered_count", 0),
            target_info.get("x", 0.0),
            target_info.get("y", 0.0),
            target_info.get("z", 0.0),
            target_info.get("speed", 0.0),
            target_info.get("range_bin", 0),
            int(bool(target_info.get("is_moving", False))),
            round(breath_dev, 5),
            round(ti_br, 2),
            est.br_fft,
            est.br_peak,
            est.br_final,
            est.fft_snr,
            est.peak_count,
            est.fft_peak_diff,
            status,
            json.dumps(wave_list),
        ])
        self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None
            self._writer = None


class DBDispatcherForHyunwoo:
    """정현우 님의 14일 시계열 DB 최적화 구조 및 천채원 님의 실시간 3D UI에 맞춰 데이터를 전송하는 클래스"""

    def __init__(self, enable_http: bool = True):
        self.enable_http = enable_http
        # 리스트 대신 단일 숫자 변수만 선언 -> 메모리 공간 복잡도 O(1) 보장!
        self.br_sum = 0.0
        self.br_count = 0
        self.br_min = 999.0
        self.br_max = 0.0
        self.hr_sum = 0.0
        self.hr_count = 0

        self.active_frames = 0
        self.last_summary_time = time.time()

        # 주의/응급 지속 시간 측정용 변수
        self.abnormal_start_time = None
        self.last_event_sent_time = 0

        # 실시간 3D UI 스트리밍 주기 제어용 변수
        self.last_realtime_sent_time = 0

    def _post_async(self, url, payload, is_realtime=False, log_filename=""):
        """레이더 시리얼 수신이 멈추지 않도록 백그라운드 스레드에서 HTTP POST 전송"""
        if not self.enable_http:
            return
        try:
            res = requests.post(url, json=payload, timeout=0.3)
            if not is_realtime:
                if res.status_code in [200, 201]:
                    print(f"   🚀 [DB 서버 전송 성공] -> {url}")
                else:
                    print(f"   ⚠️ [DB 서버 응답 코드: {res.status_code}] (로컬 파일 {log_filename} 백업 완료)")
        except requests.exceptions.RequestException:
            if not is_realtime:
                print(f"   💾 [DB 로컬 백업 완료] ({log_filename}에 저장됨 - 서버 연결 대기 중)")

    def send_to_server(self, url, payload, log_filename):
        """정현우 님 서버로 HTTP POST 전송 (서버 미구동 시 로컬 파일에 자동 백업)"""
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        if self.enable_http:
            threading.Thread(
                target=self._post_async,
                args=(url, payload, False, log_filename),
                daemon=True,
            ).start()

    def send_realtime_ui_stream(
        self,
        status,
        action_state,
        clean_br,
        clean_hr,
        breath_dev,
        target_info,
        valid_num_points,
        clean_pc,
        est: BreathEstimationResult | None = None,
        ti_br: float = 0.0,
    ):
        """0.2초마다 3D 웹 대시보드(천채원 님) 및 융합/낙상 모듈(김나은/김유진 님)로 실시간 데이터 전송"""
        now = time.time()
        if now - self.last_realtime_sent_time < REALTIME_INTERVAL_SEC:
            return

        self.last_realtime_sent_time = now
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

        pc_list = np.round(clean_pc[:, :3], 2).tolist() if len(clean_pc) > 0 else []

        vital_block = {
            "breath_rate": clean_br,
            "heart_rate": clean_hr,
            "breath_deviation": round(breath_dev, 5),
            "br_fft": est.br_fft if est else 0.0,
            "br_peak": est.br_peak if est else 0.0,
            "br_ti_board": round(ti_br, 1),
        }

        realtime_payload = {
            "packet_type": "REALTIME_UI_STREAM",
            "timestamp": timestamp_str,
            "status": status,               # NORMAL, WARNING, EMERGENCY, MEASURING
            "action": action_state,         # STANDING, SITTING, LYING, NONE
            "position": {
                "id": target_info["id"],
                "x": target_info["x"],
                "y": target_info["y"],
                "z": target_info["z"],
                "speed": target_info["speed"],
                "range_bin": target_info.get("range_bin", 0),
                "is_moving": target_info["is_moving"],
            },
            "vital": vital_block,
            "valid_point_count": valid_num_points,
            "point_cloud": pc_list,
        }

        try:
            with open(REALTIME_UI_FILE, "w", encoding="utf-8") as f:
                json.dump(realtime_payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        if self.enable_http:
            threading.Thread(
                target=self._post_async,
                args=(REALTIME_SERVER_URL, realtime_payload, True),
                daemon=True,
            ).start()

    def handle_data(self, status, clean_br, clean_hr, breath_dev, target_info):
        now = time.time()
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

        if target_info["id"] is not None:
            self.active_frames += 1

        if status == "NORMAL" and clean_br > 0:
            self.br_sum += clean_br
            self.br_count += 1
            self.br_min = min(self.br_min, clean_br)
            self.br_max = max(self.br_max, clean_br)
            if clean_hr > 0:
                self.hr_sum += clean_hr
                self.hr_count += 1
            self.abnormal_start_time = None

        elif status in ["WARNING", "EMERGENCY"]:
            if self.abnormal_start_time is None:
                self.abnormal_start_time = now

            duration_sec = round(now - self.abnormal_start_time, 1)

            trigger_sec = 3.0 if status == "EMERGENCY" else 10.0
            if duration_sec >= trigger_sec and (now - self.last_event_sent_time >= 10.0):
                event_type = (
                    "APNEA_SUSPECTED"
                    if status == "EMERGENCY"
                    else ("BRADYPNEXIA_LOW" if clean_br <= 10.0 else "TACHYPNEXIA_HIGH")
                )

                event_payload = {
                    "packet_type": "DETAILED_EVENT_LOG",
                    "timestamp": timestamp_str,
                    "status_level": status,
                    "event_type": event_type,
                    "duration_sec": duration_sec,
                    "breath_rate": clean_br,
                    "heart_rate": clean_hr,
                    "breath_deviation": breath_dev,
                    "location": {
                        "x": target_info["x"],
                        "y": target_info["y"],
                        "z": target_info["z"],
                    },
                }
                print(f"\n🚨 [DB 전송 - 상세 이벤트 로그] {json.dumps(event_payload, ensure_ascii=False)}")
                self.send_to_server(DB_SERVER_URL_EVENT, event_payload, EVENT_LOG_FILE)
                self.last_event_sent_time = now

        if now - self.last_summary_time >= SUMMARY_INTERVAL_SEC:
            if self.br_count > 0:
                avg_br = round(self.br_sum / self.br_count, 1)
                min_br = round(self.br_min, 1)
                max_br = round(self.br_max, 1)
                avg_hr = round(self.hr_sum / self.hr_count, 1) if self.hr_count > 0 else 0.0

                summary_payload = {
                    "packet_type": "PERIODIC_SUMMARY_STATS",
                    "timestamp": timestamp_str,
                    "status_level": "NORMAL",
                    "period_seconds": SUMMARY_INTERVAL_SEC,
                    "avg_breath_rate": avg_br,
                    "min_breath_rate": min_br,
                    "max_breath_rate": max_br,
                    "valid_sample_count": self.br_count,
                    "avg_heart_rate": avg_hr,
                    "active_time_sec": round(self.active_frames * 0.09, 1),
                }
                print(f"\n📊 [DB 전송 - 주기적 요약 통계] {json.dumps(summary_payload, ensure_ascii=False)}")
                self.send_to_server(DB_SERVER_URL_SUMMARY, summary_payload, SUMMARY_LOG_FILE)

            self.br_sum = 0.0
            self.br_count = 0
            self.br_min = 999.0
            self.br_max = 0.0
            self.hr_sum = 0.0
            self.hr_count = 0
            self.active_frames = 0
            self.last_summary_time = now


class AdvancedVitalProcessor:
    """
    [김기은 님 핵심 파트] 5단계 PC 레이더 신호 처리 및 호흡(BR) 감지 프로세서
      1단계: Static Clutter Removal + ROI/SNR 필터 + DBSCAN 클러스터링
      2단계: 3D Kalman Filter 대상 추적 및 Range Bin 산출
      3단계: Phase Extraction & Unwrapping + 선형 드리프트 제거
      4단계: Median Filter + 0.1~0.5 Hz Butterworth Bandpass Filter
      5단계: FFT(Zero-padding) vs Peak Detection 동시 추정 + 안정화 + 상태 분류
    """

    def __init__(self, fs: float = 1.0 / 0.09, window_sec: float = 20.0, min_window_sec: float = 10.0, smooth_size: int = 7):
        self.fs = fs
        self.pc_preprocessor = PointCloudPreprocessor(
            roi={"x_min": -3.0, "x_max": 3.0, "y_min": 0.1, "y_max": 5.0, "z_min": -2.0, "z_max": 2.0},
            min_snr=1.0,
            dbscan_eps=0.50,
            dbscan_min_samples=3,
        )
        self.kalman_tracker = KalmanTracker3D(dt=1.0 / fs)
        self.breath_pipeline = BreathingSignalPipeline(
            fs=fs,
            window_sec=window_sec,
            min_window_sec=min_window_sec,
            lowcut_hz=0.10,
            highcut_hz=0.50,
            smooth_window=smooth_size,
        )
        # 초기 10초 워밍업 구간 보조용 TI 보드 BR/HR 버퍼
        self.ti_br_buffer = deque(maxlen=smooth_size)
        self.hr_buffer = deque(maxlen=smooth_size)

    def process_point_cloud_and_track(self, raw_point_cloud, ti_track_data=None):
        """
        1~2단계: 포인트 클라우드 정적 노이즈 제거 + DBSCAN 클러스터링 + PC Kalman Filter 추적
        - 포인트가 일시적으로 적어 DBSCAN 클러스터가 안 잡히면 TI 보드 trackData 좌표를 보조 측정치로 활용
        """
        clean_pc, centroid, pc_stats = self.pc_preprocessor.filter_and_cluster(
            raw_point_cloud, prev_target_pos=self.kalman_tracker.position
        )

        # DBSCAN 중심점이 없을 때 TI tracker 좌표로 부드럽게 폴백
        measurement = centroid
        if measurement is None and ti_track_data is not None and len(ti_track_data) > 0:
            t = ti_track_data[0]
            measurement = np.array([float(t[1]), float(t[2]), float(t[3])], dtype=float)

        target_info = self.kalman_tracker.update(measurement)
        valid_num_points = len(clean_pc)
        return clean_pc, valid_num_points, target_info, pc_stats

    def estimate_posture(self, target_id, z_height):
        """Z축 높이 기반 3D 아바타 자세(Action) 1차 추정"""
        if target_id is None:
            return "NONE"
        if z_height > 0.4:
            return "STANDING"
        elif z_height > -0.2:
            return "SITTING"
        else:
            return "LYING"

    def process_vitals(self, breath_waveform, raw_br_ti, raw_hr_ti, breath_dev, is_moving, valid_num_points):
        """
        3~5단계: 위상 언래핑 -> 0.1~0.5Hz BPF -> FFT / Peak Detection 추정 및 정상·주의·응급 판별
        """
        # PC 파이프라인에 새 호흡 파형 청크 추가 (TI Vital Signs TLV의 15샘플 배열 또는 I/Q 샘플)
        if breath_waveform is not None and len(breath_waveform) > 0:
            self.breath_pipeline.push_samples(breath_waveform, deduplicate=True)

        est = self.breath_pipeline.process_current_buffer(is_moving=is_moving)

        # 워밍업/비교용 TI 보드 출력값 버퍼 갱신
        if not is_moving and (6.0 <= raw_br_ti <= 35.0) and breath_dev >= 0.002:
            self.ti_br_buffer.append(raw_br_ti)
        if not is_moving and (40.0 <= raw_hr_ti <= 150.0) and breath_dev >= 0.002:
            self.hr_buffer.append(raw_hr_ti)

        # 최종 호흡수: PC 파이프라인 버퍼(10초 이상)가 준비되면 PC 자체 추정치 채택, 워밍업 중엔 TI 보조값 사용
        if est.is_valid and est.br_final > 0.0:
            clean_br = est.br_final
        elif len(self.ti_br_buffer) >= 3:
            clean_br = float(np.median(self.ti_br_buffer) * 0.6 + np.mean(self.ti_br_buffer) * 0.4)
        elif len(self.ti_br_buffer) > 0:
            clean_br = float(np.mean(self.ti_br_buffer))
        else:
            clean_br = 0.0

        clean_hr = float(np.median(self.hr_buffer)) if len(self.hr_buffer) > 0 else 0.0

        # 3단계 상태 판별 (정상 / 주의 / 응급)
        if clean_br == 0.0:
            status = "MEASURING"
        elif clean_br < 10.0 or clean_br > 25.0:
            status = "WARNING"
        else:
            status = "NORMAL"

        # 유효 포인트 존재 + 정지 상태인데 호흡 편차가 극도로 낮으면 무호흡 응급 판정
        if valid_num_points >= 5 and not is_moving and breath_dev < 0.0008:
            status = "EMERGENCY"

        return round(clean_br, 1), round(clean_hr, 1), status, est


# =====================================================================
# 모드 1: 실시간 레이더(TI IWR6843ISK) 실행
# =====================================================================

def run_live_radar(args):
    from gui_parser import UARTParser
    import demo_defines

    demo_name = getattr(demo_defines, 'DEMO_VITALS', DEMO_NAME)
    cfg_path = args.cfg if args.cfg else CFG_FILE_PATH
    frame_period_sec = parse_frame_period_sec(cfg_path, default_sec=0.09)
    fs = 1.0 / frame_period_sec

    parser = UARTParser(type='DoubleCOMPort')
    processor = AdvancedVitalProcessor(fs=fs, window_sec=args.window_sec, min_window_sec=10.0)
    db_dispatcher = DBDispatcherForHyunwoo(enable_http=not args.no_http)
    csv_logger = ExperimentCSVLogger(log_dir=args.log_dir, label=args.label, enabled=not args.no_csv)
    comparator = BRErrorComparator(target_error_bpm=2.0)

    sig = list(inspect.signature(parser.connectComPorts).parameters.keys())
    if sig == ['cliCom', 'cliBaud', 'dataCom', 'dataBaud']:
        parser.connectComPorts(args.cli_port, CLI_BAUD, args.data_port, DATA_BAUD)
    else:
        parser.connectComPorts(args.cli_port, args.data_port, CLI_BAUD, DATA_BAUD)

    with open(cfg_path, 'r', encoding='utf-8', errors='ignore') as f:
        cfg_lines = f.readlines()
    parser.sendCfg(cfg_lines)

    print(f"-> 설정 완료! (fs={fs:.2f} Hz, 윈도우={args.window_sec}s, 실험라벨='{args.label}', GT={args.gt_bpm})")
    if csv_logger.filepath:
        print(f"-> 실험 CSV 자동 저장 위치: {csv_logger.filepath}")
    print("-> 5단계 PC 파이프라인(Clutter/DBSCAN -> Kalman -> Unwrap -> 0.1~0.5Hz BPF -> FFT vs Peak) 작동 중... (Ctrl+C로 종료 및 비교 리포트 출력)\n")

    try:
        while True:
            try:
                output_dict = parser.readAndParseUartDoubleCOMPort(demo=demo_name)
            except Exception:
                continue

            if not output_dict:
                time.sleep(0.01)
                continue

            frame_num = output_dict.get('frameNum', 0)
            raw_targets = output_dict.get('numDetectedTracks', 0)
            raw_pc = output_dict.get('pointCloud', None)
            ti_tracks = output_dict.get('trackData', None)

            # 1~2단계: PC 포인트 클라우드 노이즈 제거(DBSCAN) + Kalman Filter 추적
            clean_pc, valid_num_points, target_info, pc_stats = processor.process_point_cloud_and_track(
                raw_pc, ti_track_data=ti_tracks
            )
            action_state = processor.estimate_posture(target_info["id"], target_info["z"])

            # 3~5단계: 호흡 파형 신호 처리 (FFT vs Peak Detection 동시 추정)
            if 'vitals' in output_dict:
                v = output_dict['vitals']
                raw_br_ti = float(v.get('breathRate', 0.0))
                raw_hr_ti = float(v.get('heartRate', 0.0))
                breath_dev = float(v.get('breathDeviation', 0.0))
                breath_wave = v.get('breathWaveform', [])

                clean_br, clean_hr, status, est = processor.process_vitals(
                    breath_wave, raw_br_ti, raw_hr_ti, breath_dev,
                    target_info["is_moving"], valid_num_points
                )

                if est.is_valid:
                    comparator.add_record(
                        br_fft=est.br_fft,
                        br_peak=est.br_peak,
                        br_final=est.br_final,
                        br_ti=raw_br_ti,
                        gt_bpm=args.gt_bpm,
                    )

                buf_pct = min(100, int(len(processor.breath_pipeline.raw_buffer) / processor.breath_pipeline.min_samples * 100))
                print(
                    f"[Frame {frame_num:5d}] 유효점:{valid_num_points:2d} | 위치:({target_info['x']:5.2f},{target_info['y']:4.2f},{target_info['z']:5.2f})m "
                    f"(Bin:{target_info['range_bin']:2d}) | 자세:{action_state:<8} | "
                    f"FFT:{est.br_fft:5.1f} / Peak:{est.br_peak:5.1f} / TI:{raw_br_ti:5.1f} -> 최종:{clean_br:5.1f} BPM "
                    f"(버퍼:{buf_pct:3d}%) | [{status}]"
                )

                csv_logger.log_row(
                    frame_num=frame_num,
                    gt_bpm=args.gt_bpm,
                    pc_stats=pc_stats,
                    target_info=target_info,
                    breath_dev=breath_dev,
                    ti_br=raw_br_ti,
                    est=est,
                    status=status,
                    waveform_chunk=breath_wave,
                )

                if raw_targets > 0 or target_info["id"] is not None:
                    db_dispatcher.handle_data(status, clean_br, clean_hr, round(breath_dev, 5), target_info)

                db_dispatcher.send_realtime_ui_stream(
                    status, action_state, clean_br, clean_hr, breath_dev,
                    target_info, valid_num_points, clean_pc, est=est, ti_br=raw_br_ti
                )

            time.sleep(0.01)

    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")
    finally:
        csv_logger.close()
        print("\n" + comparator.format_report())
        if csv_logger.filepath:
            print(f"💾 저장된 실험 CSV 파일: {csv_logger.filepath}")


# =====================================================================
# 모드 2: 레이더 미연결 시 PC 시뮬레이션 테스트 (--simulate)
# =====================================================================

def run_simulation_mode(args):
    gt_bpm = args.gt_bpm if args.gt_bpm is not None else 15.0
    duration_sec = args.duration
    fs = 1.0 / 0.09
    n_frames = int(round(duration_sec * fs))
    rng = np.random.default_rng(42)

    wavelength_mm = 299.792458 / 60.75
    t = np.arange(n_frames) / fs
    f_breath = gt_bpm / 60.0
    chest_disp_mm = (
        2.0 * np.sin(2.0 * np.pi * f_breath * t)
        + 0.30 * np.sin(2.0 * np.pi * 1.2 * t + 0.7)
        + 0.03 * t
    )
    phase_rad = (4.0 * np.pi / wavelength_mm) * chest_disp_mm
    iq_signal = 8.0 * np.exp(1j * 0.9) + 1.0 * np.exp(1j * phase_rad)
    iq_signal += rng.normal(0, 0.20, n_frames) + 1j * rng.normal(0, 0.20, n_frames)

    # 순간 움직임 스파이크 삽입
    spike_idx = rng.choice(np.arange(15, n_frames - 15), size=max(3, int(duration_sec // 12)), replace=False)
    iq_signal[spike_idx] += rng.normal(0, 2.5, len(spike_idx)) + 1j * rng.normal(0, 2.5, len(spike_idx))

    processor = AdvancedVitalProcessor(fs=fs, window_sec=args.window_sec, min_window_sec=10.0)
    csv_logger = ExperimentCSVLogger(log_dir=args.log_dir, label=f"sim_{int(gt_bpm)}bpm", enabled=not args.no_csv)
    comparator = BRErrorComparator(target_error_bpm=2.0)
    clutter_center = np.mean(iq_signal)

    true_pos = np.array([0.05, 1.40, 0.02])
    print(f"🧪 [시뮬레이션 모드] GT={gt_bpm:.1f} BPM, 길이={duration_sec:.0f}초, fs={fs:.2f} Hz")
    print("   1~5단계 파이프라인(Clutter/DBSCAN -> Kalman -> Phase Unwrap -> 0.1~0.5Hz BPF -> FFT vs Peak) 검증 중...\n")

    for frame_num in range(n_frames):
        # 3D 포인트 클라우드 (사람 10개 + 잡음 3개)
        human_pts = np.column_stack([
            true_pos[0] + rng.normal(0, 0.10, 10),
            true_pos[1] + rng.normal(0, 0.10, 10),
            true_pos[2] + rng.normal(0, 0.12, 10),
            rng.normal(0, 0.02, 10),
            rng.uniform(9.0, 18.0, 10),
        ])
        noise_pts = np.column_stack([
            rng.uniform(-2.5, 2.5, 3),
            rng.uniform(0.5, 4.5, 3),
            rng.uniform(-1.0, 1.5, 3),
            rng.uniform(-1.5, 1.5, 3),
            rng.uniform(2.0, 6.0, 3),
        ])
        raw_pc = np.vstack([human_pts, noise_pts])
        _, valid_num_points, target_info, pc_stats = processor.process_point_cloud_and_track(raw_pc)

        # 15프레임마다 TI Vital Signs 패킷과 동일한 15샘플 청크 전달
        if (frame_num + 1) % 15 == 0:
            chunk_iq = iq_signal[frame_num - 14: frame_num + 1]
            # CSV에는 복소 I/Q를 바로 리플레이 가능하도록 Clutter 제거 후 위상 파형으로 기록
            clean_br, _, status, est = processor.process_vitals(
                chunk_iq, raw_br_ti=0.0, raw_hr_ti=72.0, breath_dev=0.04,
                is_moving=target_info["is_moving"], valid_num_points=valid_num_points
            )
            if est.is_valid:
                comparator.add_record(
                    br_fft=est.br_fft,
                    br_peak=est.br_peak,
                    br_final=est.br_final,
                    gt_bpm=gt_bpm,
                )
                print(
                    f"[Frame {frame_num + 1:4d}] 위치:({target_info['x']:5.2f},{target_info['y']:4.2f},{target_info['z']:5.2f})m "
                    f"(Bin:{target_info['range_bin']:2d}) | FFT:{est.br_fft:5.2f} | Peak:{est.br_peak:5.2f} | "
                    f"최종:{clean_br:5.1f} BPM | 차이:{est.fft_peak_diff:4.2f} | [{status}]"
                )

            # 리플레이 가능한 위상 파형 청크를 CSV에 저장 (Clutter 제거 후 wrapped phase)
            chunk_phase = np.angle(chunk_iq - clutter_center)
            csv_logger.log_row(
                frame_num=frame_num + 1,
                gt_bpm=gt_bpm,
                pc_stats=pc_stats,
                target_info=target_info,
                breath_dev=0.04,
                ti_br=0.0,
                est=est,
                status=status,
                waveform_chunk=chunk_phase,
            )

    csv_logger.close()
    print("\n" + comparator.format_report())
    if csv_logger.filepath:
        print(f"💾 시뮬레이션 CSV 저장 완료 (오프라인 리플레이 테스트 가능): {csv_logger.filepath}")


# =====================================================================
# 모드 3: 녹화 CSV 오프라인 재분석 및 오차 비교 (--replay <csv>)
# =====================================================================

def run_replay_mode(args):
    csv_path = args.replay
    if not os.path.exists(csv_path):
        print(f"❌ CSV 파일을 찾을 수 없습니다: {csv_path}")
        return

    fs = 1.0 / 0.09
    pipeline = BreathingSignalPipeline(fs=fs, window_sec=args.window_sec, min_window_sec=10.0)
    comparator = BRErrorComparator(target_error_bpm=2.0)

    print(f"📂 [CSV 오프라인 재분석 모드] 파일: {csv_path} (윈도우={args.window_sec}s)\n")
    rows_processed = 0

    with open(csv_path, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            wave_str = row.get("waveform_json", "[]")
            try:
                wave_chunk = json.loads(wave_str)
            except Exception:
                wave_chunk = []
            if not wave_chunk:
                continue

            is_moving = bool(int(row.get("is_moving", 0)))
            ti_br = float(row.get("ti_br", 0.0) or 0.0)
            row_gt = row.get("gt_bpm", "")
            gt_bpm = args.gt_bpm if args.gt_bpm is not None else (float(row_gt) if row_gt not in ("", None) else None)

            pipeline.push_samples(wave_chunk, deduplicate=True)
            est = pipeline.process_current_buffer(is_moving=is_moving)
            rows_processed += 1

            if est.is_valid:
                comparator.add_record(
                    br_fft=est.br_fft,
                    br_peak=est.br_peak,
                    br_final=est.br_final,
                    br_ti=ti_br if ti_br > 0 else None,
                    gt_bpm=gt_bpm,
                )

    print(f"처리된 패킷 수: {rows_processed}개")
    print(comparator.format_report())


def build_arg_parser():
    p = argparse.ArgumentParser(description="TI IWR6843ISK 5단계 PC 신호 처리 및 호흡수(BR) 추정기")
    p.add_argument("--cli-port", default=CLI_PORT, help=f"CLI COM 포트 (기본: {CLI_PORT})")
    p.add_argument("--data-port", default=DATA_PORT, help=f"DATA COM 포트 (기본: {DATA_PORT})")
    p.add_argument("--cfg", default=None, help="레이더 .cfg 파일 경로")
    p.add_argument("--window-sec", type=float, default=20.0, help="호흡수 추정 슬라이딩 윈도우 길이(초, 기본: 20.0)")
    p.add_argument("--gt-bpm", type=float, default=None, help="실험 시 실제 정답 호흡수(BPM) — 입력 시 오차 비교표 자동 생성")
    p.add_argument("--label", default="wall_2m", help="실험 조건 라벨 (예: wall_2m, diag_2m, ceiling_2m)")
    p.add_argument("--log-dir", default=EXPERIMENT_LOG_DIR, help="CSV 실험 로그 저장 폴더 경로")
    p.add_argument("--no-csv", action="store_true", help="CSV 실험 로그 저장 비활성화")
    p.add_argument("--no-http", action="store_true", help="DB/웹 서버 HTTP 전송 비활성화")
    p.add_argument("--simulate", action="store_true", help="레이더 미연결 시 PC 시뮬레이션 검증 모드 실행")
    p.add_argument("--duration", type=float, default=60.0, help="시뮬레이션 실행 길이(초, 기본: 60)")
    p.add_argument("--replay", default=None, help="저장된 CSV 실험 파일을 오프라인으로 재분석해 FFT vs Peak 비교")
    return p


def main():
    args = build_arg_parser().parse_args()
    if args.replay:
        run_replay_mode(args)
    elif args.simulate:
        run_simulation_mode(args)
    else:
        run_live_radar(args)


if __name__ == '__main__':
    main()
