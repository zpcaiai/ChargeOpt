"""Grid-aware EMS primitives for flexible charging and secure co-optimization.

The functions in this module are pure and replayable. They produce planning
evidence only; dispatch approval and edge receipts remain separate controls.
"""

from __future__ import annotations

import copy
import math
from typing import Any

import numpy as np

from .advanced_ems import (
    canonical_hash,
    marginal_degradation_cost_per_kwh,
    project_three_phase_distflow,
)

GRID_EMS_ALGORITHMS = {
    "flexibility": "deadline-aware-aggregate-flexibility-polytope-v1",
    "secure_dispatch": "security-constrained-energy-reserve-carbon-cvar-milp-v1",
    "network_security": "n-minus-one-three-phase-lindistflow-screening-v1",
    "degradation": "rainflow-electrothermal-lifetime-evidence-v1",
}


def aggregate_ev_flexibility(sessions: list[dict[str, Any]], *, horizon: int, interval_minutes: int) -> dict[str, Any]:
    """Build exact per-session cumulative energy bounds and an aggregate envelope."""

    if not sessions:
        raise ValueError("At least one charging session is required.")
    if not 1 <= horizon <= 672 or interval_minutes not in {5, 15, 30, 60}:
        raise ValueError("Invalid flexibility horizon or interval_minutes.")
    dt = interval_minutes / 60
    normalized = [_normalize_session(item, horizon) for item in sessions]
    session_ids = [item["session_id"] for item in normalized]
    if len(session_ids) != len(set(session_ids)):
        raise ValueError("session_id values must be unique.")

    aggregate_min = np.zeros(horizon + 1)
    aggregate_max = np.zeros(horizon + 1)
    instantaneous_max = np.zeros(horizon)
    baseline = np.zeros(horizon)
    details: list[dict[str, Any]] = []
    feasible = True
    violations: list[dict[str, Any]] = []
    for session in normalized:
        remaining = session["remaining_energy_kwh"]
        efficiency = session["efficiency"]
        max_power = session["max_charge_kw"]
        arrival = session["arrival_step"]
        departure = session["departure_step"]
        slot_energy = max_power * efficiency * dt
        available = departure - arrival
        deliverable = slot_energy * available
        session_feasible = deliverable + 1e-9 >= remaining
        feasible = feasible and session_feasible
        if not session_feasible:
            violations.append(
                {
                    "session_id": session["session_id"],
                    "code": "deadline_energy_infeasible",
                    "shortfall_kwh": round(remaining - deliverable, 6),
                }
            )
        lower: list[float] = []
        upper: list[float] = []
        for boundary in range(horizon + 1):
            elapsed = max(0, min(boundary, departure) - arrival)
            future = max(0, departure - max(boundary, arrival))
            maximum = min(remaining, elapsed * slot_energy)
            minimum = max(0.0, remaining - future * slot_energy)
            lower.append(minimum)
            upper.append(maximum)
            aggregate_min[boundary] += minimum
            aggregate_max[boundary] += maximum
        for step in range(arrival, departure):
            instantaneous_max[step] += max_power
        energy_left = min(remaining, deliverable)
        latest_schedule = np.zeros(horizon)
        for step in range(departure - 1, arrival - 1, -1):
            grid_kw = min(max_power, energy_left / max(1e-12, efficiency * dt))
            latest_schedule[step] = grid_kw
            energy_left -= grid_kw * efficiency * dt
        baseline += latest_schedule
        details.append(
            {
                **session,
                "feasible": session_feasible,
                "maximum_deliverable_kwh": round(deliverable, 6),
                "cumulative_minimum_kwh": [round(value, 6) for value in lower],
                "cumulative_maximum_kwh": [round(value, 6) for value in upper],
                "latest_feasible_schedule_kw": [round(float(value), 6) for value in latest_schedule],
            }
        )
    rows = []
    for step in range(horizon):
        rows.append(
            {
                "step": step,
                "minimum_cumulative_energy_kwh": round(float(aggregate_min[step + 1]), 6),
                "maximum_cumulative_energy_kwh": round(float(aggregate_max[step + 1]), 6),
                "maximum_charge_kw": round(float(instantaneous_max[step]), 6),
                "baseline_charge_kw": round(float(baseline[step]), 6),
                "upward_reserve_kw": round(float(baseline[step]), 6),
                "downward_reserve_kw": round(float(instantaneous_max[step] - baseline[step]), 6),
            }
        )
    payload = {"sessions": sessions, "horizon": horizon, "interval_minutes": interval_minutes}
    return {
        "algorithm": GRID_EMS_ALGORITHMS["flexibility"],
        "input_hash": canonical_hash(payload),
        "feasible": feasible,
        "violations": violations,
        "interval_minutes": interval_minutes,
        "session_count": len(sessions),
        "rows": rows,
        "sessions": details,
        "execution_authorized": False,
        "control_boundary": "aggregate flexibility evidence only",
    }


