from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from chargeopt.energy_platform import (
    allocate_charging_power,
    allocate_energy_cost,
    calculate_carbon,
    calculate_mv_result,
    derive_storage_safety_envelope,
    evaluate_series_quality,
    fit_energy_baseline,
    optimize_campus_energy,
    reconcile_energy_balance,
    reconstruct_utility_bill,
    validate_driver_profile,
    validate_energy_topology,
)
from chargeopt.industrial_edge import (
    OfflineEvidenceBuffer,
    encode_protocol_command,
    evaluate_local_command,
    verify_observed_effect,
)
from chargeopt.protocols import normalize_protocol_message, protocol_capability_matrix


def _topology() -> dict:
    return {
        "name": "Mixed park",
        "assets": [
            {"asset_key": "park", "asset_type": "park", "name": "Park", "energy_carriers": ["electricity", "cooling"]},
            {
                "asset_key": "substation",
                "asset_type": "substation",
                "name": "Substation",
                "energy_carriers": ["electricity"],
            },
            {
                "asset_key": "chiller",
                "asset_type": "chiller",
                "name": "Chiller",
                "energy_carriers": ["electricity", "cooling"],
            },
        ],
        "relationships": [
            {"source_asset_key": "park", "target_asset_key": "substation", "relationship_type": "contains"},
            {"source_asset_key": "park", "target_asset_key": "chiller", "relationship_type": "contains"},
            {
                "source_asset_key": "substation",
                "target_asset_key": "chiller",
                "relationship_type": "feeds",
                "energy_carrier": "electricity",
            },
            {"source_asset_key": "substation", "target_asset_key": "chiller", "relationship_type": "controls"},
        ],
        "points": [
            {
                "asset_key": "chiller",
                "point_code": "cooling_output_kw",
                "category": "measurement",
                "quantity_kind": "power",
                "canonical_unit": "kW",
            },
            {
                "asset_key": "chiller",
                "point_code": "leaving_water_setpoint_c",
                "category": "command",
                "quantity_kind": "temperature",
                "canonical_unit": "degC",
                "writable": True,
                "command_capability": "set_leaving_water_temperature",
            },
        ],
        "constraints": [
            {
                "asset_key": "chiller",
                "constraint_type": "thermal",
                "priority": "hard",
                "parameters": {"minimum": 5, "maximum": 12},
                "source": "nameplate",
            }
        ],
    }


def test_mixed_energy_topology_validates_and_rejects_cycles():
    result = validate_energy_topology(_topology())
    assert result["valid"] is True
    assert result["asset_count"] == 3
    broken = _topology()
    broken["relationships"].append(
        {"source_asset_key": "chiller", "target_asset_key": "park", "relationship_type": "contains"}
    )
    assert "containment_cycle" in validate_energy_topology(broken)["errors"]


def test_driver_profile_requires_identity_rotation_and_write_allowlist():
    profile = {
        "protocol": "bacnet_ip",
        "security_profile": {"mutual_identity": True, "certificate_rotation_days": 60},
        "mappings": [
            {
                "external_address": "analogOutput:1",
                "writable": True,
                "command_parameters": {"allowlist": ["presentValue"]},
            }
        ],
    }
    assert validate_driver_profile(profile)["valid"] is True
    profile["security_profile"] = {}
    assert "mutual_identity_required" in validate_driver_profile(profile)["errors"]


def test_quality_engine_detects_missing_frozen_reverse_reset_and_multiplier():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    samples = [
        {"source_timestamp": start.isoformat(), "received_at": start.isoformat(), "value": 100},
        {"source_timestamp": (start + timedelta(minutes=1)).isoformat(), "value": 100},
        {"source_timestamp": (start + timedelta(minutes=2)).isoformat(), "value": 100},
        {"source_timestamp": (start + timedelta(minutes=3)).isoformat(), "value": 100},
        {"source_timestamp": (start + timedelta(minutes=8)).isoformat(), "value": -2},
    ]
    result = evaluate_series_quality(
        samples,
        {
            "expected_interval_seconds": 60,
            "freeze_count": 4,
            "allow_reverse": False,
            "cumulative": True,
            "multiplier": 0,
        },
    )
    assert {"missing", "frozen", "reverse_flow", "meter_reset", "multiplier_error"} <= set(result["flags"])
    assert result["trusted_for_settlement"] is False


