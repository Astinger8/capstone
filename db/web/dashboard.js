"use strict";

const element = (id) => document.getElementById(id);
const setText = (id, text) => { element(id).textContent = text; };
const svgNamespace = "http://www.w3.org/2000/svg";
const dateInput = element("report-date");
const chart = element("chart");
const statuses = {
  measured: "측정 완료",
  no_record: "기록 없음",
  no_valid_measurement: "유효 측정 없음",
};
let dailyReadings = [];
let loading = false;

function formatNumber(value) {
  return value === null ? "—" : value.toFixed(2);
}

function svgElement(name, attributes, text) {
  const node = document.createElementNS(svgNamespace, name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function renderChart() {
  const values = dailyReadings.map((day) => day.average_bpm).filter(Number.isFinite);
  if (!values.length) {
    const message = document.createElement("p");
    message.className = "chart-empty";
    message.textContent = "이 기간에 그래프로 표시할 유효 측정값이 없어요.";
    chart.replaceChildren(message);
    return;
  }

  const width = chart.clientWidth;
  if (!width) return;
  const height = 254;
  const plot = { left: 36, right: width - 18, top: 24, bottom: 218 };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const step = Math.max(0.5, Math.ceil((max - min) * 2 / 3) / 2);
  const lower = Math.max(0, Math.floor(min / step) * step - step);
  const upper = Math.ceil(max / step) * step + step;
  const x = (i) => plot.left + (plot.right - plot.left) * i / (dailyReadings.length - 1);
  const y = (value) => plot.bottom - (plot.bottom - plot.top) * (value - lower) / (upper - lower);
  const divider = (x(6) + x(7)) / 2;
  const svg = svgElement("svg", {
    viewBox: `0 0 ${width} ${height}`,
    role: "img",
    "aria-label": "날짜별 평균 호흡수 그래프. 자세한 값은 날짜별 기록 표에서 확인할 수 있습니다.",
  });

  svg.append(svgElement("rect", {
    x: plot.left, y: plot.top, width: divider - plot.left, height: plot.bottom - plot.top, fill: "#f5f7fb",
  }));
  svg.append(svgElement("rect", {
    x: divider, y: plot.top, width: plot.right - divider, height: plot.bottom - plot.top, fill: "#f0f9f5",
  }));
  svg.append(svgElement("text", { x: 0, y: 12, class: "axis-label" }, "회/분"));
  for (let value = lower; value <= upper; value += step) {
    svg.append(svgElement("line", { x1: plot.left, x2: plot.right, y1: y(value), y2: y(value), class: "chart-grid" }));
    svg.append(svgElement("text", { x: plot.left - 7, y: y(value) + 4, "text-anchor": "end", class: "axis-label" }, value));
  }
  svg.append(svgElement("line", { x1: divider, x2: divider, y1: plot.top, y2: plot.bottom, class: "chart-divider" }));

  for (const [start, end, color] of [[0, 7, "#536c9b"], [7, 14, "#087765"]]) {
    let segment = [];
    const dots = [];
    for (let i = start; i < end; i += 1) {
      const day = dailyReadings[i];
      if (day.average_bpm === null) {
        drawSegment(svg, segment, color);
        segment = [];
        continue;
      }
      const point = [x(i), y(day.average_bpm)];
      segment.push(point);
      const dot = svgElement("circle", { cx: point[0], cy: point[1], r: 4.5, fill: color, stroke: "white", "stroke-width": 1.5 });
      dot.append(svgElement("title", {}, `${day.date} · ${formatNumber(day.average_bpm)}회/분`));
      dots.push(dot);
    }
    drawSegment(svg, segment, color);
    svg.append(...dots);
  }

  dailyReadings.forEach((day, i) => {
    if (width < 580 && ![0, 3, 6, 9, 13].includes(i)) return;
    svg.append(svgElement("text", {
      x: x(i), y: 241, "text-anchor": "middle", class: "axis-label",
    }, day.date.slice(5).replace("-", "/")));
  });
  chart.replaceChildren(svg);
}

function drawSegment(svg, points, color) {
  if (points.length < 2) return;
  svg.append(svgElement("polyline", {
    points: points.map((point) => point.join(",")).join(" "),
    fill: "none", stroke: color, "stroke-width": 3, "stroke-linejoin": "round", "stroke-linecap": "round",
  }));
}

function renderReport(report) {
  const { previous_week: previous, recent_week: recent, comparison, daily } = report;
  if (!Array.isArray(daily) || daily.length !== 14) throw new Error("Unexpected report period");
  dateInput.value = report.report_date;
  setText("subject-label", `대상자 ${report.subject_id}`);
  setText("date-range", `${previous.start_date} ~ ${recent.end_date} · 한국 시간`);
  setText("previous-average", formatNumber(previous.average_bpm));
  setText("recent-average", formatNumber(recent.average_bpm));
  setText("previous-valid", `유효 측정 ${previous.valid_count} / ${previous.record_count}건`);
  setText("recent-valid", `유효 측정 ${recent.valid_count} / ${recent.record_count}건`);

  const change = comparison.average_change_bpm;
  setText("average-change", change > 0 ? `+${formatNumber(change)}` : formatNumber(change));
  setText("comparison-note", comparison.status === "ready" ? "최근 평균 − 직전 평균" : "관측 부족으로 비교 불가");

  const measuredDays = previous.measured_days + recent.measured_days;
  setText("measured-days", measuredDays);
  setText("coverage-note", measuredDays === 14 ? "14일 모두 유효 측정 있음" : `유효 측정이 없는 날 ${14 - measuredDays}일`);
  const missingDays = daily.filter((day) => day.valid_count === 0);
  element("missing-note").hidden = missingDays.length === 0;
  if (missingDays.length) {
    const dates = missingDays.map((day) => day.date.slice(5).replace("-", "/")).join(", ");
    setText("missing-note", `유효 측정이 없는 날 ${missingDays.length}일 · ${dates}`);
  }

  const rows = daily.map((day) => {
    const row = document.createElement("tr");
    for (const value of [day.date, `${day.valid_count} / ${day.record_count}`, formatNumber(day.average_bpm), statuses[day.status]]) {
      const cell = document.createElement("td");
      cell.textContent = value;
      row.append(cell);
    }
    return row;
  });
  element("daily-rows").replaceChildren(...rows);
  element("daily-details").hidden = false;
  element("report").hidden = false;
  dailyReadings = daily;
  renderChart();
}

async function loadReport(reportDate) {
  if (loading) return;
  loading = true;
  element("load-button").disabled = true;
  dateInput.disabled = true;
  element("load-error").hidden = true;
  element("report").setAttribute("aria-busy", "true");
  element("load-status").dataset.state = "loading";
  setText("load-status", "조회 중");
  try {
    const url = new URL("/api/v1/breathing-report", window.location.origin);
    if (reportDate) url.searchParams.set("report_date", reportDate);
    const response = await fetch(url, { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    renderReport(await response.json());
    element("load-status").dataset.state = "ready";
    setText("load-status", "조회 완료");
  } catch {
    element("report").hidden = true;
    element("load-error").hidden = false;
    setText("load-error", "리포트를 불러오지 못했어요. 서버 상태와 기준일을 확인한 뒤 다시 조회해 주세요.");
    element("load-status").dataset.state = "error";
    setText("load-status", "조회 실패");
  } finally {
    element("report").setAttribute("aria-busy", "false");
    element("load-button").disabled = false;
    dateInput.disabled = false;
    loading = false;
  }
}

element("report-form").addEventListener("submit", (event) => {
  event.preventDefault();
  loadReport(dateInput.value);
});
new ResizeObserver(() => {
  if (dailyReadings.length && !element("report").hidden) renderChart();
}).observe(chart);
loadReport();