def estimate_battery_degradation(
    soc_series: list[float],
    *,
    interval_minutes: int,
    storage_capacity_kwh: float,
    replacement_cost: float,
    soh: float = 0.95,
    temperature_c: float | list[float] = 25,
) -> dict[str, Any]:
    """Estimate cycle and calendar life consumption from a SOC trajectory."""

    if len(soc_series) < 3 or interval_minutes not in {5, 15, 30, 60}:
        raise ValueError("At least three SOC points and a supported interval are required.")
    soc = np.asarray(soc_series, dtype=float)
    if np.any(~np.isfinite(soc)) or np.any((soc < 0) | (soc > 1)):
        raise ValueError("soc_series must contain finite values in [0, 1].")
    if storage_capacity_kwh <= 0 or replacement_cost < 0 or not 0 < soh <= 1:
        raise ValueError("Battery capacity, replacement cost, and SOH are invalid.")
    if isinstance(temperature_c, list):
        temperatures = np.asarray(temperature_c, dtype=float)
        if len(temperatures) != len(soc):
            raise ValueError("temperature_c must be scalar or match soc_series.")
    else:
        temperatures = np.full(len(soc), float(temperature_c))
    if np.any(~np.isfinite(temperatures)) or np.any((temperatures < -40) | (temperatures > 100)):
        raise ValueError("temperature_c contains an invalid value.")

    cycles = _rainflow_cycles(soc.tolist())
    mean_temperature = float(np.mean(temperatures))
    thermal_factor = math.exp((mean_temperature - 25) / 28)
    health_factor = 1 + 2.5 * max(0.0, 0.85 - soh)
    nominal_cycle_life = 5500.0
    cycle_life_loss = sum(count * max(depth, 1e-6) ** 1.7 for depth, _mean, count in cycles)
    cycle_life_loss *= thermal_factor * health_factor / nominal_cycle_life
    elapsed_days = (len(soc) - 1) * interval_minutes / (60 * 24)
    mean_soc = float(np.mean(soc))
    calendar_factor = math.exp((mean_temperature - 25) / 35) * (1 + 1.8 * max(0.0, mean_soc - 0.55))
    calendar_life_loss = elapsed_days / (365 * 15) * calendar_factor
    total_life_loss = cycle_life_loss + calendar_life_loss
    throughput_kwh = float(np.sum(np.abs(np.diff(soc))) * storage_capacity_kwh)
    payload = {
        "soc_series": soc_series,
        "interval_minutes": interval_minutes,
        "storage_capacity_kwh": storage_capacity_kwh,
        "replacement_cost": replacement_cost,
        "soh": soh,
        "temperature_c": temperature_c,
    }
    return {
        "algorithm": GRID_EMS_ALGORITHMS["degradation"],
        "input_hash": canonical_hash(payload),
        "evidence_scope": "engineering_lifetime_model_not_warranty_certification",
        "cycle_count": round(sum(item[2] for item in cycles), 6),
        "equivalent_full_cycles": round(sum(item[0] * item[2] for item in cycles), 6),
        "rainflow_cycles": [
            {"depth": round(depth, 7), "mean_soc": round(mean, 7), "count": count} for depth, mean, count in cycles
        ],
        "throughput_kwh": round(throughput_kwh, 6),
        "cycle_life_loss": round(cycle_life_loss, 10),
        "calendar_life_loss": round(calendar_life_loss, 10),
        "total_life_loss": round(total_life_loss, 10),
        "estimated_degradation_cost": round(total_life_loss * replacement_cost, 6),
        "assumptions": {
            "nominal_cycle_life": nominal_cycle_life,
            "depth_exponent": 1.7,
            "mean_temperature_c": round(mean_temperature, 5),
            "thermal_factor": round(thermal_factor, 8),
            "health_factor": round(health_factor, 8),
        },
        "execution_authorized": False,
    }


