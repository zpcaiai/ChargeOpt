from __future__ import annotations

from unittest.mock import patch


def _task() -> dict[str, object]:
    return {
        "id": "tsk-1",
        "tenant_id": "t-001",
        "station_id": "st-hq-hongqiao",
        "device_id": "dev-1",
        "task_type": "dispatch.execute",
        "status": "running",
        "priority": 10,
        "payload": {"kw": 120},
        "result": {},
        "attempts": 1,
        "max_attempts": 3,
        "lease_expires_at": None,
        "locked_by": "worker-1",
        "last_error": None,
    }


def test_worker_execute_once_dry_run_records_edge_receipt():
    from chargeopt.worker import execute_once

    task = _task()
    with (
        patch("chargeopt.worker.claim_next_task", return_value=task) as claim,
        patch(
            "chargeopt.worker.record_edge_receipt",
            return_value={"id": "rcp-1", "task_id": "tsk-1", "status": "succeeded"},
        ) as receipt,
        patch("chargeopt.worker.complete_task") as complete,
    ):
        result = execute_once("worker-1", tenant_id="t-001", task_types=["dispatch.execute"], dry_run=True)

    assert result["claimed"] is True
    assert result["edge_status"] == "succeeded"
    assert claim.call_args.args[:3] == ("t-001", "worker-1", ["dispatch.execute"])
    assert receipt.call_args.args[0] == "t-001"
    assert receipt.call_args.args[1] == "tsk-1"
    assert receipt.call_args.kwargs["scope_tenant_id"] == "t-001"
    complete.assert_not_called()


def test_worker_execute_once_fails_without_gateway_and_uses_retry_path():
    from chargeopt.worker import execute_once

    task = _task()
    with (
        patch("chargeopt.worker.claim_next_task", return_value=task),
        patch("chargeopt.worker.record_edge_receipt") as receipt,
        patch("chargeopt.worker.complete_task", return_value=task | {"status": "queued"}) as complete,
    ):
        result = execute_once("worker-1", tenant_id="t-001", task_types=["dispatch.execute"])

    assert result["status"] == "failed"
    assert "EDGE_GATEWAY_URL" in result["error"]
    assert complete.call_args.args[0] == "tsk-1"
    assert complete.call_args.args[3] == "failed"
    receipt.assert_not_called()


def test_worker_gateway_accepted_records_non_terminal_receipt():
    from chargeopt.worker import execute_once

    task = _task()
    with (
        patch("chargeopt.worker.claim_next_task", return_value=task),
        patch("chargeopt.worker._post_gateway", return_value={"http_status": 202, "gateway": {"status": "accepted"}}),
        patch(
            "chargeopt.worker.record_edge_receipt",
            return_value={"id": "rcp-1", "task_id": "tsk-1", "status": "accepted"},
        ) as receipt,
    ):
        result = execute_once(
            "worker-1",
            tenant_id="t-001",
            task_types=["dispatch.execute"],
            gateway_url="https://edge.example.test/execute",
        )

    assert result["edge_status"] == "accepted"
    assert receipt.call_args.args[4] == "accepted"
