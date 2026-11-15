import os
import json
import random
from datetime import datetime, timedelta

# =====================================================================
# 레이더 데이터 추출 코드와 동일한 파일명 사용
# =====================================================================
SUMMARY_FILE = "db_summary_stats.jsonl"
EVENT_FILE = "db_event_logs.jsonl"
WEEKLY_ARCHIVE_FILE = "weekly_summary_permanent_archive.jsonl" # 영구 누적 보관용
WEB_REPORT_JSON = "weekly_report_ui.json"                      # 웹 대시보드(UI) 전달용

# =====================================================================
# [STEP 1] 14일 치(지난주 7일 + 이번주 7일) 가상 데이터 생성기
# 시나리오: 지난주(1주 차)는 안정적이나, 이번주(2주 차)에 새벽 호흡 불규칙이 증가하고 활동량이 감소함
# =====================================================================
def generate_14days_mock_data():
    print("1. 14일 치 시계열 테스트 데이터(.jsonl) 생성 중...")
    now = datetime.now()
    
    summary_records = []
    event_records = []

    # 15일 전 데이터 1건 추가 (14일 보관 후 자동 삭제 로직 테스트용)
    expired_time = (now - timedelta(days=15)).strftime("%Y-%m-%d %H:%M:%S")
    event_records.append({
        "packet_type": "DETAILED_EVENT_LOG",
        "timestamp": expired_time,
        "status_level": "WARNING",
        "event_type": "BRADYPNEXIA_LOW",
        "duration_sec": 15.0,
        "breath_rate": 9.5,
        "heart_rate": 65.0,
        "breath_deviation": 0.0025,
        "location": {"x": 0.2, "y": 1.5, "z": 0.1}
    })

    # 과거 14일(Day -14 ~ Day -1) 데이터 생성
    for day_offset in range(14, 0, -1):
        target_date = now - timedelta(days=day_offset)
        is_this_week = (day_offset <= 7) # 최근 7일(이번 주) 여부

        # 1) 하루 24시간 요약 통계 생성 (1시간 단위로 압축해 하루 24줄 생성)
        for hour in range(24):
            ts = target_date.replace(hour=hour, minute=0, second=0).strftime("%Y-%m-%d %H:%M:%S")
            
            # 지난주보다 이번주에 평균 호흡수가 약간 상승하고 활동 시간은 감소하도록 설정
            base_br = 17.8 if is_this_week else 15.6
            avg_br = round(random.gauss(base_br, 0.8), 1)
            
            # 낮 시간대(08~20시)에 주로 활동, 이번주에 활동량 약 25% 감소 시뮬레이션
            if 8 <= hour <= 20:
                base_active = 900 if is_this_week else 1250 # 시간당 활동 초(sec)
                active_sec = round(max(0.0, random.gauss(base_active, 150)), 1)
            else:
                active_sec = round(max(0.0, random.gauss(120, 30)), 1)

            summary_records.append({
                "packet_type": "PERIODIC_SUMMARY_STATS",
                "timestamp": ts,
                "status_level": "NORMAL",
                "period_seconds": 3600,
                "avg_breath_rate": avg_br,
                "min_breath_rate": round(avg_br - 2.1, 1),
                "max_breath_rate": round(avg_br + 2.4, 1),
                "avg_heart_rate": round(random.gauss(72.0, 3.0), 1),
                "active_time_sec": active_sec
            })

        # 2) 주의(WARNING) 및 응급(EMERGENCY) 상세 이벤트 로그 생성
        # 지난주: 하루 0~1건 발생 / 이번주: 하루 2~4건 발생 (특히 새벽 00~06시에 집중)
        num_events = random.randint(2, 4) if is_this_week else random.randint(0, 1)
        for _ in range(num_events):
            # 이번주에는 새벽(0~5시)에 호흡 불규칙이 자주 발생하도록 시간대 설정
            event_hour = random.choice([1, 2, 3, 4, 5, 14, 22]) if is_this_week else random.randint(0, 23)
            ev_ts = target_date.replace(hour=event_hour, minute=random.randint(10, 50), second=0).strftime("%Y-%m-%d %H:%M:%S")
            
            ev_type = random.choice(["TACHYPNEXIA_HIGH", "BRADYPNEXIA_LOW"])
            br_val = round(random.uniform(24.5, 28.0), 1) if ev_type == "TACHYPNEXIA_HIGH" else round(random.uniform(8.0, 9.8), 1)

            event_records.append({
                "packet_type": "DETAILED_EVENT_LOG",
                "timestamp": ev_ts,
                "status_level": "WARNING",
                "event_type": ev_type,
                "duration_sec": round(random.uniform(15.0, 65.0), 1),
                "breath_rate": br_val,
                "heart_rate": round(random.uniform(60.0, 95.0), 1),
                "breath_deviation": round(random.uniform(0.0021, 0.0045), 5),
                "location": {"x": round(random.uniform(-0.5, 0.5), 2), "y": round(random.uniform(1.2, 2.2), 2), "z": -0.35}
            })

    # 파일로 저장
    with open(SUMMARY_FILE, "w", encoding="utf-8") as f:
        for r in summary_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    with open(EVENT_FILE, "w", encoding="utf-8") as f:
        for r in event_records:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"   -> 생성 완료! (요약 통계: {len(summary_records)}건, 상세 이벤트: {len(event_records)}건)\n")