def test_reconciliation_blocks_bad_revenue_meter():
    result = reconcile_energy_balance(
        {
            "carrier": "electricity",
            "inputs": [{"value": 100, "uncertainty": 1}],
            "outputs": [{"value": 96, "uncertainty": 1}],
            "technical_loss": 2,
            "required_meters": [{"quality_code": "bad"}],
        }
    )
    assert result["residual"] == 2
    assert result["status"] == "blocked"
    assert result["settlement_authorized"] is False


def test_charging_power_share_holds_site_limit_and_blocks_unconsented_v2g():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    result = allocate_charging_power(
        {
            "now": now.isoformat(),
            "site_limit_kw": 100,
            "phase_limits_kw": {"A": 60, "B": 60},
            "sessions": [
                {
                    "session_id": "s1",
                    "departure_deadline": (now + timedelta(hours=1)).isoformat(),
                    "target_energy_kwh": 80,
                    "delivered_energy_kwh": 0,
                    "maximum_power_kw": 80,
                    "minimum_service_kw": 30,
                    "phase": "A",
                },
                {
                    "session_id": "s2",
                    "departure_deadline": (now + timedelta(hours=2)).isoformat(),
                    "target_energy_kwh": 60,
                    "delivered_energy_kwh": 0,
                    "maximum_power_kw": 60,
                    "minimum_service_kw": 20,
                    "phase": "B",
                    "direction": "discharge",
                    "v2g_opt_in": False,
                },
            ],
        }
    )
    assert result["allocated_kw"] <= 100
    assert next(item for item in result["allocations"] if item["session_id"] == "s2")["allocated_kw"] == 0
    assert result["execution_authorized"] is False


def test_storage_envelope_blocks_fire_and_thermal_risk():
    result = derive_storage_safety_envelope(
        {
            "soc": 0.7,
            "soh": 0.94,
            "rated_energy_kwh": 1000,
            "rated_power_kw": 500,
            "maximum_temperature_c": 62,
            "cooling_available": False,
            "fire_system_normal": False,
            "cell_voltage_delta_v": 0.2,
        }
    )
    assert result["control_allowed"] is False
    assert result["sop_charge_kw"] == 0
    assert {"temperature_trip", "cooling_unavailable", "fire_system_abnormal", "cell_imbalance"} <= set(
        result["block_reasons"]
    )


def test_campus_milp_couples_chiller_electricity_and_meets_services():
    result = optimize_campus_energy(
        {
            "interval_minutes": 60,
            "timescale": "intraday",
            "periods": [{"demand": {"electricity": 100, "cooling": 300}, "prices": {"electricity": 0.8}}],
            "equipment": [
                {"asset_id": "grid", "output_carrier": "electricity", "maximum_output": 1000},
                {
                    "asset_id": "chiller",
                    "input_carrier": "electricity",
                    "output_carrier": "cooling",
                    "maximum_output": 500,
                    "minimum_output": 50,
                    "efficiency": 5,
                },
            ],
        }
    )
    assert result["status"] == "completed"
    assert result["hard_constraints_satisfied"] is True
    electric = next(item for item in result["balances"] if item["carrier"] == "electricity")
    assert electric["conversion_consumption"] == 60
    assert electric["supply"] == 160


def test_campus_milp_fails_safe_when_supply_is_missing():
    result = optimize_campus_energy(
        {
            "periods": [{"demand": {"steam": 10}}],
            "equipment": [{"asset_id": "grid", "output_carrier": "electricity", "maximum_output": 100}],
        }
    )
    assert result["status"] == "safe_fallback"
    assert result["execution_authorized"] is False


