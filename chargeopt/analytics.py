from __future__ import annotations

import math
from datetime import datetime, timedelta
from statistics import mean
from typing import Any

from .data import Repository
from .domain import Station, TelemetryPoint


def build_overview(repo: Repository) -> dict[str, Any]:
    stations = [station_summary(repo, station) for station in repo.stations]
    totals = {
        "station_count": len(stations),
        "today_energy_kwh": round(sum(item["today_energy_kwh"] for item in stations), 1),
        "today_revenue": round(sum(item["today_revenue"] for item in stations), 0),
        "today_margin": round(sum(item["today_margin"] for item in stations), 0),
        "current_power_kw": round(sum(item["current_power_kw"] for item in stations), 1),
        "queue_length": sum(item["queue_length"] for item in stations),
        "demand_peak_kw": round(sum(item["demand_peak_kw"] for item in stations), 1),
        "monthly_savings_potential": round(sum(item["monthly_savings_potential"] for item in stations), 0),
        "vpp_capacity_kw": round(sum(item["vpp_capacity_kw"] for item in stations), 1),
    }
    totals["portfolio_health"] = round(mean(item["health_score"] for item in stations), 1)
    totals["gross_margin_rate"] = round(
        totals["today_margin"] / max(1, totals["today_revenue"]) * 100,
        1,
    )
    return {
        "tenant": repo.tenants[0].__dict__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "totals": totals,
        "stations": stations,
        "dispatch": build_dispatch(repo),
        "vpp": build_vpp(repo),
        "portfolio_series": portfolio_series(repo),
    }


def station_summary(repo: Repository, station: Station) -> dict[str, Any]:
    points = repo.station_points(station.id)
    tariff = repo.tariff_for(station)
    current = points[-1]
    energy = sum(point.energy_kwh for point in points)
    revenue = sum(point.revenue for point in points)
    grid_cost = sum(point.grid_kw * tariff.price_at(point.timestamp.hour) for point in points)
    demand_peak = max(point.grid_kw for point in points)
    demand_charge_day = demand_peak * tariff.demand_charge_per_kw_month / 30
    opex_day = station.monthly_opex / 30
    margin = revenue - grid_cost - demand_charge_day - opex_day
    utilization = sum(point.connector_occupied for point in points) / (len(points) * station.connector_count)
    demand_headroom = max(0, station.transformer_capacity_kw - current.grid_kw)
    monthly_savings = estimate_monthly_savings(station, points, tariff)
    vpp_kw = adjustable_capacity(station, current)
    peak_cut_kw = max(0, demand_peak - station.transformer_capacity_kw * 0.78)
    storage_utilization = abs(sum(point.storage_power_kw for point in points)) / max(1, station.storage_power_kw * len(points))
    health_score = _health_score(margin, revenue, utilization, current.queue_length, current.alert_count, station.reliability_score)
    return {
        "id": station.id,
        "name": station.name,
        "type": station.station_type,
        "address": station.address,
        "lat": station.latitude,
        "lng": station.longitude,
        "dispatch_mode": station.dispatch_mode,
        "connectors": station.connector_count,
        "transformer_capacity_kw": station.transformer_capacity_kw,
        "storage_capacity_kwh": station.storage_capacity_kwh,
        "storage_power_kw": station.storage_power_kw,
        "pv_capacity_kw": station.pv_capacity_kw,
        "current_power_kw": round(current.grid_kw, 1),
        "current_load_kw": round(current.load_kw, 1),
        "storage_soc": round(current.storage_soc * 100, 1),
        "storage_power_kw_now": current.storage_power_kw,
        "queue_length": current.queue_length,
        "occupied_connectors": current.connector_occupied,
        "connector_utilization": round(utilization * 100, 1),
        "storage_utilization": round(storage_utilization * 100, 1),
        "today_energy_kwh": round(energy, 1),
        "today_revenue": round(revenue, 0),
        "grid_cost": round(grid_cost, 0),
        "demand_charge_day": round(demand_charge_day, 0),
        "today_margin": round(margin, 0),
        "margin_rate": round(margin / max(1, revenue) * 100, 1),
        "demand_peak_kw": round(demand_peak, 1),
        "demand_headroom_kw": round(demand_headroom, 1),
        "peak_cut_opportunity_kw": round(peak_cut_kw, 1),
        "monthly_savings_potential": round(monthly_savings, 0),
        "vpp_capacity_kw": round(vpp_kw, 1),
        "health_score": health_score,
        "alert_count": len([alert for alert in repo.station_alerts(station.id) if not alert.acknowledged]),
        "tariff": tariff.name,
    }


