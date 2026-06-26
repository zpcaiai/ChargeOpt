const state = {
  overview: null,
  stationDetail: null,
  selectedStationId: null,
  lang: localStorage.getItem("lang") || "zh",
};

const $ = (id) => document.getElementById(id);
const money = (value) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
const number = (value, digits = 0) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);

const TRANSLATIONS = {
  zh: {
    "nav.cockpit": "驾驶舱", "nav.cockpit.title": "运营驾驶舱",
    "nav.station": "站点", "nav.station.title": "站点详情",
    "nav.dispatch": "调度", "nav.dispatch.title": "调度中心",
    "nav.roi": "ROI", "nav.roi.title": "储能ROI模拟",
    "nav.vpp": "VPP", "nav.vpp.title": "VPP资源",
    "mode.label": "控制模式", "mode.value": "建议模式", "mode.hint": "需人工审批",
    "header.title": "能源调度运营中心", "header.loading": "加载中...",
    "btn.refresh": "刷新数据",
    "metric.health": "组合健康度", "metric.health.unit": "评分",
    "metric.revenue": "今日收入",
    "metric.margin": "毛利润",
    "metric.power": "当前功率", "metric.power.unit": "kW 从电网",
    "metric.queue": "排队", "metric.queue.unit": "辆",
    "metric.vpp": "VPP 容量", "metric.vpp.unit": "可靠 kW",
    "metric.storage": "储能", "metric.headroom": "余量", "metric.today": "今日",
    "panel.portfolioLoad": "组合负荷", "panel.portfolioLoad.desc": "电网进口、光伏、储能动作及排队压力",
    "panel.savings": "节省潜力", "panel.savings.desc": "来自削峰、储能及排队缓解的月度价值",
    "panel.stationPortfolio": "站点组合", "panel.stationPortfolio.desc": "经济健康、利用率、储能状态及VPP就绪度",
    "panel.alerts": "告警", "panel.alerts.desc": "未处理及已确认的运行事件",
    "panel.forecast": "24小时预测", "panel.forecast.desc": "负荷、排队、电价及峰值概率",
    "panel.pricing": "动态定价", "panel.pricing.desc": "推荐的公共服务费调整",
    "panel.dispatchQueue": "调度队列", "panel.dispatchQueue.desc": "控制动作前的可审计建议",
    "panel.storagePlan": "储能计划", "panel.storagePlan.desc": "所选站点的滚动计划",
    "panel.roi": "储能ROI模拟器", "panel.roi.desc": "容量、PCS功率、CAPEX及VPP收益",
    "panel.investment": "投资方案",
    "panel.drEvent": "需求响应事件",
    "panel.resourcePool": "资源池", "panel.resourcePool.desc": "储能可用性、负荷削减、置信度及成本",
    "th.station": "站点", "th.health": "健康度", "th.power": "功率", "th.peak": "峰值",
    "th.storage": "储能", "th.utilization": "利用率", "th.margin": "利润", "th.vpp": "VPP",
    "roi.capacity": "容量 kWh", "roi.power": "PCS功率 kW", "roi.capex": "CAPEX 元/kWh", "roi.vpp": "VPP收益",
    "roi.netBenefit": "净收益", "roi.cnyYear": "元/年", "roi.payback": "回收期", "roi.years": "年", "roi.irrHint": "10年估算",
    "roi.invest": "投资方案符合门槛要求", "roi.review": "请在审批前复核假设条件",
    "vpp.reliable": "可靠容量", "vpp.requested": "需求量", "vpp.revenue": "收益", "vpp.resources": "资源", "vpp.stations": "站点",
    "live": "实时数据", "stations": "个站点", "generated": "生成于",
    "recommendations": "条建议", "no.alerts": "无告警。",
  },
  en: {
    "nav.cockpit": "Cockpit", "nav.cockpit.title": "Operating cockpit",
    "nav.station": "Station", "nav.station.title": "Station detail",
    "nav.dispatch": "Dispatch", "nav.dispatch.title": "Dispatch center",
    "nav.roi": "ROI", "nav.roi.title": "Storage ROI simulator",
    "nav.vpp": "VPP", "nav.vpp.title": "VPP resources",
    "mode.label": "Control Mode", "mode.value": "Recommendation", "mode.hint": "Approval required",
    "header.title": "Energy Dispatch Operations", "header.loading": "Loading portfolio...",
    "btn.refresh": "Refresh data",
    "metric.health": "Portfolio Health", "metric.health.unit": "score",
    "metric.revenue": "Today Revenue",
    "metric.margin": "Gross Margin",
    "metric.power": "Current Power", "metric.power.unit": "kW import",
    "metric.queue": "Queue", "metric.queue.unit": "vehicles",
    "metric.vpp": "VPP Capacity", "metric.vpp.unit": "reliable kW",
    "metric.storage": "storage", "metric.headroom": "Headroom", "metric.today": "today",
    "panel.portfolioLoad": "Portfolio Load", "panel.portfolioLoad.desc": "Grid import, PV, storage action, and queue pressure",
    "panel.savings": "Savings Potential", "panel.savings.desc": "Monthly value from peak control, storage, and queue relief",
    "panel.stationPortfolio": "Station Portfolio", "panel.stationPortfolio.desc": "Economic health, utilization, storage state, and VPP readiness",
    "panel.alerts": "Alerts", "panel.alerts.desc": "Open issues and acknowledged operating events",
    "panel.forecast": "24h Forecast", "panel.forecast.desc": "Load, queue, price, and peak probability",
    "panel.pricing": "Dynamic Pricing", "panel.pricing.desc": "Recommended public service-fee adjustment",
    "panel.dispatchQueue": "Dispatch Queue", "panel.dispatchQueue.desc": "Auditable recommendations before any control action",
    "panel.storagePlan": "Storage Plan", "panel.storagePlan.desc": "Rolling plan for selected station",
    "panel.roi": "Storage ROI Simulator", "panel.roi.desc": "Capacity, PCS power, CAPEX, and VPP revenue case",
    "panel.investment": "Investment Case",
    "panel.drEvent": "Demand Response Event",
    "panel.resourcePool": "Resource Pool", "panel.resourcePool.desc": "Storage availability, load curtailment, confidence, and cost",
    "th.station": "Station", "th.health": "Health", "th.power": "Power", "th.peak": "Peak",
    "th.storage": "Storage", "th.utilization": "Utilization", "th.margin": "Margin", "th.vpp": "VPP",
    "roi.capacity": "Capacity kWh", "roi.power": "PCS power kW", "roi.capex": "CAPEX CNY/kWh", "roi.vpp": "VPP revenue",
    "roi.netBenefit": "Net Benefit", "roi.cnyYear": "CNY/year", "roi.payback": "Payback", "roi.years": "years", "roi.irrHint": "10y estimate",
    "roi.invest": "Investment case meets hurdle", "roi.review": "Review assumptions before approval",
    "vpp.reliable": "Reliable Capacity", "vpp.requested": "Requested", "vpp.revenue": "Revenue", "vpp.resources": "Resources", "vpp.stations": "stations",
    "live": "Live fixture", "stations": "stations", "generated": "generated",
    "recommendations": "recommendations", "no.alerts": "No alerts.",
  },
};

