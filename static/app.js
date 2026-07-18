const state = {
  overview: null,
  revenueDiagnostics: null,
  trading: null,
  stationDetail: null,
  twin: null,
  twinSimulation: null,
  selectedStationId: null,
  lang: localStorage.getItem("lang") || "zh",
  token: sessionStorage.getItem("chargeoptToken"),
};

const $ = (id) => document.getElementById(id);
const money = (value) => new Intl.NumberFormat("en-US", { maximumFractionDigits: 0 }).format(value);
const number = (value, digits = 0) => new Intl.NumberFormat("en-US", { maximumFractionDigits: digits }).format(value);

const TRANSLATIONS = {
  zh: {
    "nav.cockpit": "驾驶舱", "nav.cockpit.title": "运营驾驶舱",
    "nav.station": "站点", "nav.station.title": "站点详情",
    "nav.twin": "孪生", "nav.twin.title": "充电站数字孪生",
    "nav.dispatch": "调度", "nav.dispatch.title": "调度中心",
    "nav.roi": "ROI", "nav.roi.title": "储能ROI模拟",
    "nav.vpp": "VPP", "nav.vpp.title": "VPP资源",
    "nav.trading": "交易", "nav.trading.title": "VPP 自动交易",
    "mode.label": "控制模式", "mode.value": "自动交易", "mode.hint": "风控策略已启用",
    "header.title": "能源调度运营中心", "header.loading": "加载中...",
    "btn.refresh": "刷新数据", "btn.logout": "退出登录",
    "login.title": "安全登录", "login.desc": "使用租户运营账号进入自动交易控制台", "login.email": "邮箱", "login.password": "密码", "login.submit": "登录",
    "metric.health": "组合健康度", "metric.health.unit": "评分",
    "metric.revenue": "今日收入",
    "metric.margin": "毛利润",
    "metric.power": "当前功率", "metric.power.unit": "kW 从电网",
    "metric.queue": "排队", "metric.queue.unit": "辆",
    "metric.vpp": "VPP 容量", "metric.vpp.unit": "可靠 kW",
    "metric.storage": "储能", "metric.headroom": "余量", "metric.today": "今日",
    "panel.portfolioLoad": "组合负荷", "panel.portfolioLoad.desc": "电网进口、光伏、储能动作及排队压力",
    "panel.savings": "节省潜力", "panel.savings.desc": "来自削峰、储能及排队缓解的月度价值",
    "panel.proof": "收益证明", "panel.proof.desc": "同一站点在反事实基线下每月多赚/少亏金额",
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
    "proof.monthly": "月度净增益", "proof.annual": "年化增益", "proof.confidence": "P90置信区间",
    "trade.market": "交易市场", "trade.openOrders": "未结订单", "trade.orders": "笔", "trade.filled": "24h 成交", "trade.breaker": "熔断器",
    "trade.ordersTitle": "市场订单", "trade.ordersDesc": "报价、成交、交付窗口和风险状态", "trade.status": "状态", "trade.product": "产品", "trade.delivery": "交付", "trade.quantity": "容量", "trade.price": "限价", "trade.fill": "成交",
    "trade.autopilot": "自动交易", "trade.autopilotDesc": "预测、风控、报价与场站调度闭环", "trade.settlements": "结算与证据", "trade.settlementsDesc": "计量基线、偏差费用、罚金和证据根哈希",
    "twin.trust": "孪生可信度", "twin.headroom": "变压器余量", "twin.evidence": "证据等级",
    "twin.autoGate": "自动控制门", "twin.topology": "设备拓扑", "twin.topologyDesc": "站点电气关系、设备容量与通信控制边界",
    "twin.diagnostics": "诊断事件", "twin.diagnosticsDesc": "基于拓扑、残差和约束传播的根因排序",
    "twin.trajectory": "物理轨迹", "twin.trajectoryDesc": "负荷、光伏、储能与变压器约束的确定性仿真",
    "twin.scenario": "场景实验", "twin.scenarioDesc": "在隔离仿真中调整负荷和储能动作",
    "twin.loadMultiplier": "负荷倍率", "twin.storageCommand": "储能指令 kW", "twin.horizon": "仿真步数", "twin.run": "运行仿真",
  },
  en: {
    "nav.cockpit": "Cockpit", "nav.cockpit.title": "Operating cockpit",
    "nav.station": "Station", "nav.station.title": "Station detail",
    "nav.twin": "Twin", "nav.twin.title": "Charging-station digital twin",
    "nav.dispatch": "Dispatch", "nav.dispatch.title": "Dispatch center",
    "nav.roi": "ROI", "nav.roi.title": "Storage ROI simulator",
    "nav.vpp": "VPP", "nav.vpp.title": "VPP resources",
    "nav.trading": "Trading", "nav.trading.title": "Automated VPP trading",
    "mode.label": "Control Mode", "mode.value": "Autopilot", "mode.hint": "Risk policy active",
    "header.title": "Energy Dispatch Operations", "header.loading": "Loading portfolio...",
    "btn.refresh": "Refresh data", "btn.logout": "Sign out",
    "login.title": "Secure sign in", "login.desc": "Use your tenant operator account to open the trading control plane", "login.email": "Email", "login.password": "Password", "login.submit": "Sign in",
    "metric.health": "Portfolio Health", "metric.health.unit": "score",
    "metric.revenue": "Today Revenue",
    "metric.margin": "Gross Margin",
    "metric.power": "Current Power", "metric.power.unit": "kW import",
    "metric.queue": "Queue", "metric.queue.unit": "vehicles",
    "metric.vpp": "VPP Capacity", "metric.vpp.unit": "reliable kW",
    "metric.storage": "storage", "metric.headroom": "Headroom", "metric.today": "today",
    "panel.portfolioLoad": "Portfolio Load", "panel.portfolioLoad.desc": "Grid import, PV, storage action, and queue pressure",
    "panel.savings": "Savings Potential", "panel.savings.desc": "Monthly value from peak control, storage, and queue relief",
    "panel.proof": "Profit Proof", "panel.proof.desc": "Monthly profit lift versus the same station counterfactual",
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
    "proof.monthly": "Monthly net lift", "proof.annual": "Annualized lift", "proof.confidence": "P90 interval",
    "trade.market": "Market", "trade.openOrders": "Open orders", "trade.orders": "orders", "trade.filled": "24h filled", "trade.breaker": "Circuit breaker",
    "trade.ordersTitle": "Market orders", "trade.ordersDesc": "Bid, fill, delivery window, and risk state", "trade.status": "Status", "trade.product": "Product", "trade.delivery": "Delivery", "trade.quantity": "Capacity", "trade.price": "Limit", "trade.fill": "Filled",
    "trade.autopilot": "Trading autopilot", "trade.autopilotDesc": "Forecast, risk, bid, and site dispatch closure", "trade.settlements": "Settlement evidence", "trade.settlementsDesc": "Meter baseline, imbalance, penalties, and evidence root hash",
    "twin.trust": "Twin trust", "twin.headroom": "Transformer headroom", "twin.evidence": "Evidence class",
    "twin.autoGate": "Autonomy gate", "twin.topology": "Asset topology", "twin.topologyDesc": "Electrical flow, equipment ratings, and control boundaries",
    "twin.diagnostics": "Diagnostics", "twin.diagnosticsDesc": "Root causes ranked from topology, residuals, and constraints",
    "twin.trajectory": "Physical trajectory", "twin.trajectoryDesc": "Deterministic load, PV, storage, and transformer simulation",
    "twin.scenario": "Scenario lab", "twin.scenarioDesc": "Adjust load and storage actions in an isolated simulation",
    "twin.loadMultiplier": "Load multiplier", "twin.storageCommand": "Storage command kW", "twin.horizon": "Simulation steps", "twin.run": "Run simulation",
  },
};