def assess_n_minus_one_security(
    network: dict[str, Any], intervals: list[dict[str, Any]], contingencies: list[dict[str, Any]]
) -> dict[str, Any]:
    """Screen every interval against transformer derates and radial line outages."""

    if not intervals or not contingencies:
        raise ValueError("intervals and contingencies are required.")
    if len(intervals) > 672 or len(contingencies) > 256:
        raise ValueError("Security assessment size exceeds the replay limit.")
    assessments: list[dict[str, Any]] = []
    secure = True
    for interval_index, interval in enumerate(intervals):
        proposals = interval.get("proposals") or []
        if not proposals:
            raise ValueError("Every security interval requires proposals.")
        for contingency in contingencies:
            result = _assess_contingency(network, proposals, contingency)
            qualified = bool(result["qualified"])
            secure = secure and qualified
            assessments.append(
                {
                    "interval": interval_index,
                    "at": interval.get("at"),
                    "contingency_id": contingency.get("id"),
                    **result,
                }
            )
    payload = {"network": network, "intervals": intervals, "contingencies": contingencies}
    return {
        "algorithm": GRID_EMS_ALGORITHMS["network_security"],
        "input_hash": canonical_hash(payload),
        "criterion": "N-1 screening across every supplied credible contingency",
        "n_minus_one_secure": secure,
        "qualified": secure,
        "ac_certified": False,
        "certificate_scope": "radial phase-decoupled screening; external unbalanced AC protection study required",
        "assessments": assessments,
        "execution_authorized": False,
        "control_boundary": "security certificate only",
    }


