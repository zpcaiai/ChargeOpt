from unittest.mock import patch

import pytest

from chargeopt.optimizer import solve_dispatch_optimization


def test_milp_plan_respects_soc_transformer_and_ramp(repo):
    result = solve_dispatch_optimization(repo, "t-001", "st-hq-hongqiao", 12, "cost")
    plan = result["dispatch_plan"]
    station = next(item for item in repo.stations if item.id == "st-hq-hongqiao")
    constraints = result["constraints"]

    assert len(plan) == 12
    assert all(constraints["soc_min"] * 100 <= row["projected_soc"] <= constraints["soc_max"] * 100 for row in plan)
    assert all(
        row["projected_grid_kw"] <= station.transformer_capacity_kw * constraints["transformer_max_ratio"] + 0.1
        for row in plan
    )
    assert all(
        abs(current["power_kw"] - previous["power_kw"])
        <= station.storage_power_kw * constraints["max_hourly_ramp_ratio"] + 0.1
        for previous, current in zip(plan, plan[1:], strict=False)
    )
    assert plan[-1]["projected_soc"] >= constraints["vpp_reserve_soc"] * 100


def test_production_fails_closed_when_exact_solver_missing(repo):
    with (
        patch("chargeopt.optimizer._solve_station_milp", side_effect=ImportError("scipy")),
        patch.dict("os.environ", {"ENVIRONMENT": "production"}, clear=True),
        pytest.raises(RuntimeError, match="MILP solver is unavailable"),
    ):
        solve_dispatch_optimization(repo, "t-001", "st-hq-hongqiao", 4, "balanced")


def test_development_can_use_explicitly_labeled_fallback(repo):
    with (
        patch("chargeopt.optimizer._solve_station_milp", side_effect=ImportError("scipy")),
        patch.dict("os.environ", {"CHARGEOPT_REQUIRE_EXACT_SOLVER": "false"}, clear=True),
    ):
        result = solve_dispatch_optimization(repo, "t-001", "st-hq-hongqiao", 4, "balanced")
    assert result["solver"] == "discrete-mpc-dp-fallback-v2"
    assert result["constraints"]["solver_evidence"][0]["exact"] is False
