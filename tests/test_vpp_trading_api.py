from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest


@pytest.mark.asyncio
async def test_trading_dashboard_and_automation_endpoints(client):
    dashboard = {
        "generated_at": datetime.now(UTC).isoformat(),
        "connection": {"market_code": "sandbox", "mode": "sandbox"},
        "risk_policy": {"id": "risk-1", "version": 1},
        "circuit_breaker": {"state": "closed"},
        "metrics": {"open_orders": 0},
        "orders": [],
        "automation_runs": [],
        "settlements": [],
    }
    with patch("chargeopt.app.trading_dashboard", return_value=dashboard):
        response = await client.get("/api/vpp/trading/dashboard")
    assert response.status_code == 200
    assert response.json()["circuit_breaker"]["state"] == "closed"

    with patch(
        "chargeopt.app.run_automation_cycle",
        return_value={
            "tenant_id": "t-001",
            "cycle_key": "2026-01-01T00:00:00+00:00",
            "status": "completed",
            "orders_created": 2,
        },
    ) as run:
        response = await client.post("/api/vpp/trading/automation/run", json={"trigger_source": "test"})
    assert response.status_code == 202
    assert response.json()["orders_created"] == 2
    assert run.call_args.kwargs["actor"] == "dev-admin"


@pytest.mark.asyncio
async def test_trade_meter_settlement_and_breaker_endpoints(client):
    start = datetime.now(UTC).replace(microsecond=0)
    trade_payload = {
        "order_id": "ord-1",
        "market_trade_id": "mt-1",
        "quantity_kw": 500,
        "price_per_kwh": 1.2,
        "traded_at": start.isoformat(),
        "payload": {},
    }
    with patch(
        "chargeopt.app.record_trade_fill",
        return_value={"id": "trd-1", "order_id": "ord-1", "status": "filled", "tasks_created": 2},
    ):
        trade = await client.post("/api/vpp/trading/trades", json=trade_payload)
    assert trade.status_code == 201
    assert trade.json()["tasks_created"] == 2

    with patch(
        "chargeopt.app.ingest_meter_interval",
        return_value={"id": "mtr-1", "delivered_kw": 480, "evidence_hash": "a" * 64, "quality": "measured"},
    ):
        meter = await client.post(
            "/api/vpp/trading/meter-intervals",
            json={
                "station_id": "st-hq-hongqiao",
                "interval_start": start.isoformat(),
                "interval_end": (start + timedelta(minutes=15)).isoformat(),
                "baseline_kw": 1000,
                "actual_grid_kw": 520,
                "quality": "measured",
                "source": "revenue-meter-1",
            },
        )
    assert meter.status_code == 202
    assert meter.json()["delivered_kw"] == 480

    with patch(
        "chargeopt.app.create_settlement_batch",
        return_value={
            "id": "stb-1",
            "status": "review",
            "trade_count": 1,
            "gross_revenue": 120,
            "imbalance_cost": 5,
            "penalties": 2,
            "net_revenue": 113,
            "evidence_root_hash": "b" * 64,
        },
    ):
        settlement = await client.post(
            "/api/vpp/trading/settlement-batches",
            json={
                "market_code": "sandbox",
                "period_start": start.isoformat(),
                "period_end": (start + timedelta(hours=1)).isoformat(),
            },
        )
    assert settlement.status_code == 201
    assert settlement.json()["net_revenue"] == 113

    breaker_result = {
        "state": "open",
        "reason": "operator_test",
        "failure_count": 1,
        "opened_at": start,
        "reset_after": None,
        "updated_by": "dev-admin",
        "updated_at": start,
    }
    with patch("chargeopt.app.set_circuit_breaker", return_value=breaker_result):
        breaker = await client.post(
            "/api/vpp/trading/circuit-breaker",
            json={"state": "open", "reason": "operator_test"},
        )
    assert breaker.status_code == 200
    assert breaker.json()["state"] == "open"
