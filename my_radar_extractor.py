import sys
import os
import time
import json
import inspect
import threading
import requests
import numpy as np
from collections import deque

# 1. common 폴더 경로 자동 추가
current_dir = os.path.dirname(os.path.abspath(__file__))
common_dir = os.path.join(current_dir, 'common')
if common_dir not in sys.path:
    sys.path.append(common_dir)

from gui_parser import UARTParser
import demo_defines

CLI_PORT = 'COM4'
DATA_PORT = 'COM3'
CLI_BAUD = 115200
DATA_BAUD = 921600
DEMO_NAME = getattr(demo_defines, 'DEMO_Vital_Signs', 'Vital Signs with People Tracking')

CFG_FILE_PATH = r'C:\capstone\radar_toolbox_4_20_00_07\source\ti\examples\Industrial_and_Personal_Electronics\Vital_Signs\Vital_Signs_With_People_Tracking\chirp_configs\vital_signs_ISK_2m.cfg'

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
EVENT_LOG_FILE = "db_event_logs.jsonl"
SUMMARY_LOG_FILE = "db_summary_stats.jsonl"
REALTIME_UI_FILE = "realtime_ui_stream.json"  # 3D 웹 UI에서 즉시 읽어갈 수 있는 최신 상태 파일


class DBDispatcherForHyunwoo:
    """정현우 님의 14일 시계열 DB 최적화 구조 및 천채원 님의 실시간 3D UI에 맞춰 데이터를 전송하는 클래스"""
    def __init__(self):
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
        # 1) 로컬 파일에 JSON 줄 단위로 기록
        with open(log_filename, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

        # 2) 백엔드 API 서버로 비동기 전송 시도
        threading.Thread(
            target=self._post_async,
            args=(url, payload, False, log_filename),
            daemon=True
        ).start()

    def send_realtime_ui_stream(self, status, action_state, clean_br, clean_hr, breath_dev, target_info, valid_num_points, clean_pc):
        """[추가] 0.2초마다 3D 웹 대시보드(천채원 님) 및 융합/낙상 모듈(김나은/김유진 님)로 실시간 데이터 전송"""
        now = time.time()
        if now - self.last_realtime_sent_time < REALTIME_INTERVAL_SEC:
            return

        self.last_realtime_sent_time = now
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

        # 포인트 클라우드에서 (x, y, z) 좌표만 추출해 소수점 2자리로 경량화
        pc_list = np.round(clean_pc[:, :3], 2).tolist() if len(clean_pc) > 0 else []

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
                "is_moving": target_info["is_moving"]
            },
            "vital": {
                "breath_rate": clean_br,
                "heart_rate": clean_hr,
                "breath_deviation": round(breath_dev, 5)
            },
            "valid_point_count": valid_num_points,
            "point_cloud": pc_list
        }

        # 1) 로컬 최신 상태 파일(realtime_ui_stream.json) 덮어쓰기 (웹 UI 로컬 테스트용)
        try:
            with open(REALTIME_UI_FILE, "w", encoding="utf-8") as f:
                json.dump(realtime_payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

        # 2) 실시간 서버 엔드포인트로 비동기 전송 (레이더 딜레이 방지)
        threading.Thread(
            target=self._post_async,
            args=(REALTIME_SERVER_URL, realtime_payload, True),
            daemon=True
        ).start()

    def handle_data(self, status, clean_br, clean_hr, breath_dev, target_info):
        now = time.time()
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")

        # 1. 사람이 감지되었을 때 활동 프레임 누적
        if target_info["id"] is not None:
            self.active_frames += 1

        # 2. [정상 단계] 원본 리스트 저장 없이 즉시 변수에 누적 연산 후 원본 폐기 (O(1) 메모리)
        if status == "NORMAL" and clean_br > 0:
            self.br_sum += clean_br
            self.br_count += 1
            self.br_min = min(self.br_min, clean_br)
            self.br_max = max(self.br_max, clean_br)
            if clean_hr > 0:
                self.hr_sum += clean_hr
                self.hr_count += 1
            self.abnormal_start_time = None

        # 3. [주의 및 응급 단계] 발생 시각, 유형, 지속 시간, 당시 호흡수 상세 로그 기록
        elif status in ["WARNING", "EMERGENCY"]:
            if self.abnormal_start_time is None:
                self.abnormal_start_time = now
            
            duration_sec = round(now - self.abnormal_start_time, 1)

            # 응급(EMERGENCY)은 3초 이상, 주의(WARNING)는 10초 이상 지속 시 DB로 이벤트 전송 (중복 전송 방지 10초 쿨타임)
            trigger_sec = 3.0 if status == "EMERGENCY" else 10.0
            if duration_sec >= trigger_sec and (now - self.last_event_sent_time >= 10.0):
                event_type = "APNEA_SUSPECTED" if status == "EMERGENCY" else ("BRADYPNEXIA_LOW" if clean_br <= 10.0 else "TACHYPNEXIA_HIGH")
                
                event_payload = {
                    "packet_type": "DETAILED_EVENT_LOG",
                    "timestamp": timestamp_str,
                    "status_level": status,          # WARNING 또는 EMERGENCY
                    "event_type": event_type,        # 세부 유형 (서호흡, 빈호흡, 무호흡 의심)
                    "duration_sec": duration_sec,    # 지속 시간(초)
                    "breath_rate": clean_br,         # 당시 호흡수
                    "heart_rate": clean_hr,          # 당시 심박수
                    "breath_deviation": breath_dev,
                    "location": {
                        "x": target_info["x"],
                        "y": target_info["y"],
                        "z": target_info["z"]
                    }
                }
                print(f"\n🚨 [DB 전송 - 상세 이벤트 로그] {json.dumps(event_payload, ensure_ascii=False)}")
                self.send_to_server(DB_SERVER_URL_EVENT, event_payload, EVENT_LOG_FILE)
                self.last_event_sent_time = now

        # 4. 일정 주기(예: 60초)마다 누적된 변수로 요약 통계 산출 후 변수 0으로 초기화
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
                    "active_time_sec": round(self.active_frames * 0.09, 1) # 프레임당 약 0.09초 환산
                }
                print(f"\n📊 [DB 전송 - 주기적 요약 통계] {json.dumps(summary_payload, ensure_ascii=False)}")
                self.send_to_server(DB_SERVER_URL_SUMMARY, summary_payload, SUMMARY_LOG_FILE)
            
            # O(1) 누적 변수 초기화 (메모리 완벽 비우기)
            self.br_sum = 0.0
            self.br_count = 0
            self.br_min = 999.0
            self.br_max = 0.0
            self.hr_sum = 0.0
            self.hr_count = 0
            self.active_frames = 0
            self.last_summary_time = now


