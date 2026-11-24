"use strict";

const element = (id) => document.getElementById(id);
const setText = (id, value) => { element(id).textContent = value; };
const metrics = [
  ["breath_rate_bpm", "평균 호흡수", "회/분", 1],
  ["total_active_hours", "총 활동 시간", "시간", 1],
  ["warning_event_count", "주의 이벤트", "건", 0],
  ["emergency_event_count", "응급 이벤트", "건", 0],
];
const periods = [
  ["dawn_00_06", "새벽 (00~06시)"],
  ["day_06_18", "낮 (06~18시)"],
  ["night_18_24", "밤 (18~24시)"],
];

function node(tag, className, value) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (value !== undefined) element.textContent = value;
  return element;
}

function metricCard(label, unit, digits, values) {
  const card = node("article", "metric");
  const pair = node("p", "metric-pair");
  const arrow = node("span", "arrow", "→");
  arrow.setAttribute("aria-label", "에서");
  pair.append(
    node("span", "before", values.prev_7d.toFixed(digits)),
    arrow,
    node("span", "after", values.curr_7d.toFixed(digits)),
    node("span", "unit", unit),
  );
  card.append(node("p", "metric-label", label), pair);

  if (typeof values.change_pct === "number") {
    const sign = values.change_pct > 0 ? "+" : "";
    card.append(node("span", "change", `${sign}${values.change_pct.toFixed(1)}%`));
  } else if (values.prev_7d === values.curr_7d) {
    card.append(node("span", "change neutral", "변화 없음"));
  }
  return card;
}

function renderReport(report) {
  setText("previous-period", report.analysis_period.previous_7days);
  setText("current-period", report.analysis_period.current_7days);
  setText("priority", report.care_priority);
  setText("retention", report.retention_policy_result);
  setText("disclaimer", report.disclaimer);
  setText("file-source", `출처 · weekly_report_ui.json · 생성 ${report.report_generated_at}`);
  element("weekly-metrics").replaceChildren(...metrics.map(([key, label, unit, digits]) => (
    metricCard(label, unit, digits, report.comparison_metrics[key])
  )));

  const distribution = report.time_of_day_warning_distribution;
  const maximum = Math.max(1, ...periods.map(([key]) => distribution[key]));
  element("distribution").replaceChildren(...periods.map(([key, label]) => {
    const count = distribution[key];
    const row = node("li", "distribution-row");
    const bar = node("progress");
    bar.max = maximum;
    bar.value = count;
    bar.setAttribute("aria-label", `${label} 주의 ${count}건`);
    row.append(node("span", "", label), bar, node("strong", "", `${count}건`));
    return row;
  }));
  setText("peak-time", distribution.most_vulnerable_time);
  element("findings").replaceChildren(...report.summary_findings.map((text) => node("li", "", text)));
  element("weekly-content").hidden = false;
}

async function loadReport() {
  const status = element("file-status");
  try {
    const response = await fetch("/dashboard-assets/weekly_report_ui.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderReport(await response.json());
    status.dataset.state = "ready";
    status.textContent = "첨부 파일 불러옴";
  } catch {
    status.dataset.state = "error";
    status.textContent = "불러오기 실패";
    const error = element("file-error");
    error.textContent = "주간 리포트 파일을 불러오지 못했어요. 서버를 확인한 뒤 새로고침해 주세요.";
    error.hidden = false;
  }
}

loadReport();
