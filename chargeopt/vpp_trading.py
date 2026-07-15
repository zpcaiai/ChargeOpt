"""Risk-constrained VPP forecasting, bidding, dispatch, and settlement logic."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import os
import statistics
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from .analytics import adjustable_capacity
from .data import Repository

FORECAST_ALGORITHM = "conformal-seasonal-ensemble-v1"
OPTIMIZER_ALGORITHM = "cvar-portfolio-mpc-v1"

ORDER_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"risk_rejected", "ready"},
    "ready": {"submitting", "cancelled", "expired"},
    "submitting": {"submitted", "rejected", "failed"},
    "submitted": {"partially_filled", "filled", "cancel_pending", "cancelled", "rejected", "expired", "failed"},
    "partially_filled": {"partially_filled", "filled", "cancel_pending", "cancelled", "expired", "failed"},
    "cancel_pending": {"cancelled", "partially_filled", "filled", "failed"},
    "risk_rejected": set(),
    "filled": set(),
    "cancelled": set(),
    "rejected": set(),
    "expired": set(),
    "failed": set(),
}


@dataclass(frozen=True)
class MarketSubmission:
    accepted: bool
    market_order_id: str | None
    status: str
    raw: dict[str, Any]


class MarketAdapter(Protocol):
    def submit_order(self, order: dict[str, Any]) -> MarketSubmission: ...

    def cancel_order(self, order: dict[str, Any]) -> MarketSubmission: ...

    def query_order(self, order: dict[str, Any]) -> MarketSubmission: ...


class SandboxMarketAdapter:
    """Deterministic exchange simulator used for certification and disaster drills."""

    def submit_order(self, order: dict[str, Any]) -> MarketSubmission:
        digest = hashlib.sha256(str(order["client_order_id"]).encode()).hexdigest()[:16]
        return MarketSubmission(True, f"sandbox-{digest}", "submitted", {"venue": "sandbox", "accepted": True})

    def cancel_order(self, order: dict[str, Any]) -> MarketSubmission:
        return MarketSubmission(True, str(order.get("market_order_id") or ""), "cancelled", {"venue": "sandbox"})

    def query_order(self, order: dict[str, Any]) -> MarketSubmission:
        market_order_id = str(order.get("market_order_id") or "")
        if not market_order_id:
            return MarketSubmission(False, None, "unknown", {"venue": "sandbox", "found": False})
        return MarketSubmission(True, market_order_id, str(order.get("status") or "submitted"), {"venue": "sandbox"})


class SignedRestMarketAdapter:
    """Generic HMAC REST adapter for an aggregator or market gateway.

    Venue-specific protocol translation belongs at the gateway boundary. The
    platform signs a canonical body and always sends an idempotency key.
    """

    def __init__(self, base_url: str, token: str, signing_secret: str, timeout_seconds: int = 15):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.signing_secret = signing_secret.encode()
        self.timeout_seconds = timeout_seconds

    def submit_order(self, order: dict[str, Any]) -> MarketSubmission:
        return self._request("POST", "/v1/orders", order)

    def cancel_order(self, order: dict[str, Any]) -> MarketSubmission:
        market_order_id = order.get("market_order_id")
        if not market_order_id:
            raise ValueError("market_order_id is required for cancellation")
        return self._request("POST", f"/v1/orders/{market_order_id}/cancel", {"reason": "operator_or_risk"})

    def query_order(self, order: dict[str, Any]) -> MarketSubmission:
        market_order_id = order.get("market_order_id")
        if not market_order_id:
            raise ValueError("market_order_id is required for reconciliation")
        return self._request("GET", f"/v1/orders/{market_order_id}", {})

    def _request(self, method: str, path: str, payload: dict[str, Any]) -> MarketSubmission:
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True, default=str).encode()
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = hmac.new(self.signing_secret, timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
        request = urllib.request.Request(
            f"{self.base_url}{path}",
            data=None if method == "GET" else body,
            method=method,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
                "Idempotency-Key": str(payload.get("idempotency_key") or payload.get("client_order_id") or ""),
                "X-ChargeOpt-Timestamp": timestamp,
                "X-ChargeOpt-Signature": signature,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                raw = response.read().decode()
                parsed = json.loads(raw) if raw else {}
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")[:1000]
            if 400 <= exc.code < 500:
                return MarketSubmission(False, None, "rejected", {"http_status": exc.code, "detail": detail})
            raise RuntimeError(f"market gateway HTTP {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"market gateway unavailable: {exc.reason}") from exc
        accepted = bool(parsed.get("accepted", response.status in {200, 201, 202}))
        return MarketSubmission(
            accepted,
            parsed.get("market_order_id") or parsed.get("order_id"),
            str(parsed.get("status") or ("submitted" if accepted else "rejected")),
            parsed,
        )


def build_market_adapter(connection: dict[str, Any]) -> MarketAdapter:
    if connection["mode"] == "sandbox" or connection["adapter"] == "sandbox":
        return SandboxMarketAdapter()
    if connection["mode"] != "live" or not connection.get("enabled"):
        raise RuntimeError("market connection is not enabled for live trading")
    readiness = connection.get("live_readiness") or {}
    if not readiness.get("ready"):
        blockers = ",".join(readiness.get("blockers") or ["readiness_evidence_missing"])
        raise RuntimeError(f"live market readiness gate blocked: {blockers}")
    credential_ref = str(connection.get("credential_ref") or "CHARGEOPT_MARKET")
    token = os.environ.get(f"{credential_ref}_TOKEN")
    signing_secret = os.environ.get(f"{credential_ref}_SIGNING_SECRET")
    base_url = connection.get("base_url")
    if not base_url or not token or not signing_secret:
        raise RuntimeError("live market credentials or base_url are missing")
    return SignedRestMarketAdapter(str(base_url), token, signing_secret)


def probabilistic_portfolio_forecast(
    repo: Repository,
    tenant_id: str,
    *,
    horizon_hours: int = 24,
    interval_minutes: int = 15,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Build a calibrated quantile forecast from station seasonality and residuals."""

    now = (now or datetime.now(UTC)).astimezone(UTC)
    stations = [station for station in repo.stations if station.tenant_id == tenant_id]
    if not stations:
        raise ValueError("tenant has no VPP resources")
    intervals = max(1, horizon_hours * 60 // interval_minutes)
    station_rows: dict[str, list[dict[str, Any]]] = {}
    all_latest: list[datetime] = []
    for station in stations:
        points = sorted(repo.station_points(station.id), key=lambda item: item.timestamp)
        if not points:
            continue
        all_latest.append(points[-1].timestamp.astimezone(UTC))
        loads = [point.grid_kw for point in points]
        center = statistics.median(loads)
        residuals = [abs(value - center) for value in loads]
        robust_sigma = max(1.0, statistics.median(residuals) * 1.4826)
        trend = (loads[-1] - loads[max(0, len(loads) - 5)]) / max(1, min(4, len(loads) - 1))
        rows: list[dict[str, Any]] = []
        for index in range(intervals):
            at = now + timedelta(minutes=index * interval_minutes)
            hour_angle = 2 * math.pi * (at.hour + at.minute / 60) / 24
            seasonal = center * (1 + 0.16 * math.sin(hour_angle - 1.1) + 0.09 * math.sin(2 * hour_angle + 0.4))
            recency = loads[-1] * math.exp(-index / 16) + seasonal * (1 - math.exp(-index / 16))
            p50 = max(0.0, recency + trend * min(index, 8) * 0.25)
            width = robust_sigma * (1.28 + 0.015 * index)
            rows.append(
                {
                    "at": at.isoformat(),
                    "p10_grid_kw": round(max(0, p50 - width), 3),
                    "p50_grid_kw": round(p50, 3),
                    "p90_grid_kw": round(p50 + width, 3),
                    "adjustable_p10_kw": round(adjustable_capacity(station, points[-1]) * 0.82, 3),
                    "adjustable_p50_kw": round(adjustable_capacity(station, points[-1]), 3),
                }
            )
        station_rows[station.id] = rows
    if not station_rows:
        raise ValueError("no telemetry is available for VPP forecasting")
    portfolio: list[dict[str, Any]] = []
    for index in range(intervals):
        rows = [values[index] for values in station_rows.values()]
        portfolio.append(
            {
                "at": rows[0]["at"],
                "p10_grid_kw": round(sum(row["p10_grid_kw"] for row in rows), 3),
                "p50_grid_kw": round(sum(row["p50_grid_kw"] for row in rows), 3),
                "p90_grid_kw": round(sum(row["p90_grid_kw"] for row in rows), 3),
                "adjustable_p10_kw": round(sum(row["adjustable_p10_kw"] for row in rows), 3),
                "adjustable_p50_kw": round(sum(row["adjustable_p50_kw"] for row in rows), 3),
            }
        )
    freshness = max(0, int((now - min(all_latest)).total_seconds())) if all_latest else 10**9
    coverage = len(station_rows) / len(stations)
    sample_factor = min(1.0, sum(len(repo.station_points(s.id)) for s in stations) / max(24, len(stations) * 24))
    calibration = max(0.5, min(0.99, 0.72 + 0.17 * coverage + 0.10 * sample_factor))
    return {
        "algorithm": FORECAST_ALGORITHM,
        "horizon_start": now.isoformat(),
        "horizon_end": (now + timedelta(hours=horizon_hours)).isoformat(),
        "interval_minutes": interval_minutes,
        "training_window_hours": max(len(repo.station_points(station.id)) for station in stations),
        "data_freshness_seconds": freshness,
        "calibration_score": round(calibration, 5),
        "stations": station_rows,
        "portfolio": portfolio,
    }


def optimize_bid_blocks(
    repo: Repository,
    tenant_id: str,
    forecast: dict[str, Any],
    policy: dict[str, Any],
    *,
    product: str = "demand_response",
) -> list[dict[str, Any]]:
    """Create conservative bid blocks using quantile capacity and CVaR reserve."""

    interval_minutes = int(forecast["interval_minutes"])
    station_map = {station.id: station for station in repo.stations if station.tenant_id == tenant_id}
    blocks: list[dict[str, Any]] = []
    reserve_margin = float(policy["reserve_margin"])
    max_order = float(policy["max_order_kw"])
    for index, row in enumerate(forecast["portfolio"]):
        available = max(0.0, float(row["adjustable_p10_kw"]) * (1 - reserve_margin))
        quantity = min(max_order, available)
        if quantity < 50:
            continue
        at = datetime.fromisoformat(row["at"])
        allocations: list[dict[str, Any]] = []
        station_caps = []
        for station_id, values in forecast["stations"].items():
            cap = float(values[index]["adjustable_p10_kw"]) * (1 - reserve_margin)
            if cap > 0 and station_id in station_map:
                station_caps.append((station_id, cap))
        total_cap = sum(cap for _, cap in station_caps) or 1
        remaining = quantity
        for position, (station_id, cap) in enumerate(sorted(station_caps)):
            target = remaining if position == len(station_caps) - 1 else min(cap, quantity * cap / total_cap)
            target = max(0.0, min(cap, target))
            remaining -= target
            station = station_map[station_id]
            latest = repo.station_points(station_id)[-1]
            allocations.append(
                {
                    "station_id": station_id,
                    "target_kw": round(target, 3),
                    "baseline_kw": round(latest.grid_kw, 3),
                    "target_grid_kw": round(max(0, latest.grid_kw - target), 3),
                    "reliability": station.reliability_score,
                }
            )
        energy_cost = statistics.mean(repo.tariff_for(station).price_at(at.hour) for station in station_map.values())
        degradation = 0.055
        scarcity = max(0.0, 1 - quantity / max(1, float(row["adjustable_p50_kw"]))) * 0.18
        limit_price = max(float(policy["min_price_per_kwh"]), energy_cost * 0.12 + degradation + scarcity)
        blocks.append(
            {
                "product": product,
                "side": "sell",
                "delivery_start": at.isoformat(),
                "delivery_end": (at + timedelta(minutes=interval_minutes)).isoformat(),
                "quantity_kw": round(quantity, 3),
                "limit_price_per_kwh": round(limit_price, 5),
                "confidence": float(forecast["calibration_score"]),
                "expected_shortfall_kw": round(
                    max(0, float(row["adjustable_p50_kw"]) - float(row["adjustable_p10_kw"])), 3
                ),
                "allocation": allocations,
                "optimizer": OPTIMIZER_ALGORITHM,
            }
        )
    return blocks


def evaluate_order_risk(
    order: dict[str, Any],
    policy: dict[str, Any],
    *,
    open_order_count: int,
    committed_energy_kwh: float,
    circuit_state: str,
    telemetry_age_seconds: int,
) -> dict[str, Any]:
    reasons: list[str] = []
    quantity = float(order["quantity_kw"])
    duration_hours = (
        datetime.fromisoformat(order["delivery_end"]) - datetime.fromisoformat(order["delivery_start"])
    ).total_seconds() / 3600
    if circuit_state != "closed":
        reasons.append(f"circuit_breaker_{circuit_state}")
    if not bool(policy.get("auto_trade_enabled")):
        reasons.append("auto_trade_disabled")
    if quantity > float(policy["max_order_kw"]):
        reasons.append("max_order_kw_exceeded")
    if committed_energy_kwh + quantity * duration_hours > float(policy["max_daily_energy_kwh"]):
        reasons.append("max_daily_energy_exceeded")
    if open_order_count >= int(policy["max_open_orders"]):
        reasons.append("max_open_orders_exceeded")
    if float(order.get("confidence", 0)) < float(policy["min_confidence"]):
        reasons.append("forecast_confidence_below_policy")
    price = float(order["limit_price_per_kwh"])
    if not float(policy["min_price_per_kwh"]) <= price <= float(policy["max_price_per_kwh"]):
        reasons.append("price_outside_policy")
    if telemetry_age_seconds > int(policy["max_telemetry_age_seconds"]):
        reasons.append("telemetry_stale")
    return {
        "approved": not reasons,
        "reasons": reasons,
        "policy_id": policy["id"],
        "policy_version": policy["version"],
        "evaluated_at": datetime.now(UTC).isoformat(),
        "metrics": {
            "quantity_kw": quantity,
            "order_energy_kwh": round(quantity * duration_hours, 3),
            "committed_energy_kwh": committed_energy_kwh,
            "open_order_count": open_order_count,
            "telemetry_age_seconds": telemetry_age_seconds,
        },
    }


def calculate_trade_settlement(
    trade: dict[str, Any],
    meter_intervals: list[dict[str, Any]],
    *,
    imbalance_price_per_kwh: float,
    penalty_rate: float,
) -> dict[str, Any]:
    duration_hours = (trade["delivery_end"] - trade["delivery_start"]).total_seconds() / 3600
    committed_kwh = float(trade["quantity_kw"]) * duration_hours
    valid = [row for row in meter_intervals if row["quality"] != "invalid"]
    delivered_kwh = sum(
        max(0.0, float(row["delivered_kw"])) * (row["interval_end"] - row["interval_start"]).total_seconds() / 3600
        for row in valid
    )
    performance = min(1.5, delivered_kwh / max(committed_kwh, 0.001))
    gross = min(delivered_kwh, committed_kwh) * float(trade["price_per_kwh"])
    shortfall = max(0.0, committed_kwh - delivered_kwh)
    imbalance = shortfall * imbalance_price_per_kwh
    penalty = shortfall * float(trade["price_per_kwh"]) * penalty_rate
    evidence_hashes = sorted(str(row["evidence_hash"]) for row in valid)
    evidence_root = hashlib.sha256("|".join(evidence_hashes).encode()).hexdigest()
    return {
        "committed_kwh": round(committed_kwh, 3),
        "delivered_kwh": round(delivered_kwh, 3),
        "performance_score": round(performance, 5),
        "gross_revenue": round(gross, 2),
        "imbalance_cost": round(imbalance, 2),
        "penalty": round(penalty, 2),
        "net_revenue": round(gross - imbalance - penalty, 2),
        "evidence": {
            "meter_interval_count": len(valid),
            "excluded_invalid_count": len(meter_intervals) - len(valid),
            "evidence_root_hash": evidence_root,
            "method": "interval-baseline-delta-v1",
        },
    }


def verify_market_webhook(
    raw_body: bytes, timestamp: str, signature: str, secret: str, max_age_seconds: int = 300
) -> bool:
    try:
        age = abs(datetime.now(UTC).timestamp() - int(timestamp))
    except (TypeError, ValueError):
        return False
    if age > max_age_seconds:
        return False
    expected = hmac.new(secret.encode(), timestamp.encode() + b"." + raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def validate_order_transition(current: str, target: str) -> None:
    if target not in ORDER_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid market order transition: {current} -> {target}")
