const state = {
  overview: null,
  stationDetail: null,
  selectedStationId: null,
};

const $ = (id) => document.getElementById(id);
const money = (value) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
const number = (value, digits = 0) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);

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
  $("tenantLine").textContent = `${state.overview.tenant.name} · ${totals.station_count} stations · generated ${state.overview.generated_at}`;
  $("generatedAt").textContent = "Live fixture";
  $("mHealth").textContent = totals.portfolio_health;
  $("mRevenue").textContent = money(totals.today_revenue);
  $("mMargin").textContent = money(totals.today_margin);
  $("mMarginRate").textContent = `${totals.gross_margin_rate}% margin`;
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
  `).join("") || "<p>No alerts.</p>";
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
  $("dispatchSummary").textContent = `${dispatch.summary.count} recommendations`;
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
  $("roiDecision").textContent = roi.recommendation === "invest" ? "Investment case meets hurdle" : "Review assumptions before approval";
  $("rCapex").textContent = money(roi.capex);
  $("rBenefit").textContent = money(roi.annual_net_benefit);
  $("rPayback").textContent = roi.payback_years;
  $("rIrr").textContent = `${roi.irr}%`;
  const rows = [
    ["Demand savings", roi.annual_demand_savings, "#2563eb"],
    ["Arbitrage", roi.annual_arbitrage, "#0f9f6e"],
    ["VPP revenue", roi.annual_vpp_revenue, "#0891b2"],
    ["Degradation", roi.annual_degradation_cost, "#dc2626"],
    ["Maintenance", roi.annual_maintenance, "#d97706"],
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

document.querySelectorAll(".nav-item").forEach((item) => item.addEventListener("click", () => setView(item.dataset.view)));
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
  document.body.innerHTML = `<main class="workspace"><section class="panel"><h1>ChargeOpt OS failed to load</h1><p>${error.message}</p></section></main>`;
});
