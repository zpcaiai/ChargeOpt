"""PostgreSQL persistence for the VPP automated trading control plane."""

from __future__ import annotations

import hashlib
import json
from csv import DictWriter
from datetime import UTC, datetime
from io import StringIO
from typing import Any
from uuid import uuid4

from .db import get_connection
from .vpp_trading import calculate_trade_settlement, validate_order_transition


def _json(payload: Any):
    from psycopg.types.json import Json

    return Json(payload)


def _tenant_context(conn, tenant_id: str) -> None:
    conn.execute("SELECT set_config('chargeopt.tenant_id', %s, true)", (tenant_id,))


def _columns(cursor, row) -> dict[str, Any]:
    return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


def _audit(conn, tenant_id: str, actor: str, action: str, target: str, detail: str) -> None:
    conn.execute(
        """
        INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
        VALUES (%s, %s, now(), %s, %s, %s, %s)
        """,
        (f"au-{uuid4().hex}", tenant_id, actor, action, target, detail),
    )


def list_automation_tenants() -> list[str]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, "*")
        rows = conn.execute(
            """
            SELECT DISTINCT mc.tenant_id
            FROM chargeopt.market_connections mc
            JOIN chargeopt.vpp_risk_policies rp ON rp.tenant_id = mc.tenant_id AND rp.status = 'active'
            WHERE mc.enabled AND mc.mode <> 'disabled' AND rp.auto_trade_enabled
            ORDER BY mc.tenant_id
            """
        ).fetchall()
    return [str(row[0]) for row in rows]


def get_trading_context(tenant_id: str) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        policy_cursor = conn.execute(
            "SELECT * FROM chargeopt.vpp_risk_policies WHERE tenant_id = %s AND status = 'active'",
            (tenant_id,),
        )
        policy_row = policy_cursor.fetchone()
        if policy_row is None:
            raise RuntimeError("no active VPP risk policy")
        connection_cursor = conn.execute(
            """
            SELECT * FROM chargeopt.market_connections
            WHERE tenant_id = %s AND enabled AND mode <> 'disabled'
            ORDER BY CASE mode WHEN 'live' THEN 0 ELSE 1 END, created_at
            LIMIT 1
            """,
            (tenant_id,),
        )
        connection_row = connection_cursor.fetchone()
        if connection_row is None:
            raise RuntimeError("no enabled market connection")
        metrics = conn.execute(
            """
            SELECT
                count(*) FILTER (WHERE status IN ('ready','submitting','submitted','partially_filled','cancel_pending')),
                COALESCE(sum(quantity_kw * EXTRACT(EPOCH FROM (delivery_end-delivery_start))/3600)
                    FILTER (WHERE delivery_start::date = CURRENT_DATE AND status NOT IN ('risk_rejected','cancelled','rejected','failed')), 0)
            FROM chargeopt.market_orders WHERE tenant_id = %s
            """,
            (tenant_id,),
        ).fetchone()
        breaker = conn.execute(
            "SELECT state, reason, failure_count, reset_after FROM chargeopt.vpp_circuit_breakers WHERE tenant_id = %s AND scope = 'global'",
            (tenant_id,),
        ).fetchone()
    connection = _columns(connection_cursor, connection_row)
    if connection["mode"] == "live":
        from .operations_assurance import live_market_readiness

        connection["live_readiness"] = live_market_readiness(tenant_id)
    return {
        "policy": _columns(policy_cursor, policy_row),
        "connection": connection,
        "open_order_count": int(metrics[0]),
        "committed_energy_kwh": float(metrics[1]),
        "circuit_breaker": {
            "state": str(breaker[0]) if breaker else "open",
            "reason": breaker[1] if breaker else "missing_breaker",
            "failure_count": int(breaker[2]) if breaker else 0,
            "reset_after": breaker[3] if breaker else None,
        },
    }