# =====================================================================
# [STEP 2] 14일 시계열 비교 및 주간 건강 리포트 생성 알고리즘
# =====================================================================
class WeeklyReportAnalyzer:
    def __init__(self, summary_file, event_file):
        self.summary_file = summary_file
        self.event_file = event_file

    def _get_time_slot(self, hour):
        """시간대 분류: 새벽(00~06시), 낮(06~18시), 밤(18~24시)"""
        if 0 <= hour < 6:
            return "DAWN_00_06"   # 새벽
        elif 6 <= hour < 18:
            return "DAY_06_18"    # 낮
        else:
            return "NIGHT_18_24"  # 밤

    def _calc_change_rate(self, prev_val, curr_val):
        """전주 대비 증감률(%) 계산"""
        if prev_val == 0:
            return 100.0 if curr_val > 0 else 0.0
        return round(((curr_val - prev_val) / prev_val) * 100.0, 1)

    def run_weekly_analysis(self):
        now = datetime.now()
        cutoff_14days = now - timedelta(days=14)
        cutoff_7days = now - timedelta(days=7)

        # 직전 7일(prev_week) vs 최근 7일(curr_week) 데이터 분류 바구니
        stats = {
            "prev_week": {"br_list": [], "active_sec": 0.0, "warnings": [], "emergencies": 0, "time_slots": {"DAWN_00_06": 0, "DAY_06_18": 0, "NIGHT_18_24": 0}},
            "curr_week": {"br_list": [], "active_sec": 0.0, "warnings": [], "emergencies": 0, "time_slots": {"DAWN_00_06": 0, "DAY_06_18": 0, "NIGHT_18_24": 0}}
        }

        retained_summaries = []
        retained_events = []
        expired_count = 0

        # 1. 요약 통계 파일 읽기 및 14일 기준 분류
        with open(self.summary_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                data = json.loads(line)
                dt = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")

                if dt < cutoff_14days:
                    expired_count += 1
                    continue # 14일 지난 상세 데이터는 보관 제외(자동 폐기)

                retained_summaries.append(data)
                target_bucket = "curr_week" if dt >= cutoff_7days else "prev_week"
                stats[target_bucket]["br_list"].append(data["avg_breath_rate"])
                stats[target_bucket]["active_sec"] += data["active_time_sec"]

        # 2. 상세 이벤트 로그 읽기 및 시간대별(낮/밤/새벽) 분석
        with open(self.event_file, "r", encoding="utf-8") as f:
            for line in f:
                if not line.strip(): continue
                data = json.loads(line)
                dt = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")

                if dt < cutoff_14days:
                    expired_count += 1
                    continue # 14일 경과 이벤트 로그 자동 삭제

                retained_events.append(data)
                target_bucket = "curr_week" if dt >= cutoff_7days else "prev_week"

                if data["status_level"] == "EMERGENCY":
                    stats[target_bucket]["emergencies"] += 1
                elif data["status_level"] == "WARNING":
                    stats[target_bucket]["warnings"].append(data)
                    slot = self._get_time_slot(dt.hour)
                    stats[target_bucket]["time_slots"][slot] += 1

        # 3. 14일 보관 정책 적용 (14일 이내 데이터만 파일에 다시 덮어쓰기)
        with open(self.summary_file, "w", encoding="utf-8") as f:
            for r in retained_summaries:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        with open(self.event_file, "w", encoding="utf-8") as f:
            for r in retained_events:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

        # 4. 직전 7일 vs 최근 7일 핵심 지표 산출
        prev_avg_br = round(sum(stats["prev_week"]["br_list"]) / max(1, len(stats["prev_week"]["br_list"])), 1)
        curr_avg_br = round(sum(stats["curr_week"]["br_list"]) / max(1, len(stats["curr_week"]["br_list"])), 1)
        br_change_pct = self._calc_change_rate(prev_avg_br, curr_avg_br)

        prev_active_hr = round(stats["prev_week"]["active_sec"] / 3600.0, 1)
        curr_active_hr = round(stats["curr_week"]["active_sec"] / 3600.0, 1)
        active_change_pct = self._calc_change_rate(prev_active_hr, curr_active_hr)

        prev_warn_cnt = len(stats["prev_week"]["warnings"])
        curr_warn_cnt = len(stats["curr_week"]["warnings"])
        warn_change_pct = self._calc_change_rate(prev_warn_cnt, curr_warn_cnt)

        # 가장 주의 이벤트가 많이 발생한 취약 시간대 찾기
        slots_kor = {"DAWN_00_06": "새벽(00~06시)", "DAY_06_18": "낮(06~18시)", "NIGHT_18_24": "밤(18~24시)"}
        peak_slot_key = max(stats["curr_week"]["time_slots"], key=stats["curr_week"]["time_slots"].get)
        peak_slot_name = slots_kor[peak_slot_key]

        # 5. 돌봄 인력(생활지원사/요양보호사) 방문 우선순위 및 자동 코멘트 생성
        findings = []
        if warn_change_pct >= 30.0:
            findings.append(f"주의 단계(호흡 불규칙) 발생 빈도가 전주 대비 {warn_change_pct:+.1f}% 증가했으며, 주로 '{peak_slot_name}' 시간대에 집중되었습니다.")
        if active_change_pct <= -15.0:
            findings.append(f"주간 총 활동 시간이 전주 대비 {abs(active_change_pct):.1f}% 감소하여 기력 저하 및 무활동 증가가 관찰됩니다.")
        if not findings:
            findings.append("전주 대비 호흡 및 활동 패턴이 안정적인 범위 내에서 유지되고 있습니다.")

        care_priority = "HIGH (우선 방문 권고)" if (warn_change_pct >= 50.0 or active_change_pct <= -20.0 or stats["curr_week"]["emergencies"] > 0) else "NORMAL (정기 관찰)"

        # 최종 리포트 패킷 조립
        report_packet = {
            "report_generated_at": now.strftime("%Y-%m-%d %H:%M:%S"),
            "analysis_period": {
                "previous_7days": f"{(now - timedelta(days=14)).strftime('%Y-%m-%d')} ~ {(now - timedelta(days=8)).strftime('%Y-%m-%d')}",
                "current_7days": f"{(now - timedelta(days=7)).strftime('%Y-%m-%d')} ~ {(now - timedelta(days=1)).strftime('%Y-%m-%d')}"
            },
            "care_priority": care_priority,
            "comparison_metrics": {
                "breath_rate_bpm": {"prev_7d": prev_avg_br, "curr_7d": curr_avg_br, "change_pct": br_change_pct},
                "total_active_hours": {"prev_7d": prev_active_hr, "curr_7d": curr_active_hr, "change_pct": active_change_pct},
                "warning_event_count": {"prev_7d": prev_warn_cnt, "curr_7d": curr_warn_cnt, "change_pct": warn_change_pct},
                "emergency_event_count": {"prev_7d": stats["prev_week"]["emergencies"], "curr_7d": stats["curr_week"]["emergencies"]}
            },
            "time_of_day_warning_distribution": {
                "dawn_00_06": stats["curr_week"]["time_slots"]["DAWN_00_06"],
                "day_06_18": stats["curr_week"]["time_slots"]["DAY_06_18"],
                "night_18_24": stats["curr_week"]["time_slots"]["NIGHT_18_24"],
                "most_vulnerable_time": peak_slot_name
            },
            "summary_findings": findings,
            "retention_policy_result": f"14일 경과 상세 데이터 {expired_count}건 자동 폐기 완료",
            "disclaimer": "본 리포트는 의료 진단 목적이 아닌 보조적 참고 정보로 제공됩니다."
        }

        # 6. 주간 요약 지표 영구 누적 저장 (장기 추이 확인용) 및 웹 UI용 JSON 저장
        with open(WEEKLY_ARCHIVE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "week_ending": now.strftime("%Y-%m-%d"),
                "avg_breath_rate": curr_avg_br,
                "total_active_hours": curr_active_hr,
                "warning_count": curr_warn_cnt,
                "emergency_count": stats["curr_week"]["emergencies"]
            }, ensure_ascii=False) + "\n")

        with open(WEB_REPORT_JSON, "w", encoding="utf-8") as f:
            json.dump(report_packet, f, indent=2, ensure_ascii=False)

        return report_packet


