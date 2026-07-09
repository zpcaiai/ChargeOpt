from chargeopt.analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail
from chargeopt.data import load_repository
from chargeopt.revenue_intelligence import build_revenue_diagnostics


def test_overview_has_portfolio_metrics():
    repo = load_repository()
    overview = build_overview(repo)

    assert overview["totals"]["station_count"] == 3
    assert overview["totals"]["today_revenue"] > 0
    assert overview["totals"]["monthly_savings_potential"] > 0
    assert len(overview["portfolio_series"]) == 24


def test_station_detail_contains_forecast_and_storage_plan():
    repo = load_repository()
    detail = station_detail(repo, "st-hq-hongqiao")

    assert detail["station"]["name"] == "虹桥枢纽超充站"
    assert len(detail["telemetry"]) == 24
    assert len(detail["forecast"]) == 24
    assert len(detail["storage_plan"]) == 24
    assert {row["action"] for row in detail["storage_plan"]} <= {"charge", "discharge", "hold"}


def test_dispatch_is_auditable_and_has_recommendations():
    repo = load_repository()
    dispatch = build_dispatch(repo)

    assert dispatch["approval_required"] is True
    assert dispatch["summary"]["count"] == len(dispatch["recommendations"])
    assert all("rationale" in item for item in dispatch["recommendations"])
    assert all(item["approval"] in {"required", "observe"} for item in dispatch["recommendations"])


def test_vpp_capacity_decomposes_event_to_stations():
    repo = load_repository()
    vpp = build_vpp(repo)

    assert vpp["reliable_capacity_kw"] > 0
    assert len(vpp["resources"]) == len(repo.stations)
    assert len(vpp["allocations"]) == len(repo.stations)
    assert sum(item["target_kw"] for item in vpp["allocations"]) <= vpp["event"]["requested_kw"] + 1


def test_roi_case_returns_positive_business_metrics():
    repo = load_repository()
    roi = simulate_roi(repo, capacity_kwh=1200, power_kw=600, capex_per_kwh=1150, include_vpp=True)

    assert roi["capex"] > 0
    assert roi["annual_net_benefit"] > 0
    assert roi["payback_years"] > 0
    assert "recommendation" in roi


def test_revenue_diagnostics_prove_monthly_counterfactual_value():
    repo = load_repository()
    diagnostics = build_revenue_diagnostics(repo)

    assert diagnostics["portfolio"]["monthly_net_impact"] > 0
    assert diagnostics["portfolio"]["annualized_net_impact"] == diagnostics["portfolio"]["monthly_net_impact"] * 12
    assert len(diagnostics["stations"]) == len(repo.stations)
    assert diagnostics["moat"]["device_adapter_protocols"] == ["ocpp", "modbus", "mqtt"]
    assert "counterfactual" in diagnostics["algorithm"]["name"]


def test_revenue_diagnostics_can_filter_single_station():
    repo = load_repository()
    diagnostics = build_revenue_diagnostics(repo, "st-hq-hongqiao")

    assert diagnostics["scope"]["station_count"] == 1
    assert diagnostics["stations"][0]["station_id"] == "st-hq-hongqiao"


def test_revenue_diagnostics_ignore_non_commercial_device_telemetry():
    repo = load_repository()
    sample = repo.station_points("st-hq-hongqiao")[-1]
    device_only = sample.__class__(
        station_id=sample.station_id,
        timestamp=sample.timestamp,
        load_kw=sample.load_kw,
        pv_kw=sample.pv_kw,
        grid_kw=sample.grid_kw,
        storage_power_kw=sample.storage_power_kw,
        storage_soc=sample.storage_soc,
        connector_occupied=sample.connector_occupied,
        queue_length=sample.queue_length,
        sessions=0,
        energy_kwh=sample.energy_kwh,
        revenue=0.0,
        alert_count=0,
    )
    contaminated = repo.__class__(
        repo.tenants,
        repo.regions,
        repo.tariff_plans,
        repo.stations,
        (*repo.telemetry, device_only),
        repo.alerts,
        repo.vpp_events,
        repo.audit,
    )

    diagnostics = build_revenue_diagnostics(contaminated, "st-hq-hongqiao")

    assert diagnostics["stations"][0]["monthly_net_impact"] > 0
    assert diagnostics["stations"][0]["operational_kpis"]["non_commercial_telemetry_ignored"] == 1
