"""Deterministic charging-station digital-twin models and evidence contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Iterable
from dataclasses import asdict, replace
from datetime import UTC, date, datetime, timedelta
from statistics import mean, pstdev
from typing import Any

from .data import Repository
from .domain import Station

EVIDENCE_CLASSES = ("synthetic", "replay", "shadow", "observed", "field_qualified")
TWIN_HEALTH_STATES = ("healthy", "degraded", "untrusted", "offline")
QUALITY_CODES = ("good", "suspect", "bad", "substituted", "estimated")
ASSET_TYPES = {
    "station",
    "transformer",
    "bus",
    "meter",
    "charger",
    "connector",
    "pcs",
    "battery_system",
    "battery_rack",
    "battery_pack",
    "pv_inverter",
    "sensor",
    "gateway",
}

ALGORITHM_VERSIONS = {
    "topology": "asset-graph-validator-v1",
    "quality": "telemetry-quality-contract-v1",
    "estimator": "hybrid-balance-kalman-v1",
    "simulation": "electro-thermal-queue-twin-v1",
    "diagnostics": "topology-residual-diagnostics-v1",
    "calibration": "robust-affine-calibration-v1",
    "trajectory_comparison": "predicted-realized-comparison-v1",
    "causal": "aipw-ridge-logit-v1",
    "qualification": "field-qualification-gate-v1",
    "fault_injection": "commissioning-fault-suite-v1",
}

POINT_CONTRACTS: dict[str, dict[str, Any]] = {
    "load_kw": {"unit": "kW", "minimum": 0.0, "maximum_factor": 1.5},
    "grid_kw": {"unit": "kW", "minimum": -10.0, "maximum_factor": 1.3},
    "pv_kw": {"unit": "kW", "minimum": 0.0, "maximum_factor": 1.25},
    "storage_power_kw": {"unit": "kW", "minimum_factor": -1.05, "maximum_factor": 1.05},
    "storage_soc": {"unit": "ratio", "minimum": 0.0, "maximum": 1.0},
    "storage_soh": {"unit": "ratio", "minimum": 0.0, "maximum": 1.05},
    "battery_temperature_c": {"unit": "degC", "minimum": -40.0, "maximum": 90.0},
    "transformer_temperature_c": {"unit": "degC", "minimum": -40.0, "maximum": 180.0},
    "connector_available": {"unit": "count", "minimum": 0.0},
    "queue_length": {"unit": "count", "minimum": 0.0},
}

UNIT_CONVERSIONS = {
    ("W", "kW"): 0.001,
    ("MW", "kW"): 1000.0,
    ("percent", "ratio"): 0.01,
    ("%", "ratio"): 0.01,
}


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=_json_default).encode()
    return hashlib.sha256(encoded).hexdigest()


def evidence_contract(
    evidence_class: str,
    algorithm: str,
    input_payload: Any,
    *,
    topology_version: str | None = None,
    model_version: str | None = None,
    quality_flags: Iterable[str] = (),
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"Unsupported evidence class: {evidence_class}")
    timestamp = generated_at or datetime.now(UTC)
    return {
        "evidence_class": evidence_class,
        "algorithm_version": algorithm,
        "model_version": model_version or algorithm,
        "topology_version": topology_version,
        "input_hash": canonical_hash(input_payload),
        "quality_flags": sorted(set(quality_flags)),
        "generated_at": timestamp.isoformat(),
    }


def build_default_topology(station: Station) -> dict[str, Any]:
    assets: list[dict[str, Any]] = [
        _asset("station", "station", station.name),
        _asset(
            "transformer-main",
            "transformer",
            "Main transformer",
            rated_power_kw=station.transformer_capacity_kw,
        ),
        _asset("bus-main", "bus", "Main LV bus", rated_power_kw=station.transformer_capacity_kw),
        _asset("meter-grid", "meter", "Grid revenue meter"),
        _asset("gateway-main", "gateway", "Site edge gateway"),
    ]
    relationships = [
        _relationship("station", "transformer-main", "contains"),
        _relationship("station", "bus-main", "contains"),
        _relationship("station", "meter-grid", "contains"),
        _relationship("station", "gateway-main", "contains"),
        _relationship("transformer-main", "bus-main", "feeds"),
        _relationship("meter-grid", "transformer-main", "meters"),
        _relationship("gateway-main", "meter-grid", "communicates_with"),
    ]
    if station.storage_capacity_kwh > 0:
        assets.extend(
            [
                _asset("pcs-main", "pcs", "Storage PCS", rated_power_kw=station.storage_power_kw),
                _asset(
                    "battery-main",
                    "battery_system",
                    "Battery energy storage system",
                    rated_power_kw=station.storage_power_kw,
                    rated_energy_kwh=station.storage_capacity_kwh,
                ),
            ]
        )
        relationships.extend(
            [
                _relationship("station", "pcs-main", "contains"),
                _relationship("station", "battery-main", "contains"),
                _relationship("battery-main", "pcs-main", "feeds"),
                _relationship("pcs-main", "bus-main", "feeds"),
                _relationship("gateway-main", "pcs-main", "controls"),
            ]
        )
    if station.pv_capacity_kw > 0:
        assets.append(_asset("pv-inverter-main", "pv_inverter", "PV inverter", rated_power_kw=station.pv_capacity_kw))
        relationships.extend(
            [
                _relationship("station", "pv-inverter-main", "contains"),
                _relationship("pv-inverter-main", "bus-main", "feeds"),
                _relationship("gateway-main", "pv-inverter-main", "controls"),
            ]
        )
    connectors_per_charger = max(1, math.ceil(station.connector_count / max(1, station.charger_count)))
    connector_index = 0
    for charger_index in range(station.charger_count):
        charger_key = f"charger-{charger_index + 1:03d}"
        assets.append(
            _asset(
                charger_key, "charger", f"Charger {charger_index + 1}", rated_power_kw=station.max_connector_power_kw
            )
        )
        relationships.extend(
            [
                _relationship("station", charger_key, "contains"),
                _relationship("bus-main", charger_key, "feeds"),
                _relationship("gateway-main", charger_key, "controls"),
            ]
        )
        for _local_index in range(connectors_per_charger):
            if connector_index >= station.connector_count:
                break
            connector_index += 1
            connector_key = f"connector-{connector_index:03d}"
            assets.append(
                _asset(
                    connector_key,
                    "connector",
                    f"Connector {connector_index}",
                    rated_power_kw=station.max_connector_power_kw,
                )
            )
            relationships.append(_relationship(charger_key, connector_key, "contains"))
    payload = {
        "station_id": station.id,
        "version": 1,
        "assets": assets,
        "relationships": relationships,
    }
    validation = validate_topology(payload)
    return payload | {
        "topology_hash": canonical_hash(payload),
        "validation": validation,
        "algorithm_version": ALGORITHM_VERSIONS["topology"],
    }


def validate_topology(topology: dict[str, Any]) -> dict[str, Any]:
    assets = topology.get("assets", [])
    relationships = topology.get("relationships", [])
    errors: list[str] = []
    warnings: list[str] = []
    keys = [str(item.get("asset_key", "")) for item in assets]
    key_set = set(keys)
    if len(keys) != len(key_set):
        errors.append("duplicate_asset_key")
    if keys.count("station") != 1:
        errors.append("exactly_one_station_root_required")
    for asset in assets:
        if asset.get("asset_type") not in ASSET_TYPES:
            errors.append(f"unsupported_asset_type:{asset.get('asset_key')}")
        for field in ("rated_power_kw", "rated_energy_kwh"):
            value = asset.get(field)
            if value is not None and float(value) < 0:
                errors.append(f"negative_rating:{asset.get('asset_key')}:{field}")
    contains_graph: dict[str, list[str]] = {}
    for relationship in relationships:
        source = relationship.get("source_asset_key")
        target = relationship.get("target_asset_key")
        if source not in key_set or target not in key_set:
            errors.append(f"relationship_unknown_asset:{source}:{target}")
            continue
        if source == target:
            errors.append(f"self_relationship:{source}")
        if relationship.get("relationship_type") == "contains":
            contains_graph.setdefault(str(source), []).append(str(target))
    if _has_cycle(contains_graph):
        errors.append("containment_cycle")
    contained = {target for targets in contains_graph.values() for target in targets}
    orphaned = sorted(key_set - contained - {"station"})
    if orphaned:
        warnings.append("orphaned_assets:" + ",".join(orphaned))
    transformer_count = sum(item.get("asset_type") == "transformer" for item in assets)
    if transformer_count == 0:
        warnings.append("transformer_missing")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "asset_count": len(assets),
        "relationship_count": len(relationships),
    }


def normalize_measurement(
    payload: dict[str, Any],
    *,
    station: Station | None = None,
    received_at: datetime | None = None,
) -> dict[str, Any]:
    point_code = str(payload["point_code"])
    contract = POINT_CONTRACTS.get(point_code)
    if contract is None:
        raise ValueError(f"Unknown point_code: {point_code}")
    source_timestamp = _as_utc(payload["source_timestamp"])
    receive_time = _as_utc(received_at or payload.get("received_at") or datetime.now(UTC))
    source_unit = str(payload["unit"])
    canonical_unit = str(contract["unit"])
    if source_unit == canonical_unit:
        value = float(payload["value"])
    else:
        factor = UNIT_CONVERSIONS.get((source_unit, canonical_unit))
        if factor is None:
            raise ValueError(f"Unsupported unit conversion: {source_unit} -> {canonical_unit}")
        value = float(payload["value"]) * factor
    flags: list[str] = []
    age_seconds = (receive_time - source_timestamp).total_seconds()
    if age_seconds < -30:
        flags.append("future_timestamp")
    if age_seconds > 900:
        flags.append("stale")
    if abs(age_seconds) > 120:
        flags.append("clock_skew")
    minimum = _point_bound(contract, "minimum", station)
    maximum = _point_bound(contract, "maximum", station)
    if minimum is not None and value < minimum:
        flags.append("below_range")
    if maximum is not None and value > maximum:
        flags.append("above_range")
    if any(flag in flags for flag in ("future_timestamp", "below_range", "above_range")):
        quality_code = "bad"
    elif flags:
        quality_code = "suspect"
    else:
        quality_code = "good"
    raw = {
        "point_code": point_code,
        "value": payload["value"],
        "unit": source_unit,
        "source_timestamp": source_timestamp.isoformat(),
        "source": payload.get("source", "unknown"),
        "sequence_number": payload.get("sequence_number"),
    }
    idempotency_key = payload.get("idempotency_key") or canonical_hash(raw)
    return {
        "station_id": payload.get("station_id"),
        "asset_key": payload.get("asset_key"),
        "point_code": point_code,
        "value": round(value, 8),
        "unit": canonical_unit,
        "source_timestamp": source_timestamp.isoformat(),
        "received_at": receive_time.isoformat(),
        "sequence_number": payload.get("sequence_number"),
        "source": payload.get("source", "unknown"),
        "quality_code": quality_code,
        "quality_flags": sorted(flags),
        "raw_payload": payload.get("raw_payload", {}),
        "evidence_hash": canonical_hash(payload),
        "idempotency_key": str(idempotency_key),
        "algorithm_version": ALGORITHM_VERSIONS["quality"],
    }


def estimate_station_state(
    station: Station,
    measurements: list[dict[str, Any]],
    *,
    previous: dict[str, Any] | None = None,
    evidence_class: str = "observed",
    topology_version: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if evidence_class not in EVIDENCE_CLASSES:
        raise ValueError(f"Unsupported evidence class: {evidence_class}")
    timestamp = _as_utc(now or datetime.now(UTC))
    latest = _latest_measurements(measurements)
    required = ("load_kw", "grid_kw", "pv_kw", "storage_power_kw", "storage_soc")
    missing = [code for code in required if code not in latest]
    flags = sorted({flag for item in latest.values() for flag in item.get("quality_flags", [])})
    flags.extend(f"missing:{code}" for code in missing)
    load_kw = _measurement_value(latest, "load_kw", 0.0)
    grid_kw = _measurement_value(latest, "grid_kw", load_kw)
    pv_kw = _measurement_value(latest, "pv_kw", 0.0)
    storage_power_kw = _measurement_value(latest, "storage_power_kw", 0.0)
    measured_soc = _measurement_value(latest, "storage_soc", 0.5)
    balance_expected = load_kw + storage_power_kw - pv_kw
    balance_residual = grid_kw - balance_expected
    age_seconds = max(
        [(timestamp - _as_utc(item["source_timestamp"])).total_seconds() for item in latest.values()] or [float("inf")]
    )
    previous_soc = measured_soc
    previous_variance = 0.01
    previous_time = timestamp - timedelta(minutes=15)
    if previous:
        previous_soc = float(previous.get("storage_soc", measured_soc))
        previous_variance = float(previous.get("soc_variance", 0.01))
        previous_time = _as_utc(previous.get("estimated_at", previous_time))
    dt_hours = max(0.0, min(1.0, (timestamp - previous_time).total_seconds() / 3600))
    capacity = max(1.0, station.storage_capacity_kwh)
    eta = 0.94 - 0.03 * min(1.0, abs(storage_power_kw) / max(1.0, station.storage_power_kw))
    predicted_soc = previous_soc + (
        storage_power_kw * eta * dt_hours / capacity
        if storage_power_kw >= 0
        else storage_power_kw / max(eta, 0.8) * dt_hours / capacity
    )
    process_variance = previous_variance + 0.0004 + abs(storage_power_kw) / capacity * 0.0002
    measurement_variance = 0.0009 if latest.get("storage_soc", {}).get("quality_code") == "good" else 0.01
    kalman_gain = process_variance / (process_variance + measurement_variance)
    estimated_soc = _clamp(predicted_soc + kalman_gain * (measured_soc - predicted_soc), 0.0, 1.0)
    soc_variance = max(1e-6, (1 - kalman_gain) * process_variance)
    soc_sigma = math.sqrt(soc_variance)
    measured_soh = _measurement_value(latest, "storage_soh", 0.95)
    temperature = _measurement_value(latest, "battery_temperature_c", 25.0)
    soh_penalty = max(0.0, abs(temperature - 25.0) - 15.0) * 0.0002
    estimated_soh = _clamp(measured_soh - soh_penalty, 0.5, 1.0)
    headroom = station.transformer_capacity_kw - grid_kw
    quality_penalty = sum(
        0.05 if item.get("quality_code") == "suspect" else 0.15
        for item in latest.values()
        if item.get("quality_code") != "good"
    )
    residual_penalty = min(0.35, abs(balance_residual) / max(1.0, station.transformer_capacity_kw) * 2)
    freshness_penalty = 0.4 if age_seconds == float("inf") else min(0.4, max(0.0, age_seconds) / 1800 * 0.4)
    observability_penalty = len(missing) * 0.08
    trust_score = _clamp(1 - quality_penalty - residual_penalty - freshness_penalty - observability_penalty, 0.0, 1.0)
    if age_seconds > 1800 or len(missing) >= 3:
        health = "offline"
    elif trust_score < 0.5:
        health = "untrusted"
    elif trust_score < 0.8:
        health = "degraded"
    else:
        health = "healthy"
    gate_reasons = []
    if missing:
        gate_reasons.append("critical_measurements_missing")
    if age_seconds > 900:
        gate_reasons.append("telemetry_stale")
    if trust_score < 0.8:
        gate_reasons.append("twin_trust_below_threshold")
    if abs(balance_residual) > max(20.0, station.transformer_capacity_kw * 0.05):
        gate_reasons.append("power_balance_residual_high")
    if evidence_class != "field_qualified":
        gate_reasons.append("field_qualification_required_for_autonomy")
    input_payload = {"station": asdict(station), "measurements": measurements, "previous": previous}
    contract = evidence_contract(
        evidence_class,
        ALGORITHM_VERSIONS["estimator"],
        input_payload,
        topology_version=topology_version,
        quality_flags=flags,
        generated_at=timestamp,
    )
    states = [
        _state(
            "storage_soc",
            estimated_soc,
            "ratio",
            estimated_soc - 1.64 * soc_sigma,
            estimated_soc + 1.64 * soc_sigma,
            trust_score,
            measured_soc - predicted_soc,
        ),
        _state(
            "storage_soh",
            estimated_soh,
            "ratio",
            estimated_soh - 0.02,
            estimated_soh + 0.02,
            trust_score * 0.9,
            measured_soh - estimated_soh,
        ),
        _state(
            "transformer_headroom_kw",
            headroom,
            "kW",
            headroom - abs(balance_residual),
            headroom + abs(balance_residual),
            trust_score,
            balance_residual,
        ),
        _state(
            "power_balance_residual_kw",
            balance_residual,
            "kW",
            balance_residual - 5.0,
            balance_residual + 5.0,
            trust_score,
            balance_residual,
        ),
        _state("conversion_efficiency", eta, "ratio", eta - 0.02, eta + 0.02, trust_score * 0.95, None),
    ]
    return {
        "station_id": station.id,
        "estimated_at": timestamp.isoformat(),
        "health": health,
        "trust_score": round(trust_score, 6),
        "states": states,
        "storage_soc": round(estimated_soc, 6),
        "soc_variance": round(soc_variance, 8),
        "estimated_soh": round(estimated_soh, 6),
        "transformer_headroom_kw": round(headroom, 3),
        "balance_residual_kw": round(balance_residual, 3),
        "autonomy_gate": {"allowed": not gate_reasons, "reasons": sorted(set(gate_reasons))},
        "contract": contract,
    }


def simulate_station(
    station: Station,
    initial_state: dict[str, Any],
    schedule: list[dict[str, Any]],
    *,
    interval_minutes: int = 15,
    evidence_class: str = "synthetic",
    topology_version: str | None = None,
    random_seed: int = 0,
) -> dict[str, Any]:
    if not schedule:
        raise ValueError("schedule must not be empty")
    if not 1 <= interval_minutes <= 60:
        raise ValueError("interval_minutes must be between 1 and 60")
    dt_hours = interval_minutes / 60
    soc = _clamp(float(initial_state.get("storage_soc", 0.5)), 0.0, 1.0)
    soh = _clamp(float(initial_state.get("storage_soh", initial_state.get("estimated_soh", 0.95))), 0.5, 1.0)
    battery_temp = float(initial_state.get("battery_temperature_c", 25.0))
    transformer_temp = float(initial_state.get("transformer_temperature_c", 45.0))
    queue = max(0.0, float(initial_state.get("queue_length", 0)))
    capacity = max(1.0, station.storage_capacity_kwh * soh)
    outputs: list[dict[str, Any]] = []
    throughput = 0.0
    curtailed_kwh = 0.0
    unserved_sessions = 0.0
    max_grid = 0.0
    max_transformer_temp = transformer_temp
    for index, row in enumerate(schedule):
        load_kw = max(0.0, float(row.get("load_kw", 0.0)))
        pv_kw = max(0.0, float(row.get("pv_kw", 0.0)))
        requested_power = float(row.get("storage_power_kw", 0.0))
        ambient = float(row.get("ambient_temperature_c", 25.0))
        available_connectors = max(0.0, float(row.get("available_connectors", station.connector_count)))
        arrivals = max(0.0, float(row.get("arrivals", 0.0)))
        service_rate = max(0.0, float(row.get("service_rate", available_connectors * 0.65)))
        power_limit = station.storage_power_kw * max(0.0, 1 - max(0.0, battery_temp - 45.0) / 25.0)
        storage_power = _clamp(requested_power, -power_limit, power_limit)
        ratio = abs(storage_power) / max(1.0, station.storage_power_kw)
        efficiency = _clamp(0.965 - 0.035 * ratio - max(0.0, abs(battery_temp - 25.0) - 15) * 0.001, 0.82, 0.97)
        if storage_power >= 0:
            next_soc = soc + storage_power * efficiency * dt_hours / capacity
        else:
            next_soc = soc + storage_power / efficiency * dt_hours / capacity
        clipped_soc = _clamp(next_soc, 0.10, 0.95)
        if clipped_soc != next_soc:
            energy_room = (clipped_soc - soc) * capacity
            storage_power = energy_room / dt_hours / (efficiency if energy_room >= 0 else 1 / efficiency)
        soc = clipped_soc
        battery_target = ambient + 8.0 + 22.0 * ratio**1.7
        battery_temp += (battery_target - battery_temp) * min(1.0, dt_hours / 1.5)
        pv_used = min(pv_kw, load_kw + max(0.0, storage_power))
        curtailed_kwh += max(0.0, pv_kw - pv_used) * dt_hours
        grid_kw = max(0.0, load_kw + storage_power - pv_used)
        loading = grid_kw / max(1.0, station.transformer_capacity_kw)
        transformer_target = ambient + 55.0 * loading**1.6
        transformer_temp += (transformer_target - transformer_temp) * min(1.0, dt_hours / 2.0)
        queue = max(0.0, queue + arrivals - service_rate * dt_hours)
        if queue > station.connector_count * 0.5:
            unserved_sessions += (queue - station.connector_count * 0.5) * dt_hours * 0.05
        step_throughput = abs(storage_power) * dt_hours
        throughput += step_throughput
        soh = max(0.5, soh - step_throughput / max(1.0, station.storage_capacity_kwh) * 0.00004)
        max_grid = max(max_grid, grid_kw)
        max_transformer_temp = max(max_transformer_temp, transformer_temp)
        outputs.append(
            {
                "step": index,
                "timestamp": row.get("timestamp"),
                "load_kw": round(load_kw, 3),
                "pv_kw": round(pv_kw, 3),
                "storage_power_kw": round(storage_power, 3),
                "grid_kw": round(grid_kw, 3),
                "storage_soc": round(soc, 6),
                "storage_soh": round(soh, 8),
                "conversion_efficiency": round(efficiency, 6),
                "battery_temperature_c": round(battery_temp, 3),
                "transformer_temperature_c": round(transformer_temp, 3),
                "transformer_loading_ratio": round(loading, 6),
                "queue_length": round(queue, 3),
                "constraint_violations": _simulation_violations(soc, battery_temp, transformer_temp, loading),
            }
        )
    violations = sum(len(row["constraint_violations"]) for row in outputs)
    input_payload = {
        "station": asdict(station),
        "initial_state": initial_state,
        "schedule": schedule,
        "interval_minutes": interval_minutes,
        "random_seed": random_seed,
    }
    contract = evidence_contract(
        evidence_class,
        ALGORITHM_VERSIONS["simulation"],
        input_payload,
        topology_version=topology_version,
        quality_flags=("constraint_violation",) if violations else (),
        generated_at=_as_utc(schedule[0].get("timestamp") or "1970-01-01T00:00:00+00:00"),
    )
    return {
        "station_id": station.id,
        "interval_minutes": interval_minutes,
        "random_seed": random_seed,
        "trajectory": outputs,
        "metrics": {
            "max_grid_kw": round(max_grid, 3),
            "max_transformer_temperature_c": round(max_transformer_temp, 3),
            "battery_throughput_kwh": round(throughput, 3),
            "pv_curtailed_kwh": round(curtailed_kwh, 3),
            "unserved_sessions": round(unserved_sessions, 3),
            "constraint_violation_count": violations,
            "final_soc": round(soc, 6),
            "final_soh": round(soh, 8),
        },
        "contract": contract,
    }


def diagnose_twin(
    station: Station, snapshot: dict[str, Any], simulation: dict[str, Any] | None = None
) -> dict[str, Any]:
    detected_at = snapshot.get("estimated_at") or datetime.now(UTC).isoformat()
    diagnostics: list[dict[str, Any]] = []
    trust = float(snapshot.get("trust_score", 0.0))
    residual = abs(float(snapshot.get("balance_residual_kw", 0.0)))
    headroom = float(snapshot.get("transformer_headroom_kw", 0.0))
    if trust < 0.8:
        diagnostics.append(
            _diagnostic(
                station,
                "twin_trust_low",
                "high" if trust < 0.5 else "warning",
                1 - trust,
                "Twin state is not trustworthy enough for automatic control.",
                ["telemetry_quality", "sensor_bias", "communications_delay"],
                {"trust_score": trust, "gate": snapshot.get("autonomy_gate", {})},
                detected_at,
            )
        )
    if residual > max(20.0, station.transformer_capacity_kw * 0.05):
        diagnostics.append(
            _diagnostic(
                station,
                "power_balance_residual",
                "high",
                min(0.99, residual / max(1.0, station.transformer_capacity_kw * 0.2)),
                "Measured grid power does not reconcile with load, PV, and storage power.",
                ["meter_scaling", "sign_convention", "unmetered_load", "clock_alignment"],
                {"residual_kw": residual},
                detected_at,
            )
        )
    if headroom < station.transformer_capacity_kw * 0.08:
        diagnostics.append(
            _diagnostic(
                station,
                "transformer_headroom_low",
                "critical" if headroom < 0 else "high",
                min(0.99, 1 - headroom / max(1.0, station.transformer_capacity_kw * 0.08)),
                "Transformer operating headroom is below the safe planning margin.",
                ["charging_peak", "storage_unavailable", "unexpected_site_load"],
                {"headroom_kw": headroom},
                detected_at,
            )
        )
    if simulation:
        metrics = simulation.get("metrics", {})
        if int(metrics.get("constraint_violation_count", 0)) > 0:
            diagnostics.append(
                _diagnostic(
                    station,
                    "predicted_constraint_violation",
                    "critical",
                    0.95,
                    "The proposed trajectory violates one or more physical constraints.",
                    ["unsafe_dispatch_schedule", "forecast_error", "derating"],
                    metrics,
                    detected_at,
                )
            )
        if float(metrics.get("unserved_sessions", 0)) > 0:
            diagnostics.append(
                _diagnostic(
                    station,
                    "predicted_service_loss",
                    "warning",
                    min(0.95, 0.5 + float(metrics["unserved_sessions"]) / 20),
                    "The scenario predicts charging demand that cannot be served.",
                    ["connector_unavailable", "arrival_surge", "power_limit"],
                    metrics,
                    detected_at,
                )
            )
    contract = evidence_contract(
        snapshot.get("contract", {}).get("evidence_class", "synthetic"),
        ALGORITHM_VERSIONS["diagnostics"],
        {"snapshot": snapshot, "simulation": simulation},
        topology_version=snapshot.get("contract", {}).get("topology_version"),
    )
    return {
        "station_id": station.id,
        "health": "healthy"
        if not diagnostics
        else max(diagnostics, key=lambda item: _severity_rank(item["severity"]))["severity"],
        "diagnostics": diagnostics,
        "maintenance_recommendations": maintenance_recommendations(station, snapshot, diagnostics),
        "contract": contract,
    }


def calibrate_twin_model(
    predicted: list[float],
    observed: list[float],
    *,
    evidence_class: str = "observed",
    model_scope: str = "station_power_balance",
) -> dict[str, Any]:
    if len(predicted) != len(observed) or not predicted:
        raise ValueError("predicted and observed must be non-empty and have equal lengths")
    base = {
        "model_scope": model_scope,
        "algorithm_version": ALGORITHM_VERSIONS["calibration"],
        "evidence_class": evidence_class,
        "sample_count": len(observed),
        "input_hash": canonical_hash({"predicted": predicted, "observed": observed}),
    }
    if len(observed) < 24:
        return base | {
            "status": "insufficient_evidence",
            "parameters": {},
            "metrics": {},
            "quality_gate": {"passed": False, "checks": {"minimum_24_samples": False}},
        }
    predicted_mean = mean(predicted)
    observed_mean = mean(observed)
    covariance = sum((x - predicted_mean) * (y - observed_mean) for x, y in zip(predicted, observed, strict=True))
    variance = sum((x - predicted_mean) ** 2 for x in predicted)
    scale = covariance / variance if variance > 1e-12 else 1.0
    offset = observed_mean - scale * predicted_mean
    corrected = [scale * value + offset for value in predicted]
    raw_metrics = _error_metrics(predicted, observed)
    calibrated_metrics = _error_metrics(corrected, observed)
    denominator = max(abs(observed_mean), 1.0)
    checks = {
        "minimum_24_samples": len(observed) >= 24,
        "normalized_rmse_lte_10pct": calibrated_metrics["rmse"] / denominator <= 0.10,
        "relative_bias_lte_5pct": abs(calibrated_metrics["bias"]) / denominator <= 0.05,
        "calibration_not_worse": calibrated_metrics["rmse"] <= raw_metrics["rmse"] + 1e-9,
        "scale_within_physical_range": 0.5 <= scale <= 1.5,
    }
    passed = all(checks.values())
    return base | {
        "status": "passed" if passed else "failed",
        "parameters": {"scale": round(scale, 8), "offset": round(offset, 8)},
        "metrics": {"raw": raw_metrics, "calibrated": calibrated_metrics},
        "quality_gate": {"passed": passed, "checks": checks},
    }


def compare_trajectories(
    predicted: list[dict[str, Any]],
    observed: list[dict[str, Any]],
    *,
    fields: tuple[str, ...] = ("grid_kw", "storage_soc", "transformer_temperature_c"),
) -> dict[str, Any]:
    count = min(len(predicted), len(observed))
    if count == 0:
        raise ValueError("predicted and observed trajectories must not be empty")
    metrics = {}
    missing_fields = []
    for field in fields:
        pairs = [
            (float(predicted[index][field]), float(observed[index][field]))
            for index in range(count)
            if predicted[index].get(field) is not None and observed[index].get(field) is not None
        ]
        if not pairs:
            missing_fields.append(field)
            continue
        metrics[field] = _error_metrics([item[0] for item in pairs], [item[1] for item in pairs]) | {
            "sample_count": len(pairs)
        }
    return {
        "sample_count": count,
        "metrics": metrics,
        "missing_fields": missing_fields,
        "input_hash": canonical_hash({"predicted": predicted, "observed": observed, "fields": fields}),
        "algorithm_version": ALGORITHM_VERSIONS["trajectory_comparison"],
    }


def maintenance_recommendations(
    station: Station,
    snapshot: dict[str, Any],
    diagnostics: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    recommendations = []
    actions = {
        "twin_trust_low": (
            "inspect_telemetry_chain",
            "Inspect time synchronization, point mapping, and sensor quality.",
        ),
        "power_balance_residual": ("calibrate_meters", "Calibrate revenue meter and verify power sign conventions."),
        "transformer_headroom_low": (
            "inspect_transformer_loading",
            "Review thermal loading and peak-control settings.",
        ),
        "predicted_constraint_violation": (
            "block_and_replan_dispatch",
            "Block the proposed schedule and generate a feasible plan.",
        ),
        "predicted_service_loss": (
            "inspect_charger_availability",
            "Inspect unavailable chargers and rebalance connector capacity.",
        ),
    }
    for diagnostic in diagnostics:
        action_type, recommendation = actions.get(
            diagnostic["diagnostic_type"],
            ("engineering_review", "Review the diagnostic evidence and inspect the affected asset."),
        )
        recommendations.append(
            {
                "diagnostic_fingerprint": diagnostic["fingerprint"],
                "action_type": action_type,
                "priority": "critical"
                if diagnostic["severity"] == "critical"
                else "high"
                if diagnostic["severity"] == "high"
                else "medium",
                "recommendation": recommendation,
            }
        )
    soh = float(snapshot.get("estimated_soh", 0.95))
    annual_fade = max(0.005, min(0.08, 0.015 + (1 - station.reliability_score) * 0.05))
    remaining_years = max(0.0, (soh - 0.7) / annual_fade)
    recommendations.append(
        {
            "action_type": "battery_life_planning",
            "priority": "medium" if remaining_years < 3 else "low",
            "recommendation": "Plan battery augmentation before estimated usable SOH reaches 70%.",
            "estimated_remaining_years": round(remaining_years, 2),
            "assumed_annual_soh_fade": round(annual_fade, 4),
        }
    )
    return recommendations


def run_fault_injection_suite(station: Station) -> dict[str, Any]:
    timestamp = datetime(2026, 1, 1, tzinfo=UTC)
    healthy = [
        normalize_measurement(
            {
                "station_id": station.id,
                "point_code": code,
                "value": value,
                "unit": unit,
                "source_timestamp": timestamp,
                "source": "commissioning-suite",
            },
            station=station,
            received_at=timestamp + timedelta(seconds=2),
        )
        for code, value, unit in (
            ("load_kw", 1000, "kW"),
            ("grid_kw", 900, "kW"),
            ("pv_kw", 100, "kW"),
            ("storage_power_kw", 0, "kW"),
            ("storage_soc", 0.6, "ratio"),
        )
    ]
    stale = [dict(item, source_timestamp=(timestamp - timedelta(hours=2)).isoformat()) for item in healthy]
    stale_state = estimate_station_state(station, stale, evidence_class="observed", now=timestamp)
    missing_state = estimate_station_state(station, healthy[:1], evidence_class="observed", now=timestamp)
    overload_simulation = simulate_station(
        station,
        {"storage_soc": 0.5},
        [{"timestamp": timestamp.isoformat(), "load_kw": station.transformer_capacity_kw * 1.2, "pv_kw": 0}],
        evidence_class="replay",
    )
    low_trust_blocked = False
    try:
        twin_aware_station(station, {"trust_score": 0.2, "estimated_soh": 1.0})
    except PermissionError:
        low_trust_blocked = True
    checks = {
        "stale_telemetry_blocks_autonomy": not stale_state["autonomy_gate"]["allowed"],
        "missing_measurements_block_autonomy": not missing_state["autonomy_gate"]["allowed"],
        "overload_is_detected": overload_simulation["metrics"]["constraint_violation_count"] > 0,
        "low_trust_blocks_optimizer": low_trust_blocked,
    }
    return {
        "qualified": all(checks.values()),
        "checks": checks,
        "algorithm_version": ALGORITHM_VERSIONS["fault_injection"],
        "evidence_class": "replay",
        "evidence_hash": canonical_hash(checks),
    }


def estimate_causal_uplift(
    observations: list[dict[str, Any]],
    *,
    evidence_class: str = "observed",
    estimand: str = "monthly_profit_lift",
) -> dict[str, Any]:
    input_hash = canonical_hash(observations)
    base = {
        "estimand": estimand,
        "algorithm_version": ALGORITHM_VERSIONS["causal"],
        "evidence_class": evidence_class,
        "input_hash": input_hash,
        "sample_count": len(observations),
    }
    treated = [row for row in observations if bool(row.get("treated"))]
    controls = [row for row in observations if not bool(row.get("treated"))]
    if len(observations) < 40 or len(treated) < 10 or len(controls) < 10:
        return base | {
            "status": "insufficient_evidence",
            "auditable": False,
            "blockers": ["minimum_40_samples_and_10_per_arm_required"],
        }
    feature_names = sorted({key for row in observations for key in row.get("covariates", {})})
    if not feature_names:
        return base | {
            "status": "insufficient_evidence",
            "auditable": False,
            "blockers": ["covariates_required"],
        }
    x_raw = [[float(row.get("covariates", {}).get(name, 0.0)) for name in feature_names] for row in observations]
    x = _standardize(x_raw)
    treatment = [1.0 if bool(row.get("treated")) else 0.0 for row in observations]
    outcome = [float(row["outcome"]) for row in observations]
    propensity_weights = _fit_logistic(x, treatment)
    propensity = [
        _clamp(_sigmoid(_dot(weights, [1.0, *row])), 0.02, 0.98) for row in x for weights in [propensity_weights]
    ]
    overlap = min(
        sum(0.05 <= score <= 0.95 for score in propensity) / len(propensity),
        1.0,
    )
    treated_x = [row for row, flag in zip(x, treatment, strict=True) if flag == 1]
    control_x = [row for row, flag in zip(x, treatment, strict=True) if flag == 0]
    treated_y = [value for value, flag in zip(outcome, treatment, strict=True) if flag == 1]
    control_y = [value for value, flag in zip(outcome, treatment, strict=True) if flag == 0]
    outcome_treated = _fit_ridge(treated_x, treated_y)
    outcome_control = _fit_ridge(control_x, control_y)
    influence: list[float] = []
    for row, flag, observed, score in zip(x, treatment, outcome, propensity, strict=True):
        augmented = [1.0, *row]
        mu1 = _dot(outcome_treated, augmented)
        mu0 = _dot(outcome_control, augmented)
        influence.append(mu1 - mu0 + flag * (observed - mu1) / score - (1 - flag) * (observed - mu0) / (1 - score))
    uplift = mean(influence)
    standard_error = pstdev(influence) / math.sqrt(len(influence)) if len(influence) > 1 else 0.0
    ci_low = uplift - 1.64 * standard_error
    ci_high = uplift + 1.64 * standard_error
    naive = mean(treated_y) - mean(control_y)
    placebo = _placebo_difference(outcome, treatment)
    blockers = []
    if overlap < 0.8:
        blockers.append("propensity_overlap_below_80pct")
    if abs(placebo) > max(abs(uplift) * 0.5, standard_error * 2, 1.0):
        blockers.append("placebo_effect_too_large")
    if ci_low <= 0 <= ci_high:
        blockers.append("confidence_interval_crosses_zero")
    return base | {
        "status": "completed",
        "auditable": not blockers and evidence_class in {"observed", "field_qualified"},
        "feature_names": feature_names,
        "treated_count": len(treated),
        "control_count": len(controls),
        "overlap_score": round(overlap, 6),
        "average_treatment_effect": round(uplift, 6),
        "naive_difference": round(naive, 6),
        "standard_error": round(standard_error, 6),
        "confidence_interval_90": {"low": round(ci_low, 6), "high": round(ci_high, 6)},
        "placebo_effect": round(placebo, 6),
        "blockers": blockers,
        "assumptions": [
            "conditional_exchangeability_given_covariates",
            "stable_unit_treatment_value",
            "positivity",
            "consistent_outcome_measurement",
        ],
    }


def twin_aware_station(station: Station, snapshot: dict[str, Any]) -> Station:
    trust = float(snapshot.get("trust_score", 0))
    if trust < 0.8:
        raise PermissionError("Twin trust is below 0.8; optimization is blocked.")
    soh = _clamp(float(snapshot.get("estimated_soh", 1.0)), 0.5, 1.0)
    temperature = next(
        (
            float(item["value"])
            for item in snapshot.get("states", [])
            if item.get("state_code") == "battery_temperature_c"
        ),
        25.0,
    )
    thermal_derating = _clamp(1 - max(0.0, temperature - 42.0) / 30.0, 0.3, 1.0)
    return replace(
        station,
        storage_capacity_kwh=station.storage_capacity_kwh * soh,
        storage_power_kw=station.storage_power_kw * soh * thermal_derating,
        reliability_score=min(station.reliability_score, trust),
    )


def assess_field_qualification(
    evidence: list[dict[str, Any]],
    *,
    as_of: date | None = None,
    required_shadow_days: int = 30,
) -> dict[str, Any]:
    current_date = as_of or datetime.now(UTC).date()
    qualified_by_category: dict[str, list[dict[str, Any]]] = {}
    for row in evidence:
        if bool(row.get("qualified")):
            qualified_by_category.setdefault(str(row.get("category")), []).append(row)
    blockers = []
    for category in (
        "topology",
        "device_attestation",
        "calibration",
        "slo",
        "fault_injection",
        "recovery_drill",
        "approval",
    ):
        if not qualified_by_category.get(category):
            blockers.append(f"missing_qualified_{category}")
    shadow_dates = {
        _as_date(row["evidence_date"])
        for row in qualified_by_category.get("shadow_day", [])
        if _as_date(row["evidence_date"]) <= current_date
    }
    consecutive = 0
    cursor = current_date - timedelta(days=1)
    while cursor in shadow_dates:
        consecutive += 1
        cursor -= timedelta(days=1)
    if consecutive < required_shadow_days:
        blockers.append(f"shadow_days_{consecutive}_of_{required_shadow_days}")
    return {
        "ready": not blockers,
        "as_of": current_date.isoformat(),
        "qualified_shadow_days": consecutive,
        "required_shadow_days": required_shadow_days,
        "blockers": blockers,
        "algorithm_version": ALGORITHM_VERSIONS["qualification"],
        "evidence_hash": canonical_hash(evidence),
    }


def build_twin_snapshot(repo: Repository, station_id: str, *, evidence_class: str = "synthetic") -> dict[str, Any]:
    station = next((item for item in repo.stations if item.id == station_id), None)
    if station is None:
        raise KeyError(f"Unknown station_id: {station_id}")
    points = sorted(repo.station_points(station_id), key=lambda item: item.timestamp)
    if not points:
        raise ValueError("No telemetry is available for this station.")
    current = points[-1]
    received_at = _as_utc(current.timestamp) + timedelta(seconds=2)
    raw_measurements = [
        ("load_kw", current.load_kw, "kW"),
        ("grid_kw", current.grid_kw, "kW"),
        ("pv_kw", current.pv_kw, "kW"),
        ("storage_power_kw", current.storage_power_kw, "kW"),
        ("storage_soc", current.storage_soc, "ratio"),
        ("queue_length", current.queue_length, "count"),
        ("connector_available", station.connector_count - current.connector_occupied, "count"),
    ]
    measurements = [
        normalize_measurement(
            {
                "station_id": station_id,
                "asset_key": "station",
                "point_code": code,
                "value": value,
                "unit": unit,
                "source_timestamp": current.timestamp,
                "source": "aggregate_repository",
                "idempotency_key": f"snapshot:{station_id}:{code}:{current.timestamp.isoformat()}",
            },
            station=station,
            received_at=received_at,
        )
        for code, value, unit in raw_measurements
    ]
    topology = build_default_topology(station)
    state = estimate_station_state(
        station,
        measurements,
        evidence_class=evidence_class,
        topology_version=topology["topology_hash"],
        now=received_at,
    )
    diagnostics = diagnose_twin(station, state)
    return {
        "station": asdict(station),
        "topology": topology,
        "measurements": measurements,
        "state": state,
        "diagnostics": diagnostics,
    }


def _asset(asset_key: str, asset_type: str, name: str, **ratings: Any) -> dict[str, Any]:
    return {"asset_key": asset_key, "asset_type": asset_type, "name": name, **ratings, "attributes": {}}


def _relationship(source: str, target: str, relationship_type: str) -> dict[str, Any]:
    return {
        "source_asset_key": source,
        "target_asset_key": target,
        "relationship_type": relationship_type,
        "attributes": {},
    }


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        for child in graph.get(node, []):
            if visit(child):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _point_bound(contract: dict[str, Any], bound: str, station: Station | None) -> float | None:
    if bound in contract:
        return float(contract[bound])
    factor = contract.get(f"{bound}_factor")
    if factor is None or station is None:
        return None
    if contract["unit"] == "kW":
        if float(factor) < 0 and contract is POINT_CONTRACTS["storage_power_kw"]:
            return station.storage_power_kw * float(factor)
        if contract is POINT_CONTRACTS["storage_power_kw"]:
            return station.storage_power_kw * float(factor)
        if contract is POINT_CONTRACTS["pv_kw"]:
            return station.pv_capacity_kw * float(factor)
        return station.transformer_capacity_kw * float(factor)
    return None


def _latest_measurements(measurements: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for item in measurements:
        code = str(item["point_code"])
        current = latest.get(code)
        if item.get("quality_code") != "bad" and (
            current is None or _as_utc(item["source_timestamp"]) > _as_utc(current["source_timestamp"])
        ):
            latest[code] = item
    return latest


def _measurement_value(latest: dict[str, dict[str, Any]], code: str, default: float) -> float:
    item = latest.get(code)
    return float(item["value"]) if item else default


def _state(
    code: str, value: float, unit: str, low: float, high: float, trust: float, residual: float | None
) -> dict[str, Any]:
    return {
        "state_code": code,
        "value": round(value, 8),
        "unit": unit,
        "confidence_low": round(min(low, high), 8),
        "confidence_high": round(max(low, high), 8),
        "trust_score": round(_clamp(trust, 0, 1), 6),
        "residual": None if residual is None else round(residual, 8),
    }


def _simulation_violations(soc: float, battery_temp: float, transformer_temp: float, loading: float) -> list[str]:
    violations = []
    if not 0.10 <= soc <= 0.95:
        violations.append("storage_soc")
    if battery_temp > 55:
        violations.append("battery_temperature")
    if transformer_temp > 110:
        violations.append("transformer_temperature")
    if loading > 0.95:
        violations.append("transformer_loading")
    return violations


def _diagnostic(
    station: Station,
    diagnostic_type: str,
    severity: str,
    confidence: float,
    summary: str,
    likely_causes: list[str],
    evidence: dict[str, Any],
    detected_at: str,
) -> dict[str, Any]:
    fingerprint = canonical_hash(
        {"station_id": station.id, "diagnostic_type": diagnostic_type, "causes": likely_causes}
    )
    return {
        "fingerprint": fingerprint,
        "diagnostic_type": diagnostic_type,
        "severity": severity,
        "confidence": round(_clamp(confidence, 0, 1), 6),
        "summary": summary,
        "likely_causes": likely_causes,
        "evidence": evidence,
        "first_detected_at": detected_at,
        "last_detected_at": detected_at,
        "algorithm_version": ALGORITHM_VERSIONS["diagnostics"],
    }


def _severity_rank(severity: str) -> int:
    return {"healthy": 0, "info": 1, "warning": 2, "high": 3, "critical": 4}.get(severity, 0)


def _error_metrics(predicted: list[float], observed: list[float]) -> dict[str, float]:
    residuals = [estimate - actual for estimate, actual in zip(predicted, observed, strict=True)]
    absolute = [abs(value) for value in residuals]
    return {
        "mae": round(mean(absolute), 8),
        "rmse": round(math.sqrt(mean([value * value for value in residuals])), 8),
        "bias": round(mean(residuals), 8),
        "max_absolute_error": round(max(absolute), 8),
    }


def _standardize(rows: list[list[float]]) -> list[list[float]]:
    columns = list(zip(*rows, strict=True))
    means = [mean(column) for column in columns]
    scales = [pstdev(column) or 1.0 for column in columns]
    return [[(value - means[index]) / scales[index] for index, value in enumerate(row)] for row in rows]


def _fit_logistic(x: list[list[float]], y: list[float], iterations: int = 800, rate: float = 0.08) -> list[float]:
    weights = [0.0] * (len(x[0]) + 1)
    for _ in range(iterations):
        gradient = [0.0] * len(weights)
        for row, target in zip(x, y, strict=True):
            augmented = [1.0, *row]
            error = _sigmoid(_dot(weights, augmented)) - target
            for index, value in enumerate(augmented):
                gradient[index] += error * value
        for index in range(len(weights)):
            regularization = 0.001 * weights[index] if index else 0.0
            weights[index] -= rate * (gradient[index] / len(x) + regularization)
    return weights


def _fit_ridge(x: list[list[float]], y: list[float], regularization: float = 0.05) -> list[float]:
    rows = [[1.0, *row] for row in x]
    width = len(rows[0])
    matrix = [[0.0] * width for _ in range(width)]
    vector = [0.0] * width
    for row, target in zip(rows, y, strict=True):
        for i in range(width):
            vector[i] += row[i] * target
            for j in range(width):
                matrix[i][j] += row[i] * row[j]
    for index in range(1, width):
        matrix[index][index] += regularization
    return _solve_linear(matrix, vector)


def _solve_linear(matrix: list[list[float]], vector: list[float]) -> list[float]:
    size = len(vector)
    augmented = [row[:] + [vector[index]] for index, row in enumerate(matrix)]
    for pivot in range(size):
        best = max(range(pivot, size), key=lambda index: abs(augmented[index][pivot]))
        augmented[pivot], augmented[best] = augmented[best], augmented[pivot]
        divisor = augmented[pivot][pivot]
        if abs(divisor) < 1e-10:
            augmented[pivot][pivot] = divisor = 1e-10
        augmented[pivot] = [value / divisor for value in augmented[pivot]]
        for row_index in range(size):
            if row_index == pivot:
                continue
            factor = augmented[row_index][pivot]
            augmented[row_index] = [
                value - factor * pivot_value
                for value, pivot_value in zip(augmented[row_index], augmented[pivot], strict=True)
            ]
    return [augmented[index][-1] for index in range(size)]


def _placebo_difference(outcome: list[float], treatment: list[float]) -> float:
    seed = canonical_hash({"sample_count": len(outcome), "treatment": treatment})
    order = sorted(range(len(treatment)), key=lambda index: canonical_hash(f"{seed}:{index}"))
    placebo_treatment = [treatment[index] for index in order]
    treated = [value for value, flag in zip(outcome, placebo_treatment, strict=True) if flag == 1]
    control = [value for value, flag in zip(outcome, placebo_treatment, strict=True) if flag == 0]
    return mean(treated) - mean(control) if treated and control else 0.0


def _dot(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _sigmoid(value: float) -> float:
    if value >= 0:
        exponent = math.exp(-min(value, 700))
        return 1 / (1 + exponent)
    exponent = math.exp(max(value, -700))
    return exponent / (1 + exponent)


def _clamp(value: float, low: float, high: float) -> float:
    return min(high, max(low, value))


def _as_utc(value: datetime | str) -> datetime:
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _as_date(value: date | datetime | str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "__dict__"):
        return value.__dict__
    raise TypeError(f"Cannot serialize {type(value)!r}")