function t(key) {
  return (TRANSLATIONS[state.lang] || TRANSLATIONS.zh)[key] || key;
}

function applyLang() {
  document.documentElement.lang = state.lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => {
    const key = el.getAttribute("data-i18n");
    el.textContent = t(key);
  });
  document.querySelectorAll("[data-i18n-title]").forEach((el) => {
    el.title = t(el.getAttribute("data-i18n-title"));
  });
  const btn = $("langToggle");
  if (btn) btn.textContent = state.lang === "zh" ? "EN" : "中文";
}

function toggleLang() {
  state.lang = state.lang === "zh" ? "en" : "zh";
  localStorage.setItem("lang", state.lang);
  applyLang();
  if (state.overview) { renderOverview(); renderStation(); renderDispatch(); renderRoi(); renderVpp(); }
}

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`API ${path} failed`);
  return response.json();
}

async function loadAll() {
  state.overview = await api("/api/overview");
  if (!state.selectedStationId) state.selectedStationId = state.overview.stations[0].id;
  state.stationDetail = await api(`/api/stations/${state.selectedStationId}`);
  renderOverview();
  renderStation();
  renderDispatch();
  renderRoi();
  renderVpp();
}

function renderOverview() {
  const totals = state.overview.totals;
  $("tenantLine").textContent = `${state.overview.tenant.name} · ${totals.station_count} ${t("stations")} · ${t("generated")} ${state.overview.generated_at}`;
  $("generatedAt").textContent = t("live");
  $("mHealth").textContent = totals.portfolio_health;
  $("mRevenue").textContent = money(totals.today_revenue);
  $("mMargin").textContent = money(totals.today_margin);
  $("mMarginRate").textContent = `${totals.gross_margin_rate}%`;
  $("mPower").textContent = number(totals.current_power_kw);
  $("mQueue").textContent = totals.queue_length;
  $("mVpp").textContent = number(state.overview.vpp.reliable_capacity_kw);

  const select = $("stationSelect");
  select.innerHTML = state.overview.stations.map((station) => `<option value="${station.id}">${station.name}</option>`).join("");
  select.value = state.selectedStationId;

  drawLineChart($("portfolioChart"), state.overview.portfolio_series, [
    { key: "grid_kw", label: "Grid", color: "#2563eb" },
    { key: "pv_kw", label: "PV", color: "#d97706" },
    { key: "storage_kw", label: "Storage", color: "#6d28d9" },
  ]);
  renderSavingsBars();
  renderStationRows();
}