def persist_forecast(tenant_id: str, forecast: dict[str, Any]) -> str:
    forecast_id = f"fc-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        conn.execute(
            """
            INSERT INTO chargeopt.vpp_forecast_runs (
                id, tenant_id, algorithm, horizon_start, horizon_end, interval_minutes,
                training_window_hours, data_freshness_seconds, calibration_score, payload
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                forecast_id,
                tenant_id,
                forecast["algorithm"],
                forecast["horizon_start"],
                forecast["horizon_end"],
                forecast["interval_minutes"],
                forecast["training_window_hours"],
                forecast["data_freshness_seconds"],
                forecast["calibration_score"],
                _json(forecast),
            ),
        )
    return forecast_id


def claim_automation_cycle(tenant_id: str, cycle_key: str, trigger_source: str) -> str | None:
    run_id = f"auto-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        row = conn.execute(
            """
            INSERT INTO chargeopt.vpp_automation_runs (id, tenant_id, cycle_key, status, trigger_source)
            VALUES (%s,%s,%s,'running',%s)
            ON CONFLICT (tenant_id, cycle_key) DO NOTHING
            RETURNING id
            """,
            (run_id, tenant_id, cycle_key, trigger_source),
        ).fetchone()
    return str(row[0]) if row else None


def finish_automation_cycle(
    tenant_id: str,
    run_id: str,
    status: str,
    summary: dict[str, Any],
    *,
    forecast_run_id: str | None = None,
    orders_created: int = 0,
    tasks_created: int = 0,
    error: str | None = None,
) -> None:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        conn.execute(
            """
            UPDATE chargeopt.vpp_automation_runs
            SET status=%s, summary=%s, forecast_run_id=%s, orders_created=%s,
                tasks_created=%s, error=%s, completed_at=now()
            WHERE id=%s AND tenant_id=%s
            """,
            (status, _json(summary), forecast_run_id, orders_created, tasks_created, error, run_id, tenant_id),
        )


def _append_order_event(
    conn,
    tenant_id: str,
    order_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor: str,
    payload: dict[str, Any],
) -> None:
    previous = conn.execute(
        "SELECT sequence_no, event_hash FROM chargeopt.market_order_events WHERE order_id=%s ORDER BY sequence_no DESC LIMIT 1",
        (order_id,),
    ).fetchone()
    sequence = int(previous[0]) + 1 if previous else 1
    previous_hash = str(previous[1]) if previous else None
    canonical = json.dumps(
        {
            "order_id": order_id,
            "sequence_no": sequence,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "actor": actor,
            "payload": payload,
            "previous_hash": previous_hash,
        },
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    event_hash = hashlib.sha256(canonical.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO chargeopt.market_order_events (
            tenant_id, order_id, sequence_no, event_type, from_status, to_status,
            actor, payload, previous_hash, event_hash
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            tenant_id,
            order_id,
            sequence,
            event_type,
            from_status,
            to_status,
            actor,
            _json(payload),
            previous_hash,
            event_hash,
        ),
    )


def create_market_order(
    tenant_id: str,
    connection_id: str,
    forecast_run_id: str,
    market_code: str,
    bid: dict[str, Any],
    risk_decision: dict[str, Any],
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    order_id = f"ord-{uuid4().hex}"
    client_order_id = f"co-{hashlib.sha256(idempotency_key.encode()).hexdigest()[:24]}"
    initial_status = "ready" if risk_decision["approved"] else "risk_rejected"
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        cursor = conn.execute(
            """
            INSERT INTO chargeopt.market_orders (
                id, tenant_id, connection_id, forecast_run_id, client_order_id,
                market_code, product, side, delivery_start, delivery_end, quantity_kw,
                limit_price_per_kwh, status, risk_decision, allocation, idempotency_key, created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id, idempotency_key) DO UPDATE SET updated_at=chargeopt.market_orders.updated_at
            RETURNING *
            """,
            (
                order_id,
                tenant_id,
                connection_id,
                forecast_run_id,
                client_order_id,
                market_code,
                bid["product"],
                bid["side"],
                bid["delivery_start"],
                bid["delivery_end"],
                bid["quantity_kw"],
                bid["limit_price_per_kwh"],
                initial_status,
                _json(risk_decision),
                _json(bid["allocation"]),
                idempotency_key,
                actor,
            ),
        )
        row = cursor.fetchone()
        actual = _columns(cursor, row)
        if actual["id"] == order_id:
            _append_order_event(
                conn, tenant_id, order_id, "risk_decision", "draft", initial_status, actor, risk_decision
            )
            if initial_status == "ready":
                conn.execute(
                    """
                    INSERT INTO chargeopt.vpp_outbox (
                        id, tenant_id, event_key, topic, aggregate_type, aggregate_id, payload
                    ) VALUES (%s,%s,%s,'market.order.submit','market_order',%s,%s)
                    ON CONFLICT (tenant_id,event_key) DO NOTHING
                    """,
                    (
                        f"obx-{uuid4().hex}",
                        tenant_id,
                        f"market.order.submit:{order_id}",
                        order_id,
                        _json({"order_id": order_id, "connection_id": connection_id}),
                    ),
                )
            _audit(conn, tenant_id, actor, "vpp.order_created", order_id, initial_status)
    return actual


def transition_market_order(
    tenant_id: str,
    order_id: str,
    target_status: str,
    actor: str,
    payload: dict[str, Any] | None = None,
    *,
    market_order_id: str | None = None,
    last_error: str | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        cursor = conn.execute(
            "SELECT * FROM chargeopt.market_orders WHERE id=%s AND tenant_id=%s FOR UPDATE",
            (order_id, tenant_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown order_id: {order_id}")
        current = _columns(cursor, row)
        validate_order_transition(str(current["status"]), target_status)
        terminal = target_status in {"filled", "cancelled", "rejected", "expired", "failed", "risk_rejected"}
        conn.execute(
            """
            UPDATE chargeopt.market_orders
            SET status=%s, market_order_id=COALESCE(%s, market_order_id), last_error=%s,
                submitted_at=CASE WHEN %s='submitted' THEN now() ELSE submitted_at END,
                terminal_at=CASE WHEN %s THEN now() ELSE terminal_at END, updated_at=now()
            WHERE id=%s
            """,
            (target_status, market_order_id, last_error, target_status, terminal, order_id),
        )
        _append_order_event(
            conn, tenant_id, order_id, "status_transition", str(current["status"]), target_status, actor, payload
        )
        _audit(conn, tenant_id, actor, "vpp.order_transition", order_id, f"{current['status']}->{target_status}")
        updated_cursor = conn.execute("SELECT * FROM chargeopt.market_orders WHERE id=%s", (order_id,))
        updated = updated_cursor.fetchone()
    return _columns(updated_cursor, updated)


def claim_outbox_messages(worker_id: str, *, limit: int = 20, lease_seconds: int = 120) -> list[dict[str, Any]]:
    """Lease due outbox messages with SKIP LOCKED for parallel publishers."""
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, "*")
        cursor = conn.execute(
            """
            WITH candidates AS (
                SELECT id
                FROM chargeopt.vpp_outbox
                WHERE (
                    (status IN ('pending','failed') AND available_at <= now())
                    OR (status='publishing' AND lease_expires_at < now())
                )
                  AND attempts < max_attempts
                ORDER BY available_at, created_at
                FOR UPDATE SKIP LOCKED
                LIMIT %s
            )
            UPDATE chargeopt.vpp_outbox o
            SET status='publishing', attempts=o.attempts+1, locked_by=%s,
                lease_expires_at=now()+(%s*interval '1 second'), updated_at=now()
            FROM candidates
            WHERE o.id=candidates.id
            RETURNING o.*
            """,
            (limit, worker_id, lease_seconds),
        )
        rows = [_columns(cursor, row) for row in cursor.fetchall()]
    return rows


def finish_outbox_message(
    message_id: str,
    worker_id: str,
    *,
    published: bool,
    error: str | None = None,
    retry_delay_seconds: int = 30,
) -> dict[str, Any]:
    """Publish, retry, or dead-letter a leased outbox message."""
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, "*")
        cursor = conn.execute(
            "SELECT * FROM chargeopt.vpp_outbox WHERE id=%s FOR UPDATE",
            (message_id,),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown outbox message: {message_id}")
        current = _columns(cursor, row)
        if current["locked_by"] != worker_id:
            raise PermissionError("Outbox lease is owned by another worker.")
        if published:
            next_status = "published"
        elif int(current["attempts"]) >= int(current["max_attempts"]):
            next_status = "dead_letter"
        else:
            next_status = "failed"
        updated_cursor = conn.execute(
            """
            UPDATE chargeopt.vpp_outbox
            SET status=%s, published_at=CASE WHEN %s THEN now() ELSE published_at END,
                available_at=CASE WHEN %s='failed' THEN now()+(%s*interval '1 second') ELSE available_at END,
                last_error=%s, locked_by=NULL, lease_expires_at=NULL, updated_at=now()
            WHERE id=%s
            RETURNING *
            """,
            (next_status, published, next_status, retry_delay_seconds, error, message_id),
        )
        updated = _columns(updated_cursor, updated_cursor.fetchone())
    return updated


def get_order_for_operation(tenant_id: str, order_id: str) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        cursor = conn.execute(
            """
            SELECT mo.*, mc.adapter connection_adapter, mc.mode connection_mode,
                   mc.enabled connection_enabled, mc.base_url connection_base_url,
                   mc.credential_ref connection_credential_ref, mc.participant_id connection_participant_id,
                   mc.market_certificate_status connection_certificate_status,
                   mc.market_certificate_expires_at connection_certificate_expires_at,
                   mc.trading_qualification_status connection_qualification_status,
                   mc.device_credentials_attested_at connection_device_attested_at
            FROM chargeopt.market_orders mo
            JOIN chargeopt.market_connections mc ON mc.id=mo.connection_id
            WHERE mo.id=%s AND mo.tenant_id=%s
            """,
            (order_id, tenant_id),
        )
        row = cursor.fetchone()
        if row is None:
            raise KeyError(f"Unknown order_id: {order_id}")
        result = _columns(cursor, row)
    result["connection"] = {
        "id": result["connection_id"],
        "adapter": result.pop("connection_adapter"),
        "mode": result.pop("connection_mode"),
        "enabled": result.pop("connection_enabled"),
        "base_url": result.pop("connection_base_url"),
        "credential_ref": result.pop("connection_credential_ref"),
        "participant_id": result.pop("connection_participant_id"),
        "market_certificate_status": result.pop("connection_certificate_status"),
        "market_certificate_expires_at": result.pop("connection_certificate_expires_at"),
        "trading_qualification_status": result.pop("connection_qualification_status"),
        "device_credentials_attested_at": result.pop("connection_device_attested_at"),
    }
    if result["connection"]["mode"] == "live":
        from .operations_assurance import live_market_readiness

        result["connection"]["live_readiness"] = live_market_readiness(tenant_id)
    return result


def list_orders_for_reconciliation(limit: int = 100) -> list[dict[str, Any]]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, "*")
        cursor = conn.execute(
            """
            SELECT tenant_id,id FROM chargeopt.market_orders
            WHERE market_order_id IS NOT NULL
              AND status IN ('submitting','submitted','partially_filled','cancel_pending')
              AND (last_reconciled_at IS NULL OR last_reconciled_at < now()-interval '2 minutes')
            ORDER BY COALESCE(last_reconciled_at,created_at), created_at
            LIMIT %s
            """,
            (limit,),
        )
        refs = [(str(row[0]), str(row[1])) for row in cursor.fetchall()]
    return [get_order_for_operation(tenant_id, order_id) for tenant_id, order_id in refs]


def mark_order_reconciled(
    tenant_id: str,
    order_id: str,
    reconciliation_status: str,
    actor: str,
    payload: dict[str, Any],
) -> None:
    if reconciliation_status not in {"matched", "mismatch", "unknown", "not_applicable"}:
        raise ValueError("invalid reconciliation status")
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        conn.execute(
            """
            UPDATE chargeopt.market_orders
            SET reconciliation_status=%s,last_reconciled_at=now(),updated_at=now()
            WHERE id=%s AND tenant_id=%s
            """,
            (reconciliation_status, order_id, tenant_id),
        )
        _append_order_event(
            conn,
            tenant_id,
            order_id,
            "reconciliation",
            None,
            reconciliation_status,
            actor,
            payload,
        )


def record_operational_heartbeat(
    tenant_id: str,
    component: str,
    instance_id: str,
    status: str,
    detail: dict[str, Any],
) -> None:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        conn.execute(
            """
            INSERT INTO chargeopt.vpp_operational_heartbeats
                (tenant_id,component,instance_id,status,detail)
            VALUES (%s,%s,%s,%s,%s)
            """,
            (tenant_id, component, instance_id, status, _json(detail)),
        )


def record_trade_fill(
    tenant_id: str,
    order_id: str,
    market_trade_id: str,
    quantity_kw: float,
    price_per_kwh: float,
    traded_at: datetime,
    actor: str,
    raw_payload: dict[str, Any],
) -> dict[str, Any]:
    trade_id = f"trd-{uuid4().hex}"
    tasks_created = 0
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        order_cursor = conn.execute(
            "SELECT * FROM chargeopt.market_orders WHERE id=%s AND tenant_id=%s FOR UPDATE",
            (order_id, tenant_id),
        )
        order_row = order_cursor.fetchone()
        if order_row is None:
            raise KeyError(f"Unknown order_id: {order_id}")
        order = _columns(order_cursor, order_row)
        if order["status"] not in {"submitted", "partially_filled"}:
            raise ValueError(f"order {order_id} cannot be filled from {order['status']}")
        fill = min(quantity_kw, float(order["quantity_kw"]) - float(order["filled_quantity_kw"]))
        if fill <= 0:
            raise ValueError("trade fill exceeds remaining order quantity")
        existing = conn.execute(
            "SELECT id FROM chargeopt.market_trades WHERE tenant_id=%s AND market_trade_id=%s",
            (tenant_id, market_trade_id),
        ).fetchone()
        if existing:
            return {"id": str(existing[0]), "order_id": order_id, "tasks_created": 0, "duplicate": True}
        conn.execute(
            """
            INSERT INTO chargeopt.market_trades (
                id,tenant_id,order_id,market_trade_id,quantity_kw,price_per_kwh,traded_at,
                delivery_start,delivery_end,raw_payload
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """,
            (
                trade_id,
                tenant_id,
                order_id,
                market_trade_id,
                fill,
                price_per_kwh,
                traded_at,
                order["delivery_start"],
                order["delivery_end"],
                _json(raw_payload),
            ),
        )
        new_filled = float(order["filled_quantity_kw"]) + fill
        weighted_price = (
            float(order["average_fill_price"] or 0) * float(order["filled_quantity_kw"]) + price_per_kwh * fill
        ) / new_filled
        target_status = "filled" if new_filled >= float(order["quantity_kw"]) - 0.001 else "partially_filled"
        conn.execute(
            """
            UPDATE chargeopt.market_orders SET filled_quantity_kw=%s, average_fill_price=%s,
                status=%s, terminal_at=CASE WHEN %s='filled' THEN now() ELSE terminal_at END, updated_at=now()
            WHERE id=%s
            """,
            (new_filled, weighted_price, target_status, target_status, order_id),
        )
        scale = fill / float(order["quantity_kw"])
        for allocation in order["allocation"]:
            target_kw = float(allocation["target_kw"]) * scale
            schedule_id = f"sch-{uuid4().hex}"
            task_id = f"tsk-{uuid4().hex}"
            command = {
                "command": "vpp.dispatch",
                "trade_id": trade_id,
                "order_id": order_id,
                "schedule_id": schedule_id,
                "station_id": allocation["station_id"],
                "delivery_start": order["delivery_start"].isoformat(),
                "delivery_end": order["delivery_end"].isoformat(),
                "target_adjustment_kw": round(target_kw, 3),
                "target_grid_kw": round(max(0.0, float(allocation["baseline_kw"]) - target_kw), 3),
                "safety": {"fail_mode": "hold", "receipt_required": True},
            }
            conn.execute(
                """
                INSERT INTO chargeopt.task_queue (
                    id,tenant_id,station_id,task_type,status,priority,idempotency_key,payload,scheduled_at,max_attempts
                ) VALUES (%s,%s,%s,'dispatch.execute','queued',10,%s,%s,%s,5)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (
                    task_id,
                    tenant_id,
                    allocation["station_id"],
                    f"vpp:{trade_id}:{allocation['station_id']}:{order['delivery_start'].isoformat()}",
                    _json(command),
                    order["delivery_start"],
                ),
            )
            conn.execute(
                """
                INSERT INTO chargeopt.delivery_schedules (
                    id,tenant_id,trade_id,station_id,interval_start,interval_end,baseline_kw,
                    target_adjustment_kw,target_grid_kw,status,task_id
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'scheduled',%s)
                ON CONFLICT (trade_id,station_id,interval_start) DO NOTHING
                """,
                (
                    schedule_id,
                    tenant_id,
                    trade_id,
                    allocation["station_id"],
                    order["delivery_start"],
                    order["delivery_end"],
                    allocation["baseline_kw"],
                    target_kw,
                    max(0.0, float(allocation["baseline_kw"]) - target_kw),
                    task_id,
                ),
            )
            tasks_created += 1
        _append_order_event(
            conn,
            tenant_id,
            order_id,
            "trade_fill",
            str(order["status"]),
            target_status,
            actor,
            {"trade_id": trade_id, "quantity_kw": fill, "price_per_kwh": price_per_kwh},
        )
        _audit(conn, tenant_id, actor, "vpp.trade_recorded", trade_id, f"fill_kw={fill};tasks={tasks_created}")
    return {"id": trade_id, "order_id": order_id, "status": target_status, "tasks_created": tasks_created}