# =====================================================================
# [STEP 3] 실행 및 콘솔 리포트 출력
# =====================================================================
if __name__ == "__main__":
    # 1. 14일 치 더미 데이터 생성
    generate_14days_mock_data()

    # 2. 주간 리포트 분석 알고리즘 실행
    analyzer = WeeklyReportAnalyzer(SUMMARY_FILE, EVENT_FILE)
    report = analyzer.run_weekly_analysis()

    # 3. 결과 보기 좋게 출력
    print("=" * 75)
    print("📊 [후점핑 시스템] 14일 시계열 비교 기반 주간 건강 리포트 테스트 결과")
    print("=" * 75)
    print(f"• 비교 기간 : 직전 7일({report['analysis_period']['previous_7days']}) vs 최근 7일({report['analysis_period']['current_7days']})")
    print(f"• 돌봄 등급 : {report['care_priority']}")
    print("-" * 75)
    
    m = report["comparison_metrics"]
    print(f"1) 평균 호흡수 변화 : {m['breath_rate_bpm']['prev_7d']} BPM  ➡️  {m['breath_rate_bpm']['curr_7d']} BPM ({m['breath_rate_bpm']['change_pct']:+.1f}%)")
    print(f"2) 주간 총 활동시간 : {m['total_active_hours']['prev_7d']} 시간 ➡️  {m['total_active_hours']['curr_7d']} 시간 ({m['total_active_hours']['change_pct']:+.1f}%)")
    print(f"3) 주의(WARNING) 빈도: {m['warning_event_count']['prev_7d']} 건    ➡️  {m['warning_event_count']['curr_7d']} 건 ({m['warning_event_count']['change_pct']:+.1f}%)")
    
    t = report["time_of_day_warning_distribution"]
    print(f"4) 시간대별 주의 발생: 새벽 {t['dawn_00_06']}건 | 낮 {t['day_06_18']}건 | 밤 {t['night_18_24']}건 (취약 시간대: {t['most_vulnerable_time']})")
    print("-" * 75)
    print("💡 [자동 분석 소견]")
    for idx, comment in enumerate(report["summary_findings"], 1):
        print(f"  {idx}. {comment}")
    print(f"\n🗑️ [DB 최적화 정책] {report['retention_policy_result']}")
    print(f"※ {report['disclaimer']}")
    print("=" * 75)
    print(f"\n✅ 파일 생성 완료:\n  - DB 저장용: {SUMMARY_FILE}, {EVENT_FILE}\n  - 웹 대시보드(UI) 전달용: {WEB_REPORT_JSON}")