function t(key) {
  return (TRANSLATIONS[state.lang] || TRANSLATIONS.zh)[key] || key;
}

const MODE_ZH = { recommend: "建议模式", semi_auto: "半自动", auto: "全自动" };
const MODE_EN = { recommend: "recommend", semi_auto: "semi-auto", auto: "auto" };
const TYPE_ZH = { urban_ultrafast: "城区超充", heavy_truck_depot: "重卡仓储", pv_storage_charging: "光储充一体" };
const TYPE_EN = { urban_ultrafast: "urban ultrafast", heavy_truck_depot: "heavy truck depot", pv_storage_charging: "PV+storage" };
const ACTION_ZH = { hold: "持守", charge: "充电", discharge: "放电" };
const ACTION_EN = { hold: "hold", charge: "charge", discharge: "discharge" };
const PRIORITY_ZH = { critical: "严重", high: "高", medium: "中", low: "低" };
const PRIORITY_EN = { critical: "critical", high: "high", medium: "medium", low: "low" };
const APPROVAL_ZH = { required: "需审批", observe: "仅观察" };
const APPROVAL_EN = { required: "required", observe: "observe" };

function tMode(v) { return state.lang === "zh" ? (MODE_ZH[v] || v) : (MODE_EN[v] || v); }
function tType(v) { return state.lang === "zh" ? (TYPE_ZH[v] || v) : (TYPE_EN[v] || v); }
function tAction(v) { return state.lang === "zh" ? (ACTION_ZH[v] || v) : (ACTION_EN[v] || v); }
function tPriority(v) { return state.lang === "zh" ? (PRIORITY_ZH[v] || v) : (PRIORITY_EN[v] || v); }
function tApproval(v) { return state.lang === "zh" ? (APPROVAL_ZH[v] || v) : (APPROVAL_EN[v] || v); }

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
  if (state.overview) { renderOverview(); renderStation(); renderTwin(); renderDispatch(); renderRoi(); renderVpp(); renderRevenueProof(); renderTrading(); }
}