def solve_secure_rolling_dispatch(
    station: Any,
    forecast: dict[str, Any],
    sessions: list[dict[str, Any]],
    *,
    prices: list[float],
    initial_soc: float,
    soh: float = 0.95,
    temperature_c: float = 25,
    carbon_intensity_kg_per_kwh: list[float] | None = None,
    carbon_price_per_kg: float = 0.0,
    reserve_up_prices: list[float] | None = None,
    reserve_down_prices: list[float] | None = None,
    reserve_duration_minutes: int = 15,
    contingencies: list[dict[str, Any]] | None = None,
    risk_alpha: float = 0.95,
    risk_weight: float = 0.25,
    demand_charge_per_kw: float = 0.0,
    reserve_soc: float = 0.3,
    allow_service_restoration: bool = True,
    solver_time_limit_seconds: int = 30,
) -> dict[str, Any]:
    """Co-optimize battery, deadline EV charging, reserve, carbon, and CVaR."""

    rows = forecast.get("rows") or []
    horizon = len(rows)
    if not rows or horizon > 168:
        raise ValueError("Forecast horizon must contain between 1 and 168 intervals.")
    quality = forecast.get("quality_gate") or {}
    if quality and not quality.get("qualified_for_dispatch", False):
        raise RuntimeError(f"Forecast quality gate blocked dispatch: {quality.get('reason', 'unqualified')}.")
    interval_minutes = int(forecast.get("interval_minutes", 0))
    flexibility = aggregate_ev_flexibility(sessions, horizon=horizon, interval_minutes=interval_minutes)
    normalized = [_normalize_session(item, horizon) for item in sessions]
    if len(normalized) > 250:
        raise ValueError("At most 250 sessions can be optimized in one run.")
    arrays = {
        "prices": _horizon_array(prices, horizon, "prices", minimum=0),
        "carbon": _horizon_array(carbon_intensity_kg_per_kwh or [0.0] * horizon, horizon, "carbon", minimum=0),
        "reserve_up": _horizon_array(reserve_up_prices or [0.0] * horizon, horizon, "reserve_up_prices", minimum=0),
        "reserve_down": _horizon_array(
            reserve_down_prices or [0.0] * horizon, horizon, "reserve_down_prices", minimum=0
        ),
    }
    if carbon_price_per_kg < 0 or demand_charge_per_kw < 0:
        raise ValueError("Carbon and demand prices must be non-negative.")
    scenarios = np.asarray(forecast.get("scenarios_kw") or [], dtype=float)
    if scenarios.ndim != 2 or scenarios.shape[1] != horizon or len(scenarios) < 1:
        raise ValueError("Forecast scenarios do not match the horizon.")
    scenarios = scenarios[:24] + float(forecast.get("calibration", {}).get("wasserstein_radius_kw", 0))
    if np.any(~np.isfinite(scenarios)) or np.any(scenarios < 0):
        raise ValueError("Forecast scenarios must be finite and non-negative.")
    if not 0 < initial_soc <= 1 or not 0 < soh <= 1 or not 0.5 < risk_alpha < 1 or risk_weight < 0:
        raise ValueError("SOC, SOH, or CVaR configuration is invalid.")
    if reserve_duration_minutes <= 0 or reserve_duration_minutes > 240:
        raise ValueError("reserve_duration_minutes must be in (0, 240].")

    capacity_ratios = _contingency_capacity_ratios(contingencies or [], horizon)
    result = _solve_dispatch_model(
        station,
        forecast,
        normalized,
        scenarios,
        arrays,
        initial_soc=initial_soc,
        soh=soh,
        temperature_c=temperature_c,
        carbon_price_per_kg=carbon_price_per_kg,
        reserve_duration_minutes=reserve_duration_minutes,
        capacity_ratios=capacity_ratios,
        risk_alpha=risk_alpha,
        risk_weight=risk_weight,
        demand_charge_per_kw=demand_charge_per_kw,
        reserve_soc=reserve_soc,
        allow_shortfall=False,
        solver_time_limit_seconds=solver_time_limit_seconds,
    )
    restoration_used = False
    if result is None and allow_service_restoration:
        restoration_used = True
        result = _solve_dispatch_model(
            station,
            forecast,
            normalized,
            scenarios,
            arrays,
            initial_soc=initial_soc,
            soh=soh,
            temperature_c=temperature_c,
            carbon_price_per_kg=carbon_price_per_kg,
            reserve_duration_minutes=reserve_duration_minutes,
            capacity_ratios=capacity_ratios,
            risk_alpha=risk_alpha,
            risk_weight=risk_weight,
            demand_charge_per_kw=demand_charge_per_kw,
            reserve_soc=reserve_soc,
            allow_shortfall=True,
            solver_time_limit_seconds=solver_time_limit_seconds,
        )
    if result is None:
        raise RuntimeError("Security-constrained dispatch is infeasible; safety constraints were not relaxed.")
    shortfall = sum(item["shortfall_kwh"] for item in result["session_service"])
    input_payload = {
        "station_id": station.id,
        "forecast_hash": forecast.get("input_hash"),
        "sessions": sessions,
        "prices": prices,
        "initial_soc": initial_soc,
        "soh": soh,
        "temperature_c": temperature_c,
        "carbon_intensity_kg_per_kwh": carbon_intensity_kg_per_kwh,
        "carbon_price_per_kg": carbon_price_per_kg,
        "reserve_up_prices": reserve_up_prices,
        "reserve_down_prices": reserve_down_prices,
        "reserve_duration_minutes": reserve_duration_minutes,
        "contingencies": contingencies,
        "risk_alpha": risk_alpha,
        "risk_weight": risk_weight,
        "demand_charge_per_kw": demand_charge_per_kw,
        "reserve_soc": reserve_soc,
    }
    return {
        "algorithm": GRID_EMS_ALGORITHMS["secure_dispatch"],
        "input_hash": canonical_hash(input_payload),
        "exact": True,
        "status": result.pop("solver_status"),
        "service_feasible": shortfall <= 1e-6,
        "restoration_used": restoration_used,
        "safety_constraints_relaxed": False,
        "capacity_ratios": [round(value, 6) for value in capacity_ratios],
        "flexibility_evidence": flexibility,
        **result,
        "execution_authorized": False,
        "control_boundary": "recommendation_only; approval, market gate, and edge receipt remain mandatory",
    }


