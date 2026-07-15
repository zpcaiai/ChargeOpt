"""Mixed-integer rolling-horizon dispatch optimizer with a safe local fallback."""

from __future__ import annotations

import os
from typing import Any

from .analytics import forecast_load
from .data import Repository


def solve_dispatch_optimization(
    repo: Repository,
    tenant_id: str | None,
    station_id: str | None,
    horizon_hours: int,
    objective: str,
) -> dict[str, Any]:
    stations = [station for station in repo.stations if station_id in {None, station.id}]
    if tenant_id is not None:
        stations = [station for station in stations if station.tenant_id == tenant_id]
    if not stations:
        raise KeyError(f"Unknown station_id: {station_id}")

    plan: list[dict[str, Any]] = []
    objective_value = 0.0
    constraints = {
        "soc_min": 0.24,
        "soc_max": 0.92,
        "transformer_max_ratio": 0.92,
        "vpp_reserve_soc": 0.32,
        "max_hourly_ramp_ratio": 0.72,
        "soc_grid_resolution": 0.01,
        "time_step_hours": 1,
        "charge_efficiency": 0.9,
        "discharge_efficiency": 0.9,
        "mip_relative_gap": 0.001,
        "solver_time_limit_seconds": 20,
    }
    solver = "scipy-highs-milp-mpc-v1"
    solver_evidence: list[dict[str, Any]] = []
    for station in stations:
        try:
            station_plan, station_score, evidence = _solve_station_milp(
                repo, station, horizon_hours, objective, constraints
            )
        except ImportError as exc:
            if _exact_solver_required():
                raise RuntimeError("The production MILP solver is unavailable; dispatch is blocked.") from exc
            solver = "discrete-mpc-dp-fallback-v2"
            station_plan, station_score = _solve_station_mpc(repo, station, horizon_hours, objective, constraints)
            evidence = {"station_id": station.id, "exact": False, "reason": "scipy_unavailable"}
        plan.extend(station_plan)
        objective_value += station_score
        solver_evidence.append(evidence)
    return {
        "solver": solver,
        "objective": objective,
        "objective_value": round(objective_value, 3),
        "dispatch_plan": plan,
        "constraints": constraints | {"solver_evidence": solver_evidence},
        "inputs": {"tenant_id": tenant_id, "station_id": station_id, "horizon_hours": horizon_hours},
    }


def _exact_solver_required() -> bool:
    configured = os.environ.get("CHARGEOPT_REQUIRE_EXACT_SOLVER")
    if configured is not None:
        return configured.lower() in {"1", "true", "yes", "on"}
    return os.environ.get("ENVIRONMENT") == "production" or os.environ.get("VERCEL_ENV") == "production"