async function api(path, options = {}) {
  const headers = new Headers(options.headers || {});
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    const error = new Error("authentication_required");
    error.code = 401;
    throw error;
  }
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    throw new Error(detail.detail || `API ${path} failed`);
  }
  return response.json();
}

async function loadAll() {
  [state.overview, state.revenueDiagnostics] = await Promise.all([
    api("/api/overview"), api("/api/revenue-diagnostics"),
  ]);
  state.trading = await api("/api/vpp/trading/dashboard").catch(() => null);
  if (!state.selectedStationId) state.selectedStationId = state.overview.stations[0].id;
  [state.stationDetail, state.twin] = await Promise.all([
    api(`/api/stations/${state.selectedStationId}`),
    api(`/api/digital-twin/stations/${state.selectedStationId}`),
  ]);
  state.twinSimulation = null;
  renderOverview();
  renderStation();
  renderTwin();
  renderDispatch();
  renderRoi();
  renderVpp();
  renderRevenueProof();
  renderTrading();
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

  const zh = state.lang === "zh";
  drawLineChart($("portfolioChart"), state.overview.portfolio_series, [
    { key: "grid_kw", label: zh ? "电网" : "Grid", color: "#2563eb" },
    { key: "pv_kw", label: zh ? "光伏" : "PV", color: "#d97706" },
    { key: "storage_kw", label: zh ? "储能" : "Storage", color: "#6d28d9" },
  ]);
  renderSavingsBars();
  renderRevenueProof();
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
      <td><strong>${station.name}</strong><br><small>${tType(station.type)}</small></td>
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
      [state.stationDetail, state.twin] = await Promise.all([
        api(`/api/stations/${state.selectedStationId}`),
        api(`/api/digital-twin/stations/${state.selectedStationId}`),
      ]);
      state.twinSimulation = null;
      setView("station");
      renderStation();
      renderTwin();
      renderDispatch();
    });
  });
}

