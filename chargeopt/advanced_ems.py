"""Risk-aware, multi-timescale energy-management algorithms.

The module is deliberately pure: model fitting, scenario generation, dispatch,
network certification, and policy evaluation can be replayed without database
or device access. Field execution remains the responsibility of the audited
dispatch and edge-receipt control plane.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

ALGORITHMS = {
    "forecast": "adaptive-conformal-ensemble-v2",
    "scenario": "correlated-block-bootstrap-v1",
    "dispatch": "wasserstein-radius-robust-cvar-milp-mpc-v1",
    "network": "three-phase-lindistflow-projection-v1",
    "coordination": "bounded-consensus-admm-v1",
    "offline_policy": "conservative-linear-fitted-q-v1",
    "safety_projection": "physical-action-projection-v1",
}


def canonical_hash(payload: Any) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


class FoundationForecastClient:
    """Validated HTTPS adapter for a separately deployed TS foundation model."""

    def __init__(self, endpoint: str, token: str, model: str, timeout_seconds: int = 20):
        if not endpoint.startswith("https://") and not (
            endpoint.startswith("http://127.0.0.1") or endpoint.startswith("http://localhost")
        ):
            raise ValueError("Foundation-model endpoint must use HTTPS outside localhost.")
        if not token or not model:
            raise ValueError("Foundation-model token and model are required.")
        self.endpoint = endpoint
        self.token = token
        self.model = model
        self.timeout_seconds = timeout_seconds

    @classmethod
    def from_environment(cls) -> FoundationForecastClient | None:
        endpoint = os.getenv("CHARGEOPT_TSF_ENDPOINT")
        token = os.getenv("CHARGEOPT_TSF_TOKEN")
        model = os.getenv("CHARGEOPT_TSF_MODEL", "chronos-2")
        if not endpoint and not token:
            return None
        if not endpoint or not token:
            raise RuntimeError("Both CHARGEOPT_TSF_ENDPOINT and CHARGEOPT_TSF_TOKEN are required.")
        return cls(endpoint, token, model)

    def forecast(self, history: list[float], horizon: int, interval_minutes: int) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "history": history,
            "horizon": horizon,
            "interval_minutes": interval_minutes,
            "request_hash": canonical_hash([history, horizon, interval_minutes, self.model]),
        }
        request = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload, separators=(",", ":")).encode(),
            method="POST",
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                parsed = json.loads(response.read().decode())
            values = [float(value) for value in parsed.get("p50", parsed.get("predictions", []))]
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise RuntimeError(f"Foundation forecast service failed: {exc}") from exc
        if len(values) != horizon or not all(math.isfinite(value) and value >= 0 for value in values):
            raise RuntimeError("Foundation forecast response has an invalid horizon or value.")
        return {"name": self.model, "p50": values, "model_version": parsed.get("model_version", self.model)}


def calibrated_ensemble_forecast(
    history: list[float],
    *,
    horizon: int,
    interval_minutes: int = 15,
    seasonal_period: int | None = None,
    coverage: float = 0.8,
    scenario_count: int = 24,
    seed: int = 17,
    start_at: datetime | None = None,
    external_predictions: dict[str, list[float]] | None = None,
    external_metadata: dict[str, dict[str, Any]] | None = None,
    evidence_class: str = "replay",
) -> dict[str, Any]:
    """Adaptive local ensemble with split-conformal intervals and scenarios."""

    values = np.asarray(history, dtype=float)
    if len(values) < 12:
        raise ValueError("At least 12 ordered observations are required.")
    if horizon < 1 or horizon > 672:
        raise ValueError("horizon must be between 1 and 672 intervals.")
    if interval_minutes not in {5, 15, 30, 60}:
        raise ValueError("interval_minutes must be one of 5, 15, 30, 60.")
    if not math.isclose(coverage, 0.8):
        raise ValueError("P10/P90 output requires coverage=0.8.")
    if not np.all(np.isfinite(values)) or np.any(values < 0):
        raise ValueError("history must contain finite non-negative values.")
    if evidence_class not in {"synthetic", "replay", "observed", "shadow"}:
        raise ValueError("Unsupported evidence_class.")
    period = seasonal_period or max(2, 24 * 60 // interval_minutes)
    period = max(2, min(period, len(values)))
    local_predictions = _local_predictions(values, horizon, period)
    validation = min(max(4, len(values) // 5), 24, len(values) - 8)
    train = values[:-validation]
    actual = values[-validation:]
    validation_predictions = _local_predictions(train, validation, min(period, max(2, len(train) // 2)))
    errors = {
        name: float(np.mean(np.abs(np.asarray(prediction) - actual)))
        for name, prediction in validation_predictions.items()
    }
    scale = max(1e-6, float(np.median(list(errors.values()))))
    inverse_errors = {name: 1 / max(scale * 0.05, error) for name, error in errors.items()}
    predictions = dict(local_predictions)
    external_names: list[str] = []
    for name, prediction in (external_predictions or {}).items():
        candidate = np.asarray(prediction, dtype=float)
        if len(candidate) != horizon or np.any(~np.isfinite(candidate)) or np.any(candidate < 0):
            raise ValueError(f"External forecast {name!r} is invalid.")
        predictions[name] = candidate.tolist()
        external_names.append(name)
        inverse_errors[name] = 1 / scale
    weight_total = sum(inverse_errors.values())
    weights = {name: value / weight_total for name, value in inverse_errors.items()}
    p50 = np.zeros(horizon)
    for name, prediction in predictions.items():
        p50 += weights[name] * np.asarray(prediction)

    calibration_residuals = _rolling_residuals(values, period, weights)
    alpha = (1 - coverage) / 2
    lower_error = float(np.quantile(calibration_residuals, alpha, method="higher"))
    upper_error = float(np.quantile(calibration_residuals, 1 - alpha, method="higher"))
    absolute_radius = float(np.quantile(np.abs(calibration_residuals), coverage, method="higher"))
    p10 = np.maximum(0, p50 + lower_error)
    p90 = np.maximum(p50, p50 + upper_error)
    scenarios = _block_bootstrap_scenarios(p50, calibration_residuals, scenario_count, seed)
    empirical_coverage = float(np.mean((calibration_residuals >= lower_error) & (calibration_residuals <= upper_error)))
    wasserstein_radius = absolute_radius * math.sqrt(math.log(2 / 0.05) / max(2, 2 * len(calibration_residuals)))
    start = (start_at or datetime.now(UTC)).astimezone(UTC)
    complete_cycles = len(values) / period
    qualified_for_dispatch = complete_cycles >= 2 or horizon <= max(4, period // 4)
    rows = []
    for index in range(horizon):
        rows.append(
            {
                "at": (start + timedelta(minutes=index * interval_minutes)).isoformat(),
                "p10_grid_kw": round(float(p10[index]), 5),
                "p50_grid_kw": round(float(p50[index]), 5),
                "p90_grid_kw": round(float(p90[index]), 5),
            }
        )
    return {
        "algorithm": ALGORITHMS["forecast"],
        "scenario_algorithm": ALGORITHMS["scenario"],
        "evidence_class": evidence_class,
        "input_hash": canonical_hash(
            {
                "history": history,
                "horizon": horizon,
                "interval_minutes": interval_minutes,
                "seasonal_period": seasonal_period,
                "coverage": coverage,
                "scenario_count": scenario_count,
                "seed": seed,
                "external_predictions": external_predictions,
                "external_metadata": external_metadata,
                "evidence_class": evidence_class,
            }
        ),
        "interval_minutes": interval_minutes,
        "coverage_target": coverage,
        "calibration": {
            "sample_count": len(calibration_residuals),
            "empirical_coverage": round(empirical_coverage, 6),
            "lower_error_kw": round(lower_error, 6),
            "upper_error_kw": round(upper_error, 6),
            "absolute_radius_kw": round(absolute_radius, 6),
            "wasserstein_radius_kw": round(wasserstein_radius, 6),
        },
        "model_weights": {name: round(weight, 8) for name, weight in weights.items()},
        "validation_mae_kw": {name: round(error, 6) for name, error in errors.items()},
        "external_models": external_names,
        "external_metadata": external_metadata or {},
        "quality_gate": {
            "qualified_for_dispatch": qualified_for_dispatch,
            "complete_seasonal_cycles": round(complete_cycles, 3),
            "reason": None if qualified_for_dispatch else "insufficient_seasonal_history_for_horizon",
        },
        "rows": rows,
        "scenarios_kw": [[round(float(value), 5) for value in row] for row in scenarios],
    }


def _local_predictions(values: np.ndarray, horizon: int, period: int) -> dict[str, list[float]]:
    seasonal_window = values[-min(period, len(values)) :]
    seasonal = [max(0.0, float(seasonal_window[index % len(seasonal_window)])) for index in range(horizon)]
    window = values[-min(len(values), max(12, period * 2)) :]
    low, high = np.quantile(window, [0.05, 0.95])
    robust = np.clip(window, low, high)
    x = np.arange(len(robust), dtype=float)
    design = np.column_stack([np.ones(len(x)), x])
    intercept, slope = np.linalg.lstsq(design, robust, rcond=None)[0]
    current_level = intercept + slope * (len(x) - 1)
    trend = [
        max(0.0, float(current_level + slope * (index + 1) * math.exp(-index / max(4, horizon))))
        for index in range(horizon)
    ]
    time = np.arange(len(values), dtype=float)
    future = np.arange(len(values), len(values) + horizon, dtype=float)
    frequencies = sorted({max(2, period), max(2, period // 2)})

    def matrix(indices: np.ndarray) -> np.ndarray:
        columns = [np.ones(len(indices)), indices / max(1, len(values))]
        for frequency in frequencies:
            columns.extend([np.sin(2 * np.pi * indices / frequency), np.cos(2 * np.pi * indices / frequency)])
        return np.column_stack(columns)

    features = matrix(time)
    ridge = 1e-3 * np.eye(features.shape[1])
    ridge[0, 0] = 0
    coefficients = np.linalg.solve(features.T @ features + ridge, features.T @ values)
    fourier = np.maximum(0, matrix(future) @ coefficients).tolist()
    return {"seasonal": seasonal, "robust_trend": trend, "fourier_ridge": fourier}


def _rolling_residuals(values: np.ndarray, period: int, weights: dict[str, float]) -> np.ndarray:
    start = max(8, len(values) - min(96, max(12, len(values) // 2)))
    residuals = []
    local_names = {"seasonal", "robust_trend", "fourier_ridge"}
    local_weight_total = sum(weight for name, weight in weights.items() if name in local_names) or 1
    for index in range(start, len(values)):
        predictions = _local_predictions(values[:index], 1, min(period, max(2, index // 2)))
        estimate = sum(weights.get(name, 0) * row[0] for name, row in predictions.items()) / local_weight_total
        residuals.append(float(values[index] - estimate))
    if len(residuals) < 4:
        residuals = (values[-4:] - np.median(values[-8:])).tolist()
    return np.asarray(residuals, dtype=float)


def _block_bootstrap_scenarios(
    p50: np.ndarray, residuals: np.ndarray, scenario_count: int, seed: int
) -> list[list[float]]:
    if not 4 <= scenario_count <= 256:
        raise ValueError("scenario_count must be between 4 and 256.")
    rng = np.random.default_rng(seed)
    block = min(4, len(residuals))
    scenarios: list[list[float]] = []
    for _ in range(scenario_count):
        sampled: list[float] = []
        while len(sampled) < len(p50):
            start = int(rng.integers(0, max(1, len(residuals) - block + 1)))
            sampled.extend(residuals[start : start + block].tolist())
        scenarios.append(np.maximum(0, p50 + np.asarray(sampled[: len(p50)])).tolist())
    return scenarios


def marginal_degradation_cost_per_kwh(
    *,
    storage_capacity_kwh: float,
    replacement_cost: float,
    soc: float,
    soh: float,
    temperature_c: float,
) -> float:
    if (
        storage_capacity_kwh <= 0
        or replacement_cost < 0
        or not 0 <= soc <= 1
        or not 0 < soh <= 1
        or not all(math.isfinite(value) for value in (storage_capacity_kwh, replacement_cost, soc, soh, temperature_c))
    ):
        raise ValueError("storage capacity and replacement cost must be valid.")
    soc_stress = 1 + 2.4 * (soc - 0.5) ** 2
    thermal_stress = math.exp(max(0.0, temperature_c - 25) / 28)
    health_stress = 1 + 2.5 * max(0.0, 0.85 - soh)
    nominal_cycle_life = 5500
    fade_per_throughput_kwh = 1 / max(1, 2 * storage_capacity_kwh * nominal_cycle_life)
    return replacement_cost * fade_per_throughput_kwh * soc_stress * thermal_stress * health_stress


def solve_distributionally_robust_mpc(
    station: Any,
    forecast: dict[str, Any],
    *,
    prices: list[float] | None = None,
    initial_soc: float,
    soh: float = 0.95,
    temperature_c: float = 25,
    risk_alpha: float = 0.95,
    risk_weight: float = 0.25,
    demand_charge_per_kw: float = 0.0,
    replacement_cost: float | None = None,
    reserve_soc: float = 0.32,
    transformer_limit_ratio: float = 0.92,
    max_scenarios: int = 24,
    solver_time_limit_seconds: int = 30,
) -> dict[str, Any]:
    """Solve a non-anticipative scenario MILP with CVaR and DR inflation."""

    from scipy.optimize import Bounds, LinearConstraint, milp

    rows = forecast["rows"]
    n = len(rows)
    if not n:
        raise ValueError("Forecast horizon is empty.")
    quality_gate = forecast.get("quality_gate")
    if quality_gate and not quality_gate.get("qualified_for_dispatch"):
        raise RuntimeError(f"Forecast quality gate blocked dispatch: {quality_gate.get('reason', 'unqualified')}.")
    if not 0 < initial_soc < 1 or not 0 < soh <= 1:
        raise ValueError("initial_soc and soh must be in (0, 1].")
    if not 0.5 < risk_alpha < 1 or risk_weight < 0:
        raise ValueError("Invalid CVaR configuration.")
    dt = float(forecast["interval_minutes"]) / 60
    base_scenarios = forecast.get("scenarios_kw") or [[row["p50_grid_kw"] for row in rows]]
    radius = float(forecast.get("calibration", {}).get("wasserstein_radius_kw", 0))
    scenarios = np.asarray(base_scenarios[:max_scenarios], dtype=float)
    if scenarios.ndim != 2 or scenarios.shape[1] != n or not np.all(np.isfinite(scenarios)) or np.any(scenarios < 0):
        raise ValueError("Scenario matrix does not match forecast horizon.")
    if radius < 0 or not math.isfinite(radius):
        raise ValueError("Wasserstein radius must be finite and non-negative.")
    scenarios = scenarios + radius
    scenario_count = len(scenarios)
    energy_prices = np.asarray(prices or [float(row.get("price", 0.8)) for row in rows], dtype=float)
    if len(energy_prices) != n or np.any(energy_prices < 0):
        raise ValueError("prices must match the horizon and be non-negative.")

    charge = np.arange(0, n)
    discharge = np.arange(n, 2 * n)
    soc = np.arange(2 * n, 3 * n)
    charge_mode = np.arange(3 * n, 4 * n)
    discharge_mode = np.arange(4 * n, 5 * n)
    grid_start = 5 * n
    grid = np.arange(grid_start, grid_start + scenario_count * n).reshape(scenario_count, n)
    peak = np.arange(grid_start + scenario_count * n, grid_start + scenario_count * n + scenario_count)
    zeta = int(peak[-1] + 1)
    tail = np.arange(zeta + 1, zeta + 1 + scenario_count)
    size = int(tail[-1] + 1)
    objective = np.zeros(size)
    scenario_weight = 1 / scenario_count
    for scenario_index in range(scenario_count):
        objective[grid[scenario_index]] = energy_prices * dt * scenario_weight
        objective[peak[scenario_index]] = demand_charge_per_kw * scenario_weight
    replacement = (
        float(replacement_cost) if replacement_cost is not None else float(station.storage_capacity_kwh) * 1450
    )
    degradation = marginal_degradation_cost_per_kwh(
        storage_capacity_kwh=float(station.storage_capacity_kwh),
        replacement_cost=replacement,
        soc=initial_soc,
        soh=soh,
        temperature_c=temperature_c,
    )
    objective[charge] = degradation * dt
    objective[discharge] = degradation * dt
    objective[zeta] = risk_weight
    objective[tail] = risk_weight / ((1 - risk_alpha) * scenario_count)

    lower_bounds = np.zeros(size)
    upper_bounds = np.full(size, np.inf)
    lower_bounds[zeta] = 0
    power_limit = max(0.0, float(station.storage_power_kw))
    capacity = max(1.0, float(station.storage_capacity_kwh))
    transformer_limit = float(station.transformer_capacity_kw) * transformer_limit_ratio
    lower_bounds[soc] = 0.20
    upper_bounds[soc] = 0.92
    upper_bounds[charge] = power_limit
    upper_bounds[discharge] = power_limit
    upper_bounds[charge_mode] = 1
    upper_bounds[discharge_mode] = 1
    upper_bounds[grid.ravel()] = transformer_limit
    upper_bounds[peak] = transformer_limit
    integrality = np.zeros(size)
    integrality[charge_mode] = 1
    integrality[discharge_mode] = 1
    matrix_rows: list[np.ndarray] = []
    constraint_lower: list[float] = []
    constraint_upper: list[float] = []

    def add(coefficients: dict[int, float], low: float, high: float) -> None:
        row = np.zeros(size)
        for index, value in coefficients.items():
            row[int(index)] = value
        matrix_rows.append(row)
        constraint_lower.append(low)
        constraint_upper.append(high)

    eta_charge = max(0.82, min(0.98, 0.94 - 0.02 * max(0, temperature_c - 30) / 15))
    eta_discharge = max(0.82, min(0.98, eta_charge - 0.01))
    ramp = power_limit * 0.65
    for step in range(n):
        soc_coefficients = {
            int(soc[step]): 1,
            int(charge[step]): -(eta_charge * dt / capacity),
            int(discharge[step]): dt / (eta_discharge * capacity),
        }
        if step == 0:
            add(soc_coefficients, initial_soc, initial_soc)
        else:
            soc_coefficients[int(soc[step - 1])] = -1
            add(soc_coefficients, 0, 0)
        add({int(charge[step]): 1, int(charge_mode[step]): -power_limit}, -np.inf, 0)
        add({int(discharge[step]): 1, int(discharge_mode[step]): -power_limit}, -np.inf, 0)
        add({int(charge_mode[step]): 1, int(discharge_mode[step]): 1}, -np.inf, 1)
        ramp_coefficients = {int(charge[step]): 1, int(discharge[step]): -1}
        if step > 0:
            ramp_coefficients[int(charge[step - 1])] = -1
            ramp_coefficients[int(discharge[step - 1])] = 1
        add(ramp_coefficients, -ramp, ramp)
        for scenario_index in range(scenario_count):
            add(
                {
                    int(grid[scenario_index, step]): 1,
                    int(charge[step]): -1,
                    int(discharge[step]): 1,
                },
                float(scenarios[scenario_index, step]),
                float(scenarios[scenario_index, step]),
            )
            add(
                {int(grid[scenario_index, step]): 1, int(peak[scenario_index]): -1},
                -np.inf,
                0,
            )
    add({int(soc[-1]): 1}, reserve_soc, np.inf)
    for scenario_index in range(scenario_count):
        coefficients = {int(tail[scenario_index]): 1, zeta: 1, int(peak[scenario_index]): -demand_charge_per_kw}
        for step in range(n):
            coefficients[int(grid[scenario_index, step])] = -float(energy_prices[step] * dt)
            coefficients[int(charge[step])] = coefficients.get(int(charge[step]), 0) - degradation * dt
            coefficients[int(discharge[step])] = coefficients.get(int(discharge[step]), 0) - degradation * dt
        add(coefficients, 0, np.inf)
    result = milp(
        objective,
        integrality=integrality,
        bounds=Bounds(lower_bounds, upper_bounds),
        constraints=LinearConstraint(
            np.asarray(matrix_rows), np.asarray(constraint_lower), np.asarray(constraint_upper)
        ),
        options={"time_limit": solver_time_limit_seconds, "mip_rel_gap": 0.001, "presolve": True},
    )
    if not result.success or result.x is None:
        raise RuntimeError(f"Risk-aware MPC is infeasible or unsolved: {result.message}")
    solution = result.x
    scenario_costs = []
    for scenario_index in range(scenario_count):
        cost = float(np.dot(energy_prices * dt, solution[grid[scenario_index]]))
        cost += demand_charge_per_kw * float(solution[peak[scenario_index]])
        cost += degradation * dt * float(np.sum(solution[charge] + solution[discharge]))
        scenario_costs.append(cost)
    quantile = float(np.quantile(scenario_costs, risk_alpha))
    cvar = float(np.mean([cost for cost in scenario_costs if cost >= quantile]))
    plan = []
    for step, row in enumerate(rows):
        power = float(solution[charge[step]] - solution[discharge[step]])
        grid_values = solution[grid[:, step]]
        plan.append(
            {
                "at": row["at"],
                "storage_power_kw": round(power, 5),
                "projected_soc": round(float(solution[soc[step]]), 7),
                "expected_grid_kw": round(float(np.mean(grid_values)), 5),
                "p95_grid_kw": round(float(np.quantile(grid_values, 0.95)), 5),
                "transformer_margin_kw": round(transformer_limit - float(np.max(grid_values)), 5),
                "degradation_cost": round(degradation * abs(power) * dt, 7),
            }
        )
    return {
        "algorithm": ALGORITHMS["dispatch"],
        "input_hash": canonical_hash(
            {
                "forecast_hash": forecast.get("input_hash"),
                "station": station.id,
                "prices": energy_prices.tolist(),
                "initial_soc": initial_soc,
                "soh": soh,
                "temperature_c": temperature_c,
                "risk_alpha": risk_alpha,
                "risk_weight": risk_weight,
                "demand_charge_per_kw": demand_charge_per_kw,
                "replacement_cost": replacement,
                "reserve_soc": reserve_soc,
                "transformer_limit_ratio": transformer_limit_ratio,
                "max_scenarios": max_scenarios,
            }
        ),
        "exact": True,
        "status": "optimal" if int(result.status) == 0 else "feasible",
        "objective_value": round(float(result.fun), 6),
        "mip_gap": round(float(getattr(result, "mip_gap", 0) or 0), 8),
        "node_count": int(getattr(result, "mip_node_count", 0) or 0),
        "scenario_count": scenario_count,
        "risk": {
            "alpha": risk_alpha,
            "expected_cost": round(float(np.mean(scenario_costs)), 6),
            "var_cost": round(quantile, 6),
            "cvar_cost": round(cvar, 6),
            "wasserstein_radius_kw": round(radius, 6),
        },
        "battery": {
            "soh": soh,
            "temperature_c": temperature_c,
            "marginal_degradation_cost_per_kwh": round(degradation, 9),
        },
        "constraints": {
            "transformer_limit_kw": transformer_limit,
            "reserve_soc": reserve_soc,
            "soc_min": 0.20,
            "soc_max": 0.92,
            "ramp_limit_kw": ramp,
        },
        "dispatch_plan": plan,
        "execution_authorized": False,
        "control_boundary": "recommendation_only; approval and edge receipt remain mandatory",
    }


def project_three_phase_distflow(network: dict[str, Any], proposals: list[dict[str, Any]]) -> dict[str, Any]:
    """Project charging demand through radial phase-wise LinDistFlow and AC screening."""

    from scipy.optimize import linprog

    root = str(network.get("root_bus") or "")
    lines = network.get("lines") or []
    if not root or not lines or not proposals:
        raise ValueError("root_bus, lines, and proposals are required.")
    if any(float(item.get("proposed_kw", -1)) < 0 for item in proposals):
        raise ValueError("Network projection currently accepts non-negative load proposals.")
    phases = ("A", "B", "C")
    if any(item.get("phase", "A") not in phases for item in proposals):
        raise ValueError("Proposal phase must be A, B, or C.")
    if any(not item.get("station_id") or not item.get("bus") for item in proposals):
        raise ValueError("Every proposal requires station_id and bus.")
    if any(not 0.8 <= float(item.get("power_factor", 0.98)) <= 1 for item in proposals):
        raise ValueError("Proposal power_factor must be between 0.8 and 1.")
    for line in lines:
        if line.get("phase") is not None and line["phase"] not in phases:
            raise ValueError("Line phase must be A, B, or C.")
        if str(line.get("from_bus")) == str(line.get("to_bus")):
            raise ValueError("Network lines cannot be self loops.")
        if float(line.get("limit_kw", 0)) <= 0:
            raise ValueError("Every network line requires a positive limit_kw.")
        if float(line.get("resistance_ohm", 0)) < 0 or float(line.get("reactance_ohm", 0)) < 0:
            raise ValueError("Network impedance cannot be negative.")
    accepted = np.zeros(len(proposals))
    certificates: dict[str, Any] = {}
    for phase in phases:
        phase_indices = [index for index, item in enumerate(proposals) if item.get("phase", "A") == phase]
        if not phase_indices:
            continue
        phase_lines = [item for item in lines if item.get("phase", phase) == phase]
        to_buses = [str(item["to_bus"]) for item in phase_lines]
        if len(to_buses) != len(set(to_buses)) or root in to_buses:
            raise ValueError(f"Phase {phase} network has duplicate parents or feeds the root bus.")
        parent_line = {str(item["to_bus"]): item for item in phase_lines}
        paths: dict[int, list[dict[str, Any]]] = {}
        for index in phase_indices:
            bus = str(proposals[index]["bus"])
            path = []
            visited = set()
            while bus != root:
                if bus in visited or bus not in parent_line:
                    raise ValueError(f"Phase {phase} network is not a radial tree at bus {bus}.")
                visited.add(bus)
                line = parent_line[bus]
                path.append(line)
                bus = str(line["from_bus"])
            paths[index] = list(reversed(path))
        local_count = len(phase_indices)
        objective = np.asarray([-float(proposals[index].get("priority", 1)) for index in phase_indices])
        a_ub: list[list[float]] = []
        b_ub: list[float] = []
        for line in phase_lines:
            coefficients = [1.0 if line in paths[index] else 0.0 for index in phase_indices]
            if any(coefficients):
                a_ub.append(coefficients)
                b_ub.append(float(line["limit_kw"]))
        transformer_limit = float(network.get("transformer_limit_kw", math.inf)) / 3
        if math.isfinite(transformer_limit):
            a_ub.append([1.0] * local_count)
            b_ub.append(transformer_limit)
        minimum_voltage = float(network.get("minimum_voltage_pu", 0.94))
        for monitored_index in phase_indices:
            monitored_path = paths[monitored_index]
            coefficients = []
            for source_index in phase_indices:
                power_factor = max(0.8, min(1.0, float(proposals[source_index].get("power_factor", 0.98))))
                q_ratio = math.tan(math.acos(power_factor))
                drop = 0.0
                for line in monitored_path:
                    if line in paths[source_index]:
                        voltage_v = float(line.get("voltage_kv", network.get("voltage_kv", 0.4))) * 1000 / math.sqrt(3)
                        drop += (
                            2
                            * (float(line.get("resistance_ohm", 0)) + float(line.get("reactance_ohm", 0)) * q_ratio)
                            * 1000
                            / max(1, voltage_v**2)
                        )
                coefficients.append(drop)
            a_ub.append(coefficients)
            b_ub.append(1 - minimum_voltage)
        bounds = [(0.0, float(proposals[index]["proposed_kw"])) for index in phase_indices]
        result = linprog(
            objective,
            A_ub=np.asarray(a_ub) if a_ub else None,
            b_ub=np.asarray(b_ub) if b_ub else None,
            bounds=bounds,
            method="highs",
        )
        if not result.success or result.x is None:
            raise RuntimeError(f"Phase {phase} DistFlow projection failed: {result.message}")
        for local_index, proposal_index in enumerate(phase_indices):
            accepted[proposal_index] = result.x[local_index]
        line_flows = {
            str(line.get("id") or f"{line['from_bus']}-{line['to_bus']}-{phase}"): sum(
                accepted[index] for index in phase_indices if line in paths[index]
            )
            for line in phase_lines
        }
        voltages = {}
        for proposal_index in phase_indices:
            power_factor = max(0.8, min(1.0, float(proposals[proposal_index].get("power_factor", 0.98))))
            q_ratio = math.tan(math.acos(power_factor))
            drop = 0.0
            for line in paths[proposal_index]:
                flow = line_flows[str(line.get("id") or f"{line['from_bus']}-{line['to_bus']}-{phase}")]
                voltage_v = float(line.get("voltage_kv", network.get("voltage_kv", 0.4))) * 1000 / math.sqrt(3)
                drop += (
                    2
                    * (float(line.get("resistance_ohm", 0)) + float(line.get("reactance_ohm", 0)) * q_ratio)
                    * flow
                    * 1000
                    / max(1, voltage_v**2)
                )
            voltages[str(proposals[proposal_index]["bus"])] = 1 - drop
        certificates[phase] = {
            "line_flows_kw": {key: round(float(value), 6) for key, value in line_flows.items()},
            "bus_voltage_pu": {key: round(float(value), 7) for key, value in voltages.items()},
            "minimum_voltage_pu": round(min(voltages.values()), 7),
        }
    allocations = []
    for index, proposal in enumerate(proposals):
        allocations.append(
            {
                "station_id": proposal["station_id"],
                "bus": proposal["bus"],
                "phase": proposal.get("phase", "A"),
                "proposed_kw": round(float(proposal["proposed_kw"]), 6),
                "accepted_kw": round(float(accepted[index]), 6),
                "curtailed_kw": round(float(proposal["proposed_kw"]) - float(accepted[index]), 6),
            }
        )
    qualified = all(
        item["minimum_voltage_pu"] >= float(network.get("minimum_voltage_pu", 0.94)) - 1e-7
        for item in certificates.values()
    )
    return {
        "algorithm": ALGORITHMS["network"],
        "input_hash": canonical_hash({"network": network, "proposals": proposals}),
        "qualified": qualified,
        "ac_certified": False,
        "certificate_scope": "radial phase-decoupled LinDistFlow; field AC study remains required",
        "execution_authorized": False,
        "allocations": allocations,
        "phases": certificates,
    }


def coordinate_portfolio_admm(
    resources: list[dict[str, Any]],
    target_kw: float,
    *,
    rho: float = 1.0,
    tolerance: float = 1e-4,
    max_iterations: int = 500,
) -> dict[str, Any]:
    """Bounded consensus ADMM for privacy-preserving station allocation."""

    if len(resources) < 2:
        raise ValueError("At least two resources are required for distributed coordination.")
    if rho <= 0 or tolerance <= 0 or max_iterations < 1 or not math.isfinite(target_kw):
        raise ValueError("ADMM parameters and target must be finite and valid.")
    lower = np.asarray([float(item.get("minimum_kw", 0)) for item in resources])
    upper = np.asarray([float(item["maximum_kw"]) for item in resources])
    if (
        not np.all(np.isfinite(lower))
        or not np.all(np.isfinite(upper))
        or np.any(upper < lower)
        or not float(np.sum(lower)) <= target_kw <= float(np.sum(upper))
    ):
        raise ValueError("Portfolio target is outside aggregate resource bounds.")
    quadratic = np.asarray([max(1e-8, float(item.get("quadratic_cost", 1))) for item in resources])
    linear = np.asarray([float(item.get("linear_cost", 0)) for item in resources])
    x = np.clip(np.full(len(resources), target_kw / len(resources)), lower, upper)
    z = _project_bounded_sum(x, lower, upper, target_kw)
    dual = np.zeros(len(resources))
    converged = False
    primal = dual_residual = math.inf
    iterations = 0
    for _iteration in range(1, max_iterations + 1):
        iterations = _iteration
        x = np.clip((rho * (z - dual) - linear) / (quadratic + rho), lower, upper)
        previous_z = z.copy()
        z = _project_bounded_sum(x + dual, lower, upper, target_kw)
        dual += x - z
        primal = float(np.linalg.norm(x - z))
        dual_residual = float(rho * np.linalg.norm(z - previous_z))
        if primal <= tolerance and dual_residual <= tolerance:
            converged = True
            break
    if not converged:
        raise RuntimeError("ADMM allocation did not converge within the configured limit.")
    allocations = [
        {
            "station_id": resource["station_id"],
            "target_kw": round(float(z[index]), 6),
            "local_cost": round(float(0.5 * quadratic[index] * z[index] ** 2 + linear[index] * z[index]), 6),
        }
        for index, resource in enumerate(resources)
    ]
    return {
        "algorithm": ALGORITHMS["coordination"],
        "input_hash": canonical_hash(
            {
                "resources": resources,
                "target_kw": target_kw,
                "rho": rho,
                "tolerance": tolerance,
                "max_iterations": max_iterations,
            }
        ),
        "converged": True,
        "iterations": iterations,
        "primal_residual": round(primal, 10),
        "dual_residual": round(dual_residual, 10),
        "target_kw": target_kw,
        "allocated_kw": round(sum(item["target_kw"] for item in allocations), 6),
        "allocations": allocations,
    }


def _project_bounded_sum(values: np.ndarray, lower: np.ndarray, upper: np.ndarray, target: float) -> np.ndarray:
    low = float(np.min(values - upper)) - 1
    high = float(np.max(values - lower)) + 1
    for _ in range(100):
        midpoint = (low + high) / 2
        projected = np.clip(values - midpoint, lower, upper)
        if float(np.sum(projected)) > target:
            low = midpoint
        else:
            high = midpoint
    projected = np.clip(values - (low + high) / 2, lower, upper)
    correction = target - float(np.sum(projected))
    for index in np.argsort(-(upper - projected) if correction > 0 else -(projected - lower)):
        room = upper[index] - projected[index] if correction > 0 else projected[index] - lower[index]
        delta = math.copysign(min(abs(correction), room), correction)
        projected[index] += delta
        correction -= delta
        if abs(correction) < 1e-9:
            break
    return projected


def project_safe_action(proposed_kw: float, constraints: dict[str, float]) -> dict[str, Any]:
    """Project a learned action into the station's instantaneous safe set."""

    required = {
        "soc",
        "soc_min",
        "soc_max",
        "capacity_kwh",
        "interval_hours",
        "charge_limit_kw",
        "discharge_limit_kw",
        "transformer_headroom_kw",
        "previous_power_kw",
        "ramp_limit_kw",
        "temperature_c",
        "temperature_limit_c",
    }
    missing = sorted(required - constraints.keys())
    if missing:
        return {
            "algorithm": ALGORITHMS["safety_projection"],
            "allowed": False,
            "projected_kw": 0.0,
            "reasons": [f"missing_{key}" for key in missing],
        }
    try:
        values = [float(constraints[key]) for key in required]
    except (TypeError, ValueError):
        values = [math.nan]
    if (
        not all(math.isfinite(value) for value in values)
        or constraints["capacity_kwh"] <= 0
        or constraints["interval_hours"] <= 0
        or constraints["charge_limit_kw"] < 0
        or constraints["discharge_limit_kw"] < 0
        or constraints["ramp_limit_kw"] < 0
        or not constraints["soc_min"] <= constraints["soc"] <= constraints["soc_max"]
    ):
        return {
            "algorithm": ALGORITHMS["safety_projection"],
            "allowed": False,
            "projected_kw": 0.0,
            "reasons": ["invalid_constraints"],
        }
    reasons = []
    if constraints["temperature_c"] >= constraints["temperature_limit_c"]:
        return {
            "algorithm": ALGORITHMS["safety_projection"],
            "allowed": False,
            "projected_kw": 0.0,
            "reasons": ["temperature_limit"],
        }
    lower = -abs(constraints["discharge_limit_kw"])
    upper = min(abs(constraints["charge_limit_kw"]), max(0.0, constraints["transformer_headroom_kw"]))
    energy_factor = constraints["capacity_kwh"] / max(1e-9, constraints["interval_hours"])
    lower = max(lower, -(constraints["soc"] - constraints["soc_min"]) * energy_factor * 0.9)
    upper = min(upper, (constraints["soc_max"] - constraints["soc"]) * energy_factor / 0.9)
    lower = max(lower, constraints["previous_power_kw"] - constraints["ramp_limit_kw"])
    upper = min(upper, constraints["previous_power_kw"] + constraints["ramp_limit_kw"])
    if lower > upper:
        return {
            "algorithm": ALGORITHMS["safety_projection"],
            "allowed": False,
            "projected_kw": 0.0,
            "reasons": ["empty_safe_set"],
        }
    projected = min(upper, max(lower, proposed_kw))
    if abs(projected - proposed_kw) > 1e-8:
        reasons.append("action_projected_to_safe_set")
    return {
        "algorithm": ALGORITHMS["safety_projection"],
        "allowed": True,
        "proposed_kw": round(proposed_kw, 6),
        "projected_kw": round(projected, 6),
        "safe_interval_kw": [round(lower, 6), round(upper, 6)],
        "reasons": reasons,
        "input_hash": canonical_hash({"proposed_kw": proposed_kw, "constraints": constraints}),
    }