def _solve_station_milp(repo, station, horizon_hours: int, objective: str, constraints: dict[str, Any]):
    try:
        import numpy as np
        from scipy.optimize import Bounds, LinearConstraint, milp
    except ImportError:
        raise

    tariff = repo.tariff_for(station)
    points = repo.station_points(station.id)
    initial_soc = float(points[-1].storage_soc)
    base_forecast = forecast_load(repo, station.id)
    forecast = [base_forecast[index % len(base_forecast)] for index in range(horizon_hours)]
    n = len(forecast)
    charge = list(range(0, n))
    discharge = list(range(n, 2 * n))
    soc = list(range(2 * n, 3 * n))
    grid = list(range(3 * n, 4 * n))
    charge_mode = list(range(4 * n, 5 * n))
    discharge_mode = list(range(5 * n, 6 * n))
    peak = 6 * n
    size = peak + 1

    c = np.zeros(size)
    energy_weight = 1.0 if objective == "cost" else 0.25 if objective == "revenue" else 0.8
    demand_weight = 1.0 if objective == "cost" else 0.35 if objective == "revenue" else 0.8
    for step, row in enumerate(forecast):
        hour = int(row["label"].split(":", 1)[0])
        price = float(tariff.price_at(hour))
        c[grid[step]] = price * energy_weight
        c[charge[step]] = 0.055
        c[discharge[step]] = 0.055 + (0.27 * max(0, int(row["queue_length"]) - 2))
        c[charge_mode[step]] = 1e-5
        c[discharge_mode[step]] = 1e-5
    c[peak] = float(tariff.demand_charge_per_kw_month) / 30 * demand_weight

    lb = np.zeros(size)
    ub = np.full(size, np.inf)
    power_limit = max(0.0, float(station.storage_power_kw))
    transformer_limit = float(station.transformer_capacity_kw) * constraints["transformer_max_ratio"]
    for step in range(n):
        ub[charge[step]] = power_limit
        ub[discharge[step]] = power_limit
        lb[soc[step]] = constraints["soc_min"]
        ub[soc[step]] = constraints["soc_max"]
        ub[grid[step]] = transformer_limit
        ub[charge_mode[step]] = 1
        ub[discharge_mode[step]] = 1
    ub[peak] = transformer_limit
    integrality = np.zeros(size)
    integrality[charge_mode] = 1
    integrality[discharge_mode] = 1

    rows: list[Any] = []
    lower: list[float] = []
    upper: list[float] = []

    def add(coefficients: dict[int, float], low: float, high: float) -> None:
        row = np.zeros(size)
        for index, value in coefficients.items():
            row[index] = value
        rows.append(row)
        lower.append(low)
        upper.append(high)

    capacity = max(1.0, float(station.storage_capacity_kwh))
    eta_charge = constraints["charge_efficiency"]
    eta_discharge = constraints["discharge_efficiency"]
    ramp = power_limit * constraints["max_hourly_ramp_ratio"]
    for step, row in enumerate(forecast):
        base_grid = float(row["grid_kw"])
        add({grid[step]: 1, charge[step]: -1, discharge[step]: 1}, base_grid, base_grid)
        soc_coefficients = {
            soc[step]: 1,
            charge[step]: -(eta_charge / capacity),
            discharge[step]: 1 / (eta_discharge * capacity),
        }
        if step == 0:
            add(soc_coefficients, initial_soc, initial_soc)
        else:
            soc_coefficients[soc[step - 1]] = -1
            add(soc_coefficients, 0, 0)
        add({charge[step]: 1, charge_mode[step]: -power_limit}, -np.inf, 0)
        add({discharge[step]: 1, discharge_mode[step]: -power_limit}, -np.inf, 0)
        add({charge_mode[step]: 1, discharge_mode[step]: 1}, -np.inf, 1)
        ramp_coefficients = {charge[step]: 1, discharge[step]: -1}
        if step > 0:
            ramp_coefficients[charge[step - 1]] = -1
            ramp_coefficients[discharge[step - 1]] = 1
        add(ramp_coefficients, -ramp, ramp)
        add({grid[step]: 1, peak: -1}, -np.inf, 0)
    add({soc[-1]: 1}, constraints["vpp_reserve_soc"], np.inf)

    result = milp(
        c,
        integrality=integrality,
        bounds=Bounds(lb, ub),
        constraints=LinearConstraint(np.asarray(rows), np.asarray(lower), np.asarray(upper)),
        options={
            "time_limit": constraints["solver_time_limit_seconds"],
            "mip_rel_gap": constraints["mip_relative_gap"],
            "presolve": True,
        },
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"MILP dispatch is infeasible or unsolved for {station.id}: {result.message}")

    solution = result.x
    plan = []
    for step, row in enumerate(forecast):
        power = float(solution[charge[step]] - solution[discharge[step]])
        projected_grid = float(solution[grid[step]])
        hour = int(row["label"].split(":", 1)[0])
        price = float(tariff.price_at(hour))
        plan.append(
            {
                "station_id": station.id,
                "label": row["label"],
                "action": _action(power),
                "power_kw": round(power, 1),
                "projected_grid_kw": round(projected_grid, 1),
                "projected_soc": round(float(solution[soc[step]]) * 100, 1),
                "constraint_margin_kw": round(transformer_limit - projected_grid, 1),
                "risk_adjusted_cost": round(float(c[grid[step]] * projected_grid + 0.055 * abs(power)), 2),
                "shadow_price": round(_shadow_price(projected_grid, price, station, tariff), 3),
            }
        )
    evidence = {
        "station_id": station.id,
        "exact": True,
        "solver_status": int(result.status),
        "mip_gap": round(float(getattr(result, "mip_gap", 0.0) or 0.0), 8),
        "node_count": int(getattr(result, "mip_node_count", 0) or 0),
        "objective_cost": round(float(result.fun), 5),
    }
    return plan, -float(result.fun), evidence