def _solve_dispatch_model(
    station: Any,
    forecast: dict[str, Any],
    sessions: list[dict[str, Any]],
    scenarios: np.ndarray,
    arrays: dict[str, np.ndarray],
    **options: Any,
) -> dict[str, Any] | None:
    from scipy.optimize import Bounds, LinearConstraint, milp
    from scipy.sparse import coo_array

    horizon = scenarios.shape[1]
    scenario_count = scenarios.shape[0]
    session_count = len(sessions)
    dt = float(forecast["interval_minutes"]) / 60
    initial_soc = float(options["initial_soc"])
    soh = float(options["soh"])
    temperature_c = float(options["temperature_c"])
    risk_alpha = float(options["risk_alpha"])
    risk_weight = float(options["risk_weight"])
    demand_charge = float(options["demand_charge_per_kw"])
    reserve_soc = float(options["reserve_soc"])
    allow_shortfall = bool(options["allow_shortfall"])
    capacity_ratios = np.asarray(options["capacity_ratios"], dtype=float)
    carbon_price = float(options["carbon_price_per_kg"])
    reserve_duration = float(options["reserve_duration_minutes"]) / 60

    cursor = 0
    charge = np.arange(cursor, cursor + horizon)
    cursor += horizon
    discharge = np.arange(cursor, cursor + horizon)
    cursor += horizon
    soc = np.arange(cursor, cursor + horizon)
    cursor += horizon
    charge_mode = np.arange(cursor, cursor + horizon)
    cursor += horizon
    discharge_mode = np.arange(cursor, cursor + horizon)
    cursor += horizon
    ev = np.arange(cursor, cursor + session_count * horizon).reshape(session_count, horizon)
    cursor += session_count * horizon
    shortfall = np.arange(cursor, cursor + session_count)
    cursor += session_count
    reserve_up = np.arange(cursor, cursor + horizon)
    cursor += horizon
    reserve_down = np.arange(cursor, cursor + horizon)
    cursor += horizon
    grid = np.arange(cursor, cursor + scenario_count * horizon).reshape(scenario_count, horizon)
    cursor += scenario_count * horizon
    peak = np.arange(cursor, cursor + scenario_count)
    cursor += scenario_count
    zeta = cursor
    cursor += 1
    tail = np.arange(cursor, cursor + scenario_count)
    cursor += scenario_count
    size = cursor

    objective = np.zeros(size)
    energy_and_carbon = arrays["prices"] + arrays["carbon"] * carbon_price
    for scenario_index in range(scenario_count):
        objective[grid[scenario_index]] = energy_and_carbon * dt / scenario_count
        objective[peak[scenario_index]] = demand_charge / scenario_count
    replacement = max(0.0, float(station.storage_capacity_kwh) * 1450)
    degradation = marginal_degradation_cost_per_kwh(
        storage_capacity_kwh=float(station.storage_capacity_kwh),
        replacement_cost=replacement,
        soc=initial_soc,
        soh=soh,
        temperature_c=temperature_c,
    )
    objective[charge] = degradation * dt
    objective[discharge] = degradation * dt
    objective[reserve_up] = -arrays["reserve_up"] * dt
    objective[reserve_down] = -arrays["reserve_down"] * dt
    objective[shortfall] = 100_000.0
    objective[zeta] = risk_weight
    objective[tail] = risk_weight / ((1 - risk_alpha) * scenario_count)

    lower = np.zeros(size)
    upper = np.full(size, np.inf)
    lower[zeta] = -np.inf
    capacity = max(1.0, float(station.storage_capacity_kwh) * soh)
    power_limit = max(0.0, float(station.storage_power_kw))
    transformer = float(station.transformer_capacity_kw) * 0.92 * capacity_ratios
    lower[soc] = 0.2
    upper[soc] = 0.92
    upper[charge] = power_limit
    upper[discharge] = power_limit
    upper[charge_mode] = 1
    upper[discharge_mode] = 1
    upper[reserve_up] = transformer
    upper[reserve_down] = transformer
    for scenario_index in range(scenario_count):
        upper[grid[scenario_index]] = transformer
        upper[peak[scenario_index]] = float(np.max(transformer))
    for index, session in enumerate(sessions):
        upper[shortfall[index]] = session["remaining_energy_kwh"] if allow_shortfall else 0
        for step in range(horizon):
            upper[ev[index, step]] = (
                session["max_charge_kw"] if session["arrival_step"] <= step < session["departure_step"] else 0
            )
    integrality = np.zeros(size)
    integrality[charge_mode] = 1
    integrality[discharge_mode] = 1

    matrix_rows: list[dict[int, float]] = []
    constraint_low: list[float] = []
    constraint_high: list[float] = []

    def add(coefficients: dict[int, float], low_value: float, high_value: float) -> None:
        matrix_rows.append({int(key): float(value) for key, value in coefficients.items() if value})
        constraint_low.append(low_value)
        constraint_high.append(high_value)

    eta_charge = max(0.82, min(0.97, 0.95 - max(0.0, temperature_c - 35) * 0.003))
    eta_discharge = max(0.82, eta_charge - 0.01)
    for step in range(horizon):
        coefficients = {
            int(soc[step]): 1,
            int(charge[step]): -eta_charge * dt / capacity,
            int(discharge[step]): dt / (eta_discharge * capacity),
        }
        if step:
            coefficients[int(soc[step - 1])] = -1
            add(coefficients, 0, 0)
        else:
            add(coefficients, initial_soc, initial_soc)
        add({int(charge[step]): 1, int(charge_mode[step]): -power_limit}, -np.inf, 0)
        add({int(discharge[step]): 1, int(discharge_mode[step]): -power_limit}, -np.inf, 0)
        add({int(charge_mode[step]): 1, int(discharge_mode[step]): 1}, -np.inf, 1)
        ev_step = {int(ev[index, step]): -1 for index in range(session_count)}
        add(
            {
                int(reserve_up[step]): 1,
                int(discharge[step]): 1,
                int(charge[step]): -1,
                **ev_step,
            },
            -np.inf,
            power_limit,
        )
        max_ev = sum(float(upper[ev[index, step]]) for index in range(session_count))
        add(
            {
                int(reserve_down[step]): 1,
                int(charge[step]): 1,
                int(discharge[step]): -1,
                **{int(ev[index, step]): 1 for index in range(session_count)},
            },
            -np.inf,
            power_limit + max_ev,
        )
        add(
            {int(reserve_up[step]): reserve_duration, int(soc[step]): -capacity * eta_discharge},
            -np.inf,
            -0.2 * capacity * eta_discharge,
        )
        add(
            {int(reserve_down[step]): reserve_duration, int(soc[step]): capacity / eta_charge},
            -np.inf,
            0.92 * capacity / eta_charge,
        )
        for scenario_index in range(scenario_count):
            balance = {
                int(grid[scenario_index, step]): 1,
                int(charge[step]): -1,
                int(discharge[step]): 1,
                **{int(ev[index, step]): -1 for index in range(session_count)},
            }
            add(balance, float(scenarios[scenario_index, step]), float(scenarios[scenario_index, step]))
            add({int(grid[scenario_index, step]): 1, int(reserve_up[step]): -1}, 0, np.inf)
            add(
                {int(grid[scenario_index, step]): 1, int(reserve_down[step]): 1},
                -np.inf,
                float(transformer[step]),
            )
            add({int(grid[scenario_index, step]): 1, int(peak[scenario_index]): -1}, -np.inf, 0)
    add({int(soc[-1]): 1}, reserve_soc, np.inf)
    for index, session in enumerate(sessions):
        coefficients = {int(ev[index, step]): session["efficiency"] * dt for step in range(horizon)}
        coefficients[int(shortfall[index])] = 1
        add(coefficients, session["remaining_energy_kwh"], session["remaining_energy_kwh"])
    negative_common_cost = {
        **{int(charge[step]): -degradation * dt for step in range(horizon)},
        **{int(discharge[step]): -degradation * dt for step in range(horizon)},
        **{int(reserve_up[step]): float(arrays["reserve_up"][step] * dt) for step in range(horizon)},
        **{int(reserve_down[step]): float(arrays["reserve_down"][step] * dt) for step in range(horizon)},
    }
    for scenario_index in range(scenario_count):
        coefficients = {int(tail[scenario_index]): 1, zeta: 1, **negative_common_cost}
        coefficients[int(peak[scenario_index])] = -demand_charge
        for step in range(horizon):
            coefficients[int(grid[scenario_index, step])] = -float(energy_and_carbon[step] * dt)
        add(coefficients, 0, np.inf)

    row_indices: list[int] = []
    column_indices: list[int] = []
    values: list[float] = []
    for row_index, coefficients in enumerate(matrix_rows):
        for column_index, value in coefficients.items():
            row_indices.append(row_index)
            column_indices.append(column_index)
            values.append(value)
    matrix = coo_array((values, (row_indices, column_indices)), shape=(len(matrix_rows), size)).tocsr()
    solved = milp(
        objective,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(matrix, np.asarray(constraint_low), np.asarray(constraint_high)),
        options={
            "time_limit": int(options["solver_time_limit_seconds"]),
            "mip_rel_gap": 0.001,
            "presolve": True,
        },
    )
    if not solved.success or solved.x is None:
        return None
    solution = solved.x
    plan = []
    for step, row in enumerate(forecast["rows"]):
        ev_kw = float(np.sum(solution[ev[:, step]]))
        expected_grid = float(np.mean(solution[grid[:, step]]))
        plan.append(
            {
                "step": step,
                "at": row.get("at"),
                "battery_power_kw": round(float(solution[charge[step]] - solution[discharge[step]]), 6),
                "ev_charge_kw": round(ev_kw, 6),
                "projected_soc": round(float(solution[soc[step]]), 7),
                "reserve_up_kw": round(float(solution[reserve_up[step]]), 6),
                "reserve_down_kw": round(float(solution[reserve_down[step]]), 6),
                "expected_grid_kw": round(expected_grid, 6),
                "worst_case_grid_kw": round(float(np.max(solution[grid[:, step]])), 6),
                "secure_transformer_limit_kw": round(float(transformer[step]), 6),
                "transformer_margin_kw": round(float(transformer[step] - np.max(solution[grid[:, step]])), 6),
            }
        )
    service = []
    for index, session in enumerate(sessions):
        delivered = session["remaining_energy_kwh"] - float(solution[shortfall[index]])
        service.append(
            {
                "session_id": session["session_id"],
                "required_energy_kwh": round(session["remaining_energy_kwh"], 6),
                "delivered_energy_kwh": round(delivered, 6),
                "shortfall_kwh": round(float(solution[shortfall[index]]), 6),
                "departure_step": session["departure_step"],
            }
        )
    scenario_costs = []
    for scenario_index in range(scenario_count):
        energy_cost = float(np.dot(energy_and_carbon * dt, solution[grid[scenario_index]]))
        demand_cost = demand_charge * float(solution[peak[scenario_index]])
        degradation_cost = degradation * dt * float(np.sum(solution[charge] + solution[discharge]))
        reserve_revenue = float(
            np.dot(arrays["reserve_up"] * dt, solution[reserve_up])
            + np.dot(arrays["reserve_down"] * dt, solution[reserve_down])
        )
        scenario_costs.append(energy_cost + demand_cost + degradation_cost - reserve_revenue)
    var_cost = float(np.quantile(scenario_costs, risk_alpha))
    tail_costs = [value for value in scenario_costs if value >= var_cost - 1e-9]
    return {
        "solver_status": "optimal" if int(solved.status) == 0 else "feasible",
        "objective_value": round(float(solved.fun), 6),
        "mip_gap": round(float(getattr(solved, "mip_gap", 0) or 0), 8),
        "node_count": int(getattr(solved, "mip_node_count", 0) or 0),
        "risk": {
            "alpha": risk_alpha,
            "expected_cost": round(float(np.mean(scenario_costs)), 6),
            "var_cost": round(var_cost, 6),
            "cvar_cost": round(float(np.mean(tail_costs)), 6),
            "scenario_count": scenario_count,
        },
        "economics": {
            "carbon_price_per_kg": carbon_price,
            "marginal_degradation_cost_per_kwh": round(degradation, 9),
            "reserve_revenue": round(
                float(
                    np.dot(arrays["reserve_up"] * dt, solution[reserve_up])
                    + np.dot(arrays["reserve_down"] * dt, solution[reserve_down])
                ),
                6,
            ),
        },
        "session_service": service,
        "dispatch_plan": plan,
    }


