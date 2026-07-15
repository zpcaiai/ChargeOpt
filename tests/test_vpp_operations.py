"""Durability tests for outbox publishing and market reconciliation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from chargeopt.vpp_operations import process_outbox_batch, reconcile_market_orders, run_operational_maintenance
from chargeopt.vpp_trading import MarketSubmission


def _order(status: str = "ready") -> dict:
    return {
        "id": "ord-1",
        "tenant_id": "t-001",
        "connection_id": "mc-1",
        "client_order_id": "co-1",
        "market_order_id": "market-1" if status != "ready" else None,
        "market_code": "sandbox",
        "product": "demand_response",
        "side": "sell",
        "delivery_start": "2026-07-15T12:00:00+00:00",
        "delivery_end": "2026-07-15T12:15:00+00:00",
        "quantity_kw": 500,
        "limit_price_per_kwh": 1.2,
        "allocation": [],
        "idempotency_key": "idem-1",
        "status": status,
        "connection": {
            "id": "mc-1",
            "adapter": "sandbox",
            "mode": "sandbox",
            "enabled": True,
            "participant_id": "participant-1",
        },
    }


def test_outbox_publisher_submits_and_completes_message():
    message = {
        "id": "obx-1",
        "tenant_id": "t-001",
        "aggregate_id": "ord-1",
        "topic": "market.order.submit",
        "attempts": 1,
    }
    adapter = SimpleNamespace(
        submit_order=lambda payload: MarketSubmission(True, "market-1", "submitted", {"accepted": True})
    )
    order = _order()
    with (
        patch("chargeopt.vpp_operations.claim_outbox_messages", return_value=[message]),
        patch("chargeopt.vpp_operations.get_order_for_operation", return_value=order),
        patch("chargeopt.vpp_operations.build_market_adapter", return_value=adapter),
        patch("chargeopt.vpp_operations.transition_market_order", return_value=order) as transition,
        patch("chargeopt.vpp_operations.finish_outbox_message", return_value={"status": "published"}) as finish,
    ):
        result = process_outbox_batch("worker-1")
    assert result == {"claimed": 1, "published": 1, "retried": 0, "dead_letter": 0}
    assert transition.call_count == 2
    finish.assert_called_once_with("obx-1", "worker-1", published=True)


def test_outbox_publisher_dead_letters_and_opens_breaker():
    message = {
        "id": "obx-1",
        "tenant_id": "t-001",
        "aggregate_id": "ord-1",
        "topic": "market.order.submit",
        "attempts": 8,
    }
    order = _order("submitting")
    with (
        patch("chargeopt.vpp_operations.claim_outbox_messages", return_value=[message]),
        patch("chargeopt.vpp_operations.get_order_for_operation", return_value=order),
        patch("chargeopt.vpp_operations.build_market_adapter", side_effect=RuntimeError("venue down")),
        patch("chargeopt.vpp_operations.finish_outbox_message", return_value={"status": "dead_letter"}) as finish,
        patch("chargeopt.vpp_operations.transition_market_order") as transition,
        patch("chargeopt.vpp_operations.set_circuit_breaker") as breaker,
    ):
        result = process_outbox_batch("worker-1")
    assert result["dead_letter"] == 1
    assert finish.call_args.kwargs["published"] is False
    transition.assert_called_once()
    breaker.assert_called_once_with("t-001", "open", "market_outbox_dead_letter", "worker-1")


def test_reconciliation_matches_remote_order():
    order = _order("submitted")
    adapter = SimpleNamespace(
        query_order=lambda payload: MarketSubmission(True, "market-1", "submitted", {"status": "submitted"})
    )
    with (
        patch("chargeopt.vpp_operations.list_orders_for_reconciliation", return_value=[order]),
        patch("chargeopt.vpp_operations.build_market_adapter", return_value=adapter),
        patch("chargeopt.vpp_operations.mark_order_reconciled") as mark,
    ):
        result = reconcile_market_orders("worker-1")
    assert result == {"checked": 1, "matched": 1, "corrected": 0, "unknown": 0}
    mark.assert_called_once_with("t-001", "ord-1", "matched", "worker-1", {"status": "submitted"})


def test_operational_maintenance_records_heartbeat():
    with (
        patch("chargeopt.vpp_operations.reap_expired_tasks", return_value={"requeued": 1, "failed": 0, "total": 1}),
        patch(
            "chargeopt.vpp_operations.process_outbox_batch",
            return_value={"claimed": 0, "published": 0, "retried": 0, "dead_letter": 0},
        ),
        patch(
            "chargeopt.vpp_operations.reconcile_market_orders",
            return_value={"checked": 0, "matched": 0, "corrected": 0, "unknown": 0},
        ),
        patch("chargeopt.vpp_operations.list_automation_tenants", return_value=["t-001"]),
        patch("chargeopt.vpp_operations.record_operational_heartbeat") as heartbeat,
    ):
        result = run_operational_maintenance("worker-1")
    assert result["status"] == "healthy"
    heartbeat.assert_called_once()
