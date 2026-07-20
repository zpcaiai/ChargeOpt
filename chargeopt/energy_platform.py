"""Shared P0-P3 multi-energy domain, quality, control, optimization, and M&V logic.

All functions are deterministic and side-effect free. Persistence and physical
execution are intentionally kept in separate repository and worker layers.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

import numpy as np

ENERGY_CARRIERS = {
    "electricity",
    "cooling",
    "heating",
    "steam",
    "gas",
    "water",
    "compressed_air",
}

ASSET_TYPES = {
    "portfolio",
    "park",
    "site",
    "building",
    "floor",
    "zone",
    "process_area",
    "substation",
    "distribution_room",
    "transformer",
    "switchgear",
    "switch",
    "bus",
    "feeder",
    "line",
    "meter",
    "charging_station",
    "charger",
    "evse",
    "connector",
    "vehicle",
    "pcs",
    "battery_system",
    "battery_rack",
    "battery_pack",
    "bms",
    "battery_thermal_management",
    "fire_system",
    "pv_inverter",
    "wind_turbine",
    "generator",
    "chp",
    "heat_pump",
    "boiler",
    "chiller",
    "cooling_tower",
    "pump",
    "fan",
    "ahu",
    "vav",
    "air_compressor",
    "lighting_circuit",
    "production_line",
    "flexible_load",
    "interruptible_load",
    "thermal_storage",
    "gateway",
    "sensor",
}

PROTOCOLS = {
    "ocpp16",
    "ocpp201",
    "ocpp21",
    "iso15118",
    "modbus_tcp",
    "modbus_rtu",
    "mqtt",
    "bacnet_ip",
    "opc_ua",
    "iec61850",
    "iec104",
    "dlt645",
    "cjt188",
}

ALGORITHMS = {
    "topology": "multi-energy-semantic-graph-v1",
    "quality": "industrial-timeseries-quality-v1",
    "reconciliation": "carrier-meter-balance-v1",
    "charging": "deadline-fair-hierarchical-power-share-v1",
    "storage": "electrothermal-warranty-safety-envelope-v1",
    "campus": "multi-energy-security-constrained-milp-mpc-v1",
    "baseline": "covariate-adjusted-energy-baseline-v1",
    "billing": "interval-tariff-reconstruction-v1",
    "allocation": "meter-first-cost-allocation-v1",
    "mv": "adjusted-baseline-mv-v1",
    "carbon": "dual-method-carbon-accounting-v1",
}


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


def validate_energy_topology(payload: dict[str, Any]) -> dict[str, Any]:
    assets = payload.get("assets") or []
    relationships = payload.get("relationships") or []
    points = payload.get("points") or []
    constraints = payload.get("constraints") or []
    errors: list[str] = []
    warnings: list[str] = []
    keys = [str(item.get("asset_key") or "") for item in assets]
    key_set = set(keys)
    if not assets:
        errors.append("assets_required")
    if "" in key_set:
        errors.append("asset_key_required")
    if len(keys) != len(key_set):
        errors.append("duplicate_asset_key")
    roots = [item for item in assets if item.get("asset_type") in {"portfolio", "park", "site"}]
    if len(roots) != 1:
        errors.append("exactly_one_portfolio_park_or_site_root_required")
    for asset in assets:
        key = str(asset.get("asset_key") or "")
        if asset.get("asset_type") not in ASSET_TYPES:
            errors.append(f"unsupported_asset_type:{key}")
        carriers = set(asset.get("energy_carriers") or [])
        unknown = carriers - ENERGY_CARRIERS
        if unknown:
            errors.append(f"unsupported_energy_carrier:{key}:{','.join(sorted(unknown))}")
        for name, value in (asset.get("rated_parameters") or {}).items():
            if isinstance(value, (int, float)) and value < 0 and not str(name).startswith("minimum_"):
                errors.append(f"negative_rating:{key}:{name}")

    containment: dict[str, list[str]] = defaultdict(list)
    parents: dict[str, int] = defaultdict(int)
    controlled: set[str] = set()
    for item in relationships:
        source = str(item.get("source_asset_key") or "")
        target = str(item.get("target_asset_key") or "")
        if source not in key_set or target not in key_set:
            errors.append(f"relationship_unknown_asset:{source}:{target}")
            continue
        if source == target:
            errors.append(f"self_relationship:{source}")
        carrier = item.get("energy_carrier")
        if carrier and carrier not in ENERGY_CARRIERS:
            errors.append(f"relationship_unknown_carrier:{source}:{target}:{carrier}")
        if item.get("relationship_type") == "contains":
            containment[source].append(target)
            parents[target] += 1
        if item.get("relationship_type") == "controls":
            controlled.add(target)
    if any(count > 1 for count in parents.values()):
        errors.append("multiple_containment_parents")
    if _has_cycle(containment):
        errors.append("containment_cycle")
    root_key = str(roots[0].get("asset_key")) if len(roots) == 1 else ""
    reachable = _reachable(containment, root_key) if root_key else set()
    orphaned = sorted(key_set - reachable - ({root_key} if root_key else set()))
    if orphaned:
        errors.append("orphaned_assets:" + ",".join(orphaned))

    point_keys: set[tuple[str, str]] = set()
    for point in points:
        asset_key = str(point.get("asset_key") or "")
        point_code = str(point.get("point_code") or "")
        pair = (asset_key, point_code)
        if asset_key not in key_set:
            errors.append(f"point_unknown_asset:{asset_key}:{point_code}")
        if not point_code:
            errors.append(f"point_code_required:{asset_key}")
        if pair in point_keys:
            errors.append(f"duplicate_point:{asset_key}:{point_code}")
        point_keys.add(pair)
        minimum = point.get("range_min")
        maximum = point.get("range_max")
        if minimum is not None and maximum is not None and float(minimum) > float(maximum):
            errors.append(f"contradictory_point_range:{asset_key}:{point_code}")
        if point.get("writable") and (point.get("category") != "command" or not point.get("command_capability")):
            errors.append(f"writable_point_without_command_capability:{asset_key}:{point_code}")
        if point.get("writable") and asset_key not in controlled:
            warnings.append(f"controlled_asset_has_no_controller_relationship:{asset_key}")

    for constraint in constraints:
        minimum = (constraint.get("parameters") or {}).get("minimum")
        maximum = (constraint.get("parameters") or {}).get("maximum")
        if minimum is not None and maximum is not None and float(minimum) > float(maximum):
            errors.append(f"contradictory_constraint:{constraint.get('asset_key', 'global')}")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "warnings": sorted(set(warnings)),
        "asset_count": len(assets),
        "relationship_count": len(relationships),
        "point_count": len(points),
        "constraint_count": len(constraints),
        "algorithm": ALGORITHMS["topology"],
        "topology_hash": canonical_hash(payload),
    }


def validate_driver_profile(profile: dict[str, Any]) -> dict[str, Any]:
    protocol = str(profile.get("protocol") or "")
    errors: list[str] = []
    security = profile.get("security_profile") or {}
    mappings = profile.get("mappings") or []
    if protocol not in PROTOCOLS:
        errors.append("unsupported_protocol")
    if not security.get("mutual_identity"):
        errors.append("mutual_identity_required")
    if not security.get("certificate_rotation_days"):
        errors.append("certificate_rotation_policy_required")
    if not mappings:
        errors.append("point_mappings_required")
    addresses = [str(item.get("external_address") or "") for item in mappings]
    if "" in addresses or len(addresses) != len(set(addresses)):
        errors.append("mapping_addresses_must_be_unique_and_nonempty")
    for mapping in mappings:
        if mapping.get("writable") and not (mapping.get("command_parameters") or {}).get("allowlist"):
            errors.append(f"write_allowlist_required:{mapping.get('external_address')}")
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "mapping_count": len(mappings),
        "mapping_hash": canonical_hash(mappings),
        "supported_protocol": protocol in PROTOCOLS,
    }


def evaluate_series_quality(samples: list[dict[str, Any]], rules: dict[str, Any] | None = None) -> dict[str, Any]:
    rules = rules or {}
    if not samples:
        return {"algorithm": ALGORITHMS["quality"], "quality_code": "bad", "flags": ["missing"], "events": []}
    ordered = sorted(samples, key=lambda item: _timestamp(item["source_timestamp"]))
    flags: set[str] = set()
    events: list[dict[str, Any]] = []
    expected_seconds = float(rules.get("expected_interval_seconds", 60))
    freeze_count = int(rules.get("freeze_count", 4))
    spike_limit = float(rules.get("spike_limit", math.inf))
    drift_limit = float(rules.get("drift_per_hour_limit", math.inf))
    minimum = rules.get("minimum")
    maximum = rules.get("maximum")
    allow_reverse = bool(rules.get("allow_reverse", True))
    rollover = rules.get("rollover_value")
    multiplier = float(rules.get("multiplier", 1))
    if not math.isfinite(multiplier) or multiplier <= 0:
        flags.add("multiplier_error")
    values = [float(item["value"]) for item in ordered]
    timestamps = [_timestamp(item["source_timestamp"]) for item in ordered]
    seen: set[tuple[datetime, float]] = set()
    frozen_run = 1
    for index, (sample, value, at) in enumerate(zip(ordered, values, timestamps, strict=True)):
        marker = (at, value)
        if marker in seen:
            flags.add("duplicate")
        seen.add(marker)
        received = _timestamp(sample.get("received_at") or sample["source_timestamp"])
        if abs((received - at).total_seconds()) > float(rules.get("clock_skew_seconds", 120)):
            flags.add("clock_skew")
        if minimum is not None and value < float(minimum) or maximum is not None and value > float(maximum):
            flags.add("out_of_range")
        if not allow_reverse and value < 0:
            flags.add("reverse_flow")
        if index == 0:
            continue
        gap = (at - timestamps[index - 1]).total_seconds()
        if gap > expected_seconds * float(rules.get("gap_factor", 1.8)):
            flags.add("missing")
        delta = value - values[index - 1]
        if abs(delta) > spike_limit:
            flags.add("spike")
        if value == values[index - 1]:
            frozen_run += 1
            if frozen_run >= freeze_count:
                flags.add("frozen")
        else:
            frozen_run = 1
        if rules.get("cumulative") and delta < 0:
            if rollover is not None and values[index - 1] > float(rollover) * 0.9 and value < float(rollover) * 0.1:
                flags.add("rollover")
            else:
                flags.add("meter_reset")
    duration_hours = max((timestamps[-1] - timestamps[0]).total_seconds() / 3600, 1e-9)
    if len(values) >= 3 and abs(values[-1] - values[0]) / duration_hours > drift_limit:
        flags.add("drift")
    source_groups: dict[datetime, list[float]] = defaultdict(list)
    for sample, at in zip(ordered, timestamps, strict=True):
        source_groups[at].append(float(sample["value"]))
    disagreement = float(rules.get("source_disagreement", math.inf))
    if any(max(group) - min(group) > disagreement for group in source_groups.values() if len(group) > 1):
        flags.add("source_disagreement")
    for flag in sorted(flags):
        events.append(
            {
                "event_type": flag,
                "severity": "critical" if flag in {"out_of_range", "multiplier_error", "meter_reset"} else "warning",
                "window_start": timestamps[0].isoformat(),
                "window_end": timestamps[-1].isoformat(),
            }
        )
    quality = "bad" if flags & {"out_of_range", "multiplier_error"} else "suspect" if flags else "good"
    return {
        "algorithm": ALGORITHMS["quality"],
        "input_hash": canonical_hash({"samples": samples, "rules": rules}),
        "quality_code": quality,
        "flags": sorted(flags),
        "events": events,
        "sample_count": len(samples),
        "trusted_for_settlement": quality == "good" and not flags,
    }


def reconcile_energy_balance(payload: dict[str, Any]) -> dict[str, Any]:
    inputs = sum(float(item["value"]) for item in payload.get("inputs") or [])
    outputs = sum(float(item["value"]) for item in payload.get("outputs") or [])
    storage_delta = float(payload.get("storage_delta", 0))
    technical_loss = float(payload.get("technical_loss", 0))
    uncertainty = sum(abs(float(item.get("uncertainty", 0))) for item in (payload.get("inputs") or []))
    uncertainty += sum(abs(float(item.get("uncertainty", 0))) for item in (payload.get("outputs") or []))
    residual = inputs - outputs - storage_delta - technical_loss
    tolerance = max(float(payload.get("absolute_tolerance", 0)), uncertainty)
    status = "balanced" if abs(residual) <= tolerance else "warning"
    if any(item.get("quality_code") in {"bad", "estimated"} for item in payload.get("required_meters") or []):
        status = "blocked"
    return {
        "algorithm": ALGORITHMS["reconciliation"],
        "input_hash": canonical_hash(payload),
        "carrier": payload.get("carrier", "electricity"),
        "status": status,
        "input_total": round(inputs, 8),
        "output_total": round(outputs, 8),
        "storage_delta": round(storage_delta, 8),
        "technical_loss": round(technical_loss, 8),
        "residual": round(residual, 8),
        "uncertainty": round(tolerance, 8),
        "settlement_authorized": status == "balanced",
    }


def allocate_charging_power(payload: dict[str, Any]) -> dict[str, Any]:
    sessions = payload.get("sessions") or []
    site_limit = float(payload.get("site_limit_kw", 0))
    phase_limits = {key: float(value) for key, value in (payload.get("phase_limits_kw") or {}).items()}
    now = _timestamp(payload.get("now") or datetime.now(UTC))
    if site_limit < 0 or not sessions:
        raise ValueError("sessions and a non-negative site_limit_kw are required")
    allocations: list[dict[str, Any]] = []
    remaining_site = site_limit
    remaining_phase = dict(phase_limits)
    scored = []
    for item in sessions:
        departure = _timestamp(item["departure_deadline"])
        hours = max((departure - now).total_seconds() / 3600, 1 / 60)
        need = max(0.0, float(item["target_energy_kwh"]) - float(item.get("delivered_energy_kwh", 0)))
        urgency = need / hours
        score = float(item.get("priority", 1)) * 1000 + urgency
        scored.append((score, str(item["session_id"]), item, need, hours))
    for _, session_id, item, need, hours in sorted(scored, reverse=True):
        phase = str(item.get("phase", "ABC"))
        max_power = float(item["maximum_power_kw"])
        minimum = float(item.get("minimum_service_kw", 0))
        required_rate = need / max(hours * float(item.get("efficiency", 0.92)), 1e-9)
        requested = min(max_power, max(minimum, required_rate))
        phase_headroom = remaining_phase.get(phase, remaining_site)
        power = max(0.0, min(requested, remaining_site, phase_headroom))
        shortfall = max(0.0, minimum - power)
        remaining_site -= power
        if phase in remaining_phase:
            remaining_phase[phase] -= power
        direction = str(item.get("direction", "charge"))
        if direction == "discharge" and not (
            item.get("v2g_opt_in") and item.get("vehicle_v2g_capable") and item.get("charger_v2g_capable")
        ):
            power = 0.0
            shortfall = max(shortfall, minimum)
        allocations.append(
            {
                "session_id": session_id,
                "connector_asset_id": item.get("connector_asset_id"),
                "phase": phase,
                "direction": direction,
                "allocated_kw": round(power, 6),
                "minimum_service_shortfall_kw": round(shortfall, 6),
                "departure_risk": shortfall > 1e-9 or power + 1e-9 < min(required_rate, max_power),
            }
        )
    service_shortfall = sum(item["minimum_service_shortfall_kw"] for item in allocations)
    return {
        "algorithm": ALGORITHMS["charging"],
        "input_hash": canonical_hash(payload),
        "allocations": allocations,
        "allocated_kw": round(sum(item["allocated_kw"] for item in allocations), 6),
        "site_headroom_kw": round(remaining_site, 6),
        "service_feasible": service_shortfall <= 1e-9,
        "service_shortfall_kw": round(service_shortfall, 6),
        "execution_authorized": False,
        "control_boundary": "approval, OCPP profile acknowledgement, and observed meter effect required",
    }


def derive_storage_safety_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    required = ("soc", "soh", "rated_energy_kwh", "rated_power_kw", "maximum_temperature_c")
    if any(name not in payload for name in required):
        raise ValueError("storage state is incomplete")
    soc = float(payload["soc"])
    soh = float(payload["soh"])
    rated_energy = float(payload["rated_energy_kwh"])
    rated_power = float(payload["rated_power_kw"])
    temperature = float(payload["maximum_temperature_c"])
    voltage_delta = float(payload.get("cell_voltage_delta_v", 0))
    temperature_delta = float(payload.get("temperature_delta_c", 0))
    throughput = float(payload.get("warranty_throughput_kwh", 0))
    throughput_limit = float(payload.get("warranty_throughput_limit_kwh", math.inf))
    reasons: list[str] = []
    if not 0 <= soc <= 1 or not 0 < soh <= 1.1:
        reasons.append("state_out_of_range")
    if temperature >= float(payload.get("trip_temperature_c", 60)):
        reasons.append("temperature_trip")
    if voltage_delta > float(payload.get("maximum_cell_voltage_delta_v", 0.15)):
        reasons.append("cell_imbalance")
    if temperature_delta > float(payload.get("maximum_temperature_delta_c", 12)):
        reasons.append("thermal_imbalance")
    if not payload.get("cooling_available", True):
        reasons.append("cooling_unavailable")
    if not payload.get("fire_system_normal", True):
        reasons.append("fire_system_abnormal")
    if payload.get("critical_alarms"):
        reasons.append("critical_alarm_active")
    if float(payload.get("trust_score", 1)) < float(payload.get("minimum_trust_score", 0.8)):
        reasons.append("state_untrusted")
    warranty_remaining = max(0.0, throughput_limit - throughput)
    derate = min(1.0, max(0.0, (float(payload.get("derate_temperature_c", 45)) + 15 - temperature) / 15))
    imbalance_derate = max(0.0, 1 - voltage_delta / max(float(payload.get("maximum_cell_voltage_delta_v", 0.15)), 1e-9))
    usable_capacity = max(0.0, rated_energy * soh)
    charge_limit = rated_power * derate * imbalance_derate if soc < float(payload.get("maximum_soc", 0.92)) else 0.0
    discharge_limit = rated_power * derate * imbalance_derate if soc > float(payload.get("minimum_soc", 0.2)) else 0.0
    if warranty_remaining <= 0:
        reasons.append("warranty_throughput_exhausted")
        charge_limit = discharge_limit = 0.0
    hard_block = bool(reasons)
    if hard_block:
        charge_limit = discharge_limit = 0.0
    return {
        "algorithm": ALGORITHMS["storage"],
        "input_hash": canonical_hash(payload),
        "soc": soc,
        "soh": soh,
        "soe_kwh": round(soc * usable_capacity, 6),
        "usable_capacity_kwh": round(usable_capacity, 6),
        "sop_charge_kw": round(charge_limit, 6),
        "sop_discharge_kw": round(discharge_limit, 6),
        "warranty_remaining_throughput_kwh": round(warranty_remaining, 6)
        if math.isfinite(warranty_remaining)
        else None,
        "control_allowed": not hard_block,
        "block_reasons": sorted(set(reasons)),
        "black_start_eligible": not hard_block
        and bool(payload.get("black_start_capable"))
        and soc >= float(payload.get("black_start_minimum_soc", 0.5)),
    }


def optimize_campus_energy(payload: dict[str, Any]) -> dict[str, Any]:
    """Solve a carrier-coupled, equipment-commitment MILP for one planning horizon."""

    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_array

    periods = payload.get("periods") or []
    equipment = payload.get("equipment") or []
    if not periods or not equipment:
        raise ValueError("periods and equipment are required")
    horizon = len(periods)
    if horizon > 192 or len(equipment) > 250:
        raise ValueError("campus optimization input exceeds bounded solve limits")
    carriers = sorted(
        {str(item["output_carrier"]) for item in equipment} | {key for row in periods for key in row.get("demand", {})}
    )
    if set(carriers) - ENERGY_CARRIERS:
        raise ValueError("unsupported energy carrier in campus model")
    n_equipment = len(equipment)
    output_count = horizon * n_equipment
    on_offset = output_count
    variable_count = output_count * 2
    objective = np.zeros(variable_count)
    integrality = np.zeros(variable_count)
    integrality[on_offset:] = 1
    lower = np.zeros(variable_count)
    upper = np.full(variable_count, np.inf)
    rows: list[dict[int, float]] = []
    lows: list[float] = []
    highs: list[float] = []
    carbon_price = float(payload.get("carbon_price_per_kg", 0))
    interval_hours = float(payload.get("interval_minutes", 60)) / 60

    def out_index(step: int, device: int) -> int:
        return step * n_equipment + device

    def on_index(step: int, device: int) -> int:
        return on_offset + step * n_equipment + device

    for step in range(horizon):
        price_map = periods[step].get("prices") or {}
        for device, item in enumerate(equipment):
            output = out_index(step, device)
            on = on_index(step, device)
            maximum = float(item["maximum_output"])
            minimum = float(item.get("minimum_output", 0))
            upper[output] = maximum
            upper[on] = 1
            input_carrier = str(item.get("input_carrier") or "electricity")
            efficiency = max(float(item.get("efficiency", 1)), 1e-6)
            variable_cost = float(item.get("variable_cost_per_output", 0))
            variable_cost += float(price_map.get(input_carrier, 0)) / efficiency
            variable_cost += carbon_price * float(item.get("carbon_kg_per_output", 0))
            objective[output] = variable_cost * interval_hours
            objective[on] = float(item.get("start_cost", 0))
            rows.append({output: 1, on: -maximum})
            lows.append(-np.inf)
            highs.append(0)
            rows.append({output: -1, on: minimum})
            lows.append(-np.inf)
            highs.append(0)
            if step > 0:
                ramp = float(item.get("ramp_limit", maximum))
                previous = out_index(step - 1, device)
                rows.append({output: 1, previous: -1})
                lows.append(-ramp)
                highs.append(ramp)
    for step, period in enumerate(periods):
        for carrier in carriers:
            demand = float((period.get("demand") or {}).get(carrier, 0))
            coefficients: dict[int, float] = {}
            for device, item in enumerate(equipment):
                index = out_index(step, device)
                if item["output_carrier"] == carrier:
                    coefficients[index] = coefficients.get(index, 0) + 1
                if item.get("input_carrier") == carrier and item["output_carrier"] != carrier:
                    efficiency = max(float(item.get("efficiency", 1)), 1e-6)
                    coefficients[index] = coefficients.get(index, 0) - 1 / efficiency
            has_supply = any(item["output_carrier"] == carrier for item in equipment)
            if demand > 0 and not has_supply:
                return _blocked_campus_plan(payload, f"no_supply_equipment:{carrier}")
            rows.append(coefficients)
            lows.append(demand)
            highs.append(np.inf)
    matrix_rows: list[int] = []
    matrix_cols: list[int] = []
    matrix_data: list[float] = []
    for row_index, coefficients in enumerate(rows):
        for column, value in coefficients.items():
            matrix_rows.append(row_index)
            matrix_cols.append(column)
            matrix_data.append(value)
    matrix = coo_array((matrix_data, (matrix_rows, matrix_cols)), shape=(len(rows), variable_count)).tocsr()
    result = milp(
        objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, np.asarray(lows), np.asarray(highs)),
        options={"time_limit": float(payload.get("solver_time_limit_seconds", 30)), "mip_rel_gap": 0.001},
    )
    if not result.success or result.x is None:
        return _blocked_campus_plan(payload, f"solver_infeasible:{result.message}")
    dispatch: list[dict[str, Any]] = []
    balances: list[dict[str, Any]] = []
    for step, period in enumerate(periods):
        supplies: dict[str, float] = defaultdict(float)
        conversion_consumption: dict[str, float] = defaultdict(float)
        for device, item in enumerate(equipment):
            value = float(result.x[out_index(step, device)])
            supplies[str(item["output_carrier"])] += value
            input_carrier = item.get("input_carrier")
            if input_carrier and input_carrier != item["output_carrier"]:
                conversion_consumption[str(input_carrier)] += value / max(float(item.get("efficiency", 1)), 1e-6)
            dispatch.append(
                {
                    "step": step,
                    "at": period.get("at"),
                    "asset_id": item["asset_id"],
                    "output_carrier": item["output_carrier"],
                    "setpoint": round(value, 6),
                    "unit": item.get("unit", "kW"),
                    "committed": bool(round(float(result.x[on_index(step, device)]))),
                }
            )
        for carrier in carriers:
            demand = float((period.get("demand") or {}).get(carrier, 0))
            balances.append(
                {
                    "step": step,
                    "carrier": carrier,
                    "demand": round(demand, 6),
                    "supply": round(supplies[carrier], 6),
                    "conversion_consumption": round(conversion_consumption[carrier], 6),
                    "margin": round(supplies[carrier] - conversion_consumption[carrier] - demand, 6),
                }
            )
    return {
        "algorithm": ALGORITHMS["campus"],
        "input_hash": canonical_hash(payload),
        "status": "completed",
        "timescale": payload.get("timescale", "day_ahead"),
        "objective_value": round(float(result.fun), 6),
        "solver_status": int(result.status),
        "mip_gap": round(float(getattr(result, "mip_gap", 0) or 0), 8),
        "dispatch": dispatch,
        "balances": balances,
        "hard_constraints_satisfied": all(item["margin"] >= -1e-6 for item in balances),
        "service_shortfall": [],
        "execution_authorized": False,
        "control_boundary": "supervisory setpoints require approval, local interlocks, equipment acknowledgement, and observed effect",
    }


def fit_energy_baseline(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("observations") or []
    covariates = list(payload.get("covariates") or [])
    if len(rows) < max(8, len(covariates) + 3):
        raise ValueError("insufficient baseline observations")
    y = np.asarray([float(item["energy"]) for item in rows], dtype=float)
    x = np.asarray([[1.0, *[float(item["covariates"].get(name, 0)) for name in covariates]] for item in rows])
    if np.any(~np.isfinite(x)) or np.any(~np.isfinite(y)):
        raise ValueError("baseline observations must be finite")
    coefficients, *_ = np.linalg.lstsq(x, y, rcond=None)
    prediction = x @ coefficients
    residual = y - prediction
    rmse = float(np.sqrt(np.mean(residual**2)))
    mean_y = float(np.mean(y))
    denominator = float(np.sum((y - mean_y) ** 2))
    r2 = 1 - float(np.sum(residual**2)) / denominator if denominator > 1e-12 else 0.0
    cv_rmse = rmse / max(abs(mean_y), 1e-9)
    passed = r2 >= float(payload.get("minimum_r2", 0.5)) and cv_rmse <= float(payload.get("maximum_cv_rmse", 0.3))
    coefficient_map = {"intercept": round(float(coefficients[0]), 9)}
    coefficient_map.update({name: round(float(coefficients[index + 1]), 9) for index, name in enumerate(covariates)})
    return {
        "algorithm": ALGORITHMS["baseline"],
        "input_hash": canonical_hash(payload),
        "status": "validated" if passed else "rejected",
        "coefficients": coefficient_map,
        "metrics": {
            "sample_count": len(rows),
            "r2": round(r2, 8),
            "rmse": round(rmse, 8),
            "cv_rmse": round(cv_rmse, 8),
        },
        "uncertainty": {"p90_absolute": round(1.645 * float(np.std(residual, ddof=min(1, len(residual) - 1))), 8)},
        "applicability": {
            name: {
                "minimum": min(float(item["covariates"].get(name, 0)) for item in rows),
                "maximum": max(float(item["covariates"].get(name, 0)) for item in rows),
            }
            for name in covariates
        },
        "evidence_grade": "observational",
    }


def evaluate_enpi(payload: dict[str, Any]) -> dict[str, Any]:
    numerator = float(payload["energy"])
    denominator = payload.get("denominator")
    value = numerator if denominator is None else numerator / max(float(denominator), 1e-12)
    target = payload.get("target")
    return {
        "value": round(value, 8),
        "unit": payload["unit"],
        "target": target,
        "on_target": target is None or value <= float(target),
        "significant_energy_use": bool(payload.get("significant_energy_use")),
    }


def reconstruct_utility_bill(payload: dict[str, Any]) -> dict[str, Any]:
    intervals = payload.get("intervals") or []
    if not intervals:
        raise ValueError("billing intervals are required")
    if any(item.get("quality_code") == "bad" for item in intervals):
        return {
            "algorithm": ALGORITHMS["billing"],
            "input_hash": canonical_hash(payload),
            "status": "blocked",
            "blockers": ["bad_revenue_meter_quality"],
            "financially_usable": False,
        }
    energy_charge = sum(float(item["energy_kwh"]) * float(item["price_per_kwh"]) for item in intervals)
    peak = max(float(item.get("demand_kw", 0)) for item in intervals)
    demand_charge = peak * float(payload.get("demand_charge_per_kw", 0))
    capacity = float(payload.get("contract_capacity_kw", 0))
    capacity_penalty = max(0.0, peak - capacity) * float(payload.get("capacity_exceedance_per_kw", 0))
    fixed = float(payload.get("fixed_charge", 0))
    taxes = (energy_charge + demand_charge + capacity_penalty + fixed) * float(payload.get("tax_rate", 0))
    total = energy_charge + demand_charge + capacity_penalty + fixed + taxes
    invoiced = payload.get("invoiced_amount")
    discrepancy = total - float(invoiced) if invoiced is not None else None
    return {
        "algorithm": ALGORITHMS["billing"],
        "input_hash": canonical_hash(payload),
        "status": "review"
        if discrepancy is not None and abs(discrepancy) > float(payload.get("discrepancy_tolerance", 1))
        else "reconstructed",
        "energy_charge": round(energy_charge, 2),
        "demand_charge": round(demand_charge, 2),
        "capacity_penalty": round(capacity_penalty, 2),
        "fixed_charge": round(fixed, 2),
        "taxes": round(taxes, 2),
        "reconstructed_amount": round(total, 2),
        "discrepancy_amount": round(discrepancy, 2) if discrepancy is not None else None,
        "peak_demand_kw": round(peak, 6),
        "contract_capacity_recommendation_kw": round(peak * float(payload.get("capacity_margin", 1.05)), 3),
        "financially_usable": True,
    }


def allocate_energy_cost(payload: dict[str, Any]) -> dict[str, Any]:
    total = float(payload["total_amount"])
    recipients = payload.get("recipients") or []
    if not recipients:
        raise ValueError("allocation recipients are required")
    weights = [max(0.0, float(item.get("meter_value", item.get("weight", 0)))) for item in recipients]
    denominator = sum(weights)
    if denominator <= 0:
        raise ValueError("allocation weights must sum to a positive value")
    allocations = []
    assigned = 0.0
    for index, (item, weight) in enumerate(zip(recipients, weights, strict=True)):
        amount = total - assigned if index == len(recipients) - 1 else round(total * weight / denominator, 2)
        assigned += amount
        allocations.append(
            {
                "recipient_id": item["recipient_id"],
                "weight": weight,
                "share": round(weight / denominator, 8),
                "amount": round(amount, 2),
            }
        )
    return {
        "algorithm": ALGORITHMS["allocation"],
        "input_hash": canonical_hash(payload),
        "total_amount": round(total, 2),
        "allocations": allocations,
        "reconciled": abs(sum(item["amount"] for item in allocations) - total) < 0.01,
    }


def calculate_mv_result(payload: dict[str, Any]) -> dict[str, Any]:
    baseline = float(payload["adjusted_baseline_energy"])
    actual = float(payload["actual_energy"])
    savings = baseline - actual
    service = payload.get("service_impact") or {}
    service_ok = all(bool(value) for value in service.values()) if service else True
    uncertainty = abs(float(payload.get("uncertainty_energy", 0)))
    price = float(payload.get("blended_price_per_unit", 0))
    factor = float(payload.get("carbon_factor_kg_per_unit", 0))
    data_grade = str(payload.get("meter_quality", "operational"))
    grade = "revenue_grade" if data_grade == "revenue_grade" and service_ok else "observational"
    if not service_ok:
        grade = "engineering"
    return {
        "algorithm": ALGORITHMS["mv"],
        "input_hash": canonical_hash(payload),
        "baseline_energy": round(baseline, 6),
        "actual_energy": round(actual, 6),
        "adjusted_savings_energy": round(savings, 6),
        "avoided_cost": round(savings * price, 2),
        "avoided_carbon_kg": round(savings * factor, 6),
        "uncertainty": {
            "absolute_energy": uncertainty,
            "lower": round(savings - uncertainty, 6),
            "upper": round(savings + uncertainty, 6),
        },
        "service_impact": service,
        "service_preserved": service_ok,
        "evidence_grade": grade,
        "claim_authorized": savings - uncertainty > 0 and service_ok and grade == "revenue_grade",
    }


def calculate_carbon(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("activities") or []
    factors = payload.get("factors") or {}
    location = 0.0
    market = 0.0
    details = []
    for item in rows:
        carrier = str(item["carrier"])
        quantity = float(item["quantity"])
        factor = factors.get(carrier) or {}
        location_value = quantity * float(factor.get("location_based", 0))
        market_value = quantity * float(factor.get("market_based", factor.get("location_based", 0)))
        location += location_value
        market += market_value
        details.append(
            {
                "carrier": carrier,
                "quantity": quantity,
                "location_based_kg": round(location_value, 6),
                "market_based_kg": round(market_value, 6),
            }
        )
    return {
        "algorithm": ALGORITHMS["carbon"],
        "input_hash": canonical_hash(payload),
        "location_based_kg": round(location, 6),
        "market_based_kg": round(market, 6),
        "details": details,
        "renewable_instruments": payload.get("renewable_instruments") or [],
    }


def platform_capabilities() -> dict[str, Any]:
    return {
        "phases": {"P0": "code_complete", "P1": "code_complete", "P2": "code_complete", "P3": "code_complete"},
        "energy_carriers": sorted(ENERGY_CARRIERS),
        "asset_types": sorted(ASSET_TYPES),
        "protocols": sorted(PROTOCOLS),
        "algorithms": ALGORITHMS,
        "evidence_boundary": "software code-complete; vendor conformance and field qualification remain external",
    }


def _blocked_campus_plan(payload: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "algorithm": ALGORITHMS["campus"],
        "input_hash": canonical_hash(payload),
        "status": "safe_fallback",
        "timescale": payload.get("timescale", "day_ahead"),
        "dispatch": [],
        "balances": [],
        "hard_constraints_satisfied": False,
        "blockers": [reason],
        "execution_authorized": False,
        "fallback": "hold_last_safe_setpoints_and_preserve_local_control",
    }


def _timestamp(value: Any) -> datetime:
    result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=UTC)
    return result.astimezone(UTC)


def _has_cycle(graph: dict[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node: str) -> bool:
        if node in visiting:
            return True
        if node in visited:
            return False
        visiting.add(node)
        if any(visit(child) for child in graph.get(node, [])):
            return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(visit(node) for node in graph)


def _reachable(graph: dict[str, list[str]], root: str) -> set[str]:
    result: set[str] = set()
    stack = list(graph.get(root, []))
    while stack:
        node = stack.pop()
        if node in result:
            continue
        result.add(node)
        stack.extend(graph.get(node, []))
    return result
