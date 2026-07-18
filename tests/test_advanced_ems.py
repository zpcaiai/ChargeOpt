from __future__ import annotations

import math

import pytest

from chargeopt.advanced_ems import (
    ALGORITHMS,
    FoundationForecastClient,
    calibrated_ensemble_forecast,
    coordinate_portfolio_admm,
    evaluate_offline_policy,
    marginal_degradation_cost_per_kwh,
    project_safe_action,
    project_three_phase_distflow,
    solve_distributionally_robust_mpc,
    train_conservative_fitted_q,
)
from chargeopt.domain import Station


def _station(**overrides) -> Station:
    values = {
        "id": "station-ems",
        "tenant_id": "tenant-ems",
        "region_id": "region-1",
        "name": "EMS test station",
        "station_type": "ultra_fast",
        "address": "Test address",
        "latitude": 31.2,
        "longitude": 121.5,
        "transformer_capacity_kw": 1000.0,
        "charger_count": 12,
        "connector_count": 24,
        "max_connector_power_kw": 250.0,
        "storage_capacity_kwh": 800.0,
        "storage_power_kw": 300.0,
        "pv_capacity_kw": 100.0,
        "tariff_plan_id": "tariff-1",
        "monthly_opex": 40000.0,
        "reliability_score": 0.98,
        "dispatch_mode": "auto",
    }
    return Station(**(values | overrides))


def _history(points: int = 96) -> list[float]:
    return [420 + 55 * math.sin(index * 2 * math.pi / 24) + index * 0.15 for index in range(points)]


def _safety_constraints() -> dict[str, float]:
    return {
        "soc": 0.55,
        "soc_min": 0.2,
        "soc_max": 0.9,
        "capacity_kwh": 800.0,
        "interval_hours": 0.25,
        "charge_limit_kw": 300.0,
        "discharge_limit_kw": 300.0,
        "transformer_headroom_kw": 120.0,
        "previous_power_kw": 20.0,
        "ramp_limit_kw": 80.0,
        "temperature_c": 28.0,
        "temperature_limit_c": 45.0,
    }


def test_calibrated_forecast_is_deterministic_ordered_and_provenanced():
    first = calibrated_ensemble_forecast(_history(), horizon=12, interval_minutes=60, scenario_count=8, seed=42)
    second = calibrated_ensemble_forecast(_history(), horizon=12, interval_minutes=60, scenario_count=8, seed=42)

    assert first["algorithm"] == ALGORITHMS["forecast"]
    assert first["evidence_class"] == "replay"
    assert first["scenarios_kw"] == second["scenarios_kw"]
    assert first["input_hash"] == second["input_hash"]
    assert sum(first["model_weights"].values()) == pytest.approx(1.0)
    assert len(first["scenarios_kw"]) == 8
    assert all(len(row) == 12 for row in first["scenarios_kw"])
    for row in first["rows"]:
        assert 0 <= row["p10_grid_kw"] <= row["p50_grid_kw"] <= row["p90_grid_kw"]


def test_forecast_validates_inputs_and_external_model():
    with pytest.raises(ValueError, match="At least 12"):
        calibrated_ensemble_forecast([1.0] * 11, horizon=4)
    with pytest.raises(ValueError, match="External forecast"):
        calibrated_ensemble_forecast(_history(), horizon=4, interval_minutes=60, external_predictions={"bad": [1.0]})
    enriched = calibrated_ensemble_forecast(
        _history(),
        horizon=4,
        interval_minutes=60,
        external_predictions={"tsfm": [410.0, 420.0, 430.0, 440.0]},
        evidence_class="shadow",
    )
    assert enriched["evidence_class"] == "shadow"
    assert enriched["external_models"] == ["tsfm"]


