from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_auth_helpers_hash_and_permissions():
    from chargeopt.auth import development_principal, has_permission, hash_password, verify_password

    digest = hash_password("secret-pass", "salt")
    assert verify_password("secret-pass", "salt", digest) is True
    assert verify_password("wrong-pass", "salt", digest) is False
    assert has_permission(development_principal(), "vpp:settle") is True


def test_protocol_normalizers():
    from chargeopt.protocols import normalize_protocol_message

    ocpp = normalize_protocol_message(
        "ocpp",
        "MeterValues",
        {
            "meterValue": [
                {
                    "timestamp": "2026-07-09T12:00:00Z",
                    "sampledValue": [{"measurand": "Power.Active.Import", "value": "240000"}],
                }
            ]
        },
    )
    assert ocpp["kind"] == "telemetry"
    assert ocpp["load_kw"] == 240

    modbus = normalize_protocol_message("modbus", "HoldingRegisters", {"registers": {"40001": 1200, "40003": 0.7}})
    assert modbus["load_kw"] == 1200
    assert modbus["storage_soc"] == 0.7

    mqtt = normalize_protocol_message("mqtt", "telemetry/site", {"load_kw": 88})
    assert mqtt["kind"] == "telemetry"


def test_optimizer_generates_constrained_plan(repo):
    from chargeopt.optimizer import solve_dispatch_optimization

    result = solve_dispatch_optimization(repo, "t-001", None, 4, "balanced")

    assert result["solver"] == "discrete-milp-search-v1"
    assert len(result["dispatch_plan"]) >= 4
    assert result["constraints"]["soc_min"] == 0.22


@pytest.mark.asyncio
async def test_protocol_message_endpoint_ingests_telemetry(client):
    with (
        patch(
            "chargeopt.app.persist_protocol_message",
            return_value={"id": 7, "tenant_id": "t-001", "device_id": "dev-1"},
        ),
        patch(
            "chargeopt.app.ingest_telemetry",
            return_value={
                "station_id": "st-hq-hongqiao",
                "timestamp": "2026-07-09T12:00:00Z",
                "created": True,
                "idempotency_key": "ocpp-key-1",
            },
        ),
    ):
        resp = await client.post(
            "/api/protocols/ocpp/messages",
            json={
                "station_id": "st-hq-hongqiao",
                "external_id": "cp-1",
                "message_type": "MeterValues",
                "idempotency_key": "ocpp-key-1",
                "payload": {
                    "meterValue": [
                        {
                            "timestamp": "2026-07-09T12:00:00Z",
                            "sampledValue": [{"measurand": "Power.Active.Import", "value": "100000"}],
                        }
                    ]
                },
            },
        )

    assert resp.status_code == 202
    assert resp.json()["telemetry_ingested"] is True


@pytest.mark.asyncio
async def test_task_approval_receipt_optimization_and_settlement_endpoints(client):
    with patch(
        "chargeopt.app.enqueue_task",
        return_value={
            "id": "tsk-1",
            "tenant_id": "t-001",
            "station_id": "st-hq-hongqiao",
            "device_id": None,
            "task_type": "dispatch.execute",
            "status": "queued",
            "priority": 10,
            "payload": {"kw": 100},
            "result": {},
        },
    ):
        task = await client.post(
            "/api/tasks",
            json={
                "station_id": "st-hq-hongqiao",
                "task_type": "dispatch.execute",
                "priority": 10,
                "payload": {"kw": 100},
            },
        )
    assert task.status_code == 201

    with patch(
        "chargeopt.app.request_dispatch_approval",
        return_value={"id": "apv-1", "recommendation_id": "rec-1", "status": "pending", "task_id": None},
    ):
        approval = await client.post(
            "/api/dispatch/recommendations/rec-1/approval", json={"actor": "ops", "reason": "review"}
        )
    assert approval.status_code == 201

    with patch(
        "chargeopt.app.review_dispatch_approval",
        return_value={"id": "apv-1", "recommendation_id": "rec-1", "status": "approved", "task_id": "tsk-1"},
    ):
        approved = await client.post(
            "/api/dispatch/recommendations/rec-1/approve", json={"actor": "ops", "reason": "ok"}
        )
    assert approved.json()["task_id"] == "tsk-1"

    with patch(
        "chargeopt.app.record_edge_receipt", return_value={"id": "rcp-1", "task_id": "tsk-1", "status": "succeeded"}
    ):
        receipt = await client.post(
            "/api/edge/receipts", json={"task_id": "tsk-1", "status": "succeeded", "payload": {}}
        )
    assert receipt.status_code == 202

    with patch("chargeopt.app.persist_optimization_run", return_value="opt-1"):
        opt = await client.post("/api/optimization/runs", json={"horizon_hours": 3, "objective": "balanced"})
    assert opt.status_code == 201
    assert opt.json()["id"] == "opt-1"

    with patch(
        "chargeopt.app.settle_vpp_event",
        return_value={
            "id": "set-1",
            "event_id": "vpp-1",
            "performance_score": 0.95,
            "gross_revenue": 100,
            "penalty": 0,
            "net_revenue": 100,
        },
    ):
        settlement = await client.post(
            "/api/vpp/settlements",
            json={"event_id": "vpp-1", "baseline_kw": 1000, "delivered_kw": 950, "settled_by": "ops", "evidence": {}},
        )
    assert settlement.status_code == 201


def test_repository_auth_session_and_settlement_math():
    from chargeopt.repository import authenticate_user, settle_vpp_event

    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda s: s
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    login_row = (
        "usr-1",
        "t-001",
        "operator@example.com",
        "Operator",
        "tenant_admin",
        "salt",
        __import__("chargeopt.auth", fromlist=["hash_password"]).hash_password("password-1", "salt"),
    )
    conn.execute.return_value.fetchone.return_value = login_row
    ctx = MagicMock()
    ctx.__enter__ = lambda s: conn
    ctx.__exit__ = MagicMock(return_value=False)
    with patch("chargeopt.repository.get_connection", return_value=ctx):
        result = authenticate_user("operator@example.com", "password-1")
    assert result["principal"].tenant_id == "t-001"

    conn2 = MagicMock()
    conn2.transaction.return_value.__enter__ = lambda s: s
    conn2.transaction.return_value.__exit__ = MagicMock(return_value=False)
    tenant_cursor = MagicMock()
    tenant_cursor.fetchone.return_value = ("t-001",)
    event_cursor = MagicMock()
    event_cursor.fetchone.return_value = (60, 0.15)
    conn2.execute.side_effect = [tenant_cursor, MagicMock(), event_cursor, MagicMock(), MagicMock(), MagicMock()]
    ctx2 = MagicMock()
    ctx2.__enter__ = lambda s: conn2
    ctx2.__exit__ = MagicMock(return_value=False)
    with patch("chargeopt.repository.get_connection", return_value=ctx2):
        settlement = settle_vpp_event("vpp-1", 1000, 950, "ops", {"source": "meter"})
    assert settlement["net_revenue"] > 0