def _normalize_session(item: dict[str, Any], horizon: int) -> dict[str, Any]:
    try:
        session_id = str(item["session_id"])
        arrival = int(item.get("arrival_step", 0))
        departure = int(item["departure_step"])
        required = float(item["required_energy_kwh"])
        delivered = float(item.get("delivered_energy_kwh", 0))
        max_charge = float(item["max_charge_kw"])
        efficiency = float(item.get("efficiency", 0.94))
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Every session requires valid identity, deadline, energy, and power fields.") from exc
    if not session_id or not 0 <= arrival < departure <= horizon:
        raise ValueError("Session arrival/departure steps are outside the optimization horizon.")
    if required < 0 or delivered < 0 or delivered > required or max_charge <= 0 or not 0.8 <= efficiency <= 1:
        raise ValueError("Session energy, max_charge_kw, or efficiency is invalid.")
    return {
        "session_id": session_id,
        "arrival_step": arrival,
        "departure_step": departure,
        "required_energy_kwh": required,
        "delivered_energy_kwh": delivered,
        "remaining_energy_kwh": required - delivered,
        "max_charge_kw": max_charge,
        "efficiency": efficiency,
    }


def _horizon_array(values: list[float], horizon: int, name: str, *, minimum: float) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    if len(array) != horizon or np.any(~np.isfinite(array)) or np.any(array < minimum):
        raise ValueError(f"{name} must contain {horizon} finite values greater than or equal to {minimum}.")
    return array