function renderRevenueProof() {
  if (!state.revenueDiagnostics) return;
  const proof = state.revenueDiagnostics;
  const portfolio = proof.portfolio;
  const interval = portfolio.confidence_interval;
  $("proofSummary").textContent = portfolio.proof_statement;
  $("proofMetrics").innerHTML = `
    <article class="metric"><span>${t("proof.monthly")}</span><strong>${money(portfolio.monthly_net_impact)}</strong><small>CNY</small></article>
    <article class="metric"><span>${t("proof.annual")}</span><strong>${money(portfolio.annualized_net_impact)}</strong><small>CNY</small></article>
    <article class="metric"><span>${t("proof.confidence")}</span><strong>${money(interval.p90_low)}~${money(interval.p90_high)}</strong><small>CNY</small></article>
    <article class="metric"><span>Moat</span><strong>${proof.moat.score}</strong><small>${proof.moat.data_hours} data hours</small></article>
  `;
  $("proofCards").innerHTML = proof.stations.map((item) => `
    <div class="dispatch-card">
      <strong>${item.station}</strong>
      <p>${item.proof_statement}</p>
      <div class="dispatch-meta">
        <span class="tag">${t("proof.monthly")} ${money(item.monthly_net_impact)}</span>
        <span class="tag">${item.profit_lift_percent}%</span>
        <span class="tag">${item.evidence_grade}</span>
      </div>
    </div>
  `).join("");
}

function renderStation() {
  const detail = state.stationDetail;
  const station = detail.station;
  $("stationName").textContent = station.name;
  $("stationMeta").textContent = state.lang === "zh"
    ? `${station.address} · ${station.connectors} 个充电桩 · 变压器 ${number(station.transformer_capacity_kw)} kW`
    : `${station.address} · ${station.connectors} connectors · ${number(station.transformer_capacity_kw)} kW transformer`;
  $("stationMode").textContent = tMode(station.dispatch_mode);
  $("sSoc").textContent = `${station.storage_soc}%`;
  $("sHeadroom").textContent = number(station.demand_headroom_kw);
  $("sQueue").textContent = station.queue_length;
  $("sMargin").textContent = money(station.today_margin);

  const zh2 = state.lang === "zh";
  drawLineChart($("stationChart"), detail.telemetry, [
    { key: "grid_kw", label: zh2 ? "电网" : "Grid", color: "#2563eb" },
    { key: "load_kw", label: zh2 ? "负荷" : "Load", color: "#0f9f6e" },
    { key: "pv_kw", label: zh2 ? "光伏" : "PV", color: "#d97706" },
  ]);
  drawLineChart($("forecastChart"), detail.forecast, [
    { key: "grid_kw", label: zh2 ? "预测电网" : "Forecast Grid", color: "#2563eb" },
    { key: "queue_length", label: zh2 ? "排队" : "Queue", color: "#dc2626", scale: 90 },
  ]);
  $("alertList").innerHTML = detail.alerts.map((alert) => `
    <div class="event">
      <strong>${alert.title}</strong>
      <p>${alert.detail}</p>
      <div class="dispatch-meta"><span class="tag ${alert.priority}">${tPriority(alert.priority)}</span><span class="tag">${alert.timestamp}</span></div>
    </div>
  `).join("") || `<p>${t("no.alerts")}</p>`;
  $("pricingList").innerHTML = detail.pricing.map((item) => `
    <div class="event">
      <strong>${item.label} · ${item.strategy} · ${item.service_fee_delta}</strong>
      <p>${item.note}</p>
      <small>${state.lang === "zh" ? "预计排队" : "Expected queue"} ${item.expected_queue}</small>
    </div>
  `).join("");
  renderStoragePlan();
}

