from chargeopt.analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail
from chargeopt.data import load_repository


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