function renderSavingsBars() {
  const rows = [...state.overview.stations].sort((a, b) => b.monthly_savings_potential - a.monthly_savings_potential);
  const max = Math.max(...rows.map((row) => row.monthly_savings_potential), 1);
  $("savingsBars").innerHTML = rows.map((row) => `
    <div class="bar-row">
      <span>${row.name}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(row.monthly_savings_potential / max) * 100}%"></div></div>
      <strong>${money(row.monthly_savings_potential)}</strong>
    </div>
  `).join("");
}

function renderStationRows() {
  $("stationRows").innerHTML = state.overview.stations.map((station) => `
    <tr class="station-row" data-station="${station.id}">
      <td><strong>${station.name}</strong><br><small>${station.type}</small></td>
      <td>${station.health_score}</td>
      <td>${number(station.current_power_kw)} kW</td>
      <td>${number(station.demand_peak_kw)} kW</td>
      <td>${station.storage_soc}%</td>
      <td>${station.connector_utilization}%</td>
      <td>${money(station.today_margin)}</td>
      <td>${number(station.vpp_capacity_kw)} kW</td>
    </tr>
  `).join("");
  document.querySelectorAll(".station-row").forEach((row) => {
    row.addEventListener("click", async () => {
      state.selectedStationId = row.dataset.station;
      $("stationSelect").value = state.selectedStationId;
      state.stationDetail = await api(`/api/stations/${state.selectedStationId}`);
      setView("station");
      renderStation();
      renderDispatch();
    });
  });
}

function renderStation() {
  const detail = state.stationDetail;
  const station = detail.station;
  $("stationName").textContent = station.name;
  $("stationMeta").textContent = `${station.address} · ${station.connectors} connectors · ${number(station.transformer_capacity_kw)} kW transformer`;
  $("stationMode").textContent = station.dispatch_mode;
  $("sSoc").textContent = `${station.storage_soc}%`;
  $("sHeadroom").textContent = number(station.demand_headroom_kw);
  $("sQueue").textContent = station.queue_length;
  $("sMargin").textContent = money(station.today_margin);

  drawLineChart($("stationChart"), detail.telemetry, [
    { key: "grid_kw", label: "Grid", color: "#2563eb" },
    { key: "load_kw", label: "Load", color: "#0f9f6e" },
    { key: "pv_kw", label: "PV", color: "#d97706" },
  ]);
  drawLineChart($("forecastChart"), detail.forecast, [
    { key: "grid_kw", label: "Forecast Grid", color: "#2563eb" },
    { key: "queue_length", label: "Queue", color: "#dc2626", scale: 90 },
  ]);
  $("alertList").innerHTML = detail.alerts.map((alert) => `
    <div class="event">
      <strong>${alert.title}</strong>
      <p>${alert.detail}</p>
      <div class="dispatch-meta"><span class="tag ${alert.priority}">${alert.priority}</span><span class="tag">${alert.timestamp}</span></div>
    </div>
  `).join("") || `<p>${t("no.alerts")}</p>`;
  $("pricingList").innerHTML = detail.pricing.map((item) => `
    <div class="event">
      <strong>${item.label} · ${item.strategy} · ${item.service_fee_delta}</strong>
      <p>${item.note}</p>
      <small>Expected queue ${item.expected_queue}</small>
    </div>
  `).join("");
  renderStoragePlan();
}