def ingest_meter_interval(tenant_id: str, payload: dict[str, Any], actor: str) -> dict[str, Any]:
    interval_id = f"mtr-{uuid4().hex}"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    evidence_hash = hashlib.sha256(canonical.encode()).hexdigest()
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        station = conn.execute(
            "SELECT tenant_id FROM chargeopt.stations WHERE id=%s", (payload["station_id"],)
        ).fetchone()
        if station is None:
            raise KeyError(f"Unknown station_id: {payload['station_id']}")
        if str(station[0]) != tenant_id:
            raise PermissionError("station belongs to another tenant")
        delivered_kw = max(0.0, float(payload["baseline_kw"]) - float(payload["actual_grid_kw"]))
        row = conn.execute(
            """
            INSERT INTO chargeopt.vpp_meter_intervals (
                id,tenant_id,station_id,interval_start,interval_end,baseline_kw,actual_grid_kw,
                delivered_kw,quality,source,evidence_hash,payload
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,station_id,interval_start,source) DO UPDATE SET
                actual_grid_kw=EXCLUDED.actual_grid_kw, delivered_kw=EXCLUDED.delivered_kw,
                quality=EXCLUDED.quality, evidence_hash=EXCLUDED.evidence_hash,
                payload=EXCLUDED.payload, received_at=now()
            RETURNING id, delivered_kw, evidence_hash, quality
            """,
            (
                interval_id,
                tenant_id,
                payload["station_id"],
                payload["interval_start"],
                payload["interval_end"],
                payload["baseline_kw"],
                payload["actual_grid_kw"],
                delivered_kw,
                payload["quality"],
                payload["source"],
                evidence_hash,
                _json(payload),
            ),
        ).fetchone()
        _audit(conn, tenant_id, actor, "vpp.meter_ingested", str(row[0]), evidence_hash)
    return {"id": str(row[0]), "delivered_kw": float(row[1]), "evidence_hash": str(row[2]), "quality": str(row[3])}


