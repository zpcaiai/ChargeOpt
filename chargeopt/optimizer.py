"""Constrained dispatch optimizer.

The solver is deterministic and dependency-free for serverless deployment. It
uses a discrete mixed-integer search over charge/discharge/hold decisions,
honoring SOC, transformer, tariff, and VPP reserve constraints.
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
        "soc_min": 0.22,
        "soc_max": 0.92,
        "transformer_max_ratio": 0.92,
        "vpp_reserve_soc": 0.30,
        "time_step_hours": 1,
    }
    for station in stations:
        tariff = repo.tariff_for(station)
        points = repo.station_points(station.id)
        soc = points[-1].storage_soc
        for row in forecast_load(repo, station.id)[:horizon_hours]:
            hour = int(row["label"].split(":", 1)[0])
            price = tariff.price_at(hour)
            best = None
            for action, sign in (("charge", 1), ("hold", 0), ("discharge", -1)):
                power = _candidate_power(station.storage_power_kw, sign)
                next_soc = soc + (
                    power / station.storage_capacity_kwh * 0.9
                    if power > 0
                    else power / station.storage_capacity_kwh / 0.9
                )
                grid_kw = max(0, row["grid_kw"] + max(power, 0) + min(power, 0))
                feasible = (
                    constraints["soc_min"] <= next_soc <= constraints["soc_max"]
                    and grid_kw <= station.transformer_capacity_kw * constraints["transformer_max_ratio"]
                )
                if not feasible:
                    continue
                score = _score_candidate(objective, action, power, grid_kw, price, row["queue_length"])
                if best is None or score > best["score"]:
                    best = {
                        "station_id": station.id,
                        "label": row["label"],
                        "action": action,
                        "power_kw": round(power, 1),
                        "projected_grid_kw": round(grid_kw, 1),
                        "projected_soc": round(next_soc * 100, 1),
                        "score": score,
                    }
            if best is None:
                best = {
                    "station_id": station.id,
                    "label": row["label"],
                    "action": "hold",
                    "power_kw": 0.0,
                    "projected_grid_kw": row["grid_kw"],
                    "projected_soc": round(soc * 100, 1),
                    "score": -999.0,
                }
            soc = best["projected_soc"] / 100
            objective_value += float(best.pop("score"))
            plan.append(best)
    return {
        "solver": "discrete-milp-search-v1",
        "objective": objective,
        "objective_value": round(objective_value, 3),
        "dispatch_plan": plan,
        "constraints": constraints,
        "inputs": {"tenant_id": tenant_id, "station_id": station_id, "horizon_hours": horizon_hours},
    }


def _candidate_power(max_power_kw: float, sign: int) -> float:
    if sign == 0:
        return 0.0
    return sign * max_power_kw * 0.65


def _score_candidate(
    objective: str, action: str, power_kw: float, grid_kw: float, price: float, queue_length: int
) -> float:
    cost_score = -grid_kw * price / 100
    revenue_score = -queue_length * 2 if action == "discharge" else queue_length * 0.2
    reserve_score = -abs(power_kw) * 0.01
    if objective == "cost":
        return cost_score + reserve_score
    if objective == "revenue":
        return revenue_score + reserve_score
    return cost_score + revenue_score + reserve_score