def _contingency_capacity_ratios(contingencies: list[dict[str, Any]], horizon: int) -> list[float]:
    ratios = np.ones(horizon)
    for item in contingencies:
        if not item.get("id") or "available_capacity_ratio" not in item:
            raise ValueError("Every dispatch contingency requires an id and available_capacity_ratio.")
        ratio = float(item.get("available_capacity_ratio", 1))
        start = int(item.get("start_step", 0))
        end = int(item.get("end_step", horizon))
        if not 0 < ratio <= 1 or not 0 <= start < end <= horizon:
            raise ValueError("Contingency capacity ratio or active interval is invalid.")
        ratios[start:end] = np.minimum(ratios[start:end], ratio)
    return ratios.tolist()


def _rainflow_cycles(values: list[float]) -> list[tuple[float, float, float]]:
    turning = [values[0]]
    for value in values[1:]:
        if value != turning[-1]:
            turning.append(value)
    if len(turning) < 2:
        return []
    extrema = [turning[0]]
    for index in range(1, len(turning) - 1):
        if (turning[index] - turning[index - 1]) * (turning[index + 1] - turning[index]) <= 0:
            extrema.append(turning[index])
    extrema.append(turning[-1])
    stack: list[float] = []
    cycles: list[tuple[float, float, float]] = []
    for point in extrema:
        stack.append(point)
        while len(stack) >= 3:
            previous = abs(stack[-2] - stack[-3])
            current = abs(stack[-1] - stack[-2])
            if current < previous:
                break
            mean = (stack[-3] + stack[-2]) / 2
            if len(stack) == 3:
                cycles.append((previous, mean, 0.5))
                stack.pop(0)
            else:
                cycles.append((previous, mean, 1.0))
                last = stack[-1]
                del stack[-3:]
                stack.append(last)
    for index in range(len(stack) - 1):
        cycles.append((abs(stack[index + 1] - stack[index]), (stack[index + 1] + stack[index]) / 2, 0.5))
    return [(depth, mean, count) for depth, mean, count in cycles if depth > 0]


