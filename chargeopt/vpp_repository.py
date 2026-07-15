"""PostgreSQL persistence for the VPP automated trading control plane."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
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
    return {
        "policy": _columns(policy_cursor, policy_row),
        "connection": _columns(connection_cursor, connection_row),
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
                "target_grid_kw": allocation["target_grid_kw"],
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
                    allocation["target_grid_kw"],
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
                JOIN chargeopt.delivery_schedules ds ON ds.station_id=mi.station_id AND ds.trade_id=%s
                WHERE mi.tenant_id=%s AND mi.interval_start >= %s AND mi.interval_end <= %s
                ORDER BY mi.interval_start
                """,
                (trade["id"], tenant_id, trade["delivery_start"], trade["delivery_end"]),
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