class AdvancedVitalProcessor:
    """[김기은 님 핵심 파트] 포인트 클라우드 노이즈 제거 + 중앙값/이동평균 복합 호흡 필터"""
    def __init__(self, window_size=7):
        self.br_buffer = deque(maxlen=window_size)
        self.hr_buffer = deque(maxlen=window_size)
        # 방 크기 관심 영역(ROI) 설정 (단위: m) - 실험 공간에 맞게 조절하세요
        self.roi = {"x_min": -3.0, "x_max": 3.0, "y_min": 0.1, "y_max": 5.0, "z_min": -2.0, "z_max": 2.0}
        self.min_snr = 1.0 # 최소 신호 대 잡음비 기준

    def filter_point_cloud(self, raw_point_cloud):
        """1. 방 영역 밖의 허상(Ghost) 및 낮은 SNR 노이즈 포인트 제거 (김나은/김유진 님 전달용)"""
        if raw_point_cloud is None or len(raw_point_cloud) == 0:
            return np.empty((0, 5))
        
        pc = np.array(raw_point_cloud)
        # TI pointCloud 구조: [x, y, z, doppler, snr, ...]
        valid_mask = (
            (pc[:, 0] >= self.roi["x_min"]) & (pc[:, 0] <= self.roi["x_max"]) &
            (pc[:, 1] >= self.roi["y_min"]) & (pc[:, 1] <= self.roi["y_max"]) &
            (pc[:, 2] >= self.roi["z_min"]) & (pc[:, 2] <= self.roi["z_max"])
        )
        if pc.shape[1] >= 5:
            valid_mask = valid_mask & (pc[:, 4] >= self.min_snr)
            
        return pc[valid_mask]

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

    def process(self, raw_br, raw_hr, breath_dev, is_moving, valid_num_points):
        """2. 움직임 아티팩트 차단 및 Median + Mean 이중 필터링"""
        if not is_moving and (6.0 <= raw_br <= 35.0) and breath_dev >= 0.002:
            self.br_buffer.append(raw_br)
        if not is_moving and (40.0 <= raw_hr <= 150.0) and breath_dev >= 0.002:
            self.hr_buffer.append(raw_hr)

        # 튀는 값(Spike)을 잡기 위해 중앙값(median)과 평균(mean)을 결합
        if len(self.br_buffer) >= 3:
            clean_br = float(np.median(self.br_buffer) * 0.6 + np.mean(self.br_buffer) * 0.4)
        elif len(self.br_buffer) > 0:
            clean_br = float(np.mean(self.br_buffer))
        else:
            clean_br = 0.0

        clean_hr = float(np.median(self.hr_buffer)) if len(self.hr_buffer) > 0 else 0.0

        # 3단계 상태 판별
        if clean_br == 0.0:
            status = "MEASURING"
        elif 12.0 <= clean_br <= 20.0:
            status = "NORMAL"
        elif clean_br <= 10.0 or clean_br >= 24.0:
            status = "WARNING"
        else:
            status = "NORMAL"

        # 노이즈가 제거된 실제 유효 포인트가 5개 이상인데 호흡 편차가 극도로 낮으면 무호흡 응급 판정
        if valid_num_points >= 5 and not is_moving and breath_dev < 0.0008:
            status = "EMERGENCY"

        return round(clean_br, 1), round(clean_hr, 1), status