def _solve_station_mpc(repo, station, horizon_hours: int, objective: str, constraints: dict[str, Any]):
    tariff = repo.tariff_for(station)
    points = repo.station_points(station.id)
    initial_soc = points[-1].storage_soc
    forecast = forecast_load(repo, station.id)[:horizon_hours]
    action_ratios = (-0.72, -0.5, -0.25, 0.0, 0.25, 0.5, 0.72)
    start_key = _soc_key(initial_soc)
    states: dict[int, dict[str, Any]] = {start_key: {"cost": 0.0, "path": [], "prev_power": 0.0}}

    for step, row in enumerate(forecast):
        next_states: dict[int, dict[str, Any]] = {}
        hour = int(row["label"].split(":", 1)[0])
        price = tariff.price_at(hour)
        for soc_key, state in states.items():
            soc = soc_key / 100
            for ratio in action_ratios:
                power = ratio * station.storage_power_kw
                if abs(power - state["prev_power"]) > station.storage_power_kw * constraints["max_hourly_ramp_ratio"]:
                    continue
                next_soc = _next_soc(soc, power, station.storage_capacity_kwh)
                grid_kw = max(0.0, row["grid_kw"] + power)
                if not _feasible(next_soc, grid_kw, station, constraints, step, len(forecast)):
                    continue
                action = _action(power)
                step_cost = _transition_cost(objective, power, grid_kw, price, row["queue_length"], station, tariff)
                total_cost = state["cost"] + step_cost
                key = _soc_key(next_soc)
                row_payload = {
                    "station_id": station.id,
                    "label": row["label"],
                    "action": action,
                    "power_kw": round(power, 1),
                    "projected_grid_kw": round(grid_kw, 1),
                    "projected_soc": round(next_soc * 100, 1),
                    "constraint_margin_kw": round(
                        station.transformer_capacity_kw * constraints["transformer_max_ratio"] - grid_kw, 1
                    ),
                    "risk_adjusted_cost": round(step_cost, 2),
                    "shadow_price": round(_shadow_price(grid_kw, price, station, tariff), 3),
                }
                if key not in next_states or total_cost < next_states[key]["cost"]:
                    next_states[key] = {
                        "cost": total_cost,
                        "path": [*state["path"], row_payload],
                        "prev_power": power,
                    }
        if not next_states:
            states = {
                _soc_key(initial_soc): {
                    "cost": state["cost"],
                    "path": [
                        *state["path"],
                        {
                            "station_id": station.id,
                            "label": row["label"],
                            "action": "hold",
                            "power_kw": 0.0,
                            "projected_grid_kw": row["grid_kw"],
                            "projected_soc": round(initial_soc * 100, 1),
                            "constraint_margin_kw": round(station.transformer_capacity_kw - row["grid_kw"], 1),
                            "risk_adjusted_cost": 9999.0,
                            "shadow_price": round(price, 3),
                        },
                    ],
                    "prev_power": 0.0,
                }
                for state in states.values()
            }
        else:
            states = _prune_states(next_states, keep=32)
    best = min(states.values(), key=lambda item: item["cost"])
    return best["path"], -float(best["cost"])


def _soc_key(soc: float) -> int:
    return round(soc * 100)


def _next_soc(soc: float, power_kw: float, capacity_kwh: float) -> float:
    if capacity_kwh <= 0:
        return soc
    if power_kw >= 0:
        return soc + power_kw / capacity_kwh * 0.9
    return soc + power_kw / capacity_kwh / 0.9


def _feasible(next_soc: float, grid_kw: float, station, constraints: dict[str, Any], step: int, horizon: int) -> bool:
    terminal_reserve = constraints["vpp_reserve_soc"] if step >= max(0, horizon - 4) else constraints["soc_min"]
    return (
        constraints["soc_min"] <= next_soc <= constraints["soc_max"]
        and next_soc >= terminal_reserve
        and grid_kw <= station.transformer_capacity_kw * constraints["transformer_max_ratio"]
    )


def _prune_states(states: dict[int, dict[str, Any]], keep: int) -> dict[int, dict[str, Any]]:
    ranked = sorted(states.items(), key=lambda item: item[1]["cost"])
    return dict(ranked[:keep])


def _action(power_kw: float) -> str:
    if power_kw > 1:
        return "charge"
    if power_kw < -1:
        return "discharge"
    return "hold"


def _transition_cost(
    objective: str, power_kw: float, grid_kw: float, price: float, queue_length: int, station, tariff
) -> float:
    energy_cost = grid_kw * price
    demand_risk = max(0.0, grid_kw - station.transformer_capacity_kw * 0.78) * tariff.demand_charge_per_kw_month / 30
    degradation_cost = abs(power_kw) * 0.055
    reserve_penalty = max(0.0, -power_kw) * 0.015 if queue_length >= 5 else 0.0
    service_penalty = max(0, queue_length - 2) * 18 if power_kw < 0 else max(0, queue_length - 5) * 8
    if objective == "cost":
        return energy_cost + demand_risk + degradation_cost + reserve_penalty
    if objective == "revenue":
        return service_penalty + degradation_cost + demand_risk * 0.35
    return energy_cost + demand_risk + degradation_cost + reserve_penalty + service_penalty * 0.45


def _shadow_price(grid_kw: float, price: float, station, tariff) -> float:
    congestion = max(0.0, grid_kw - station.transformer_capacity_kw * 0.78)
    return price + congestion / max(1, station.transformer_capacity_kw) * tariff.demand_charge_per_kw_month / 30