def create_settlement_batch(
    tenant_id: str,
    market_code: str,
    period_start: datetime,
    period_end: datetime,
    actor: str,
    *,
    imbalance_price_per_kwh: float,
    penalty_rate: float,
) -> dict[str, Any]:
    batch_id = f"stb-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        trade_cursor = conn.execute(
            """
            SELECT mt.* FROM chargeopt.market_trades mt
            JOIN chargeopt.market_orders mo ON mo.id=mt.order_id
            WHERE mt.tenant_id=%s AND mo.market_code=%s
              AND mt.delivery_start >= %s AND mt.delivery_end <= %s
            ORDER BY mt.delivery_start
            """,
            (tenant_id, market_code, period_start, period_end),
        )
        trades = [_columns(trade_cursor, row) for row in trade_cursor.fetchall()]
        if not trades:
            raise ValueError("no trades found for settlement period")
        lines: list[tuple[dict[str, Any], dict[str, Any]]] = []
        all_hashes: list[str] = []
        for trade in trades:
            meter_cursor = conn.execute(
                """
                SELECT mi.* FROM chargeopt.vpp_meter_intervals mi
                WHERE mi.tenant_id=%s AND mi.interval_start >= %s AND mi.interval_end <= %s
                  AND EXISTS (
                      SELECT 1 FROM chargeopt.delivery_schedules ds
                      WHERE ds.station_id=mi.station_id AND ds.trade_id=%s
                  )
                ORDER BY mi.interval_start
                """,
                (tenant_id, trade["delivery_start"], trade["delivery_end"], trade["id"]),
            )
            meters = [_columns(meter_cursor, row) for row in meter_cursor.fetchall()]
            result = calculate_trade_settlement(
                trade,
                meters,
                imbalance_price_per_kwh=imbalance_price_per_kwh,
                penalty_rate=penalty_rate,
            )
            all_hashes.append(result["evidence"]["evidence_root_hash"])
            lines.append((trade, result))
        root_hash = hashlib.sha256("|".join(sorted(all_hashes)).encode()).hexdigest()
        gross = sum(result["gross_revenue"] for _, result in lines)
        imbalance = sum(result["imbalance_cost"] for _, result in lines)
        penalties = sum(result["penalty"] for _, result in lines)
        net = sum(result["net_revenue"] for _, result in lines)
        inserted = conn.execute(
            """
            INSERT INTO chargeopt.vpp_settlement_batches (
                id,tenant_id,market_code,period_start,period_end,status,gross_revenue,
                imbalance_cost,penalties,net_revenue,evidence_root_hash,created_by
            ) VALUES (%s,%s,%s,%s,%s,'review',%s,%s,%s,%s,%s,%s)
            ON CONFLICT (tenant_id,market_code,period_start,period_end) DO NOTHING
            """,
            (
                batch_id,
                tenant_id,
                market_code,
                period_start,
                period_end,
                gross,
                imbalance,
                penalties,
                net,
                root_hash,
                actor,
            ),
        ).rowcount
        if not inserted:
            raise ValueError("settlement batch already exists for this period")
        for trade, result in lines:
            conn.execute(
                """
                INSERT INTO chargeopt.vpp_settlement_lines (
                    id,tenant_id,batch_id,trade_id,committed_kwh,delivered_kwh,performance_score,
                    gross_revenue,imbalance_cost,penalty,net_revenue,evidence
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (batch_id,trade_id) DO NOTHING
                """,
                (
                    f"stl-{uuid4().hex}",
                    tenant_id,
                    batch_id,
                    trade["id"],
                    result["committed_kwh"],
                    result["delivered_kwh"],
                    result["performance_score"],
                    result["gross_revenue"],
                    result["imbalance_cost"],
                    result["penalty"],
                    result["net_revenue"],
                    _json(result["evidence"]),
                ),
            )
        _audit(conn, tenant_id, actor, "vpp.settlement_calculated", batch_id, f"net={round(net, 2)}")
        _append_settlement_event(
            conn, tenant_id, batch_id, "calculated", None, "review", actor, None, {"net_revenue": round(net, 2)}
        )
    return {
        "id": batch_id,
        "status": "review",
        "trade_count": len(lines),
        "gross_revenue": round(gross, 2),
        "imbalance_cost": round(imbalance, 2),
        "penalties": round(penalties, 2),
        "net_revenue": round(net, 2),
        "evidence_root_hash": root_hash,
    }