def _assess_contingency(
    network: dict[str, Any], proposals: list[dict[str, Any]], contingency: dict[str, Any]
) -> dict[str, Any]:
    contingency_type = contingency.get("type")
    changed = copy.deepcopy(network)
    unserved_kw = 0.0
    evaluated = copy.deepcopy(proposals)
    if contingency_type == "transformer_derate":
        ratio = float(contingency.get("available_capacity_ratio", 0))
        if not 0 < ratio <= 1:
            raise ValueError("Transformer contingency requires available_capacity_ratio in (0, 1].")
        changed["transformer_limit_kw"] = float(changed.get("transformer_limit_kw", math.inf)) * ratio
    elif contingency_type == "line_derate":
        line_id = str(contingency.get("line_id") or "")
        ratio = float(contingency.get("available_capacity_ratio", 0))
        matches = [line for line in changed.get("lines", []) if str(line.get("id")) == line_id]
        if len(matches) != 1 or not 0 < ratio <= 1:
            raise ValueError("Line derate requires one known line_id and a ratio in (0, 1].")
        matches[0]["limit_kw"] = float(matches[0]["limit_kw"]) * ratio
    elif contingency_type == "line_outage":
        line_id = str(contingency.get("line_id") or "")
        lines = changed.get("lines", [])
        matches = [line for line in lines if str(line.get("id")) == line_id]
        if len(matches) != 1:
            raise ValueError("Line outage requires one known line_id.")
        disconnected = {str(matches[0]["to_bus"])}
        changed_flag = True
        while changed_flag:
            changed_flag = False
            for line in lines:
                if str(line["from_bus"]) in disconnected and str(line["to_bus"]) not in disconnected:
                    disconnected.add(str(line["to_bus"]))
                    changed_flag = True
        affected = [item for item in evaluated if str(item.get("bus")) in disconnected]
        unserved_kw = sum(float(item["proposed_kw"]) for item in affected)
        evaluated = [item for item in evaluated if str(item.get("bus")) not in disconnected]
        changed["lines"] = [
            line
            for line in lines
            if str(line.get("from_bus")) not in disconnected and str(line.get("to_bus")) not in disconnected
        ]
    else:
        raise ValueError("Unsupported contingency type.")
    projection = project_three_phase_distflow(changed, evaluated) if evaluated else None
    curtailed = sum(item["curtailed_kw"] for item in projection["allocations"]) if projection else 0.0
    qualified = unserved_kw <= 1e-6 and curtailed <= 1e-6 and bool(projection is None or projection["qualified"])
    return {
        "qualified": qualified,
        "unserved_kw": round(unserved_kw, 6),
        "curtailed_kw": round(float(curtailed), 6),
        "projection": projection,
    }
