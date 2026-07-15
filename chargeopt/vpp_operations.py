"""Durable outbox publishing, market reconciliation, and operational maintenance."""

from __future__ import annotations

import argparse
import json
import os
import time
from datetime import UTC, datetime
from typing import Any

import structlog

from .config import get_settings
from .repository import reap_expired_tasks
from .vpp_automation import run_all_automation_cycles
from .vpp_repository import (
    claim_outbox_messages,
    finish_outbox_message,
    get_order_for_operation,
    list_automation_tenants,
    list_orders_for_reconciliation,
    mark_order_reconciled,
    record_operational_heartbeat,
    set_circuit_breaker,
    transition_market_order,
)
from .vpp_trading import ORDER_TRANSITIONS, build_market_adapter

logger = structlog.get_logger(__name__)


def _submission_target_status(current_status: str, result: Any) -> str:
    """Normalize venue responses before applying the local order state machine."""
    if not result.accepted:
        return "rejected"
    status = str(result.status or "submitted").lower()
    if status == "accepted":
        status = "submitted"
    allowed = ORDER_TRANSITIONS.get(current_status, set())
    return status if status in allowed else "submitted"


def _submission_payload(order: dict[str, Any]) -> dict[str, Any]:
    return {
        "client_order_id": order["client_order_id"],
        "idempotency_key": order["idempotency_key"],
        "market_code": order["market_code"],
        "participant_id": order["connection"]["participant_id"],
        "product": order["product"],
        "side": order["side"],
        "delivery_start": order["delivery_start"],
        "delivery_end": order["delivery_end"],
        "quantity_kw": float(order["quantity_kw"]),
        "limit_price_per_kwh": float(order["limit_price_per_kwh"]),
        "allocation": order["allocation"],
    }


def process_outbox_batch(worker_id: str, *, limit: int = 20) -> dict[str, int]:
    messages = claim_outbox_messages(worker_id, limit=limit)
    published = 0
    retried = 0
    dead_letter = 0
    for message in messages:
        tenant_id = str(message["tenant_id"])
        order_id = str(message["aggregate_id"])
        order: dict[str, Any] | None = None
        try:
            if message["topic"] != "market.order.submit":
                raise ValueError(f"unsupported outbox topic: {message['topic']}")
            order = get_order_for_operation(tenant_id, order_id)
            if order["status"] in {"submitted", "partially_filled", "filled", "cancelled", "rejected", "expired"}:
                finish_outbox_message(str(message["id"]), worker_id, published=True)
                published += 1
                continue
            if order["status"] not in {"ready", "submitting"}:
                raise ValueError(f"order cannot be submitted from state {order['status']}")
            adapter = build_market_adapter(order["connection"])
            if order["status"] == "ready":
                order = transition_market_order(
                    tenant_id,
                    order_id,
                    "submitting",
                    worker_id,
                    {"outbox_id": message["id"], "attempt": message["attempts"]},
                )
                order["connection"] = get_order_for_operation(tenant_id, order_id)["connection"]
            result = adapter.submit_order(_submission_payload(order))
            target = _submission_target_status(str(order["status"]), result)
            transition_market_order(
                tenant_id,
                order_id,
                target,
                worker_id,
                result.raw,
                market_order_id=result.market_order_id,
                last_error=None if result.accepted else str(result.raw)[:1000],
            )
            finish_outbox_message(str(message["id"]), worker_id, published=True)
            published += 1
        except Exception as exc:
            outcome = finish_outbox_message(
                str(message["id"]),
                worker_id,
                published=False,
                error=f"{type(exc).__name__}: {exc}"[:2000],
            )
            if outcome["status"] == "dead_letter":
                dead_letter += 1
                if order and order["status"] == "submitting":
                    transition_market_order(
                        tenant_id,
                        order_id,
                        "failed",
                        worker_id,
                        {"reason": "outbox_dead_letter", "error": str(exc)},
                        last_error=str(exc)[:1000],
                    )
                set_circuit_breaker(tenant_id, "open", "market_outbox_dead_letter", worker_id)
            else:
                retried += 1
            logger.warning(
                "vpp_outbox_publish_failed",
                tenant_id=tenant_id,
                order_id=order_id,
                outbox_id=message["id"],
                outcome=outcome["status"],
                error=str(exc),
            )
    return {"claimed": len(messages), "published": published, "retried": retried, "dead_letter": dead_letter}


