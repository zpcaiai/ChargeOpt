"""Create a point-in-time Neon branch, validate it, record RPO/RTO, and delete it."""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

API_BASE = "https://console.neon.tech/api/v2"
LATEST_MIGRATION = max(path.stem for path in (Path(__file__).resolve().parents[1] / "migrations").glob("*.sql"))


def _api_request(api_key: str, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        method=method,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:2000]
        raise RuntimeError(f"Neon API {method} {path} returned HTTP {exc.code}: {detail}") from exc


def _validate_restored_database(connection_uri: str) -> dict[str, Any]:
    import psycopg

    with psycopg.connect(connection_uri, connect_timeout=15) as conn:
        conn.execute("SET LOCAL ROLE chargeopt_app")
        conn.execute("SELECT set_config('chargeopt.tenant_id', '*', true)")
        latest = conn.execute("SELECT max(version) FROM chargeopt.schema_migrations").fetchone()[0]
        table_count = conn.execute("SELECT count(*) FROM pg_tables WHERE schemaname='chargeopt'").fetchone()[0]
        tenant_count = conn.execute("SELECT count(*) FROM chargeopt.tenants").fetchone()[0]
        event_integrity = conn.execute(
            """SELECT count(*) FROM chargeopt.market_order_events moe
               LEFT JOIN chargeopt.market_orders mo ON mo.id=moe.order_id
               WHERE mo.id IS NULL"""
        ).fetchone()[0]
    checks = {
        "latest_migration_present": str(latest) >= LATEST_MIGRATION,
        "chargeopt_table_count_gte_35": int(table_count) >= 35,
        "tenant_data_present": int(tenant_count) > 0,
        "market_event_references_valid": int(event_integrity) == 0,
    }
    if not all(checks.values()):
        raise RuntimeError(f"restored database validation failed: {checks}")
    return {
        "checks": checks,
        "latest_migration": str(latest),
        "chargeopt_table_count": int(table_count),
        "tenant_count": int(tenant_count),
    }


def _record_drill(
    database_url: str,
    source_timestamp: datetime,
    branch_id: str | None,
    status: str,
    rpo_seconds: int | None,
    rto_seconds: int | None,
    validation: dict[str, Any],
    error: str | None,
) -> None:
    import psycopg
    from psycopg.types.json import Json

    with psycopg.connect(database_url) as conn, conn.transaction():
        conn.execute("SET LOCAL ROLE chargeopt_app")
        conn.execute("SELECT set_config('chargeopt.tenant_id', '*', true)")
        tenants = conn.execute("SELECT id FROM chargeopt.tenants").fetchall()
        for (tenant_id,) in tenants:
            conn.execute(
                """INSERT INTO chargeopt.recovery_drills
                   (id,tenant_id,provider,source_timestamp,restored_branch_id,status,rpo_seconds,
                    rto_seconds,validation,error,completed_at,initiated_by)
                   VALUES (%s,%s,'neon',%s,%s,%s,%s,%s,%s,%s,now(),'github-actions')""",
                (
                    f"dr-{uuid4().hex}",
                    tenant_id,
                    source_timestamp,
                    branch_id,
                    status,
                    rpo_seconds,
                    rto_seconds,
                    Json(validation),
                    error,
                ),
            )


def main() -> int:
    api_key = os.environ.get("NEON_API_KEY")
    project_id = os.environ.get("NEON_PROJECT_ID")
    database_url = os.environ.get("DATABASE_URL")
    if not api_key or not project_id or not database_url:
        raise SystemExit("NEON_API_KEY, NEON_PROJECT_ID, and DATABASE_URL are required")
    parsed = urllib.parse.urlparse(database_url)
    role_name = urllib.parse.unquote(parsed.username or "")
    database_name = parsed.path.lstrip("/")
    if not role_name or not database_name:
        raise SystemExit("DATABASE_URL must include a role and database name")

    started = time.monotonic()
    source_timestamp = datetime.now(UTC) - timedelta(minutes=int(os.environ.get("NEON_RESTORE_AGE_MINUTES", "15")))
    branch_name = f"chargeopt-dr-{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}"
    branch_id: str | None = None
    validation: dict[str, Any] = {}
    error: str | None = None
    status = "failed"
    rpo_seconds: int | None = None
    rto_seconds: int | None = None
    try:
        branch_payload: dict[str, Any] = {
            "name": branch_name,
            "parent_timestamp": source_timestamp.isoformat().replace("+00:00", "Z"),
        }
        if os.environ.get("NEON_PARENT_BRANCH_ID"):
            branch_payload["parent_id"] = os.environ["NEON_PARENT_BRANCH_ID"]
        created = _api_request(
            api_key,
            "POST",
            f"/projects/{project_id}/branches",
            {"branch": branch_payload, "endpoints": [{"type": "read_write", "suspend_timeout_seconds": 0}]},
        )
        branch = created["branch"]
        branch_id = str(branch["id"])
        actual_timestamp = datetime.fromisoformat(
            str(branch.get("parent_timestamp") or source_timestamp.isoformat()).replace("Z", "+00:00")
        )
        rpo_seconds = abs(int((actual_timestamp - source_timestamp).total_seconds()))
        query = urllib.parse.urlencode(
            {"branch_id": branch_id, "database_name": database_name, "role_name": role_name, "pooled": "false"}
        )
        connection = _api_request(api_key, "GET", f"/projects/{project_id}/connection_uri?{query}")
        connection_uri = str(connection["uri"])
        deadline = time.monotonic() + 300
        while True:
            try:
                validation = _validate_restored_database(connection_uri)
                break
            except Exception:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(5)
        rto_seconds = int(time.monotonic() - started)
        validation["rpo_target_seconds"] = 900
        validation["rto_target_seconds"] = 1800
        if rpo_seconds > 900 or rto_seconds > 1800:
            raise RuntimeError(f"recovery objectives exceeded: rpo={rpo_seconds}s rto={rto_seconds}s")
        status = "passed"
        return 0
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"[:4000]
        raise
    finally:
        rto_seconds = rto_seconds if rto_seconds is not None else int(time.monotonic() - started)
        try:
            _record_drill(
                database_url, source_timestamp, branch_id, status, rpo_seconds, rto_seconds, validation, error
            )
        finally:
            if branch_id:
                _api_request(api_key, "DELETE", f"/projects/{project_id}/branches/{branch_id}")


if __name__ == "__main__":
    raise SystemExit(main())
