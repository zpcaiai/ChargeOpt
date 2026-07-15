"""Operational incident, SLO, and immutable daily shadow-run evidence."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from uuid import uuid4

from .db import get_connection


def _tenant_context(conn, tenant_id: str) -> None:
    conn.execute("SELECT set_config('chargeopt.tenant_id', %s, true)", (tenant_id,))


def record_shadow_day(tenant_id: str, evidence_date: date, actor: str = "assurance-worker") -> dict[str, Any]:
    if evidence_date >= datetime.now(UTC).date():
        raise ValueError("shadow evidence can only be finalized for a completed UTC day")
    start = datetime.combine(evidence_date, time.min, tzinfo=UTC)
    end = start + timedelta(days=1)
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        existing = conn.execute(
            "SELECT qualified,evidence_hash,qualification_reasons FROM chargeopt.shadow_run_evidence WHERE tenant_id=%s AND evidence_date=%s",
            (tenant_id, evidence_date),
        ).fetchone()
        if existing:
            return {
                "tenant_id": tenant_id,
                "evidence_date": evidence_date.isoformat(),
                "qualified": bool(existing[0]),
                "evidence_hash": str(existing[1]),
                "qualification_reasons": existing[2],
                "duplicate": True,
            }
        automation = conn.execute(
            """SELECT count(*),count(*) FILTER (WHERE status='completed'),
                      count(*) FILTER (WHERE status IN ('failed','degraded')),
                      COALESCE(sum(orders_created),0)
               FROM chargeopt.vpp_automation_runs
               WHERE tenant_id=%s AND started_at >= %s AND started_at < %s""",
            (tenant_id, start, end),
        ).fetchone()
        reconciliation = conn.execute(
            """SELECT count(*) FILTER (WHERE reconciliation_status='matched'),
                      count(*) FILTER (WHERE reconciliation_status='mismatch')
               FROM chargeopt.market_orders
               WHERE tenant_id=%s AND created_at >= %s AND created_at < %s""",
            (tenant_id, start, end),
        ).fetchone()
        outbox_dead = conn.execute(
            """SELECT count(*) FROM chargeopt.vpp_outbox
               WHERE tenant_id=%s AND status='dead_letter' AND updated_at >= %s AND updated_at < %s""",
            (tenant_id, start, end),
        ).fetchone()[0]
        dispatch_failed = conn.execute(
            """SELECT count(*) FROM chargeopt.task_queue
               WHERE tenant_id=%s AND task_type='dispatch.execute' AND status='failed'
                 AND updated_at >= %s AND updated_at < %s""",
            (tenant_id, start, end),
        ).fetchone()[0]
        settlement_failed = conn.execute(
            """SELECT count(*) FROM chargeopt.vpp_settlement_batches
               WHERE tenant_id=%s AND status IN ('failed','disputed') AND created_at >= %s AND created_at < %s""",
            (tenant_id, start, end),
        ).fetchone()[0]
        critical = conn.execute(
            """SELECT count(*) FROM chargeopt.operational_incidents
               WHERE tenant_id=%s AND severity='critical' AND first_detected_at < %s
                 AND (resolved_at IS NULL OR resolved_at >= %s)""",
            (tenant_id, end, start),
        ).fetchone()[0]
        completed_ratio = int(automation[1]) / max(1, int(automation[0]))
        checks = {
            "automation_cycles_gte_250": int(automation[0]) >= 250,
            "automation_success_rate_gte_98pct": completed_ratio >= 0.98,
            "no_reconciliation_mismatch": int(reconciliation[1]) == 0,
            "no_outbox_dead_letter": int(outbox_dead) == 0,
            "no_dispatch_failure": int(dispatch_failed) == 0,
            "no_settlement_failure_or_dispute": int(settlement_failed) == 0,
            "no_critical_incident": int(critical) == 0,
        }
        payload = {
            "tenant_id": tenant_id,
            "evidence_date": evidence_date.isoformat(),
            "automation_cycles": int(automation[0]),
            "completed_cycles": int(automation[1]),
            "failed_cycles": int(automation[2]),
            "orders_created": int(automation[3]),
            "reconciled_orders": int(reconciliation[0]),
            "reconciliation_mismatches": int(reconciliation[1]),
            "outbox_dead_letters": int(outbox_dead),
            "dispatch_failures": int(dispatch_failed),
            "settlement_failures": int(settlement_failed),
            "critical_incidents": int(critical),
            "qualified": all(checks.values()),
            "qualification_reasons": checks,
        }
        evidence_hash = hashlib.sha256(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        evidence_id = f"shd-{uuid4().hex}"
        conn.execute(
            """
            INSERT INTO chargeopt.shadow_run_evidence (
                id,tenant_id,evidence_date,automation_cycles,completed_cycles,failed_cycles,
                orders_created,reconciled_orders,reconciliation_mismatches,outbox_dead_letters,
                dispatch_failures,settlement_failures,critical_incidents,qualified,
                qualification_reasons,evidence_hash,recorded_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                evidence_id,
                tenant_id,
                evidence_date,
                payload["automation_cycles"],
                payload["completed_cycles"],
                payload["failed_cycles"],
                payload["orders_created"],
                payload["reconciled_orders"],
                payload["reconciliation_mismatches"],
                payload["outbox_dead_letters"],
                payload["dispatch_failures"],
                payload["settlement_failures"],
                payload["critical_incidents"],
                payload["qualified"],
                _json(checks),
                evidence_hash,
                actor,
            ),
        )
    return payload | {"id": evidence_id, "evidence_hash": evidence_hash, "duplicate": False}