def station_detail(repo: Repository, station_id: str) -> dict[str, Any]:
    station = _station(repo, station_id)
    points = repo.station_points(station.id)
    tariff = repo.tariff_for(station)
    return {
        "station": station_summary(repo, station),
        "telemetry": [point_payload(point, tariff) for point in points],
        "forecast": forecast_load(repo, station.id),
        "storage_plan": storage_plan(repo, station.id),
        "pricing": pricing_suggestions(repo, station.id),
        "alerts": [alert.__dict__ | {"timestamp": alert.timestamp.isoformat(timespec="minutes")} for alert in repo.station_alerts(station.id)],
        "recommendations": [item for item in build_dispatch(repo)["recommendations"] if item["station_id"] == station.id],
    }


def point_payload(point: TelemetryPoint, tariff) -> dict[str, Any]:
    return {
        "time": point.timestamp.isoformat(timespec="minutes"),
        "label": point.timestamp.strftime("%H:%M"),
        "load_kw": point.load_kw,
        "grid_kw": point.grid_kw,
        "pv_kw": point.pv_kw,
        "storage_power_kw": point.storage_power_kw,
        "storage_soc": round(point.storage_soc * 100, 1),
        "queue_length": point.queue_length,
        "occupied": point.connector_occupied,
        "energy_kwh": point.energy_kwh,
        "revenue": point.revenue,
        "price": tariff.price_at(point.timestamp.hour),
        "period": tariff.period_name_at(point.timestamp.hour),
    }


def portfolio_series(repo: Repository) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    timestamps = sorted({point.timestamp for point in repo.telemetry})
    for timestamp in timestamps:
        points = [point for point in repo.telemetry if point.timestamp == timestamp]
        rows.append(
            {
                "label": timestamp.strftime("%H:%M"),
                "grid_kw": round(sum(point.grid_kw for point in points), 1),
                "load_kw": round(sum(point.load_kw for point in points), 1),
                "pv_kw": round(sum(point.pv_kw for point in points), 1),
                "storage_kw": round(sum(point.storage_power_kw for point in points), 1),
                "queue": sum(point.queue_length for point in points),
            }
        )
    return rows


def forecast_load(repo: Repository, station_id: str) -> list[dict[str, Any]]:
    station = _station(repo, station_id)
    points = repo.station_points(station.id)
    latest = points[-1].timestamp
    tariff = repo.tariff_for(station)
    last_24 = {point.timestamp.hour: point for point in points}
    forecast: list[dict[str, Any]] = []
    for step in range(1, 25):
        ts = latest + timedelta(hours=step)
        base = last_24.get(ts.hour, points[step % len(points)])
        price = tariff.price_at(ts.hour)
        price_push = 0.94 if price > 1.0 else 1.07 if price < 0.5 else 1.0
        trend = 1 + 0.035 * math.sin(step / 24 * math.tau)
        load_kw = min(station.transformer_capacity_kw * 0.98, base.load_kw * price_push * trend)
        queue = max(0, round((load_kw / station.transformer_capacity_kw - 0.7) * 28))
        forecast.append(
            {
                "time": ts.isoformat(timespec="minutes"),
                "label": ts.strftime("%H:%M"),
                "load_kw": round(load_kw, 1),
                "grid_kw": round(max(0, load_kw - base.pv_kw * 0.65), 1),
                "queue_length": queue,
                "price": price,
                "peak_probability": round(min(0.96, max(0.08, load_kw / station.transformer_capacity_kw)), 2),
            }
        )
    return forecast