def test_foundation_forecast_adapter_is_fail_closed(monkeypatch):
    with pytest.raises(ValueError, match="HTTPS"):
        FoundationForecastClient("http://example.com/model", "token", "model")
    monkeypatch.setenv("CHARGEOPT_TSF_ENDPOINT", "https://models.example.com/forecast")
    monkeypatch.delenv("CHARGEOPT_TSF_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="Both"):
        FoundationForecastClient.from_environment()


def test_degradation_price_increases_with_stress():
    nominal = marginal_degradation_cost_per_kwh(
        storage_capacity_kwh=800, replacement_cost=1_000_000, soc=0.5, soh=0.95, temperature_c=25
    )
    stressed = marginal_degradation_cost_per_kwh(
        storage_capacity_kwh=800, replacement_cost=1_000_000, soc=0.9, soh=0.7, temperature_c=45
    )
    assert nominal > 0
    assert stressed > nominal


def test_distributionally_robust_mpc_returns_feasible_risk_evidence():
    forecast = calibrated_ensemble_forecast(_history(), horizon=8, interval_minutes=60, scenario_count=8, seed=7)
    result = solve_distributionally_robust_mpc(
        _station(),
        forecast,
        prices=[0.35, 0.35, 0.45, 0.8, 1.2, 1.2, 0.55, 0.4],
        initial_soc=0.62,
        demand_charge_per_kw=0.03,
        risk_weight=0.2,
    )

    assert result["algorithm"] == ALGORITHMS["dispatch"]
    assert result["exact"] is True
    assert len(result["dispatch_plan"]) == 8
    assert result["risk"]["cvar_cost"] >= result["risk"]["var_cost"]
    assert all(0.2 <= row["projected_soc"] <= 0.92 for row in result["dispatch_plan"])
    assert all(row["transformer_margin_kw"] >= -1e-4 for row in result["dispatch_plan"])


def test_distributionally_robust_mpc_fails_closed_when_grid_limit_is_impossible():
    forecast = calibrated_ensemble_forecast([1500.0] * 48, horizon=4, interval_minutes=60, scenario_count=4)
    with pytest.raises(RuntimeError, match="infeasible or unsolved"):
        solve_distributionally_robust_mpc(
            _station(storage_power_kw=0.0), forecast, initial_soc=0.5, solver_time_limit_seconds=5
        )
    with pytest.raises(ValueError, match="coverage=0.8"):
        calibrated_ensemble_forecast(_history(), horizon=4, interval_minutes=60, coverage=0.5)


def test_three_phase_distflow_projects_line_and_voltage_limits():
    network = {
        "root_bus": "grid",
        "transformer_limit_kw": 180,
        "minimum_voltage_pu": 0.94,
        "voltage_kv": 0.4,
        "lines": [
            {
                "id": "line-1",
                "from_bus": "grid",
                "to_bus": "bus-a",
                "phase": "A",
                "limit_kw": 70,
                "resistance_ohm": 0.01,
                "reactance_ohm": 0.006,
            }
        ],
    }
    result = project_three_phase_distflow(
        network,
        [
            {
                "station_id": "station-a",
                "bus": "bus-a",
                "phase": "A",
                "proposed_kw": 100,
                "priority": 2,
                "power_factor": 0.98,
            }
        ],
    )

    assert result["qualified"] is True
    assert result["ac_certified"] is False
    assert result["allocations"][0]["accepted_kw"] <= 60.000001
    assert result["phases"]["A"]["minimum_voltage_pu"] >= 0.94


def test_three_phase_distflow_rejects_non_radial_or_disconnected_network():
    with pytest.raises(ValueError, match="not a radial tree"):
        project_three_phase_distflow(
            {
                "root_bus": "grid",
                "lines": [{"from_bus": "grid", "to_bus": "known", "phase": "A", "limit_kw": 50}],
            },
            [{"station_id": "s", "bus": "missing", "phase": "A", "proposed_kw": 20}],
        )
    with pytest.raises(ValueError, match="duplicate parents"):
        project_three_phase_distflow(
            {
                "root_bus": "grid",
                "lines": [
                    {"from_bus": "grid", "to_bus": "bus", "phase": "A", "limit_kw": 50},
                    {"from_bus": "other", "to_bus": "bus", "phase": "A", "limit_kw": 50},
                ],
            },
            [{"station_id": "s", "bus": "bus", "phase": "A", "proposed_kw": 20}],
        )


def test_admm_coordination_converges_with_bounds_and_exact_target():
    result = coordinate_portfolio_admm(
        [
            {"station_id": "a", "minimum_kw": 0, "maximum_kw": 100, "quadratic_cost": 1.0},
            {"station_id": "b", "minimum_kw": 20, "maximum_kw": 120, "quadratic_cost": 0.5},
            {"station_id": "c", "minimum_kw": 0, "maximum_kw": 80, "quadratic_cost": 2.0},
        ],
        180,
        tolerance=1e-5,
    )
    assert result["converged"] is True
    assert result["allocated_kw"] == pytest.approx(180, abs=1e-5)
    assert all(0 <= item["target_kw"] <= 120 for item in result["allocations"])
    with pytest.raises(ValueError, match="outside aggregate"):
        coordinate_portfolio_admm([{"station_id": "a", "maximum_kw": 10}, {"station_id": "b", "maximum_kw": 10}], 30)


def test_physical_action_projection_is_fail_closed_and_bounded():
    missing = project_safe_action(50, {"soc": 0.5})
    assert missing["allowed"] is False
    assert "missing_temperature_c" in missing["reasons"]

    projected = project_safe_action(300, _safety_constraints())
    assert projected["allowed"] is True
    assert projected["projected_kw"] == pytest.approx(100.0)
    assert projected["reasons"] == ["action_projected_to_safe_set"]

    hot = _safety_constraints() | {"temperature_c": 50.0}
    assert project_safe_action(20, hot)["allowed"] is False
    invalid = _safety_constraints() | {"capacity_kwh": float("nan")}
    assert project_safe_action(20, invalid)["reasons"] == ["invalid_constraints"]


def test_offline_policy_stays_shadow_only_and_rejects_ood_state():
    actions = [-50.0, 0.0, 50.0]
    transitions = []
    for index in range(45):
        action_index = index % len(actions)
        state = [0.45 + 0.01 * (index % 7), 0.4 + 0.02 * (index % 5), 0.8 + 0.01 * (index % 3)]
        transitions.append(
            {
                "state": state,
                "next_state": [state[0] + actions[action_index] / 8000, state[1], state[2]],
                "action_index": action_index,
                "reward": -abs(state[1] - 0.45) - abs(actions[action_index]) * 0.001,
                "safe": True,
            }
        )
    transitions.append(
        {
            "state": [0.9, 1.0, 1.0],
            "next_state": [0.9, 1.0, 1.0],
            "action_index": 2,
            "reward": 1000,
            "safe": False,
        }
    )
    model = train_conservative_fitted_q(transitions, actions)
    assert model["approved_for_control"] is False
    assert model["unsafe_samples_excluded"] == 1

    allowed = evaluate_offline_policy(model, model["feature_mean"], _safety_constraints())
    assert allowed["usage"] == "shadow_advisory_only"
    assert allowed["projection"]["allowed"] is True

    rejected = evaluate_offline_policy(model, [9.0, 9.0, 9.0], _safety_constraints())
    assert rejected["algorithm"] == ALGORITHMS["offline_policy"]
    assert rejected["allowed"] is False
    assert rejected["reason"] == "out_of_distribution"
    assert len(rejected["input_hash"]) == 64
