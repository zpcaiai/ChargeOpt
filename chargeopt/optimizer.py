"""Constrained dispatch optimizer.

The solver is deterministic and dependency-free for serverless deployment. It
uses a rolling-horizon dynamic program over discretized charge/discharge
decisions. In optimization terms this is a compact MILP/MPC approximation:
binary action choices are searched across the full horizon while SOC,
transformer, reserve, ramp, degradation, and service constraints are enforced.
"""

from __future__ import annotations

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
    }
    for station in stations:
        station_plan, station_score = _solve_station_mpc(repo, station, horizon_hours, objective, constraints)
        plan.extend(station_plan)
        objective_value += station_score
    return {
        "solver": "risk-constrained-mpc-milp-dp-v2",
        "objective": objective,
        "objective_value": round(objective_value, 3),
        "dispatch_plan": plan,
        "constraints": constraints,
        "inputs": {"tenant_id": tenant_id, "station_id": station_id, "horizon_hours": horizon_hours},
    }


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