def reconcile_market_orders(worker_id: str, *, limit: int = 100) -> dict[str, int]:
    orders = list_orders_for_reconciliation(limit=limit)
    matched = 0
    corrected = 0
    unknown = 0
    for order in orders:
        tenant_id = str(order["tenant_id"])
        order_id = str(order["id"])
        try:
            result = build_market_adapter(order["connection"]).query_order(order)
            remote_status = result.status.lower()
            current_status = str(order["status"])
            if remote_status == current_status:
                mark_order_reconciled(tenant_id, order_id, "matched", worker_id, result.raw)
                matched += 1
            elif remote_status in ORDER_TRANSITIONS.get(current_status, set()):
                transition_market_order(
                    tenant_id,
                    order_id,
                    remote_status,
                    worker_id,
                    {"source": "market_reconciliation", **result.raw},
                    market_order_id=result.market_order_id,
                )
                mark_order_reconciled(tenant_id, order_id, "matched", worker_id, result.raw)
                corrected += 1
            else:
                mark_order_reconciled(
                    tenant_id,
                    order_id,
                    "mismatch",
                    worker_id,
                    {"local_status": current_status, "remote_status": remote_status, **result.raw},
                )
                set_circuit_breaker(tenant_id, "open", "market_order_reconciliation_mismatch", worker_id)
                unknown += 1
        except Exception as exc:
            mark_order_reconciled(
                tenant_id,
                order_id,
                "unknown",
                worker_id,
                {"error_type": type(exc).__name__, "error": str(exc)[:1000]},
            )
            unknown += 1
    return {"checked": len(orders), "matched": matched, "corrected": corrected, "unknown": unknown}


def run_operational_maintenance(instance_id: str = "vpp-operations") -> dict[str, Any]:
    started_at = datetime.now(UTC)
    task_reaper = reap_expired_tasks("*", instance_id)
    outbox = process_outbox_batch(instance_id)
    reconciliation = reconcile_market_orders(instance_id)
    status = "degraded" if outbox["dead_letter"] or reconciliation["unknown"] else "healthy"
    detail = {
        "task_reaper": task_reaper,
        "outbox": outbox,
        "reconciliation": reconciliation,
        "duration_ms": round((datetime.now(UTC) - started_at).total_seconds() * 1000, 2),
    }
    for tenant_id in list_automation_tenants():
        record_operational_heartbeat(tenant_id, "vpp-operations", instance_id, status, detail)
    return {"status": status, **detail}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the durable ChargeOpt VPP operations worker.")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--instance-id", default=os.environ.get("CHARGEOPT_VPP_WORKER_ID", "vpp-operations"))
    parser.add_argument("--poll-interval", type=float, default=5.0)
    parser.add_argument("--automation-interval", type=float, default=300.0)
    args = parser.parse_args(argv)
    settings = get_settings()
    next_automation_at = 0.0
    while True:
        now = time.monotonic()
        automation = None
        if settings.vpp_automation_enabled and now >= next_automation_at:
            automation = run_all_automation_cycles(trigger_source=f"ha-worker:{args.instance_id}")
            next_automation_at = now + args.automation_interval
        elif not settings.vpp_automation_enabled:
            automation = {"status": "disabled", "tenant_count": 0, "results": []}
            next_automation_at = now + args.automation_interval
        result = run_operational_maintenance(args.instance_id) | {"automation": automation}
        print(json.dumps(result, default=str), flush=True)
        if args.once:
            return 0 if result["status"] != "failed" else 1
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
