"""Append-only PostgreSQL evidence storage for advanced EMS runs."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from .db import get_connection
from .repository import _ensure_tenant_allowed, _set_tenant_context


def persist_ems_evidence(
    tenant_id: str,
    station_id: str | None,
    evidence_type: str,
    algorithm_version: str,
    evidence_class: str,
    input_hash: str,
    request_payload: dict[str, Any],
    result_payload: dict[str, Any],
    idempotency_key: str,
    actor: str,
    scope_tenant_id: str | None = None,
) -> dict[str, Any]:
    from psycopg.types.json import Json

    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        if station_id is not None:
            station = conn.execute(
                "SELECT tenant_id FROM chargeopt.stations WHERE id=%s AND tenant_id=%s",
                (station_id, tenant_id),
            ).fetchone()
            if station is None:
                raise KeyError(f"Unknown station_id: {station_id}")
        existing = conn.execute(
            """
            SELECT id,input_hash,result_payload,created_at
            FROM chargeopt.ems_evidence_runs
            WHERE tenant_id=%s AND evidence_type=%s AND idempotency_key=%s
            """,
            (tenant_id, evidence_type, idempotency_key),
        ).fetchone()
        if existing is not None:
            if existing[1] != input_hash:
                raise ValueError("Idempotency key was already used with different EMS inputs.")
            return {
                "id": existing[0],
                "persisted": True,
                "replayed": True,
                "created_at": existing[3].isoformat(),
                "result": existing[2],
            }
        run_id = f"ems-{uuid4().hex}"
        conn.execute(
            """
            INSERT INTO chargeopt.ems_evidence_runs (
                id,tenant_id,station_id,evidence_type,algorithm_version,status,evidence_class,
                input_hash,request_payload,result_payload,idempotency_key,created_by
            ) VALUES (%s,%s,%s,%s,%s,'completed',%s,%s,%s,%s,%s,%s)
            """,
            (
                run_id,
                tenant_id,
                station_id,
                evidence_type,
                algorithm_version,
                evidence_class,
                input_hash,
                Json(request_payload),
                Json(result_payload),
                idempotency_key,
                actor,
            ),
        )
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id,tenant_id,timestamp,actor,action,target,detail)
            VALUES (%s,%s,now(),%s,%s,%s,%s)
            """,
            (
                f"au-{uuid4().hex}",
                tenant_id,
                actor,
                f"ems.{evidence_type}.completed",
                run_id,
                f"algorithm={algorithm_version};input_hash={input_hash}",
            ),
        )
    return {"id": run_id, "persisted": True, "replayed": False, "result": result_payload}


def list_ems_evidence(
    tenant_id: str,
    *,
    evidence_type: str | None = None,
    station_id: str | None = None,
    limit: int = 100,
    scope_tenant_id: str | None = None,
) -> list[dict[str, Any]]:
    _ensure_tenant_allowed(scope_tenant_id, tenant_id)
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, tenant_id)
        rows = conn.execute(
            """
            SELECT id,station_id,evidence_type,algorithm_version,status,evidence_class,input_hash,
                   result_payload,created_by,created_at
            FROM chargeopt.ems_evidence_runs
            WHERE tenant_id=%s
              AND (%s IS NULL OR evidence_type=%s)
              AND (%s IS NULL OR station_id=%s)
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (tenant_id, evidence_type, evidence_type, station_id, station_id, limit),
        ).fetchall()
    return [
        {
            "id": row[0],
            "station_id": row[1],
            "evidence_type": row[2],
            "algorithm_version": row[3],
            "status": row[4],
            "evidence_class": row[5],
            "input_hash": row[6],
            "result": row[7],
            "created_by": row[8],
            "created_at": row[9].isoformat(),
        }
        for row in rows
    ]
