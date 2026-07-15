"""Idempotent unattended VPP trading cycle orchestration."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

import structlog

from .repository import load_repository_from_db
from .vpp_repository import (
    claim_automation_cycle,
    create_market_order,
    finish_automation_cycle,
    get_trading_context,
    list_automation_tenants,
    persist_forecast,
)
from .vpp_trading import (
    evaluate_order_risk,
    optimize_bid_blocks,
    probabilistic_portfolio_forecast,
)

logger = structlog.get_logger(__name__)


def _cycle_key(now: datetime, interval_minutes: int = 5) -> str:
    bucket = now.minute - now.minute % interval_minutes
    return now.replace(minute=bucket, second=0, microsecond=0).isoformat()


def run_automation_cycle(
    tenant_id: str,
    *,
    trigger_source: str = "scheduler",
    actor: str = "vpp-autopilot",
    now: datetime | None = None,
    max_orders_per_cycle: int = 8,
) -> dict[str, Any]:
    now = (now or datetime.now(UTC)).astimezone(UTC)
    cycle_key = _cycle_key(now)
    run_id = claim_automation_cycle(tenant_id, cycle_key, trigger_source)
    if run_id is None:
        return {"tenant_id": tenant_id, "cycle_key": cycle_key, "status": "duplicate", "orders_created": 0}

    forecast_id: str | None = None
    orders_created = 0
    enqueued = 0
    rejected = 0
    failures: list[str] = []
    try:
        context = get_trading_context(tenant_id)
        breaker = context["circuit_breaker"]
        if breaker["state"] != "closed":
            summary = {"reason": "circuit_breaker_not_closed", "breaker": breaker}
            finish_automation_cycle(tenant_id, run_id, "skipped", summary)
            return {"tenant_id": tenant_id, "cycle_key": cycle_key, "status": "skipped", "orders_created": 0, **summary}
        connection = context["connection"]
        if connection["mode"] == "live" and not (connection.get("live_readiness") or {}).get("ready"):
            summary = {
                "reason": "live_market_readiness_gate",
                "blockers": (connection.get("live_readiness") or {}).get("blockers", ["readiness_unknown"]),
            }
            finish_automation_cycle(tenant_id, run_id, "skipped", summary)
            return {"tenant_id": tenant_id, "cycle_key": cycle_key, "status": "skipped", "orders_created": 0, **summary}

        repo = load_repository_from_db(tenant_id)
        forecast = probabilistic_portfolio_forecast(repo, tenant_id, now=now)
        forecast_id = persist_forecast(tenant_id, forecast)
        bids = optimize_bid_blocks(repo, tenant_id, forecast, context["policy"])
        for bid in bids[:max_orders_per_cycle]:
            idempotency_seed = "|".join(
                [tenant_id, context["connection"]["id"], bid["product"], bid["delivery_start"], bid["side"]]
            )
            idempotency_key = hashlib.sha256(idempotency_seed.encode()).hexdigest()
            risk = evaluate_order_risk(
                bid,
                context["policy"],
                open_order_count=context["open_order_count"] + enqueued,
                committed_energy_kwh=context["committed_energy_kwh"],
                circuit_state=context["circuit_breaker"]["state"],
                telemetry_age_seconds=forecast["data_freshness_seconds"],
            )
            order = create_market_order(
                tenant_id,
                context["connection"]["id"],
                forecast_id,
                context["connection"]["market_code"],
                bid,
                risk,
                actor,
                idempotency_key,
            )
            if order["status"] == "risk_rejected":
                rejected += 1
                continue
            if order["status"] != "ready":
                continue
            orders_created += 1
            enqueued += 1
        status = "degraded" if failures else "completed"
        summary = {
            "candidate_bids": len(bids),
            "orders_created": orders_created,
            "enqueued_for_submission": enqueued,
            "risk_or_market_rejected": rejected,
            "failures": failures[:10],
            "forecast_calibration": forecast["calibration_score"],
            "telemetry_age_seconds": forecast["data_freshness_seconds"],
        }
        finish_automation_cycle(
            tenant_id,
            run_id,
            status,
            summary,
            forecast_run_id=forecast_id,
            orders_created=orders_created,
        )
        logger.info("vpp_automation_cycle", tenant_id=tenant_id, cycle_key=cycle_key, status=status, **summary)
        return {"tenant_id": tenant_id, "cycle_key": cycle_key, "status": status, **summary}
    except Exception as exc:
        finish_automation_cycle(
            tenant_id,
            run_id,
            "failed",
            {"phase": "cycle", "error_type": type(exc).__name__},
            forecast_run_id=forecast_id,
            orders_created=orders_created,
            error=str(exc)[:2000],
        )
        logger.exception("vpp_automation_cycle_failed", tenant_id=tenant_id, cycle_key=cycle_key)
        raise


def run_all_automation_cycles(*, trigger_source: str = "cron") -> dict[str, Any]:
    tenants = list_automation_tenants()
    results: list[dict[str, Any]] = []
    for tenant_id in tenants:
        try:
            results.append(run_automation_cycle(tenant_id, trigger_source=trigger_source))
        except Exception as exc:
            results.append({"tenant_id": tenant_id, "status": "failed", "error": str(exc)[:1000]})
    return {
        "status": "completed" if all(row["status"] != "failed" for row in results) else "degraded",
        "tenant_count": len(tenants),
        "results": results,
    }
