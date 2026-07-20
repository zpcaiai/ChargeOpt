from __future__ import annotations

import math

import pytest

from chargeopt.advanced_ems import calibrated_ensemble_forecast
from chargeopt.domain import Station
from chargeopt.grid_ems import (
    GRID_EMS_ALGORITHMS,
    aggregate_ev_flexibility,
    assess_n_minus_one_security,
    estimate_battery_degradation,
    solve_secure_rolling_dispatch,
)


def _station(**overrides) -> Station:
    values = {
        "id": "station-grid-ems",
        "tenant_id": "tenant-grid-ems",
        "region_id": "region-1",
        "name": "Grid EMS test station",
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
        "dispatch_mode": "recommend",
    }
    return Station(**(values | overrides))


def _forecast(horizon: int = 6) -> dict:
    history = [420 + 35 * math.sin(index * 2 * math.pi / 24) for index in range(96)]
    return calibrated_ensemble_forecast(history, horizon=horizon, interval_minutes=60, scenario_count=6, seed=9)


def _session(**overrides) -> dict:
    return {
        "session_id": "session-1",
        "arrival_step": 0,
        "departure_step": 4,
        "required_energy_kwh": 120,
        "delivered_energy_kwh": 0,
        "max_charge_kw": 80,
        "efficiency": 0.95,
    } | overrides


def _network() -> dict:
    return {
        "root_bus": "grid",
        "transformer_limit_kw": 300,
        "minimum_voltage_pu": 0.94,
        "voltage_kv": 0.4,
        "lines": [
            {
                "id": "line-a",
                "from_bus": "grid",
                "to_bus": "bus-a",
                "phase": "A",
                "limit_kw": 100,
                "resistance_ohm": 0.005,
                "reactance_ohm": 0.003,
            }
        ],
    }


def _proposals(power_kw: float = 40) -> list[dict]:
    return [
        {
            "station_id": "station-grid-ems",
            "bus": "bus-a",
            "phase": "A",
            "proposed_kw": power_kw,
            "power_factor": 0.98,
        }
    ]


def test_flexibility_envelope_tracks_deadlines_and_detects_infeasible_service():
    result = aggregate_ev_flexibility([_session()], horizon=6, interval_minutes=60)

    assert result["algorithm"] == GRID_EMS_ALGORITHMS["flexibility"]
    assert result["feasible"] is True
    assert result["rows"][0]["minimum_cumulative_energy_kwh"] == 0
    assert result["rows"][3]["minimum_cumulative_energy_kwh"] == pytest.approx(120)
    assert sum(result["sessions"][0]["latest_feasible_schedule_kw"]) * 0.95 == pytest.approx(120)

    rejected = aggregate_ev_flexibility(
        [_session(required_energy_kwh=400, departure_step=2)], horizon=6, interval_minutes=60
    )
    assert rejected["feasible"] is False
    assert rejected["violations"][0]["code"] == "deadline_energy_infeasible"
    assert rejected["execution_authorized"] is False


def test_rainflow_degradation_is_replayable_and_temperature_sensitive():
    kwargs = {
        "interval_minutes": 15,
        "storage_capacity_kwh": 800,
        "replacement_cost": 1_000_000,
        "soh": 0.92,
    }
    cool = estimate_battery_degradation([0.5, 0.8, 0.3, 0.75, 0.5], temperature_c=25, **kwargs)
    hot = estimate_battery_degradation([0.5, 0.8, 0.3, 0.75, 0.5], temperature_c=48, **kwargs)

    assert cool["algorithm"] == GRID_EMS_ALGORITHMS["degradation"]
    assert cool["equivalent_full_cycles"] > 0
    assert hot["total_life_loss"] > cool["total_life_loss"]
    assert hot["estimated_degradation_cost"] > cool["estimated_degradation_cost"]
    assert cool["evidence_scope"] == "engineering_lifetime_model_not_warranty_certification"


def test_n_minus_one_screening_fails_on_derate_and_radial_outage():
    intervals = [{"at": "2026-07-20T00:00:00Z", "proposals": _proposals(80)}]
    result = assess_n_minus_one_security(
        _network(),
        intervals,
        [
            {"id": "transformer-n1", "type": "transformer_derate", "available_capacity_ratio": 0.5},
            {"id": "line-n1", "type": "line_outage", "line_id": "line-a"},
        ],
    )

    assert result["algorithm"] == GRID_EMS_ALGORITHMS["network_security"]
    assert result["n_minus_one_secure"] is False
    assert result["ac_certified"] is False
    assert result["assessments"][0]["curtailed_kw"] > 0
    assert result["assessments"][1]["unserved_kw"] == 80


def test_secure_dispatch_cooptimizes_service_reserve_carbon_and_n_minus_one_capacity():
    result = solve_secure_rolling_dispatch(
        _station(),
        _forecast(),
        [_session()],
        prices=[0.3, 0.3, 0.7, 1.2, 0.8, 0.4],
        initial_soc=0.6,
        carbon_intensity_kg_per_kwh=[0.6, 0.6, 0.5, 0.4, 0.3, 0.3],
        carbon_price_per_kg=0.15,
        reserve_up_prices=[0.02] * 6,
        reserve_down_prices=[0.01] * 6,
        contingencies=[{"id": "transformer-n1", "available_capacity_ratio": 0.8}],
        risk_alpha=0.9,
        solver_time_limit_seconds=10,
    )

    assert result["algorithm"] == GRID_EMS_ALGORITHMS["secure_dispatch"]
    assert result["exact"] is True
    assert result["service_feasible"] is True
    assert result["restoration_used"] is False
    assert result["execution_authorized"] is False
    assert result["risk"]["cvar_cost"] >= result["risk"]["var_cost"]
    assert result["session_service"][0]["shortfall_kwh"] == pytest.approx(0, abs=1e-5)
    assert all(row["transformer_margin_kw"] >= -1e-5 for row in result["dispatch_plan"])
    assert all(row["secure_transformer_limit_kw"] == pytest.approx(736) for row in result["dispatch_plan"])


def test_secure_dispatch_restores_service_only_and_never_authorizes_execution():
    result = solve_secure_rolling_dispatch(
        _station(),
        _forecast(),
        [_session(required_energy_kwh=400, departure_step=2)],
        prices=[0.4] * 6,
        initial_soc=0.55,
        allow_service_restoration=True,
        solver_time_limit_seconds=10,
    )

    assert result["restoration_used"] is True
    assert result["service_feasible"] is False
    assert result["session_service"][0]["shortfall_kwh"] > 0
    assert result["safety_constraints_relaxed"] is False
    assert result["execution_authorized"] is False

    with pytest.raises(RuntimeError, match="safety constraints were not relaxed"):
        solve_secure_rolling_dispatch(
            _station(),
            _forecast(),
            [_session(required_energy_kwh=400, departure_step=2)],
            prices=[0.4] * 6,
            initial_soc=0.55,
            allow_service_restoration=False,
            solver_time_limit_seconds=10,
        )

    with pytest.raises(ValueError, match="available_capacity_ratio"):
        solve_secure_rolling_dispatch(
            _station(),
            _forecast(),
            [_session()],
            prices=[0.4] * 6,
            initial_soc=0.55,
            contingencies=[{"id": "line-outage", "type": "line_outage"}],
            solver_time_limit_seconds=10,
        )
