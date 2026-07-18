from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from chargeopt.digital_twin import (
    assess_field_qualification,
    build_default_topology,
    build_twin_snapshot,
    calibrate_twin_model,
    compare_trajectories,
    diagnose_twin,
    estimate_causal_uplift,
    estimate_station_state,
    normalize_measurement,
    run_fault_injection_suite,
    simulate_station,
    twin_aware_station,
    validate_topology,
)


def _station(repo):
    return repo.stations[0]


def _measurement(station, code, value, unit, timestamp, *, received_at=None):
    return normalize_measurement(
        {
            "station_id": station.id,
            "asset_key": "station",
            "point_code": code,
            "value": value,
            "unit": unit,
            "source_timestamp": timestamp,
            "source": "test-meter",
            "idempotency_key": f"{code}:{timestamp.isoformat()}",
        },
        station=station,
        received_at=received_at or timestamp + timedelta(seconds=2),
    )


def test_default_topology_is_complete_and_deterministic(repo):
    station = _station(repo)
    first = build_default_topology(station)
    second = build_default_topology(station)

    assert first["topology_hash"] == second["topology_hash"]
    assert first["validation"]["valid"] is True
    assert first["validation"]["asset_count"] == 5 + 2 + 1 + station.charger_count + station.connector_count
    assert {asset["asset_type"] for asset in first["assets"]} >= {
        "station",
        "transformer",
        "charger",
        "connector",
        "pcs",
        "battery_system",
    }


def test_topology_validator_rejects_cycles_and_unknown_assets():
    topology = {
        "assets": [
            {"asset_key": "station", "asset_type": "station", "name": "Station"},
            {"asset_key": "bus", "asset_type": "bus", "name": "Bus"},
        ],
        "relationships": [
            {"source_asset_key": "station", "target_asset_key": "bus", "relationship_type": "contains"},
            {"source_asset_key": "bus", "target_asset_key": "station", "relationship_type": "contains"},
            {"source_asset_key": "missing", "target_asset_key": "bus", "relationship_type": "feeds"},
        ],
    }

    result = validate_topology(topology)

    assert result["valid"] is False
    assert "containment_cycle" in result["errors"]
    assert any(error.startswith("relationship_unknown_asset") for error in result["errors"])


def test_measurement_normalizes_units_and_marks_clock_skew(repo):
    station = _station(repo)
    source_time = datetime(2026, 7, 18, 4, tzinfo=UTC)

    normalized = _measurement(
        station,
        "storage_soc",
        65,
        "percent",
        source_time,
        received_at=source_time + timedelta(minutes=20),
    )

    assert normalized["value"] == 0.65
    assert normalized["unit"] == "ratio"
    assert normalized["quality_code"] == "suspect"
    assert normalized["quality_flags"] == ["clock_skew", "stale"]
    assert len(normalized["evidence_hash"]) == 64


def test_measurement_rejects_invalid_units(repo):
    with pytest.raises(ValueError, match="Unsupported unit conversion"):
        _measurement(_station(repo), "load_kw", 100, "amps", datetime.now(UTC))


def test_state_estimator_reports_trust_and_autonomy_gate(repo):
    station = _station(repo)
    timestamp = datetime(2026, 7, 18, 4, tzinfo=UTC)
    measurements = [
        _measurement(station, "load_kw", 1000, "kW", timestamp),
        _measurement(station, "grid_kw", 850, "kW", timestamp),
        _measurement(station, "pv_kw", 100, "kW", timestamp),
        _measurement(station, "storage_power_kw", -50, "kW", timestamp),
        _measurement(station, "storage_soc", 0.64, "ratio", timestamp),
    ]

    snapshot = estimate_station_state(
        station,
        measurements,
        evidence_class="field_qualified",
        now=timestamp + timedelta(seconds=2),
    )

    assert snapshot["health"] == "healthy"
    assert snapshot["trust_score"] >= 0.99
    assert snapshot["autonomy_gate"] == {"allowed": True, "reasons": []}
    assert snapshot["storage_soc"] == pytest.approx(0.64, abs=0.01)


def test_state_estimator_fails_closed_on_missing_stale_data(repo):
    station = _station(repo)
    timestamp = datetime(2026, 7, 18, 1, tzinfo=UTC)
    measurement = _measurement(
        station,
        "grid_kw",
        100,
        "kW",
        timestamp,
        received_at=timestamp + timedelta(hours=2),
    )

    snapshot = estimate_station_state(
        station,
        [measurement],
        evidence_class="observed",
        now=timestamp + timedelta(hours=2),
    )

    assert snapshot["health"] in {"offline", "untrusted"}
    assert snapshot["autonomy_gate"]["allowed"] is False
    assert "critical_measurements_missing" in snapshot["autonomy_gate"]["reasons"]
    assert "telemetry_stale" in snapshot["autonomy_gate"]["reasons"]


def test_simulation_is_deterministic_and_reports_physics(repo):
    station = _station(repo)
    schedule = [
        {
            "timestamp": f"2026-07-18T{hour:02d}:00:00+00:00",
            "load_kw": 900 + hour * 10,
            "pv_kw": 100,
            "storage_power_kw": -100 if hour > 2 else 80,
            "ambient_temperature_c": 30,
            "arrivals": 2,
            "service_rate": 8,
        }
        for hour in range(8)
    ]

    first = simulate_station(station, {"storage_soc": 0.6}, schedule, random_seed=7)
    second = simulate_station(station, {"storage_soc": 0.6}, schedule, random_seed=7)

    assert first == second
    assert len(first["trajectory"]) == 8
    assert first["metrics"]["battery_throughput_kwh"] > 0
    assert 0.1 <= first["metrics"]["final_soc"] <= 0.95