def main():
    parser = UARTParser(type='DoubleCOMPort')
    processor = AdvancedVitalProcessor(window_size=7)
    db_dispatcher = DBDispatcherForHyunwoo()
    
    sig = list(inspect.signature(parser.connectComPorts).parameters.keys())
    if sig == ['cliCom', 'cliBaud', 'dataCom', 'dataBaud']:
        parser.connectComPorts(CLI_PORT, CLI_BAUD, DATA_PORT, DATA_BAUD)
    else:
        parser.connectComPorts(CLI_PORT, DATA_PORT, CLI_BAUD, DATA_BAUD)

    with open(CFG_FILE_PATH, 'r') as f:
        cfg_lines = f.readlines()
    parser.sendCfg(cfg_lines)
    print("-> 설정 완료! 노이즈 필터링, O(1) 메모리 DB 전송 및 0.2초 실시간 3D UI 스트리밍이 작동 중입니다.\n")

    try:
        while True:
            try:
                output_dict = parser.readAndParseUartDoubleCOMPort(demo=DEMO_NAME)
            except Exception:
                # 바이트 밀림 에러 발생 시 멈추지 않고 다음 프레임으로 즉시 넘어감
                continue
            
            if output_dict:
                frame_num = output_dict.get('frameNum', 0)
                raw_targets = output_dict.get('numDetectedTracks', 0)
                
                # 1. 포인트 클라우드 노이즈 필터링 적용 (유효 포인트 개수 산출)
                raw_pc = output_dict.get('pointCloud', None)
                if raw_pc is not None and len(raw_pc) > 0:
                    clean_pc = processor.filter_point_cloud(raw_pc)
                    valid_num_points = len(clean_pc)
                else:
                    clean_pc = np.empty((0, 5))
                    valid_num_points = output_dict.get('numDetectedPoints', 0)

                # 2. 타겟 위치 및 이동 속도 추출
                target_info = {"id": None, "x": 0.0, "y": 0.0, "z": 0.0, "speed": 0.0, "is_moving": False}
                if 'trackData' in output_dict and len(output_dict['trackData']) > 0:
                    t = output_dict['trackData'][0]
                    vx, vy, vz = float(t[4]), float(t[5]), float(t[6])
                    speed = float(np.sqrt(vx**2 + vy**2 + vz**2))
                    target_info = {
                        "id": int(t[0]),
                        "x": round(float(t[1]), 2),
                        "y": round(float(t[2]), 2),
                        "z": round(float(t[3]), 2),
                        "speed": round(speed, 2),
                        "is_moving": bool(speed > 0.15)
                    }

                action_state = processor.estimate_posture(target_info["id"], target_info["z"])

                # 3. 호흡 신호 처리 및 DB / 실시간 UI 전송 모듈 호출
                if 'vitals' in output_dict:
                    v = output_dict['vitals']
                    raw_br = float(v.get('breathRate', 0.0))
                    raw_hr = float(v.get('heartRate', 0.0))
                    breath_dev = float(v.get('breathDeviation', 0.0))

                    clean_br, clean_hr, status = processor.process(
                        raw_br, raw_hr, breath_dev, target_info["is_moving"], valid_num_points
                    )

                    print(f"[Frame {frame_num}] 인원: {raw_targets}명 | 유효점: {valid_num_points}개 | 위치: ({target_info['x']}, {target_info['y']}, {target_info['z']})m | 자세: {action_state} | 호흡수: {clean_br:.1f} BPM | 상태: [{status}]")

                    # ① 정현우 님 DB 전송 모듈 (평소 60초 요약 / 주의·응급 시 즉시 로그)
                    if raw_targets > 0:
                        db_dispatcher.handle_data(status, clean_br, clean_hr, round(breath_dev, 5), target_info)

                    # ② 천채원 님(3D 웹 UI) & 김나은/김유진 님(좌표 정합/낙상) 실시간 0.2초 스트리밍 전송
                    db_dispatcher.send_realtime_ui_stream(
                        status, action_state, clean_br, clean_hr, breath_dev,
                        target_info, valid_num_points, clean_pc
                    )

            time.sleep(0.01)
            
    except KeyboardInterrupt:
        print("\n프로그램을 종료합니다.")

if __name__ == '__main__':
    main()