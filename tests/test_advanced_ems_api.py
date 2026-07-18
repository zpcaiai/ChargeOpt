from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from chargeopt.app import create_app, resolve_principal
from chargeopt.auth import Principal


@pytest.mark.asyncio
async def test_ems_capabilities_are_explicit_about_control_boundary(client):
    response = await client.get("/api/v1/ems/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["field_control_available"] is False
    assert payload["control_mode"] == "recommendation_and_shadow_only"
    assert "forecast" in payload["algorithms"]
    assert "external AC study required" in payload["network_certificate_scope"]


@pytest.mark.asyncio
async def test_ems_forecast_uses_synthetic_evidence_without_database(client):
    response = await client.post(
        "/api/v1/ems/forecasts",
        json={
            "station_id": "st-hq-hongqiao",
            "horizon": 8,
            "interval_minutes": 60,
            "scenario_count": 8,
            "idempotency_key": "forecast-api-test-001",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["evidence_class"] == "synthetic"
    assert payload["evidence"] == {
        "id": None,
        "persisted": False,
        "replayed": False,
        "reason": "database_disabled",
    }
    assert len(payload["rows"]) == 8
    assert len(payload["scenarios_kw"]) == 8


@pytest.mark.asyncio
async def test_ems_foundation_model_partial_configuration_returns_503(client, monkeypatch):
    monkeypatch.setenv("CHARGEOPT_TSF_ENDPOINT", "https://models.example.com/forecast")
    monkeypatch.delenv("CHARGEOPT_TSF_TOKEN", raising=False)
    response = await client.post(
        "/api/v1/ems/forecasts",
        json={
            "station_id": "st-hq-hongqiao",
            "horizon": 4,
            "use_foundation_model": True,
            "idempotency_key": "foundation-config-test",
        },
    )
    assert response.status_code == 503
    assert "Both CHARGEOPT_TSF_ENDPOINT" in response.json()["detail"]


@pytest.mark.asyncio
async def test_ems_dispatch_returns_risk_evidence_but_cannot_execute(client):
    response = await client.post(
        "/api/v1/ems/dispatch-runs",
        json={
            "station_id": "st-hq-hongqiao",
            "history_kw": [900 + (index % 6) * 20 for index in range(48)],
            "horizon": 6,
            "interval_minutes": 60,
            "scenario_count": 6,
            "risk_alpha": 0.9,
            "idempotency_key": "dispatch-api-test-001",
        },
    )
    assert response.status_code == 201, response.text
    payload = response.json()
    assert payload["exact"] is True
    assert payload["execution_authorized"] is False
    assert payload["risk"]["cvar_cost"] >= payload["risk"]["var_cost"]
    assert payload["forecast_evidence"]["evidence_class"] == "replay"
    assert len(payload["dispatch_plan"]) == 6


@pytest.mark.asyncio
async def test_ems_network_and_portfolio_validate_tenant_station_ids(client):
    network = {
        "root_bus": "grid",
        "transformer_limit_kw": 300,
        "minimum_voltage_pu": 0.94,
        "voltage_kv": 0.4,
        "lines": [
            {
                "from_bus": "grid",
                "to_bus": "hq",
                "phase": "A",
                "limit_kw": 100,
                "resistance_ohm": 0.005,
                "reactance_ohm": 0.003,
            }
        ],
    }
    response = await client.post(
        "/api/v1/ems/network-projections",
        json={
            "tenant_id": "t-001",
            "network": network,
            "proposals": [
                {
                    "station_id": "st-hq-hongqiao",
                    "bus": "hq",
                    "phase": "A",
                    "proposed_kw": 120,
                }
            ],
            "idempotency_key": "network-api-test-001",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["ac_certified"] is False

    rejected = await client.post(
        "/api/v1/ems/network-projections",
        json={
            "tenant_id": "t-001",
            "network": network,
            "proposals": [{"station_id": "foreign", "bus": "hq", "phase": "A", "proposed_kw": 20}],
            "idempotency_key": "network-api-test-002",
        },
    )
    assert rejected.status_code == 404

    coordinated = await client.post(
        "/api/v1/ems/portfolio-coordination",
        json={
            "tenant_id": "t-001",
            "resources": [
                {"station_id": "st-hq-hongqiao", "maximum_kw": 200, "quadratic_cost": 1},
                {"station_id": "st-wg-waigaoqiao", "maximum_kw": 300, "quadratic_cost": 0.7},
            ],
            "target_kw": 320,
            "idempotency_key": "coord-api-test-001",
        },
    )
    assert coordinated.status_code == 201, coordinated.text
    assert coordinated.json()["allocated_kw"] == pytest.approx(320)
    assert coordinated.json()["execution_authorized"] is False


@pytest.mark.asyncio
async def test_ems_write_is_denied_to_read_only_analyst():
    app = create_app(use_lifespan=False)
    app.dependency_overrides[resolve_principal] = lambda: Principal(
        subject="analyst-1",
        tenant_id="t-001",
        role="analyst",
        display_name="Analyst",
        auth_type="test",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        readable = await client.get("/api/v1/ems/capabilities")
        denied = await client.post(
            "/api/v1/ems/forecasts",
            json={
                "station_id": "st-hq-hongqiao",
                "horizon": 4,
                "idempotency_key": "analyst-write-test",
            },
        )
    assert readable.status_code == 200
    assert denied.status_code == 403


def test_advanced_ems_migration_is_immutable_and_fail_closed():
    from pathlib import Path

    sql = (Path(__file__).resolve().parents[1] / "migrations" / "016_advanced_ems.sql").read_text()
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL" in sql
    assert "BEFORE UPDATE OR DELETE" in sql
    assert "UNIQUE (tenant_id, evidence_type, idempotency_key)" in sql


def test_advanced_ems_operations_ui_has_stable_controls_and_render_paths():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    html = (root / "static" / "index.html").read_text()
    javascript = (root / "static" / "app.js").read_text()
    stylesheet = (root / "static" / "styles.css").read_text()

    for element_id in ("emsBoundary", "emsAlgorithms", "emsHorizon", "emsRisk", "emsRun", "emsResult", "emsPlan"):
        assert f'id="{element_id}"' in html
    assert 'api("/api/ems/dispatch-runs"' in javascript
    assert 'api("/api/ems/capabilities"' in javascript
    assert "function renderEms()" in javascript
    assert "execution_authorized" not in html
    assert "@media (max-width: 760px)" in stylesheet
    assert ".ems-controls," in stylesheet