def test_baseline_bill_allocation_mv_and_carbon_close_commercial_evidence():
    observations = [
        {
            "energy": 100 + 2 * temperature + 3 * production,
            "covariates": {"temperature": temperature, "production": production},
        }
        for temperature, production in zip(range(10, 22), range(20, 32), strict=True)
    ]
    baseline = fit_energy_baseline({"observations": observations, "covariates": ["temperature", "production"]})
    assert baseline["status"] == "validated"
    bill = reconstruct_utility_bill(
        {
            "intervals": [{"energy_kwh": 100, "price_per_kwh": 0.8, "demand_kw": 50, "quality_code": "good"}],
            "demand_charge_per_kw": 20,
            "contract_capacity_kw": 40,
            "capacity_exceedance_per_kw": 5,
            "invoiced_amount": 1200,
        }
    )
    assert bill["financially_usable"] is True
    allocation = allocate_energy_cost(
        {
            "total_amount": bill["reconstructed_amount"],
            "recipients": [{"recipient_id": "a", "meter_value": 60}, {"recipient_id": "b", "meter_value": 40}],
        }
    )
    assert allocation["reconciled"] is True
    mv = calculate_mv_result(
        {
            "adjusted_baseline_energy": 1000,
            "actual_energy": 850,
            "uncertainty_energy": 20,
            "blended_price_per_unit": 0.8,
            "carbon_factor_kg_per_unit": 0.57,
            "meter_quality": "revenue_grade",
            "service_impact": {"comfort_met": True, "production_met": True},
        }
    )
    assert mv["claim_authorized"] is True
    carbon = calculate_carbon(
        {
            "activities": [{"carrier": "electricity", "quantity": 100}],
            "factors": {"electricity": {"location_based": 0.57, "market_based": 0.2}},
        }
    )
    assert carbon["location_based_kg"] == 57
    assert carbon["market_based_kg"] == 20


@pytest.mark.parametrize(
    "protocol", ["ocpp201", "ocpp21", "bacnet_ip", "opc_ua", "iec61850", "iec104", "dlt645", "cjt188"]
)
def test_protocol_matrix_declares_field_conformance(protocol):
    assert protocol_capability_matrix()[protocol]["field_conformance_required"] is True


def test_protocol_normalizers_cover_building_industrial_and_utility_frames():
    bacnet = normalize_protocol_message(
        "bacnet_ip",
        "COV",
        {"objects": [{"object_identifier": "analogInput:1", "present_value": 12.5, "units": "degC"}]},
    )
    assert bacnet["points"][0]["quality"] == "good"
    opc = normalize_protocol_message(
        "opc_ua",
        "DataChange",
        {
            "values": [
                {"node_id": "ns=2;s=Power", "value": 42, "status_code": "Good", "ecm_semantic": "ElectricalPower"}
            ]
        },
    )
    assert opc["points"][0]["semantic"] == "ElectricalPower"
    meter = normalize_protocol_message(
        "dlt645",
        "read",
        {"checksum_valid": True, "meter_address": "001", "value": 10, "multiplier": 100, "unit": "kWh"},
    )
    assert meter["value"] == 1000
    assert normalize_protocol_message("cjt188", "read", {"checksum_valid": False})["status"] == "rejected"


def test_edge_offline_buffer_is_idempotent_and_replays_in_order(tmp_path):
    buffer = OfflineEvidenceBuffer(tmp_path / "edge.db", maximum_rows=100)
    first = buffer.append("telemetry", {"value": 1}, datetime(2026, 1, 1, tzinfo=UTC))
    duplicate = buffer.append("telemetry", {"value": 1}, datetime(2026, 1, 1, tzinfo=UTC))
    second = buffer.append("telemetry", {"value": 2}, datetime(2026, 1, 1, 0, 1, tzinfo=UTC))
    assert first["sequence"] == duplicate["sequence"]
    assert [item["sequence"] for item in buffer.pending()] == [first["sequence"], second["sequence"]]
    buffer.mark_replayed(first["sequence"], first["evidence_hash"])
    assert [item["sequence"] for item in buffer.pending()] == [second["sequence"]]