def train_conservative_fitted_q(
    transitions: list[dict[str, Any]],
    actions_kw: list[float],
    *,
    gamma: float = 0.98,
    conservative_penalty: float = 0.2,
    ridge: float = 1e-3,
    iterations: int = 50,
) -> dict[str, Any]:
    """Train a compact offline FQI policy; unsafe samples never enter fitting."""

    safe = [item for item in transitions if item.get("safe", True)]
    if (
        not actions_kw
        or len(set(actions_kw)) != len(actions_kw)
        or not all(math.isfinite(float(action)) for action in actions_kw)
        or not 0 <= gamma < 1
        or conservative_penalty < 0
        or ridge <= 0
        or iterations < 1
    ):
        raise ValueError("Offline policy actions and hyperparameters are invalid.")
    if len(safe) < max(12, len(actions_kw) * 3):
        raise ValueError("Insufficient safe offline transitions for policy fitting.")
    states = np.asarray([item["state"] for item in safe], dtype=float)
    next_states = np.asarray([item["next_state"] for item in safe], dtype=float)
    action_indices = np.asarray([int(item["action_index"]) for item in safe], dtype=int)
    rewards = np.asarray([float(item["reward"]) for item in safe])
    done = np.asarray([bool(item.get("done", False)) for item in safe], dtype=float)
    if states.ndim != 2 or next_states.shape != states.shape:
        raise ValueError("Offline states and next_states must form equal two-dimensional matrices.")
    if not np.all(np.isfinite(states)) or not np.all(np.isfinite(next_states)) or not np.all(np.isfinite(rewards)):
        raise ValueError("Offline transitions must contain finite values.")
    if np.any(action_indices < 0) or np.any(action_indices >= len(actions_kw)):
        raise ValueError("Offline action_index is out of range.")
    mean = states.mean(axis=0)
    scale = states.std(axis=0)
    scale[scale < 1e-8] = 1
    features = _policy_features((states - mean) / scale)
    next_features = _policy_features((next_states - mean) / scale)
    weights = np.zeros((len(actions_kw), features.shape[1]))
    counts = np.bincount(action_indices, minlength=len(actions_kw))
    if np.any(counts == 0):
        raise ValueError("Every candidate action needs safe offline support.")
    identity = np.eye(features.shape[1])
    for _ in range(iterations):
        next_q = next_features @ weights.T
        targets = rewards + gamma * (1 - done) * np.max(next_q, axis=1)
        for action_index in range(len(actions_kw)):
            mask = action_indices == action_index
            design = features[mask]
            penalized_target = targets[mask] - conservative_penalty / math.sqrt(int(counts[action_index]))
            weights[action_index] = np.linalg.solve(
                design.T @ design + ridge * identity,
                design.T @ penalized_target,
            )
    covariance = np.cov(((states - mean) / scale).T)
    covariance = np.atleast_2d(covariance) + np.eye(states.shape[1]) * 1e-4
    inverse_covariance = np.linalg.inv(covariance)
    return {
        "algorithm": ALGORITHMS["offline_policy"],
        "input_hash": canonical_hash(
            {
                "transitions": transitions,
                "actions_kw": actions_kw,
                "gamma": gamma,
                "conservative_penalty": conservative_penalty,
                "ridge": ridge,
                "iterations": iterations,
            }
        ),
        "sample_count": len(safe),
        "unsafe_samples_excluded": len(transitions) - len(safe),
        "actions_kw": actions_kw,
        "feature_mean": mean.tolist(),
        "feature_scale": scale.tolist(),
        "inverse_covariance": inverse_covariance.tolist(),
        "weights": weights.tolist(),
        "action_counts": counts.tolist(),
        "approved_for_control": False,
        "usage": "shadow_advisory_only",
    }