SETTLEMENT_TRANSITIONS = {
    "review": {"approved", "disputed", "failed"},
    "approved": {"exported", "disputed"},
    "exported": {"paid", "disputed"},
    "disputed": {"review", "failed"},
    "paid": {"reversed"},
}


def _settlement_batch_for_update(conn, tenant_id: str, batch_id: str) -> dict[str, Any]:
    cursor = conn.execute(
        """
        SELECT id,tenant_id,market_code,period_start,period_end,status,gross_revenue,
               imbalance_cost,penalties,net_revenue,evidence_root_hash,created_by,approved_by,
               created_at,approved_at,exported_at,paid_at,payment_reference,reversed_at
        FROM chargeopt.vpp_settlement_batches WHERE id=%s AND tenant_id=%s FOR UPDATE
        """,
        (batch_id, tenant_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise KeyError(f"Unknown settlement batch: {batch_id}")
    return _columns(cursor, row)


def _append_settlement_event(
    conn,
    tenant_id: str,
    batch_id: str,
    event_type: str,
    from_status: str | None,
    to_status: str,
    actor: str,
    reason: str | None,
    payload: dict[str, Any],
) -> str:
    previous = conn.execute(
        """SELECT sequence_no,event_hash FROM chargeopt.vpp_settlement_events
           WHERE batch_id=%s ORDER BY sequence_no DESC LIMIT 1 FOR UPDATE""",
        (batch_id,),
    ).fetchone()
    sequence = int(previous[0]) + 1 if previous else 1
    previous_hash = str(previous[1]) if previous else None
    material = json.dumps(
        {
            "batch_id": batch_id,
            "sequence_no": sequence,
            "event_type": event_type,
            "from_status": from_status,
            "to_status": to_status,
            "actor": actor,
            "reason": reason,
            "payload": payload,
            "previous_hash": previous_hash,
        },
        separators=(",", ":"),
        sort_keys=True,
        default=str,
    )
    event_hash = hashlib.sha256(material.encode()).hexdigest()
    conn.execute(
        """
        INSERT INTO chargeopt.vpp_settlement_events (
            id,tenant_id,batch_id,sequence_no,event_type,from_status,to_status,actor,
            reason,payload,previous_hash,event_hash
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        """,
        (
            f"ste-{uuid4().hex}",
            tenant_id,
            batch_id,
            sequence,
            event_type,
            from_status,
            to_status,
            actor,
            reason,
            _json(payload),
            previous_hash,
            event_hash,
        ),
    )
    return event_hash


def _assert_settlement_transition(current: str, target: str) -> None:
    if target not in SETTLEMENT_TRANSITIONS.get(current, set()):
        raise ValueError(f"invalid settlement transition: {current} -> {target}")


def approve_settlement_batch(tenant_id: str, batch_id: str, actor: str, reason: str | None) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        batch = _settlement_batch_for_update(conn, tenant_id, batch_id)
        _assert_settlement_transition(batch["status"], "approved")
        if batch["created_by"] == actor:
            raise PermissionError("Settlement creator cannot approve their own batch.")
        conn.execute(
            "UPDATE chargeopt.vpp_settlement_batches SET status='approved',approved_by=%s,approved_at=now() WHERE id=%s",
            (actor, batch_id),
        )
        event_hash = _append_settlement_event(
            conn, tenant_id, batch_id, "approved", batch["status"], "approved", actor, reason, {}
        )
        _audit(conn, tenant_id, actor, "vpp.settlement_approved", batch_id, event_hash)
    return {"id": batch_id, "status": "approved", "event_hash": event_hash}


def dispute_settlement_batch(tenant_id: str, batch_id: str, actor: str, reason: str) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        batch = _settlement_batch_for_update(conn, tenant_id, batch_id)
        _assert_settlement_transition(batch["status"], "disputed")
        dispute_id = f"std-{uuid4().hex}"
        conn.execute(
            """INSERT INTO chargeopt.vpp_settlement_disputes
               (id,tenant_id,batch_id,status,reason,raised_by) VALUES (%s,%s,%s,'open',%s,%s)""",
            (dispute_id, tenant_id, batch_id, reason, actor),
        )
        conn.execute("UPDATE chargeopt.vpp_settlement_batches SET status='disputed' WHERE id=%s", (batch_id,))
        event_hash = _append_settlement_event(
            conn,
            tenant_id,
            batch_id,
            "disputed",
            batch["status"],
            "disputed",
            actor,
            reason,
            {"dispute_id": dispute_id},
        )
    return {"id": batch_id, "status": "disputed", "event_hash": event_hash, "dispute_id": dispute_id}


def resolve_settlement_dispute(
    tenant_id: str, batch_id: str, actor: str, resolution: str, *, accepted: bool
) -> dict[str, Any]:
    target = "review" if accepted else "failed"
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        batch = _settlement_batch_for_update(conn, tenant_id, batch_id)
        _assert_settlement_transition(batch["status"], target)
        dispute = conn.execute(
            "SELECT id FROM chargeopt.vpp_settlement_disputes WHERE batch_id=%s AND status='open' FOR UPDATE",
            (batch_id,),
        ).fetchone()
        if dispute is None:
            raise ValueError("settlement batch has no open dispute")
        conn.execute(
            """UPDATE chargeopt.vpp_settlement_disputes
               SET status=%s,resolution=%s,resolved_by=%s,resolved_at=now() WHERE id=%s""",
            ("resolved" if accepted else "rejected", resolution, actor, dispute[0]),
        )
        conn.execute("UPDATE chargeopt.vpp_settlement_batches SET status=%s WHERE id=%s", (target, batch_id))
        event_hash = _append_settlement_event(
            conn,
            tenant_id,
            batch_id,
            "dispute_resolved",
            "disputed",
            target,
            actor,
            resolution,
            {"dispute_id": str(dispute[0]), "accepted": accepted},
        )
    return {"id": batch_id, "status": target, "event_hash": event_hash, "dispute_id": str(dispute[0])}


def export_settlement_batch(
    tenant_id: str, batch_id: str, actor: str, export_format: str, destination: str
) -> dict[str, Any]:
    if export_format not in {"csv", "json"}:
        raise ValueError("settlement export format must be csv or json")
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        batch = _settlement_batch_for_update(conn, tenant_id, batch_id)
        _assert_settlement_transition(batch["status"], "exported")
        cursor = conn.execute(
            """SELECT trade_id,committed_kwh,delivered_kwh,performance_score,gross_revenue,
                      imbalance_cost,penalty,net_revenue,evidence
               FROM chargeopt.vpp_settlement_lines WHERE batch_id=%s ORDER BY trade_id""",
            (batch_id,),
        )
        lines = [_columns(cursor, row) for row in cursor.fetchall()]
        export_rows = [{key: value for key, value in line.items() if key != "evidence"} for line in lines]
        if export_format == "json":
            content = json.dumps(export_rows, separators=(",", ":"), sort_keys=True, default=str)
        else:
            output = StringIO()
            fields = list(export_rows[0]) if export_rows else ["trade_id"]
            writer = DictWriter(output, fieldnames=fields, lineterminator="\n")
            writer.writeheader()
            writer.writerows(export_rows)
            content = output.getvalue()
        content_hash = hashlib.sha256(content.encode()).hexdigest()
        export_id = f"stx-{uuid4().hex}"
        manifest = {
            "batch_id": batch_id,
            "evidence_root_hash": batch["evidence_root_hash"],
            "content_hash": content_hash,
            "row_count": len(export_rows),
        }
        conn.execute(
            """INSERT INTO chargeopt.vpp_settlement_exports
               (id,tenant_id,batch_id,format,destination,content_hash,row_count,manifest,generated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (
                export_id,
                tenant_id,
                batch_id,
                export_format,
                destination,
                content_hash,
                len(export_rows),
                _json(manifest),
                actor,
            ),
        )
        conn.execute(
            "UPDATE chargeopt.vpp_settlement_batches SET status='exported',exported_at=now() WHERE id=%s", (batch_id,)
        )
        event_hash = _append_settlement_event(
            conn,
            tenant_id,
            batch_id,
            "exported",
            "approved",
            "exported",
            actor,
            None,
            manifest | {"destination": destination},
        )
    return {
        "id": batch_id,
        "status": "exported",
        "event_hash": event_hash,
        "export_id": export_id,
        "format": export_format,
        "destination": destination,
        "content_hash": content_hash,
        "row_count": len(export_rows),
        "content": content,
    }


def mark_settlement_paid(tenant_id: str, batch_id: str, actor: str, payment_reference: str) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        batch = _settlement_batch_for_update(conn, tenant_id, batch_id)
        _assert_settlement_transition(batch["status"], "paid")
        conn.execute(
            "UPDATE chargeopt.vpp_settlement_batches SET status='paid',paid_at=now(),payment_reference=%s WHERE id=%s",
            (payment_reference, batch_id),
        )
        event_hash = _append_settlement_event(
            conn,
            tenant_id,
            batch_id,
            "paid",
            "exported",
            "paid",
            actor,
            None,
            {"payment_reference": payment_reference, "net_revenue": float(batch["net_revenue"])},
        )
    return {"id": batch_id, "status": "paid", "event_hash": event_hash, "payment_reference": payment_reference}


def reverse_settlement_batch(
    tenant_id: str, batch_id: str, actor: str, reason: str, external_reference: str | None
) -> dict[str, Any]:
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        batch = _settlement_batch_for_update(conn, tenant_id, batch_id)
        _assert_settlement_transition(batch["status"], "reversed")
        adjustment_id = f"sta-{uuid4().hex}"
        amount = -float(batch["net_revenue"])
        conn.execute(
            """INSERT INTO chargeopt.vpp_settlement_adjustments
               (id,tenant_id,batch_id,adjustment_type,amount,reason,external_reference,created_by)
               VALUES (%s,%s,%s,'reversal',%s,%s,%s,%s)""",
            (adjustment_id, tenant_id, batch_id, amount, reason, external_reference, actor),
        )
        conn.execute(
            "UPDATE chargeopt.vpp_settlement_batches SET status='reversed',reversed_at=now() WHERE id=%s", (batch_id,)
        )
        event_hash = _append_settlement_event(
            conn,
            tenant_id,
            batch_id,
            "reversed",
            "paid",
            "reversed",
            actor,
            reason,
            {"adjustment_id": adjustment_id, "amount": amount, "external_reference": external_reference},
        )
    return {
        "id": batch_id,
        "status": "reversed",
        "event_hash": event_hash,
        "adjustment_id": adjustment_id,
        "amount": amount,
    }


def set_circuit_breaker(
    tenant_id: str, state: str, reason: str, actor: str, reset_after: datetime | None = None
) -> dict[str, Any]:
    if state not in {"closed", "open", "half_open"}:
        raise ValueError("invalid circuit breaker state")
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        row = conn.execute(
            """
            INSERT INTO chargeopt.vpp_circuit_breakers (
                id,tenant_id,scope,state,reason,failure_count,opened_at,reset_after,updated_by
            ) VALUES (%s,%s,'global',%s,%s,%s,CASE WHEN %s='open' THEN now() END,%s,%s)
            ON CONFLICT (tenant_id,scope) DO UPDATE SET
                state=EXCLUDED.state, reason=EXCLUDED.reason,
                failure_count=CASE WHEN EXCLUDED.state='open' THEN chargeopt.vpp_circuit_breakers.failure_count+1 ELSE 0 END,
                opened_at=CASE WHEN EXCLUDED.state='open' THEN now() ELSE NULL END,
                reset_after=EXCLUDED.reset_after, updated_by=EXCLUDED.updated_by, updated_at=now()
            RETURNING state,reason,failure_count,opened_at,reset_after,updated_by,updated_at
            """,
            (f"cb-{uuid4().hex}", tenant_id, state, reason, 1 if state == "open" else 0, state, reset_after, actor),
        ).fetchone()
        _audit(conn, tenant_id, actor, "vpp.circuit_breaker", "global", f"{state}:{reason}")
    return {
        "state": str(row[0]),
        "reason": row[1],
        "failure_count": int(row[2]),
        "opened_at": row[3],
        "reset_after": row[4],
        "updated_by": str(row[5]),
        "updated_at": row[6],
    }


def trading_dashboard(tenant_id: str) -> dict[str, Any]:
    context = get_trading_context(tenant_id)
    with get_connection() as conn, conn.transaction():
        _tenant_context(conn, tenant_id)
        order_cursor = conn.execute(
            """
            SELECT id,client_order_id,market_order_id,product,side,delivery_start,delivery_end,
                   quantity_kw,limit_price_per_kwh,filled_quantity_kw,average_fill_price,status,last_error,created_at
            FROM chargeopt.market_orders WHERE tenant_id=%s ORDER BY created_at DESC LIMIT 50
            """,
            (tenant_id,),
        )
        orders = [_columns(order_cursor, row) for row in order_cursor.fetchall()]
        run_cursor = conn.execute(
            """
            SELECT id,status,trigger_source,orders_created,tasks_created,summary,error,started_at,completed_at
            FROM chargeopt.vpp_automation_runs WHERE tenant_id=%s ORDER BY started_at DESC LIMIT 20
            """,
            (tenant_id,),
        )
        runs = [_columns(run_cursor, row) for row in run_cursor.fetchall()]
        settlement_cursor = conn.execute(
            """
            SELECT id,market_code,period_start,period_end,status,gross_revenue,imbalance_cost,penalties,net_revenue,evidence_root_hash
            FROM chargeopt.vpp_settlement_batches WHERE tenant_id=%s ORDER BY period_start DESC LIMIT 20
            """,
            (tenant_id,),
        )
        settlements = [_columns(settlement_cursor, row) for row in settlement_cursor.fetchall()]
        metrics = conn.execute(
            """
            SELECT
              COALESCE(sum(quantity_kw) FILTER (WHERE status IN ('submitted','partially_filled','filled')),0),
              COALESCE(sum(filled_quantity_kw),0),
              count(*) FILTER (WHERE status='failed'),
              count(*) FILTER (WHERE status='risk_rejected')
            FROM chargeopt.market_orders WHERE tenant_id=%s AND created_at >= now()-interval '24 hours'
            """,
            (tenant_id,),
        ).fetchone()
    return {
        "generated_at": datetime.now(UTC),
        "connection": {
            key: context["connection"].get(key)
            for key in ("id", "market_code", "participant_id", "adapter", "mode", "enabled", "timezone")
        },
        "risk_policy": {
            key: context["policy"].get(key)
            for key in (
                "id",
                "name",
                "version",
                "max_order_kw",
                "max_daily_energy_kwh",
                "min_confidence",
                "reserve_margin",
                "auto_trade_enabled",
                "auto_dispatch_enabled",
            )
        },
        "circuit_breaker": context["circuit_breaker"],
        "metrics": {
            "submitted_kw_24h": float(metrics[0]),
            "filled_kw_24h": float(metrics[1]),
            "failed_orders_24h": int(metrics[2]),
            "risk_rejections_24h": int(metrics[3]),
            "open_orders": context["open_order_count"],
            "committed_energy_kwh": context["committed_energy_kwh"],
        },
        "orders": orders,
        "automation_runs": runs,
        "settlements": settlements,
    }