def storage_plan(repo: Repository, station_id: str) -> list[dict[str, Any]]:
    station = _station(repo, station_id)
    current_soc = repo.station_points(station.id)[-1].storage_soc
    forecast = forecast_load(repo, station.id)
    tariff = repo.tariff_for(station)
    soc = current_soc
    plan: list[dict[str, Any]] = []
    emergency_floor = 0.22
    for row in forecast:
        hour = datetime.fromisoformat(row["time"]).hour
        price = tariff.price_at(hour)
        load_ratio = row["grid_kw"] / station.transformer_capacity_kw
        action = "hold"
        power_kw = 0.0
        reason = "Preserve SOC and transformer headroom."
        if price < 0.5 and soc < 0.86:
            action = "charge"
            power_kw = min(station.storage_power_kw, station.transformer_capacity_kw * 0.12)
            soc += power_kw / station.storage_capacity_kwh * 0.9
            reason = "Valley price window; charge for evening peak and VPP reserve."
        elif (price > 1.0 or load_ratio > 0.78) and soc > emergency_floor + 0.08:
            action = "discharge"
            power_kw = -min(station.storage_power_kw, row["grid_kw"] * 0.2)
            soc += power_kw / station.storage_capacity_kwh / 0.9
            reason = "Peak price or demand threshold; discharge to reduce grid import."
        soc = min(0.92, max(emergency_floor, soc))
        plan.append(
            {
                "label": row["label"],
                "action": action,
                "power_kw": round(power_kw, 1),
                "soc": round(soc * 100, 1),
                "reason": reason,
            }
        )
    return plan


def pricing_suggestions(repo: Repository, station_id: str) -> list[dict[str, Any]]:
    station = _station(repo, station_id)
    forecast = forecast_load(repo, station.id)
    suggestions: list[dict[str, Any]] = []
    for row in forecast[:12]:
        load_ratio = row["load_kw"] / station.transformer_capacity_kw
        if load_ratio > 0.82 or row["queue_length"] >= 5:
            adjustment = "+0.16"
            strategy = "Peak protection"
            note = "Raise service fee and reserve member/fleet slots."
        elif row["price"] < 0.5 and load_ratio < 0.55:
            adjustment = "-0.12"
            strategy = "Valley attraction"
            note = "Offer off-peak coupon to lift utilization."
        else:
            adjustment = "0.00"
            strategy = "Hold"
            note = "Maintain current public price."
        suggestions.append(
            {
                "label": row["label"],
                "strategy": strategy,
                "service_fee_delta": adjustment,
                "expected_queue": row["queue_length"],
                "note": note,
            }
        )
    return suggestions


