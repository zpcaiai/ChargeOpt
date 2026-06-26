"""Integration tests for the FastAPI HTTP layer."""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_health_returns_ok(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "version" in body


@pytest.mark.asyncio
async def test_overview_endpoint(client):
    resp = await client.get("/api/overview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["totals"]["station_count"] == 3
    assert body["totals"]["today_revenue"] > 0


@pytest.mark.asyncio
async def test_stations_list(client):
    resp = await client.get("/api/stations")
    assert resp.status_code == 200
    stations = resp.json()["stations"]
    assert len(stations) == 3
    ids = {s["id"] for s in stations}
    assert "st-hq-hongqiao" in ids


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "station_id",
    [
        "st-hq-hongqiao",
        "st-wg-waigaoqiao",
        "st-sz-industrial",
    ],
)
async def test_station_detail_all(client, station_id):
    resp = await client.get(f"/api/stations/{station_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["station"]["id"] == station_id
    assert len(body["telemetry"]) == 24
    assert len(body["forecast"]) == 24
    assert len(body["storage_plan"]) == 24


@pytest.mark.asyncio
async def test_station_detail_unknown_returns_404(client):
    resp = await client.get("/api/stations/st-does-not-exist")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_dispatch_endpoint(client):
    resp = await client.get("/api/dispatch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["approval_required"] is True
    assert body["summary"]["count"] == len(body["recommendations"])


@pytest.mark.asyncio
async def test_vpp_endpoint(client):
    resp = await client.get("/api/vpp")
    assert resp.status_code == 200
    body = resp.json()
    assert body["reliable_capacity_kw"] > 0
    assert len(body["resources"]) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "params,expect_invest",
    [
        ({"capacity_kwh": 1200, "power_kw": 600, "capex_per_kwh": 1150, "vpp": True}, True),
        ({"capacity_kwh": 500, "power_kw": 250, "capex_per_kwh": 1150, "vpp": False}, False),
    ],
)
async def test_roi_variants(client, params, expect_invest):
    resp = await client.get("/api/roi", params=params)
    assert resp.status_code == 200
    body = resp.json()
    assert body["capex"] > 0
    assert body["payback_years"] > 0
    assert body["recommendation"] in {"invest", "review"}


@pytest.mark.asyncio
async def test_roi_bad_input_returns_422(client):
    resp = await client.get("/api/roi", params={"capacity_kwh": -1})
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_audit_endpoint(client):
    resp = await client.get("/api/audit")
    assert resp.status_code == 200
    assert "audit" in resp.json()


@pytest.mark.asyncio
async def test_request_id_propagated(client):
    sent_id = "test-request-id-1234"
    resp = await client.get("/health", headers={"X-Request-Id": sent_id})
    assert resp.headers.get("x-request-id") == sent_id


@pytest.mark.asyncio
async def test_request_id_generated_when_absent(client):
    resp = await client.get("/health")
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) > 0


@pytest.mark.asyncio
async def test_api_key_required_when_configured():
    """When API_KEY env var is set, unauthenticated requests are rejected."""
    import os

    from httpx import ASGITransport, AsyncClient

    from chargeopt import config as cfg_module
    from chargeopt.app import create_app

    cfg_module.get_settings.cache_clear()
    os.environ["API_KEY"] = "secret-test-key"
    try:
        test_app = create_app()
        async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as ac:
            resp = await ac.get("/api/overview")
            assert resp.status_code == 401
            resp2 = await ac.get("/api/overview", headers={"X-API-Key": "secret-test-key"})
            assert resp2.status_code == 200
    finally:
        del os.environ["API_KEY"]
        cfg_module.get_settings.cache_clear()