function renderTwin() {
  if (!state.twin) return;
  const twin = state.twin;
  const snapshot = twin.state;
  const topology = twin.topology;
  const stateByCode = Object.fromEntries(snapshot.states.map((item) => [item.state_code, item]));
  $("twTrust").textContent = `${number(snapshot.trust_score * 100, 1)}%`;
  $("twHealth").textContent = snapshot.health;
  $("twSoc").textContent = `${number(snapshot.storage_soc * 100, 1)}%`;
  $("twSoh").textContent = `SOH ${number(snapshot.estimated_soh * 100, 1)}%`;
  $("twHeadroom").textContent = number(snapshot.transformer_headroom_kw, 1);
  $("twEvidence").textContent = snapshot.contract.evidence_class;
  $("twModel").textContent = snapshot.contract.model_version;
  const gate = snapshot.autonomy_gate;
  $("twGate").classList.toggle("blocked", !gate.allowed);
  $("twGateStatus").textContent = gate.allowed ? (state.lang === "zh" ? "允许" : "Allowed") : (state.lang === "zh" ? "已阻断" : "Blocked");
  $("twGateText").textContent = gate.allowed
    ? (state.lang === "zh" ? "状态、证据和现场资格满足自动控制要求。" : "State, evidence, and field qualification satisfy autonomy requirements.")
    : gate.reasons.map(twinGateReason).join(" · ");
  $("twTopologyStatus").textContent = `${topology.validation.asset_count} ${state.lang === "zh" ? "个资产" : "assets"}`;
  renderTwinTopology(topology);
  renderTwinDiagnostics(twin.diagnostics.diagnostics);

  const trajectory = state.twinSimulation
    ? state.twinSimulation.trajectory.map((row) => ({
      ...row,
      label: row.timestamp ? new Date(row.timestamp).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) : `${row.step}`,
    }))
    : state.stationDetail.telemetry;
  drawLineChart($("twTrajectory"), trajectory, [
    { key: "grid_kw", label: state.lang === "zh" ? "电网" : "Grid", color: "#2563eb" },
    { key: "load_kw", label: state.lang === "zh" ? "负荷" : "Load", color: "#0f9f6e" },
    { key: "pv_kw", label: state.lang === "zh" ? "光伏" : "PV", color: "#d97706" },
  ]);
  if (state.twinSimulation) {
    const metrics = state.twinSimulation.metrics;
    $("twSimulationStatus").textContent = state.twinSimulation.contract.evidence_class;
    $("twSimulationMetrics").innerHTML = `
      <span class="tag">${state.lang === "zh" ? "峰值" : "Peak"} ${number(metrics.max_grid_kw, 1)} kW</span>
      <span class="tag">${state.lang === "zh" ? "吞吐量" : "Throughput"} ${number(metrics.battery_throughput_kwh, 1)} kWh</span>
      <span class="tag ${metrics.constraint_violation_count ? "critical" : "low"}">${state.lang === "zh" ? "约束越界" : "Violations"} ${metrics.constraint_violation_count}</span>
      <span class="tag">SOC ${number(metrics.final_soc * 100, 1)}%</span>
    `;
  } else {
    $("twSimulationStatus").textContent = state.lang === "zh" ? "实测轨迹" : "Measured trajectory";
    $("twSimulationMetrics").innerHTML = `
      <span class="tag">${state.lang === "zh" ? "平衡残差" : "Balance residual"} ${number(snapshot.balance_residual_kw, 2)} kW</span>
      <span class="tag">${state.lang === "zh" ? "效率" : "Efficiency"} ${number((stateByCode.conversion_efficiency?.value || 0) * 100, 1)}%</span>
    `;
  }
}

function renderTwinTopology(topology) {
  const counts = topology.assets.reduce((result, asset) => {
    result[asset.asset_type] = (result[asset.asset_type] || 0) + 1;
    return result;
  }, {});
  const infrastructure = topology.assets.filter((asset) => asset.asset_type !== "connector").slice(0, 18);
  $("twTopology").innerHTML = `
    <div class="topology-summary">${Object.entries(counts).map(([type, count]) => `<span class="tag">${twinAssetType(type)} ${count}</span>`).join("")}</div>
    <div class="asset-grid">${infrastructure.map((asset) => `
      <div class="asset-node asset-${asset.asset_type}">
        <span>${twinAssetType(asset.asset_type)}</span>
        <strong>${asset.name}</strong>
        <small>${asset.rated_power_kw ? `${number(asset.rated_power_kw)} kW` : asset.asset_key}</small>
      </div>
    `).join("")}</div>
  `;
}