def test_diagnostics_explain_low_trust_and_constraint_violation(repo):
    station = _station(repo)
    snapshot = {
        "estimated_at": "2026-07-18T04:00:00+00:00",
        "trust_score": 0.3,
        "balance_residual_kw": 400,
        "transformer_headroom_kw": -20,
        "autonomy_gate": {"allowed": False, "reasons": ["twin_trust_below_threshold"]},
        "contract": {"evidence_class": "observed", "topology_version": "v1"},
    }
    simulation = {
        "metrics": {"constraint_violation_count": 2, "unserved_sessions": 3},
    }

    result = diagnose_twin(station, snapshot, simulation)

    types = {item["diagnostic_type"] for item in result["diagnostics"]}
    assert types == {
        "twin_trust_low",
        "power_balance_residual",
        "transformer_headroom_low",
        "predicted_constraint_violation",
        "predicted_service_loss",
    }
    assert all(len(item["fingerprint"]) == 64 for item in result["diagnostics"])


def test_causal_estimator_recovers_known_effect():
    observations = []
    for index in range(160):
        demand = float(index % 16)
        temperature = float(15 + (index * 7) % 20)
        treated = (index * 13) % 17 < 8
        noise = float((index * 11) % 7 - 3)
        outcome = 200 + demand * 3 - temperature * 0.8 + (30 if treated else 0) + noise
        observations.append(
            {
                "treated": treated,
                "outcome": outcome,
                "covariates": {"demand": demand, "temperature": temperature},
            }
        )

    result = estimate_causal_uplift(observations, evidence_class="observed")

    assert result["status"] == "completed"
    assert result["average_treatment_effect"] == pytest.approx(30, abs=3)
    assert result["overlap_score"] >= 0.8
    assert result["confidence_interval_90"]["low"] > 0


def test_calibration_recovers_scale_and_offset_and_gates_quality():
    predicted = [float(index * 10) for index in range(1, 49)]
    observed = [value * 1.08 + 7 for value in predicted]

    result = calibrate_twin_model(predicted, observed, evidence_class="observed")

    assert result["status"] == "passed"
    assert result["parameters"]["scale"] == pytest.approx(1.08)
    assert result["parameters"]["offset"] == pytest.approx(7)
    assert result["metrics"]["calibrated"]["rmse"] == pytest.approx(0)


def test_trajectory_comparison_reports_missing_and_available_channels():
    predicted = [{"grid_kw": 100 + index, "storage_soc": 0.5} for index in range(8)]
    observed = [{"grid_kw": 102 + index, "storage_soc": 0.49} for index in range(8)]

    result = compare_trajectories(predicted, observed)

    assert result["metrics"]["grid_kw"]["mae"] == 2
    assert result["metrics"]["storage_soc"]["mae"] == pytest.approx(0.01)
    assert result["missing_fields"] == ["transformer_temperature_c"]


def test_commissioning_fault_suite_proves_fail_closed_behavior(repo):
    result = run_fault_injection_suite(_station(repo))

    assert result["qualified"] is True
    assert all(result["checks"].values())


def test_causal_estimator_refuses_small_sample():
    result = estimate_causal_uplift(
        [{"treated": index % 2 == 0, "outcome": float(index), "covariates": {"x": float(index)}} for index in range(12)]
    )

    assert result["status"] == "insufficient_evidence"
    assert result["auditable"] is False


def test_field_qualification_requires_all_categories_and_30_consecutive_days():
    as_of = date(2026, 7, 18)
    evidence = [
        {"category": category, "qualified": True, "evidence_date": "2026-07-17"}
        for category in (
            "topology",
            "device_attestation",
            "calibration",
            "slo",
            "fault_injection",
            "recovery_drill",
            "approval",
        )
    ]
    evidence.extend(
        {
            "category": "shadow_day",
            "qualified": True,
            "evidence_date": (as_of - timedelta(days=offset)).isoformat(),
        }
        for offset in range(1, 31)
    )

    qualified = assess_field_qualification(evidence, as_of=as_of)
    incomplete = assess_field_qualification(evidence[:-1], as_of=as_of)

    assert qualified["ready"] is True
    assert qualified["qualified_shadow_days"] == 30
    assert incomplete["ready"] is False


def test_twin_aware_station_derates_capacity_and_blocks_low_trust(repo):
    station = _station(repo)
    derated = twin_aware_station(station, {"trust_score": 0.9, "estimated_soh": 0.8, "states": []})

    assert derated.storage_capacity_kwh == pytest.approx(station.storage_capacity_kwh * 0.8)
    assert derated.storage_power_kw == pytest.approx(station.storage_power_kw * 0.8)

    with pytest.raises(PermissionError, match="trust"):
        twin_aware_station(station, {"trust_score": 0.6, "estimated_soh": 1.0})


def test_in_memory_twin_snapshot_is_explicitly_synthetic(repo):
    snapshot = build_twin_snapshot(repo, _station(repo).id)

    assert snapshot["state"]["contract"]["evidence_class"] == "synthetic"
    assert snapshot["state"]["autonomy_gate"]["allowed"] is False
    assert snapshot["topology"]["validation"]["valid"] is True
    assert snapshot["diagnostics"]["maintenance_recommendations"]