def live_market_readiness(tenant_id: str) -> dict[str, Any]:
    today = datetime.now(UTC).date()
    first_required = today - timedelta(days=30)
    last_required = today - timedelta(days=1)
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        connection_cursor = conn.execute(
            """SELECT id,market_code,participant_id,mode,enabled,market_certificate_status,
                      market_certificate_expires_at,trading_qualification_status,
                      device_credentials_attested_at,external_readiness_evidence
               FROM chargeopt.market_connections WHERE tenant_id=%s ORDER BY enabled DESC,created_at LIMIT 1""",
            (tenant_id,),
        )
        connection = connection_cursor.fetchone()
        days = conn.execute(
            """SELECT evidence_date,qualified,evidence_hash FROM chargeopt.shadow_run_evidence
               WHERE tenant_id=%s AND evidence_date BETWEEN %s AND %s ORDER BY evidence_date""",
            (tenant_id, first_required, last_required),
        ).fetchall()
    if connection is None:
        return {"ready": False, "blockers": ["market_connection_missing"], "shadow_qualified_days": 0}
    by_date = {row[0]: row for row in days}
    required_dates = [first_required + timedelta(days=index) for index in range(30)]
    qualified_days = sum(bool(by_date.get(day) and by_date[day][1]) for day in required_dates)
    certificate_valid = connection[6] is not None and connection[6] > datetime.now(UTC)
    checks = {
        "live_market_mode": connection[3] == "live",
        "connection_enabled": bool(connection[4]),
        "market_certificate_verified_and_valid": connection[5] == "verified" and certificate_valid,
        "trading_qualification_verified": connection[7] == "verified",
        "participant_identifier_present": bool(connection[2]),
        "device_credentials_attested": connection[8] is not None,
        "thirty_consecutive_shadow_days": qualified_days == 30,
    }
    return {
        "ready": all(checks.values()),
        "connection_id": str(connection[0]),
        "market_code": str(connection[1]),
        "checks": checks,
        "blockers": [name for name, passed in checks.items() if not passed],
        "shadow_qualified_days": qualified_days,
        "shadow_required_days": 30,
        "shadow_window_start": first_required.isoformat(),
        "shadow_window_end": last_required.isoformat(),
        "external_readiness_evidence": connection[9] or {},
    }


def run_assurance_checks(actor: str = "assurance-worker") -> dict[str, Any]:
    now = datetime.now(UTC)
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, "*")
        tenants = [str(row[0]) for row in conn.execute("SELECT id FROM chargeopt.tenants ORDER BY id").fetchall()]
    results = []
    for tenant_id in tenants:
        with get_connection() as conn, conn.transaction():
            _tenant_context(conn, tenant_id)
            heartbeat = conn.execute(
                "SELECT max(observed_at) FROM chargeopt.vpp_operational_heartbeats WHERE tenant_id=%s",
                (tenant_id,),
            ).fetchone()[0]
            dead_letters = conn.execute(
                "SELECT count(*) FROM chargeopt.vpp_outbox WHERE tenant_id=%s AND status='dead_letter'",
                (tenant_id,),
            ).fetchone()[0]
            mismatches = conn.execute(
                "SELECT count(*) FROM chargeopt.market_orders WHERE tenant_id=%s AND reconciliation_status='mismatch'",
                (tenant_id,),
            ).fetchone()[0]
            heartbeat_age = int((now - heartbeat).total_seconds()) if heartbeat else 10**9
            checks = {
                "worker_heartbeat_age_seconds": {
                    "value": heartbeat_age,
                    "target": 600,
                    "compliant": heartbeat_age <= 600,
                },
                "outbox_dead_letters": {"value": int(dead_letters), "target": 0, "compliant": int(dead_letters) == 0},
                "reconciliation_mismatches": {"value": int(mismatches), "target": 0, "compliant": int(mismatches) == 0},
            }
            for metric, measurement in checks.items():
                conn.execute(
                    """INSERT INTO chargeopt.slo_measurements
                       (tenant_id,metric,value,target,compliant,window_start,window_end,evidence)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (
                        tenant_id,
                        metric,
                        measurement["value"],
                        measurement["target"],
                        measurement["compliant"],
                        now - timedelta(minutes=10),
                        now,
                        _json(measurement),
                    ),
                )
                if not measurement["compliant"]:
                    _open_incident(conn, tenant_id, metric, measurement, actor)
            results.append(
                {
                    "tenant_id": tenant_id,
                    "checks": checks,
                    "healthy": all(item["compliant"] for item in checks.values()),
                }
            )
    return {"status": "healthy" if all(row["healthy"] for row in results) else "degraded", "tenants": results}


def _open_incident(conn, tenant_id: str, fingerprint: str, evidence: dict[str, Any], actor: str) -> None:
    severity = "critical" if fingerprint in {"worker_heartbeat_age_seconds", "outbox_dead_letters"} else "warning"
    conn.execute(
        """INSERT INTO chargeopt.operational_incidents
           (id,tenant_id,fingerprint,component,severity,status,summary,evidence)
           VALUES (%s,%s,%s,'vpp-operations',%s,'open',%s,%s)
           ON CONFLICT (tenant_id,fingerprint) WHERE status IN ('open','acknowledged')
           DO UPDATE SET last_detected_at=now(),evidence=EXCLUDED.evidence""",
        (
            f"inc-{uuid4().hex}",
            tenant_id,
            fingerprint,
            severity,
            f"SLO violation: {fingerprint}",
            _json(evidence | {"actor": actor}),
        ),
    )


def _json(payload: Any):
    from psycopg.types.json import Json

    return Json(payload)