def evaluate_offline_policy(
    model: dict[str, Any],
    state: list[float],
    safety_constraints: dict[str, float],
    *,
    max_mahalanobis: float = 4.0,
) -> dict[str, Any]:
    values = np.asarray(state, dtype=float)
    mean = np.asarray(model["feature_mean"], dtype=float)
    scale = np.asarray(model["feature_scale"], dtype=float)
    if values.shape != mean.shape:
        raise ValueError("Policy state dimension does not match the trained model.")
    normalized = (values - mean) / scale
    inverse_covariance = np.asarray(model["inverse_covariance"], dtype=float)
    distance = float(math.sqrt(max(0.0, normalized @ inverse_covariance @ normalized)))
    input_hash = canonical_hash(
        {
            "model_hash": model.get("input_hash"),
            "state": state,
            "safety_constraints": safety_constraints,
            "max_mahalanobis": max_mahalanobis,
        }
    )
    if distance > max_mahalanobis:
        return {
            "algorithm": model["algorithm"],
            "input_hash": input_hash,
            "allowed": False,
            "reason": "out_of_distribution",
            "mahalanobis": round(distance, 6),
        }
    q_values = _policy_features(normalized.reshape(1, -1)) @ np.asarray(model["weights"], dtype=float).T
    action_index = int(np.argmax(q_values[0]))
    proposed = float(model["actions_kw"][action_index])
    projection = project_safe_action(proposed, safety_constraints)
    return {
        "algorithm": model["algorithm"],
        "input_hash": input_hash,
        "allowed": bool(projection["allowed"]),
        "usage": "shadow_advisory_only",
        "mahalanobis": round(distance, 6),
        "action_index": action_index,
        "q_values": [round(float(value), 6) for value in q_values[0]],
        "projection": projection,
    }


def _policy_features(states: np.ndarray) -> np.ndarray:
    states = np.atleast_2d(states)
    return np.column_stack([np.ones(len(states)), states, states**2])