function renderDispatch() {
  const dispatch = state.overview.dispatch;
  $("dispatchSummary").textContent = `${dispatch.summary.count} ${t("recommendations")}`;
  $("dispatchList").innerHTML = dispatch.recommendations.map((item) => `
    <div class="dispatch-card">
      <strong>${item.title} · ${item.station}</strong>
      <p>${item.action}</p>
      <div class="dispatch-meta">
        <span class="tag ${item.risk}">${item.risk}</span>
        <span class="tag">${item.window}</span>
        <span class="tag">${item.mode}</span>
        <span class="tag">${item.approval}</span>
        <span class="tag">value ${number(item.value, 1)}</span>
      </div>
      <small>${item.rationale}</small>
    </div>
  `).join("");
  renderStoragePlan();
}

function renderStoragePlan() {
  const detail = state.stationDetail;
  $("storagePlan").innerHTML = detail.storage_plan.slice(0, 12).map((row) => `
    <div class="plan-row">
      <strong>${row.label} · ${row.action} · ${number(row.power_kw, 1)} kW</strong>
      <p>${row.reason}</p>
      <small>SOC ${row.soc}%</small>
    </div>
  `).join("");
}

async function renderRoi() {
  const capacity = $("capacityInput").value;
  const power = $("powerInput").value;
  const capex = $("capexInput").value;
  const vpp = $("vppInput").checked;
  const roi = await api(`/api/roi?capacity_kwh=${capacity}&power_kw=${power}&capex_per_kwh=${capex}&vpp=${vpp}`);
  $("roiDecision").textContent = roi.recommendation === "invest" ? t("roi.invest") : t("roi.review");
  $("rCapex").textContent = money(roi.capex);
  $("rBenefit").textContent = money(roi.annual_net_benefit);
  $("rPayback").textContent = roi.payback_years;
  $("rIrr").textContent = `${roi.irr}%`;
  const roiLabels = state.lang === "zh"
    ? ["需量节省", "峰谷套利", "VPP收益", "衰减成本", "运维费用"]
    : ["Demand savings", "Arbitrage", "VPP revenue", "Degradation", "Maintenance"];
  const rows = [
    [roiLabels[0], roi.annual_demand_savings, "#2563eb"],
    [roiLabels[1], roi.annual_arbitrage, "#0f9f6e"],
    [roiLabels[2], roi.annual_vpp_revenue, "#0891b2"],
    [roiLabels[3], roi.annual_degradation_cost, "#dc2626"],
    [roiLabels[4], roi.annual_maintenance, "#d97706"],
  ];
  const max = Math.max(...rows.map((row) => row[1]), 1);
  $("roiBars").innerHTML = rows.map(([label, value, color]) => `
    <div class="bar-row">
      <span>${label}</span>
      <div class="bar-track"><div class="bar-fill" style="width:${(value / max) * 100}%; background:${color}"></div></div>
      <strong>${money(value)}</strong>
    </div>
  `).join("");
}

function renderVpp() {
  const vpp = state.overview.vpp;
  $("vppEvent").textContent = `${vpp.event.title} · ${vpp.event.start} · ${vpp.event.duration_minutes} min`;
  $("vppStatus").textContent = vpp.event.status;
  $("vReliable").textContent = number(vpp.reliable_capacity_kw);
  $("vRequested").textContent = number(vpp.event.requested_kw);
  $("vRevenue").textContent = money(vpp.expected_revenue);
  $("vResources").textContent = vpp.resources.length;
  $("vppAllocations").innerHTML = vpp.allocations.map((item) => `
    <div class="dispatch-card">
      <strong>${item.station}</strong>
      <p>${item.method}</p>
      <div class="dispatch-meta"><span class="tag">${number(item.target_kw)} kW target</span></div>
    </div>
  `).join("");
  $("vppResources").innerHTML = vpp.resources.map((item) => `
    <div class="event">
      <strong>${item.station}</strong>
      <p>${number(item.adjustable_kw)} kW adjustable · ${item.duration_hours} h · ${item.confidence} confidence</p>
      <small>${number(item.storage_available_kwh)} kWh storage · ${number(item.load_curtailment_kw)} kW curtailment · cost ${item.response_cost_per_kwh}</small>
    </div>
  `).join("");
}

