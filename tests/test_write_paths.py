from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest


def _transaction_conn() -> MagicMock:
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda s: s
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def _connection_context(conn: MagicMock) -> MagicMock:
    ctx = MagicMock()
    ctx.__enter__ = lambda s: conn
    ctx.__exit__ = MagicMock(return_value=False)
    return ctx


@pytest.mark.asyncio
async def test_write_endpoint_returns_503_without_db(client):
    payload = {
        "station_id": "st-hq-hongqiao",
        "timestamp": "2026-07-09T12:00:00+08:00",
        "load_kw": 1200,
        "pv_kw": 50,
        "grid_kw": 1150,
        "storage_power_kw": 0,
        "storage_soc": 0.65,
        "connector_occupied": 12,
        "queue_length": 1,
        "sessions": 8,
        "energy_kwh": 1080,
        "revenue": 1600,
        "alert_count": 0,
        "idempotency_key": "test-key-telemetry",
    }

    resp = await client.post("/api/telemetry", json=payload)

    assert resp.status_code == 503


@pytest.mark.asyncio
async def test_write_endpoint_disabled_in_production_without_api_key(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from chargeopt import config as cfg
    from chargeopt.app import create_app

    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.delenv("API_KEY", raising=False)
    cfg.get_settings.cache_clear()
    try:
        app = create_app()
        payload = {
            "station_id": "st-hq-hongqiao",
            "timestamp": "2026-07-09T12:00:00+08:00",
            "load_kw": 1200,
            "pv_kw": 50,
            "grid_kw": 1150,
            "storage_power_kw": 0,
            "storage_soc": 0.65,
            "connector_occupied": 12,
            "queue_length": 1,
            "sessions": 8,
            "energy_kwh": 1080,
            "revenue": 1600,
            "alert_count": 0,
            "idempotency_key": "test-key-prod-telemetry",
        }
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post("/api/telemetry", json=payload)
        assert resp.status_code == 503
        assert "API_KEY" in resp.json()["detail"]
    finally:
        cfg.get_settings.cache_clear()


@pytest.mark.asyncio
async def test_telemetry_ingest_endpoint_success(client):
    payload = {
        "station_id": "st-hq-hongqiao",
        "timestamp": "2026-07-09T12:00:00+08:00",
        "load_kw": 1200,
        "pv_kw": 50,
        "grid_kw": 1150,
        "storage_power_kw": 0,
        "storage_soc": 0.65,
        "connector_occupied": 12,
        "queue_length": 1,
        "sessions": 8,
        "energy_kwh": 1080,
        "revenue": 1600,
        "alert_count": 0,
        "idempotency_key": "test-key-telemetry",
    }

    with patch(
        "chargeopt.app.ingest_telemetry",
        return_value={
            "station_id": "st-hq-hongqiao",
            "timestamp": "2026-07-09T12:00:00+08:00",
            "created": True,
            "idempotency_key": "test-key-telemetry",
        },
    ):
        resp = await client.post("/api/telemetry", json=payload)

    assert resp.status_code == 202
    assert resp.json()["created"] is True


@pytest.mark.asyncio
async def test_alert_acknowledge_endpoint_success(client):
    with patch(
        "chargeopt.app.acknowledge_alert",
        return_value={"id": "al-001", "acknowledged": True},
    ):
        resp = await client.post("/api/alerts/al-001/acknowledge", json={"actor": "operator.li"})

    assert resp.status_code == 200
    assert resp.json() == {"id": "al-001", "acknowledged": True}


@pytest.mark.asyncio
async def test_dispatch_generate_endpoint_success(client):
    with patch("chargeopt.app.persist_dispatch_recommendations", return_value=3):
        resp = await client.post("/api/dispatch/recommendations/generate", json={"actor": "system"})

    assert resp.status_code == 201
    body = resp.json()
    assert body["generated"] == 3
    assert len(body["recommendations"]) == 3


@pytest.mark.asyncio
async def test_dispatch_status_endpoint_success(client):
    with patch(
        "chargeopt.app.update_dispatch_status",
        return_value={"id": "rec-1", "status": "approved"},
    ):
        resp = await client.patch(
            "/api/dispatch/recommendations/rec-1",
            json={"status": "approved", "actor": "operator.li", "reason": "ok"},
        )

    assert resp.status_code == 200
    assert resp.json()["status"] == "approved"


@pytest.mark.asyncio
async def test_roi_simulation_persist_endpoint_success(client):
    with patch("chargeopt.app.persist_roi_simulation", return_value=42):
        resp = await client.post(
            "/api/roi/simulations",
            json={
                "station_id": "st-hq-hongqiao",
                "capacity_kwh": 1200,
                "power_kw": 600,
                "capex_per_kwh": 1150,
                "vpp": True,
            },
        )

    assert resp.status_code == 201
    assert resp.json()["id"] == 42


def test_repository_ingest_telemetry_writes_and_audits():
    from chargeopt.repository import ingest_telemetry

    conn = _transaction_conn()
    conn.execute.return_value.fetchone.return_value = None

    with patch("chargeopt.repository.get_connection", return_value=_connection_context(conn)):
        result = ingest_telemetry(
            {
                "station_id": "st-1",
                "timestamp": datetime(2026, 7, 9, 12, tzinfo=UTC),
                "load_kw": 1.0,
                "pv_kw": 0.0,
                "grid_kw": 1.0,
                "storage_power_kw": 0.0,
                "storage_soc": 0.5,
                "connector_occupied": 1,
                "queue_length": 0,
                "sessions": 1,
                "energy_kwh": 1.0,
                "revenue": 1.0,
                "alert_count": 0,
                "idempotency_key": "idem-1",
                "actor": "edge",
            }
        )

    assert result["created"] is True
    assert conn.execute.call_count >= 4


def test_repository_acknowledge_unknown_alert_raises():
    from chargeopt.repository import acknowledge_alert

    conn = _transaction_conn()
    cursor = MagicMock()
    cursor.rowcount = 0
    conn.execute.return_value = cursor

    with (
        patch("chargeopt.repository.get_connection", return_value=_connection_context(conn)),
        pytest.raises(KeyError),
    ):
        acknowledge_alert("missing", "operator")


def test_repository_dispatch_status_validates_status():
    from chargeopt.repository import update_dispatch_status

    with pytest.raises(ValueError):
        update_dispatch_status("rec-1", "bad", "operator", None)


def test_repository_persist_roi_returns_id():
    from chargeopt.repository import persist_roi_simulation

    conn = _transaction_conn()
    conn.execute.return_value.fetchone.return_value = (42,)

    roi = {
        "capacity_kwh": 1200,
        "power_kw": 600,
        "capex": 1536000,
        "annual_demand_savings": 100,
        "annual_arbitrage": 100,
        "annual_vpp_revenue": 100,
        "annual_degradation_cost": 10,
        "annual_maintenance": 10,
        "annual_net_benefit": 280,
        "payback_years": 4.1,
        "irr": 19.0,
        "npv_10y": 1000,
        "recommendation": "invest",
    }

    with patch("chargeopt.repository.get_connection", return_value=_connection_context(conn)):
        simulation_id = persist_roi_simulation("st-1", roi, {"capacity_kwh": 1200})

    assert simulation_id == 42
