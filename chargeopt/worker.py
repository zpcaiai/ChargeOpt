"""Async task worker for edge-gateway dispatch execution.

The worker is intentionally small and dependency-free so it can run beside an
edge gateway, in a VM, or as a one-shot CI/smoke command.
"""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from typing import Any

from .config import get_settings
from .repository import claim_next_task, complete_task, record_edge_receipt

TERMINAL_EDGE_STATUSES = {"succeeded", "failed", "rolled_back"}
NON_TERMINAL_EDGE_STATUSES = {"accepted", "running", "queued"}


def _split_task_types(values: list[str] | None) -> list[str] | None:
    if not values:
        return None
    task_types = [item.strip() for value in values for item in value.split(",") if item.strip()]
    return task_types or None


def _json_body(payload: Any) -> dict[str, Any]:
    if not payload:
        return {}
    if isinstance(payload, dict):
        return payload
    return {"raw": payload}


def _post_gateway(
    gateway_url: str,
    gateway_token: str | None,
    task: dict[str, Any],
    timeout_seconds: int,
) -> dict[str, Any]:
    body = json.dumps({"task": task}, default=str).encode("utf-8")
    request = urllib.request.Request(
        gateway_url,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    if gateway_token:
        request.add_header("Authorization", f"Bearer {gateway_token}")
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            raw = response.read().decode("utf-8")
            parsed = json.loads(raw) if raw else {}
            return {
                "http_status": response.status,
                "gateway": _json_body(parsed),
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Edge gateway returned HTTP {exc.code}: {raw[:500]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Edge gateway request failed: {exc.reason}") from exc


def _edge_status_from_gateway(result: dict[str, Any]) -> str:
    gateway = result.get("gateway")
    gateway_payload = gateway if isinstance(gateway, dict) else {}
    status = str(
        gateway_payload.get("edge_status")
        or gateway_payload.get("status")
        or ("accepted" if int(result.get("http_status", 200)) == 202 else "succeeded")
    ).lower()
    allowed = TERMINAL_EDGE_STATUSES | NON_TERMINAL_EDGE_STATUSES
    return status if status in allowed else "accepted"


def execute_once(
    worker_id: str,
    tenant_id: str = "*",
    task_types: list[str] | None = None,
    lease_seconds: int = 300,
    gateway_url: str | None = None,
    gateway_token: str | None = None,
    timeout_seconds: int = 15,
    dry_run: bool = False,
) -> dict[str, Any]:
    task = claim_next_task(tenant_id, worker_id, task_types, lease_seconds)
    if task is None:
        return {"claimed": False}

    task_id = str(task["id"])
    try:
        if dry_run:
            gateway_result = {"http_status": 200, "gateway": {"status": "succeeded", "mode": "dry_run"}}
        elif not gateway_url:
            raise RuntimeError("EDGE_GATEWAY_URL is required unless --dry-run is enabled.")
        else:
            gateway_result = _post_gateway(gateway_url, gateway_token, task, timeout_seconds)

        edge_status = _edge_status_from_gateway(gateway_result)
        receipt = record_edge_receipt(
            tenant_id,
            task_id,
            str(task["station_id"]) if task.get("station_id") is not None else None,
            str(task["device_id"]) if task.get("device_id") is not None else None,
            edge_status,
            gateway_result,
            scope_tenant_id=None if tenant_id == "*" else tenant_id,
        )
        return {
            "claimed": True,
            "task_id": task_id,
            "status": "receipt_recorded",
            "edge_status": edge_status,
            "receipt": receipt,
        }
    except Exception as exc:
        completed = complete_task(
            task_id,
            tenant_id,
            worker_id,
            "failed",
            {"error": str(exc)},
            str(exc),
        )
        return {
            "claimed": True,
            "task_id": task_id,
            "status": "failed",
            "error": str(exc),
            "task": completed,
        }


def main(argv: list[str] | None = None) -> int:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Run the ChargeOpt task worker.")
    parser.add_argument("--once", action="store_true", help="Process one task and exit.")
    parser.add_argument("--worker-id", default=os.environ.get("CHARGEOPT_WORKER_ID", "chargeopt-worker"))
    parser.add_argument("--tenant-id", default=os.environ.get("CHARGEOPT_WORKER_TENANT", "*"))
    parser.add_argument("--task-type", action="append", dest="task_types")
    parser.add_argument("--lease-seconds", type=int, default=300)
    parser.add_argument("--gateway-url", default=settings.edge_gateway_url)
    parser.add_argument("--gateway-token", default=settings.edge_gateway_token)
    parser.add_argument("--timeout-seconds", type=int, default=15)
    parser.add_argument("--poll-interval", type=float, default=settings.worker_poll_interval_seconds)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    task_types = _split_task_types(args.task_types)
    while True:
        result = execute_once(
            worker_id=args.worker_id,
            tenant_id=args.tenant_id,
            task_types=task_types,
            lease_seconds=args.lease_seconds,
            gateway_url=args.gateway_url,
            gateway_token=args.gateway_token,
            timeout_seconds=args.timeout_seconds,
            dry_run=args.dry_run,
        )
        print(json.dumps(result, default=str), flush=True)
        if args.once:
            return 0 if result.get("status") != "failed" else 1
        time.sleep(args.poll_interval)


if __name__ == "__main__":
    raise SystemExit(main())