function renderTwinDiagnostics(diagnostics) {
  $("twDiagnostics").innerHTML = diagnostics.length ? diagnostics.map((item) => `
    <div class="event">
      <strong>${item.diagnostic_type}</strong>
      <p>${item.summary}</p>
      <div class="dispatch-meta"><span class="tag ${item.severity}">${tPriority(item.severity)}</span><span class="tag">${number(item.confidence * 100, 0)}%</span></div>
      <small>${item.likely_causes.join(" · ")}</small>
    </div>
  `).join("") : `<p>${state.lang === "zh" ? "当前未发现孪生诊断事件。" : "No twin diagnostic events detected."}</p>`;
}

function twinGateReason(reason) {
  if (state.lang !== "zh") return reason.replaceAll("_", " ");
  return {
    critical_measurements_missing: "关键测点缺失",
    telemetry_stale: "遥测已过期",
    twin_trust_below_threshold: "孪生可信度不足",
    power_balance_residual_high: "功率平衡残差过高",
    field_qualification_required_for_autonomy: "尚未完成现场资格认证",
  }[reason] || reason;
}

function twinAssetType(type) {
  if (state.lang !== "zh") return type.replaceAll("_", " ");
  return {
    station: "站点", transformer: "变压器", bus: "母线", meter: "电表", charger: "充电机",
    connector: "充电枪", pcs: "PCS", battery_system: "储能系统", battery_rack: "电池簇",
    battery_pack: "电池包", pv_inverter: "光伏逆变器", sensor: "传感器", gateway: "边缘网关",
  }[type] || type;
}

async function runTwinSimulation() {
  const button = $("twRunSimulation");
  button.disabled = true;
  try {
    const horizon = Number($("twHorizon").value);
    const loadMultiplier = Number($("twLoadMultiplier").value);
    const storageCommand = Number($("twStorageCommand").value);
    const source = state.stationDetail.forecast;
    const start = Date.now();
    const schedule = Array.from({ length: horizon }, (_, index) => {
      const row = source[index % source.length];
      return {
        timestamp: new Date(start + index * 15 * 60 * 1000).toISOString(),
        load_kw: Number(row.load_kw || row.grid_kw || 0) * loadMultiplier,
        pv_kw: Number(row.pv_kw || 0),
        storage_power_kw: storageCommand,
        ambient_temperature_c: 30,
        arrivals: Number(row.queue_length || 0) * 0.2,
      };
    });
    const evidenceClass = state.twin.state.contract.evidence_class;
    state.twinSimulation = await api("/api/digital-twin/simulations", {
      method: "POST",
      body: JSON.stringify({
        station_id: state.selectedStationId,
        scenario_type: evidenceClass === "synthetic" ? "what_if" : "shadow",
        evidence_class: evidenceClass,
        interval_minutes: 15,
        idempotency_key: `ui-${state.selectedStationId}-${Date.now()}`,
        initial_state: {
          storage_soc: state.twin.state.storage_soc,
          storage_soh: state.twin.state.estimated_soh,
        },
        schedule,
      }),
    });
    renderTwin();
  } finally {
    button.disabled = false;
  }
}