function drawLineChart(container, rows, series) {
  const width = container.clientWidth || 720;
  const height = container.clientHeight || 280;
  const pad = { top: 20, right: 24, bottom: 36, left: 48 };
  const chartW = width - pad.left - pad.right;
  const chartH = height - pad.top - pad.bottom;
  const values = rows.flatMap((row) => series.map((s) => Number(row[s.key] || 0) * (s.scale || 1)));
  const max = Math.max(...values, 1) * 1.12;
  const x = (i) => pad.left + (i / Math.max(1, rows.length - 1)) * chartW;
  const y = (v) => pad.top + chartH - (v / max) * chartH;
  const grid = [0.25, 0.5, 0.75, 1].map((g) => `<line class="gridline" x1="${pad.left}" x2="${width - pad.right}" y1="${pad.top + chartH * (1 - g)}" y2="${pad.top + chartH * (1 - g)}"/>`).join("");
  const paths = series.map((s) => {
    const d = rows.map((row, i) => `${i === 0 ? "M" : "L"} ${x(i).toFixed(1)} ${y(Number(row[s.key] || 0) * (s.scale || 1)).toFixed(1)}`).join(" ");
    return `<path d="${d}" fill="none" stroke="${s.color}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"/>`;
  }).join("");
  const labels = rows.filter((_, i) => i % Math.ceil(rows.length / 6) === 0).map((row, i, filtered) => {
    const idx = rows.indexOf(row);
    return `<text x="${x(idx)}" y="${height - 10}" text-anchor="${i === 0 ? "start" : i === filtered.length - 1 ? "end" : "middle"}" font-size="11" fill="#617087">${row.label}</text>`;
  }).join("");
  const legend = series.map((s, i) => `<g transform="translate(${pad.left + i * 116},12)"><circle r="4" fill="${s.color}"/><text x="10" y="4" font-size="11" fill="#617087">${s.label}</text></g>`).join("");
  container.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Operational chart">${grid}<line class="axis" x1="${pad.left}" x2="${width - pad.right}" y1="${height - pad.bottom}" y2="${height - pad.bottom}"/><line class="axis" x1="${pad.left}" x2="${pad.left}" y1="${pad.top}" y2="${height - pad.bottom}"/>${paths}${labels}${legend}</svg>`;
}

function setView(id) {
  document.querySelectorAll(".view").forEach((view) => view.classList.toggle("active", view.id === id));
  document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === id));
}

applyLang();
document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
$("langToggle").addEventListener("click", toggleLang);
$("refreshButton").addEventListener("click", loadAll);
$("stationSelect").addEventListener("change", async (event) => {
  state.selectedStationId = event.target.value;
  state.stationDetail = await api(`/api/stations/${state.selectedStationId}`);
  renderStation();
  renderDispatch();
});
["capacityInput", "powerInput", "capexInput", "vppInput"].forEach((id) => $(id).addEventListener("input", renderRoi));
window.addEventListener("resize", () => {
  if (state.overview) {
    drawLineChart($("portfolioChart"), state.overview.portfolio_series, [
      { key: "grid_kw", label: "Grid", color: "#2563eb" },
      { key: "pv_kw", label: "PV", color: "#d97706" },
      { key: "storage_kw", label: "Storage", color: "#6d28d9" },
    ]);
    renderStation();
  }
});

loadAll().catch((error) => {
  const msg = state.lang === "zh" ? "ChargeOpt OS 加载失败" : "ChargeOpt OS failed to load";
  document.body.innerHTML = `<main class="workspace"><section class="panel"><h1>${msg}</h1><p>${error.message}</p></section></main>`;
});
