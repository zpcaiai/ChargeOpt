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

    assert result["solver"] == "scipy-highs-milp-mpc-v1"
    assert len(result["dispatch_plan"]) >= 4
    assert result["constraints"]["soc_min"] == 0.24
    assert "shadow_price" in result["dispatch_plan"][0]
    assert all(item["exact"] is True for item in result["constraints"]["solver_evidence"])


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

    with patch(
        "chargeopt.app.settle_vpp_event",
        return_value={
            "id": "set-2",
            "event_id": "vpp-1",
            "performance_score": 1.0,
            "gross_revenue": 120,
            "penalty": 0,
            "net_revenue": 120,
        },
    ) as settle:
        settlement_default_actor = await client.post(
            "/api/vpp/settlements",
            json={"event_id": "vpp-1", "baseline_kw": 1000, "delivered_kw": 1000, "evidence": {}},
        )
    assert settlement_default_actor.status_code == 201
    assert settle.call_args.args[3] == "dev-admin"


@pytest.mark.asyncio
async def test_task_worker_claim_complete_and_reap_endpoints(client):
    task_payload = {
        "id": "tsk-1",
        "tenant_id": "t-001",
        "station_id": "st-hq-hongqiao",
        "device_id": None,
        "task_type": "dispatch.execute",
        "status": "running",
        "priority": 10,
        "payload": {"kw": 100},
        "result": {},
        "attempts": 1,
        "max_attempts": 3,
        "lease_expires_at": "2026-07-10T08:00:00Z",
        "locked_by": "worker-1",
        "last_error": None,
    }
    with patch("chargeopt.app.claim_next_task", return_value=task_payload) as claim:
        claimed = await client.post(
            "/api/tasks/claim",
            json={"worker_id": "worker-1", "task_types": ["dispatch.execute"], "lease_seconds": 120},
        )
    assert claimed.status_code == 200
    assert claimed.json()["task"]["id"] == "tsk-1"
    assert claim.call_args.args[1] == "worker-1"

    completed_payload = task_payload | {
        "status": "completed",
        "result": {"edge_status": "succeeded"},
        "locked_by": None,
        "lease_expires_at": None,
    }
    with patch("chargeopt.app.complete_task", return_value=completed_payload) as complete:
        completed = await client.post(
            "/api/tasks/tsk-1/complete",
            json={"worker_id": "worker-1", "status": "succeeded", "result": {"edge_status": "succeeded"}},
        )
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert complete.call_args.args[0] == "tsk-1"

    with patch("chargeopt.app.reap_expired_tasks", return_value={"requeued": 2, "failed": 1, "total": 3}) as reap:
        reaped = await client.post("/api/tasks/reap-expired", json={"actor": "ops"})
    assert reaped.status_code == 200
    assert reaped.json()["total"] == 3
    assert reap.call_args.args[1] == "ops"


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


def test_repository_task_worker_lifecycle_and_revenue_proof_persistence():
    from chargeopt.repository import claim_next_task, complete_task, persist_revenue_proof, reap_expired_tasks

    task_row = (
        "tsk-1",
        "t-001",
        "st-hq-hongqiao",
        None,
        "dispatch.execute",
        "running",
        10,
        {"kw": 100},
        {},
        1,
        3,
        None,
        "worker-1",
        None,
    )
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda s: s
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    claim_cursor = MagicMock()
    claim_cursor.fetchone.return_value = task_row
    conn.execute.side_effect = [MagicMock(), claim_cursor, MagicMock()]
    ctx = MagicMock()
    ctx.__enter__ = lambda s: conn
    ctx.__exit__ = MagicMock(return_value=False)
    with patch("chargeopt.repository.get_connection", return_value=ctx):
        claimed = claim_next_task("t-001", "worker-1", ["dispatch.execute"], 120)
    assert claimed["id"] == "tsk-1"
    assert claimed["attempts"] == 1

    complete_select = MagicMock()
    complete_select.fetchone.return_value = ("t-001", 1, 3, "running", "worker-1")
    complete_update = MagicMock()
    complete_update.fetchone.return_value = task_row[:5] + ("completed",) + task_row[6:14]
    conn2 = MagicMock()
    conn2.transaction.return_value.__enter__ = lambda s: s
    conn2.transaction.return_value.__exit__ = MagicMock(return_value=False)
    conn2.execute.side_effect = [MagicMock(), complete_select, MagicMock(), complete_update, MagicMock()]
    ctx2 = MagicMock()
    ctx2.__enter__ = lambda s: conn2
    ctx2.__exit__ = MagicMock(return_value=False)
    with patch("chargeopt.repository.get_connection", return_value=ctx2):
        completed = complete_task("tsk-1", "t-001", "worker-1", "succeeded", {"ok": True})
    assert completed["status"] == "completed"

    proof = {
        "generated_at": "2026-07-10T08:00:00",
        "scope": {"evidence_window_hours": 24},
        "algorithm": {"name": "counterfactual-profit-proof-v1"},
        "portfolio": {"monthly_net_impact": 1000, "confidence_interval": {"p90_low": 800, "p90_high": 1200}},
    }
    conn3 = MagicMock()
    conn3.transaction.return_value.__enter__ = lambda s: s
    conn3.transaction.return_value.__exit__ = MagicMock(return_value=False)
    ctx3 = MagicMock()
    ctx3.__enter__ = lambda s: conn3
    ctx3.__exit__ = MagicMock(return_value=False)
    with patch("chargeopt.repository.get_connection", return_value=ctx3):
        proof_id = persist_revenue_proof("t-001", None, proof, "ops")
    assert proof_id.startswith("rpf-")

    reap_cursor = MagicMock()
    reap_cursor.fetchall.return_value = [("t-001", "queued"), ("t-001", "failed")]
    conn4 = MagicMock()
    conn4.transaction.return_value.__enter__ = lambda s: s
    conn4.transaction.return_value.__exit__ = MagicMock(return_value=False)
    conn4.execute.side_effect = [MagicMock(), reap_cursor, MagicMock()]
    ctx4 = MagicMock()
    ctx4.__enter__ = lambda s: conn4
    ctx4.__exit__ = MagicMock(return_value=False)
    with patch("chargeopt.repository.get_connection", return_value=ctx4):
        reaped = reap_expired_tasks("t-001", "ops")
    assert reaped == {"requeued": 1, "failed": 1, "total": 2}