function renderDispatch() {
  const dispatch = state.overview.dispatch;
  $("dispatchSummary").textContent = `${dispatch.summary.count} ${t("recommendations")}`;
  $("dispatchList").innerHTML = dispatch.recommendations.map((item) => `
    <div class="dispatch-card">
      <strong>${item.title} · ${item.station}</strong>
      <p>${item.action}</p>
      <div class="dispatch-meta">
        <span class="tag ${item.risk}">${tPriority(item.risk)}</span>
        <span class="tag">${item.window}</span>
        <span class="tag">${tMode(item.mode)}</span>
        <span class="tag">${tApproval(item.approval)}</span>
        <span class="tag">${state.lang === "zh" ? "价值" : "value"} ${number(item.value, 1)}</span>
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
      <strong>${row.label} · ${tAction(row.action)} · ${number(row.power_kw, 1)} kW</strong>
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
  $("vppEvent").textContent = `${vpp.event.title} · ${vpp.event.start} · ${vpp.event.duration_minutes} ${state.lang === "zh" ? "分钟" : "min"}`;
  $("vppStatus").textContent = state.lang === "zh" ? "待审批" : vpp.event.status;
  $("vReliable").textContent = number(vpp.reliable_capacity_kw);
  $("vRequested").textContent = number(vpp.event.requested_kw);
  $("vRevenue").textContent = money(vpp.expected_revenue);
  $("vResources").textContent = vpp.resources.length;
  $("vppAllocations").innerHTML = vpp.allocations.map((item) => `
    <div class="dispatch-card">
      <strong>${item.station}</strong>
      <p>${item.method}</p>
      <div class="dispatch-meta"><span class="tag">${state.lang === "zh" ? "目标" : "target"} ${number(item.target_kw)} kW</span></div>
    </div>
  `).join("");
  $("vppResources").innerHTML = vpp.resources.map((item) => `
    <div class="event">
      <strong>${item.station}</strong>
      <p>${number(item.adjustable_kw)} kW ${state.lang === "zh" ? "可调" : "adjustable"} · ${item.duration_hours} ${state.lang === "zh" ? "小时" : "h"} · ${item.confidence} ${state.lang === "zh" ? "置信度" : "confidence"}</p>
      <small>${number(item.storage_available_kwh)} kWh ${state.lang === "zh" ? "储能可用" : "storage"} · ${number(item.load_curtailment_kw)} kW ${state.lang === "zh" ? "负荷削减" : "curtailment"} · ${state.lang === "zh" ? "成本" : "cost"} ${item.response_cost_per_kwh}</small>
    </div>
  `).join("");
}

function renderTrading() {
  const trading = state.trading;
  if (!trading) {
    $("tMarket").textContent = "--";
    $("tradeOrderRows").innerHTML = `<tr><td colspan="6">${state.lang === "zh" ? "交易数据库尚未配置" : "Trading database is not configured"}</td></tr>`;
    $("automationRuns").innerHTML = "";
    $("settlementRows").innerHTML = "";
    return;
  }
  $("tMarket").textContent = trading.connection.market_code;
  $("tMode").textContent = `${trading.connection.mode} · ${trading.connection.participant_id}`;
  $("tOpenOrders").textContent = trading.metrics.open_orders;
  $("tFilled").textContent = number(trading.metrics.filled_kw_24h, 1);
  $("tBreaker").textContent = trading.circuit_breaker.state;
  $("tBreaker").className = trading.circuit_breaker.state === "closed" ? "ok-text" : "danger-text";
  $("tBreakerReason").textContent = trading.circuit_breaker.reason || (state.lang === "zh" ? "运行正常" : "Operating normally");
  $("tPolicy").textContent = `${trading.risk_policy.name} · v${trading.risk_policy.version}`;
  $("tradeOrderRows").innerHTML = trading.orders.length ? trading.orders.map((order) => `
    <tr>
      <td><span class="tag order-${order.status}">${order.status}</span></td>
      <td>${order.product}</td>
      <td>${new Date(order.delivery_start).toLocaleString()}</td>
      <td>${number(order.quantity_kw, 1)} kW</td>
      <td>${number(order.limit_price_per_kwh, 3)}</td>
      <td>${number(order.filled_quantity_kw, 1)} kW</td>
    </tr>
  `).join("") : `<tr><td colspan="6">${state.lang === "zh" ? "暂无订单" : "No orders"}</td></tr>`;
  $("automationRuns").innerHTML = trading.automation_runs.length ? trading.automation_runs.map((run) => `
    <div class="event">
      <strong>${run.status} · ${new Date(run.started_at).toLocaleString()}</strong>
      <p>${run.trigger_source} · ${run.orders_created} ${state.lang === "zh" ? "笔订单" : "orders"}</p>
      <small>${run.error || `${run.tasks_created} ${state.lang === "zh" ? "个场站任务" : "site tasks"}`}</small>
    </div>
  `).join("") : `<p>${state.lang === "zh" ? "等待首个自动交易周期" : "Waiting for the first automation cycle"}</p>`;
  $("settlementRows").innerHTML = trading.settlements.length ? trading.settlements.map((batch) => `
    <div class="dispatch-card">
      <strong>${batch.market_code} · ${batch.status}</strong>
      <p>${new Date(batch.period_start).toLocaleDateString()} · ${state.lang === "zh" ? "净收益" : "net"} ${money(batch.net_revenue)} CNY</p>
      <div class="dispatch-meta"><span class="tag">${state.lang === "zh" ? "毛收入" : "gross"} ${money(batch.gross_revenue)}</span><span class="tag">${state.lang === "zh" ? "偏差" : "imbalance"} ${money(batch.imbalance_cost)}</span><span class="tag">${state.lang === "zh" ? "罚金" : "penalty"} ${money(batch.penalties)}</span></div>
      <small class="hash-text">${batch.evidence_root_hash}</small>
    </div>
  `).join("") : `<p>${state.lang === "zh" ? "暂无结算批次" : "No settlement batches"}</p>`;
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
$("logoutButton").addEventListener("click", () => {
  sessionStorage.removeItem("chargeoptToken");
  state.token = null;
  $("loginScreen").hidden = false;
});
$("loginForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  $("loginError").textContent = "";
  try {
    const result = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email: $("loginEmail").value, password: $("loginPassword").value }),
    });
    state.token = result.access_token;
    sessionStorage.setItem("chargeoptToken", state.token);
    $("loginPassword").value = "";
    $("loginScreen").hidden = true;
    await loadAll();
  } catch (error) {
    $("loginError").textContent = state.lang === "zh" ? "登录失败，请检查账号和密码。" : "Sign in failed. Check your credentials.";
  }
});
$("stationSelect").addEventListener("change", async (event) => {
  state.selectedStationId = event.target.value;
  [state.stationDetail, state.twin] = await Promise.all([
    api(`/api/stations/${state.selectedStationId}`),
    api(`/api/digital-twin/stations/${state.selectedStationId}`),
  ]);
  state.twinSimulation = null;
  renderStation();
  renderTwin();
  renderDispatch();
});
$("twRunSimulation").addEventListener("click", runTwinSimulation);
["capacityInput", "powerInput", "capexInput", "vppInput"].forEach((id) => $(id).addEventListener("input", renderRoi));
window.addEventListener("resize", () => {
  if (state.overview) {
    const zh3 = state.lang === "zh";
    drawLineChart($("portfolioChart"), state.overview.portfolio_series, [
      { key: "grid_kw", label: zh3 ? "电网" : "Grid", color: "#2563eb" },
      { key: "pv_kw", label: zh3 ? "光伏" : "PV", color: "#d97706" },
      { key: "storage_kw", label: zh3 ? "储能" : "Storage", color: "#6d28d9" },
    ]);
    renderStation();
    renderTwin();
  }
});

async function bootstrap() {
  try {
    await loadAll();
  } catch (error) {
    if (error.code === 401) {
      $("loginScreen").hidden = false;
      return;
    }
    const msg = state.lang === "zh" ? "ChargeOpt OS 加载失败" : "ChargeOpt OS failed to load";
    $("tenantLine").textContent = `${msg}: ${error.message}`;
  }
}

bootstrap();