def test_edge_command_guard_and_observed_effect_fail_closed():
    now = datetime(2026, 1, 1, tzinfo=UTC)
    command = {
        "task_id": "task-1",
        "command": "set_power",
        "value": 50,
        "mapping_version": "v1",
        "issued_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=1)).isoformat(),
        "evaluated_at": (now + timedelta(seconds=1)).isoformat(),
        "effect_tolerance": 0.05,
    }
    state = {
        "mode": "automatic",
        "role": "leader",
        "active_interlocks": [],
        "telemetry_age_seconds": 1,
        "certificate_not_after": (now + timedelta(days=30)).isoformat(),
    }
    capability = {
        "mapping_version": "v1",
        "command_allowlist": ["set_power"],
        "maximum_telemetry_age_seconds": 10,
        "safety_envelope": {"minimum": 0, "maximum": 100},
    }
    assert evaluate_local_command(command, state, capability)["accepted"] is True
    blocked = evaluate_local_command(command | {"value": 150}, state | {"active_interlocks": ["fire"]}, capability)
    assert blocked["accepted"] is False
    assert {"local_interlock_active", "command_above_safety_envelope"} <= set(blocked["reasons"])
    encoded = encode_protocol_command("iec61850", command, {"external_address": "LD0/CSWI1.Pos"})
    assert encoded["operation"] == "select_before_operate"
    verified = verify_observed_effect(
        command,
        [{"source_timestamp": (now + timedelta(seconds=5)).isoformat(), "value": 49, "quality_code": "good"}],
    )
    assert verified["verified"] is True
    assert verify_observed_effect(command, [])["rollback_required"] is True


@pytest.mark.asyncio
async def test_energy_platform_api_runs_without_database_and_blocks_persistent_topology(client):
    capabilities = await client.get("/api/v1/energy-platform/capabilities")
    assert capabilities.status_code == 200
    assert "cooling" in capabilities.json()["energy_carriers"]
    quality = await client.post(
        "/api/v1/energy-platform/quality/evaluate",
        json={
            "idempotency_key": "quality-api-001",
            "payload": {"samples": [{"source_timestamp": "2026-01-01T00:00:00+00:00", "value": 1}]},
        },
    )
    assert quality.status_code == 200, quality.text
    assert quality.json()["evidence"]["persisted"] is False
    topology = await client.post("/api/v1/energy-platform/topologies", json=_topology())
    assert topology.status_code == 503


@pytest.mark.asyncio
async def test_energy_platform_api_runs_all_three_planning_timescales(client):
    base = {
        "idempotency_key": "plan-api-test",
        "payload": {
            "periods": [{"demand": {"electricity": 100}, "prices": {"electricity": 0.8}}],
            "equipment": [{"asset_id": "grid", "output_carrier": "electricity", "maximum_output": 200}],
        },
    }
    for timescale in ("day_ahead", "intraday", "realtime"):
        body = base | {"idempotency_key": f"plan-{timescale}"}
        response = await client.post(f"/api/v1/energy-platform/plans/{timescale}", json=body)
        assert response.status_code == 200, response.text
        assert response.json()["timescale"] == timescale


def test_p0_p3_migration_and_operations_ui_have_required_controls():
    root = Path(__file__).resolve().parents[1]
    sql = (root / "migrations" / "018_energy_platform_p0_p3.sql").read_text()
    for table in (
        "energy_topology_versions",
        "device_driver_versions",
        "energy_raw_measurements",
        "charging_sessions",
        "storage_state_snapshots",
        "campus_service_requirements",
        "energy_plans",
        "energy_baselines",
        "utility_bills",
        "energy_mv_results",
    ):
        assert f"chargeopt.{table}" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "prevent_energy_evidence_mutation" in sql
    html = (root / "static" / "index.html").read_text()
    javascript = (root / "static" / "app.js").read_text()
    for element_id in ("enTopology", "enCarriers", "enProtocols", "enTimescale", "enRunPlan", "enEvidence"):
        assert f'id="{element_id}"' in html
    assert 'api("/api/energy-platform/capabilities"' in javascript
    assert "function renderEnergy()" in javascript