def build_dispatch(repo: Repository) -> dict[str, Any]:
    recommendations: list[dict[str, Any]] = []
    for station in repo.stations:
        summary = station_summary(repo, station)
        current = repo.station_points(station.id)[-1]
        forecast = forecast_load(repo, station.id)
        next_peak = max(forecast[:8], key=lambda row: row["grid_kw"])
        if summary["demand_headroom_kw"] < station.transformer_capacity_kw * 0.12:
            recommendations.append(
                _recommendation(
                    station,
                    "Demand peak guard",
                    "high",
                    "Discharge storage and cap non-priority connectors for the next peak window.",
                    min(station.storage_power_kw, station.transformer_capacity_kw * 0.16),
                    next_peak["label"],
                    "Expected demand peak reduction and lower monthly capacity charge.",
                )
            )
        if current.queue_length >= 4:
            recommendations.append(
                _recommendation(
                    station,
                    "Queue relief pricing",
                    "medium",
                    "Apply peak service fee and redirect app traffic to lower-load stations.",
                    current.queue_length,
                    "now",
                    "Protects service level without hard equipment control.",
                )
            )
        if current.storage_soc < 32 and repo.tariff_for(station).price_at(current.timestamp.hour) < 0.6:
            recommendations.append(
                _recommendation(
                    station,
                    "Storage recharge",
                    "medium",
                    "Charge battery during valley period while preserving transformer headroom.",
                    station.storage_power_kw * 0.55,
                    "now",
                    "Builds reserve for VPP and evening discharge.",
                )
            )
        recommendations.append(
            _recommendation(
                station,
                "Next-day dispatch plan",
                "low",
                "Publish 24h storage and pricing plan for operator approval.",
                summary["monthly_savings_potential"] / 30,
                "tomorrow",
                "Keeps the system in recommendation mode with auditable approval.",
            )
        )
    recommendations.sort(key=lambda item: {"critical": 0, "high": 1, "medium": 2, "low": 3}[item["risk"]])
    return {
        "mode": "recommendation",
        "approval_required": True,
        "recommendations": recommendations,
        "summary": {
            "count": len(recommendations),
            "high_risk": len([item for item in recommendations if item["risk"] in {"high", "critical"}]),
            "estimated_daily_value": round(sum(item["value"] for item in recommendations if isinstance(item["value"], (int, float))), 0),
        },
    }


def build_vpp(repo: Repository) -> dict[str, Any]:
    resources = []
    for station in repo.stations:
        current = repo.station_points(station.id)[-1]
        capacity = adjustable_capacity(station, current)
        duration = max(0.25, (current.storage_soc - 0.22) * station.storage_capacity_kwh / max(1, station.storage_power_kw))
        reliability = station.reliability_score * (0.92 if current.queue_length > 5 else 1.0)
        resources.append(
            {
                "station_id": station.id,
                "station": station.name,
                "adjustable_kw": round(capacity, 1),
                "duration_hours": round(duration, 2),
                "confidence": round(reliability, 2),
                "response_cost_per_kwh": round(0.62 + (1 - reliability) * 0.8, 2),
                "storage_available_kwh": round(max(0, current.storage_soc - 0.22) * station.storage_capacity_kwh, 1),
                "load_curtailment_kw": round(max(0, current.grid_kw - station.transformer_capacity_kw * 0.55), 1),
            }
        )
    event = repo.vpp_events[0]
    total = sum(item["adjustable_kw"] * item["confidence"] for item in resources)
    allocations = []
    for item in resources:
        share = (item["adjustable_kw"] * item["confidence"]) / max(1, total)
        allocations.append(
            {
                "station_id": item["station_id"],
                "station": item["station"],
                "target_kw": round(min(item["adjustable_kw"], event.requested_kw * share), 1),
                "method": "storage discharge + load shaping",
            }
        )
    expected_kwh = min(event.requested_kw, total) * event.duration_minutes / 60
    return {
        "event": {
            "id": event.id,
            "title": event.title,
            "start": event.start.isoformat(timespec="minutes"),
            "duration_minutes": event.duration_minutes,
            "requested_kw": event.requested_kw,
            "status": event.status,
            "incentive_per_kwh": event.incentive_per_kwh,
        },
        "total_adjustable_kw": round(sum(item["adjustable_kw"] for item in resources), 1),
        "reliable_capacity_kw": round(total, 1),
        "expected_revenue": round(expected_kwh * event.incentive_per_kwh, 0),
        "resources": resources,
        "allocations": allocations,
    }


def simulate_roi(
    repo: Repository,
    capacity_kwh: float,
    power_kw: float,
    capex_per_kwh: float,
    include_vpp: bool,
) -> dict[str, Any]:
    blended_spread = 0.72
    cycles_per_year = 286
    roundtrip_efficiency = 0.88
    demand_savings = min(power_kw * 0.62, capacity_kwh * 0.32) * mean(plan.demand_charge_per_kw_month for plan in repo.tariff_plans) * 12
    arbitrage = capacity_kwh * cycles_per_year * roundtrip_efficiency * blended_spread
    vpp_revenue = capacity_kwh * 0.18 * 70 if include_vpp else 0
    degradation = capacity_kwh * cycles_per_year * 0.055
    maintenance = capacity_kwh * 18
    capex = capacity_kwh * capex_per_kwh + power_kw * 260
    annual_benefit = demand_savings + arbitrage + vpp_revenue - degradation - maintenance
    payback = capex / max(1, annual_benefit)
    npv = -capex
    discount = 0.085
    for year in range(1, 11):
        npv += annual_benefit * (0.985 ** (year - 1)) / ((1 + discount) ** year)
    irr = _irr(capex, annual_benefit)
    return {
        "capacity_kwh": capacity_kwh,
        "power_kw": power_kw,
        "capex": round(capex, 0),
        "annual_demand_savings": round(demand_savings, 0),
        "annual_arbitrage": round(arbitrage, 0),
        "annual_vpp_revenue": round(vpp_revenue, 0),
        "annual_degradation_cost": round(degradation, 0),
        "annual_maintenance": round(maintenance, 0),
        "annual_net_benefit": round(annual_benefit, 0),
        "payback_years": round(payback, 2),
        "npv_10y": round(npv, 0),
        "irr": round(irr * 100, 1),
        "recommendation": "invest" if payback < 5.5 and npv > 0 else "review",
    }


def estimate_monthly_savings(station: Station, points: list[TelemetryPoint], tariff) -> float:
    peak = max(point.grid_kw for point in points)
    peak_cut = max(0, min(station.storage_power_kw, peak - station.transformer_capacity_kw * 0.78))
    demand_saving = peak_cut * tariff.demand_charge_per_kw_month
    valley_energy = station.storage_capacity_kwh * 0.55 * 24
    arbitrage = valley_energy * 0.42
    queue_value = sum(point.queue_length for point in points) * 85
    return max(0, demand_saving + arbitrage + queue_value)


def adjustable_capacity(station: Station, current: TelemetryPoint) -> float:
    storage_kw = min(station.storage_power_kw, max(0, current.storage_soc - 0.22) * station.storage_capacity_kwh)
    flexible_load_kw = max(0, current.grid_kw - station.transformer_capacity_kw * 0.55)
    service_guard = 0.78 if current.queue_length > 5 else 1.0
    return (storage_kw + flexible_load_kw * 0.45) * station.reliability_score * service_guard


def _station(repo: Repository, station_id: str) -> Station:
    for station in repo.stations:
        if station.id == station_id:
            return station
    raise KeyError(f"Unknown station_id: {station_id}")


def _recommendation(station: Station, title: str, risk: str, action: str, value: float, window: str, rationale: str) -> dict[str, Any]:
    return {
        "id": f"rec-{station.id}-{title.lower().replace(' ', '-')}",
        "station_id": station.id,
        "station": station.name,
        "title": title,
        "risk": risk,
        "action": action,
        "value": round(value, 1),
        "window": window,
        "mode": station.dispatch_mode,
        "approval": "required" if station.dispatch_mode in {"recommend", "semi_auto"} else "observe",
        "rationale": rationale,
    }


def _health_score(margin: float, revenue: float, utilization: float, queue: int, alerts: int, reliability: float) -> float:
    margin_component = max(0, min(34, (margin / max(1, revenue) + 0.05) * 80))
    utilization_component = max(0, 26 - abs(utilization - 0.68) * 55)
    queue_component = max(0, 18 - queue * 1.8)
    reliability_component = reliability * 16
    alert_component = max(0, 6 - alerts * 3)
    return round(margin_component + utilization_component + queue_component + reliability_component + alert_component, 1)


def _irr(capex: float, annual: float) -> float:
    best_rate = -0.5
    best_error = float("inf")
    for basis in range(-50, 501):
        rate = basis / 1000
        npv = -capex
        for year in range(1, 11):
            npv += annual * (0.985 ** (year - 1)) / ((1 + rate) ** year)
        error = abs(npv)
        if error < best_error:
            best_error = error
            best_rate = rate
    return best_rate
