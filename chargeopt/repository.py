"""PostgreSQL-backed repository implementation.

Provides the same ``Repository`` interface as ``data.py`` but reads from
the live database when ``DATABASE_URL`` is configured.  Falls back to the
deterministic in-memory fixtures otherwise (development / Vercel).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from threading import Lock
from uuid import uuid4

from .auth import Principal, hash_token, new_session_token, session_expiry, verify_password
from .config import get_settings
from .data import Repository, load_repository
from .db import get_connection
from .domain import (
    Alert,
    AuditEntry,
    Region,
    Station,
    TariffPeriod,
    TariffPlan,
    TelemetryPoint,
    Tenant,
    VppEvent,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# TTL cache for the repository (avoids a full DB round-trip per request)
# ---------------------------------------------------------------------------

_CACHE_TTL_SECONDS: float = 30.0  # refresh at most every 30 s
_cache_lock = Lock()
_cached_repos: dict[str, tuple[Repository, float]] = {}


def invalidate_repository_cache() -> None:
    """Force the next call to load_repository_from_db to re-query the DB."""
    with _cache_lock:
        _cached_repos.clear()


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def load_repository_from_db(tenant_id: str | None = None) -> Repository:
    """Return a Repository, using a short TTL cache to avoid per-request DB queries.

    Falls back to in-memory fixtures when DATABASE_URL is absent.
    """
    settings = get_settings()
    if not settings.use_db:
        logger.debug("No DATABASE_URL – using in-memory repository.")
        return _filter_repository_by_tenant(load_repository(), tenant_id)

    now = time.monotonic()
    cache_key = tenant_id or "*"
    with _cache_lock:
        cached = _cached_repos.get(cache_key)
        if cached is not None and now < cached[1]:
            return cached[0]

    try:
        repo = _load_from_postgres(tenant_id)
    except Exception as exc:
        if settings.is_production:
            logger.exception("Failed to load repository from PostgreSQL in production.")
            raise RuntimeError("Database repository load failed in production.") from exc
        logger.exception("Failed to load repository from PostgreSQL – falling back to in-memory fixtures.")
        repo = _filter_repository_by_tenant(load_repository(), tenant_id)

    with _cache_lock:
        _cached_repos[cache_key] = (repo, time.monotonic() + _CACHE_TTL_SECONDS)

    return repo


# ---------------------------------------------------------------------------
# Internal PostgreSQL loader
# ---------------------------------------------------------------------------


def _filter_repository_by_tenant(repo: Repository, tenant_id: str | None) -> Repository:
    if tenant_id is None:
        return repo
    station_ids = {station.id for station in repo.stations if station.tenant_id == tenant_id}
    return Repository(
        tenants=tuple(tenant for tenant in repo.tenants if tenant.id == tenant_id),
        regions=repo.regions,
        tariff_plans=repo.tariff_plans,
        stations=tuple(station for station in repo.stations if station.tenant_id == tenant_id),
        telemetry=tuple(point for point in repo.telemetry if point.station_id in station_ids),
        alerts=tuple(alert for alert in repo.alerts if alert.station_id in station_ids),
        vpp_events=tuple(event for event in repo.vpp_events if event.tenant_id == tenant_id),
        audit=tuple(entry for entry in repo.audit if tenant_id in entry.detail or tenant_id in entry.target),
    )


def _load_from_postgres(tenant_id: str | None = None) -> Repository:
    with get_connection() as conn:
        _set_tenant_context(conn, tenant_id)
        tenants = _load_tenants(conn, tenant_id)
        regions = _load_regions(conn)
        tariff_plans = _load_tariff_plans(conn)
        stations = _load_stations(conn, tenant_id)
        telemetry = _load_telemetry(conn, tenant_id)
        alerts = _load_alerts(conn, tenant_id)
        vpp_events = _load_vpp_events(conn, tenant_id)
        audit = _load_audit(conn, tenant_id)

    return Repository(
        tenants=tuple(tenants),
        regions=tuple(regions),
        tariff_plans=tuple(tariff_plans),
        stations=tuple(stations),
        telemetry=tuple(telemetry),
        alerts=tuple(alerts),
        vpp_events=tuple(vpp_events),
        audit=tuple(audit),
    )


def _load_tenants(conn, tenant_id: str | None = None) -> list[Tenant]:
    if tenant_id is None:
        rows = conn.execute("SELECT id, name, plan FROM chargeopt.tenants ORDER BY id").fetchall()
    else:
        rows = conn.execute(
            "SELECT id, name, plan FROM chargeopt.tenants WHERE id = %s ORDER BY id", (tenant_id,)
        ).fetchall()
    return [Tenant(r[0], r[1], r[2]) for r in rows]


def _load_regions(conn) -> list[Region]:
    rows = conn.execute("SELECT id, name, grid_operator FROM chargeopt.regions ORDER BY id").fetchall()
    return [Region(r[0], r[1], r[2]) for r in rows]


def _load_tariff_plans(conn) -> list[TariffPlan]:
    plan_rows = conn.execute(
        "SELECT id, name, demand_charge_per_kw_month, service_fee_per_kwh FROM chargeopt.tariff_plans ORDER BY id"
    ).fetchall()
    period_rows = conn.execute(
        "SELECT tariff_plan_id, name, start_hour, end_hour, energy_price_per_kwh"
        " FROM chargeopt.tariff_periods ORDER BY tariff_plan_id, start_hour"
    ).fetchall()

    periods_by_plan: dict[str, list[TariffPeriod]] = {}
    for pr in period_rows:
        periods_by_plan.setdefault(pr[0], []).append(TariffPeriod(pr[1], int(pr[2]), int(pr[3]), float(pr[4])))

    return [
        TariffPlan(
            r[0],
            r[1],
            tuple(periods_by_plan.get(r[0], [])),
            float(r[2]),
            float(r[3]),
        )
        for r in plan_rows
    ]


def _load_stations(conn, tenant_id: str | None = None) -> list[Station]:
    sql = """
        SELECT id, tenant_id, region_id, name, station_type, address,
               latitude, longitude, transformer_capacity_kw, charger_count,
               connector_count, max_connector_power_kw, storage_capacity_kwh,
               storage_power_kw, pv_capacity_kw, tariff_plan_id,
               monthly_opex, reliability_score, dispatch_mode
        FROM chargeopt.stations
        """
    params: tuple = ()
    if tenant_id is not None:
        sql += " WHERE tenant_id = %s"
        params = (tenant_id,)
    sql += " ORDER BY id"
    rows = conn.execute(sql, params).fetchall()
    return [
        Station(
            r[0],
            r[1],
            r[2],
            r[3],
            r[4],
            r[5],
            float(r[6]),
            float(r[7]),
            transformer_capacity_kw=float(r[8]),
            charger_count=int(r[9]),
            connector_count=int(r[10]),
            max_connector_power_kw=float(r[11]),
            storage_capacity_kwh=float(r[12]),
            storage_power_kw=float(r[13]),
            pv_capacity_kw=float(r[14]),
            tariff_plan_id=r[15],
            monthly_opex=float(r[16]),
            reliability_score=float(r[17]),
            dispatch_mode=r[18],
        )
        for r in rows
    ]


def _load_telemetry(conn, tenant_id: str | None = None) -> list[TelemetryPoint]:
    sql = """
        SELECT tp.station_id, tp.timestamp, tp.load_kw, tp.pv_kw, tp.grid_kw,
               tp.storage_power_kw, tp.storage_soc, tp.connector_occupied,
               tp.queue_length, tp.sessions, tp.energy_kwh, tp.revenue, tp.alert_count
        FROM chargeopt.telemetry_points tp
        JOIN chargeopt.stations s ON s.id = tp.station_id
        """
    params: tuple = ()
    if tenant_id is not None:
        sql += " WHERE s.tenant_id = %s"
        params = (tenant_id,)
    sql += " ORDER BY tp.station_id, tp.timestamp"
    rows = conn.execute(sql, params).fetchall()
    return [
        TelemetryPoint(
            r[0],
            _to_dt(r[1]),
            float(r[2]),
            float(r[3]),
            float(r[4]),
            float(r[5]),
            float(r[6]),
            int(r[7]),
            int(r[8]),
            int(r[9]),
            float(r[10]),
            float(r[11]),
            int(r[12]),
        )
        for r in rows
    ]


def _load_alerts(conn, tenant_id: str | None = None) -> list[Alert]:
    sql = """
        SELECT a.id, a.station_id, a.timestamp, a.priority, a.title, a.detail, a.acknowledged
        FROM chargeopt.alerts a
        JOIN chargeopt.stations s ON s.id = a.station_id
        """
    params: tuple = ()
    if tenant_id is not None:
        sql += " WHERE s.tenant_id = %s"
        params = (tenant_id,)
    sql += " ORDER BY a.timestamp DESC"
    rows = conn.execute(sql, params).fetchall()
    return [Alert(r[0], r[1], _to_dt(r[2]), r[3], r[4], r[5], bool(r[6])) for r in rows]


def _load_vpp_events(conn, tenant_id: str | None = None) -> list[VppEvent]:
    sql = "SELECT id, tenant_id, title, start_at, duration_minutes, requested_kw, incentive_per_kwh, status FROM chargeopt.vpp_events"
    params: tuple = ()
    if tenant_id is not None:
        sql += " WHERE tenant_id = %s"
        params = (tenant_id,)
    sql += " ORDER BY start_at DESC"
    rows = conn.execute(sql, params).fetchall()
    return [VppEvent(r[0], r[1], r[2], _to_dt(r[3]), int(r[4]), float(r[5]), float(r[6]), r[7]) for r in rows]


def _load_audit(conn, tenant_id: str | None = None) -> list[AuditEntry]:
    sql = "SELECT id, timestamp, actor, action, target, detail FROM chargeopt.audit_entries"
    params: tuple = ()
    if tenant_id is not None:
        sql += " WHERE tenant_id = %s"
        params = (tenant_id,)
    sql += " ORDER BY timestamp DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    return [AuditEntry(r[0], _to_dt(r[1]), r[2], r[3], r[4], r[5]) for r in rows]


def _to_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


def _set_tenant_context(conn, tenant_id: str | None) -> None:
    conn.execute("SELECT set_config('chargeopt.tenant_id', %s, true)", (tenant_id or "*",))


def _tenant_for_station(conn, station_id: str | None) -> str:
    if station_id is None:
        return "t-001"
    row = conn.execute("SELECT tenant_id FROM chargeopt.stations WHERE id = %s", (station_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown station_id: {station_id}")
    return str(row[0])


def _tenant_for_vpp_event(conn, event_id: str) -> str:
    row = conn.execute("SELECT tenant_id FROM chargeopt.vpp_events WHERE id = %s", (event_id,)).fetchone()
    if row is None:
        raise KeyError(f"Unknown event_id: {event_id}")
    return str(row[0])


def _tenant_for_proof(conn, tenant_id: str | None, station_id: str | None) -> str:
    if station_id is not None:
        return _tenant_for_station(conn, station_id)
    if tenant_id is not None:
        return tenant_id
    row = conn.execute("SELECT id FROM chargeopt.tenants ORDER BY id LIMIT 1").fetchone()
    return str(row[0]) if row is not None else "t-001"


def _ensure_tenant_allowed(scope_tenant_id: str | None, resource_tenant_id: str) -> None:
    if scope_tenant_id is not None and scope_tenant_id != "*" and scope_tenant_id != resource_tenant_id:
        raise PermissionError("Resource belongs to another tenant.")


# ---------------------------------------------------------------------------
# Write-path operations
# ---------------------------------------------------------------------------


def append_audit(actor: str, action: str, target: str, detail: str, tenant_id: str = "t-001") -> str:
    """Persist an audit entry and return its generated ID."""
    audit_id = f"au-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        conn.execute(
            "SELECT set_config('chargeopt.tenant_id', %s, true)",
            (tenant_id,),
        )
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), %s, %s, %s, %s)
                """,
            (audit_id, tenant_id, actor, action, target, detail),
        )
    invalidate_repository_cache()
    return audit_id


def ingest_telemetry(payload: dict, scope_tenant_id: str | None = None) -> dict[str, object]:
    """Upsert a telemetry point with idempotency tracking."""
    station_id = payload["station_id"]
    timestamp = payload["timestamp"]
    timestamp_key = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
    idempotency_key = payload.get("idempotency_key") or f"{station_id}:{timestamp_key}"
    actor = payload.get("actor") or "edge-gateway"

    with get_connection() as conn, conn.transaction():
        tenant_id = _tenant_for_station(conn, station_id)
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        existing = conn.execute(
            "SELECT 1 FROM chargeopt.telemetry_ingest_log WHERE idempotency_key = %s",
            (idempotency_key,),
        ).fetchone()
        conn.execute(
            """
                INSERT INTO chargeopt.telemetry_points (
                    station_id, timestamp, load_kw, pv_kw, grid_kw, storage_power_kw, storage_soc,
                    connector_occupied, queue_length, sessions, energy_kwh, revenue, alert_count
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (station_id, timestamp) DO UPDATE SET
                    load_kw = EXCLUDED.load_kw,
                    pv_kw = EXCLUDED.pv_kw,
                    grid_kw = EXCLUDED.grid_kw,
                    storage_power_kw = EXCLUDED.storage_power_kw,
                    storage_soc = EXCLUDED.storage_soc,
                    connector_occupied = EXCLUDED.connector_occupied,
                    queue_length = EXCLUDED.queue_length,
                    sessions = EXCLUDED.sessions,
                    energy_kwh = EXCLUDED.energy_kwh,
                    revenue = EXCLUDED.revenue,
                    alert_count = EXCLUDED.alert_count
                """,
            (
                station_id,
                timestamp,
                payload["load_kw"],
                payload["pv_kw"],
                payload["grid_kw"],
                payload["storage_power_kw"],
                payload["storage_soc"],
                payload["connector_occupied"],
                payload["queue_length"],
                payload["sessions"],
                payload["energy_kwh"],
                payload["revenue"],
                payload["alert_count"],
            ),
        )
        conn.execute(
            """
                INSERT INTO chargeopt.telemetry_ingest_log (
                    idempotency_key, station_id, telemetry_timestamp, actor
                )
                VALUES (%s, %s, %s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
            (idempotency_key, station_id, timestamp, actor),
        )
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), %s, 'telemetry.ingested', %s, %s)
                """,
            (
                f"au-{uuid4().hex}",
                tenant_id,
                actor,
                station_id,
                f"Telemetry point {timestamp_key} ingested with key {idempotency_key}.",
            ),
        )
    invalidate_repository_cache()
    return {
        "station_id": station_id,
        "timestamp": timestamp_key,
        "created": existing is None,
        "idempotency_key": idempotency_key,
    }


def acknowledge_alert(alert_id: str, actor: str, scope_tenant_id: str | None = None) -> dict[str, object]:
    """Acknowledge an alert and audit the action."""
    with get_connection() as conn, conn.transaction():
        tenant_row = conn.execute(
            """
            SELECT s.tenant_id
            FROM chargeopt.alerts a
            JOIN chargeopt.stations s ON s.id = a.station_id
            WHERE a.id = %s
            """,
            (alert_id,),
        ).fetchone()
        if tenant_row is None:
            raise KeyError(f"Unknown alert_id: {alert_id}")
        tenant_id = str(tenant_row[0])
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        cursor = conn.execute(
            "UPDATE chargeopt.alerts SET acknowledged = true WHERE id = %s",
            (alert_id,),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown alert_id: {alert_id}")
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), %s, 'alert.acknowledged', %s, 'Alert acknowledged.')
                """,
            (f"au-{uuid4().hex}", tenant_id, actor, alert_id),
        )
    invalidate_repository_cache()
    return {"id": alert_id, "acknowledged": True}


def persist_dispatch_recommendations(recommendations: list[dict], actor: str) -> int:
    """Persist generated dispatch recommendations without overwriting review status."""
    from psycopg.types.json import Json

    with get_connection() as conn, conn.transaction():
        tenant_id = "t-001"
        for item in recommendations:
            tenant_id = _tenant_for_station(conn, item["station_id"])
            _set_tenant_context(conn, tenant_id)
            conn.execute(
                """
                    INSERT INTO chargeopt.dispatch_recommendations (
                        id, tenant_id, station_id, title, risk, action, value, dispatch_window,
                        mode, approval, rationale, command_payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        tenant_id = EXCLUDED.tenant_id,
                        title = EXCLUDED.title,
                        risk = EXCLUDED.risk,
                        action = EXCLUDED.action,
                        value = EXCLUDED.value,
                        dispatch_window = EXCLUDED.dispatch_window,
                        mode = EXCLUDED.mode,
                        approval = EXCLUDED.approval,
                        rationale = EXCLUDED.rationale,
                        command_payload = EXCLUDED.command_payload,
                        updated_at = now()
                    """,
                (
                    item["id"],
                    tenant_id,
                    item["station_id"],
                    item["title"],
                    item["risk"],
                    item["action"],
                    item["value"],
                    item["window"],
                    item["mode"],
                    item["approval"],
                    item["rationale"],
                    Json(item),
                ),
            )
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), %s, 'dispatch.generated', 'dispatch_recommendations', %s)
                """,
            (f"au-{uuid4().hex}", tenant_id, actor, f"Persisted {len(recommendations)} dispatch recommendations."),
        )
    invalidate_repository_cache()
    return len(recommendations)


def update_dispatch_status(
    recommendation_id: str,
    status: str,
    actor: str,
    reason: str | None,
    scope_tenant_id: str | None = None,
) -> dict[str, str]:
    """Approve/reject/execute a persisted dispatch recommendation."""
    allowed = {"pending", "approved", "rejected", "executed", "failed", "rolled_back"}
    if status not in allowed:
        raise ValueError(f"Invalid dispatch status: {status}")

    with get_connection() as conn, conn.transaction():
        tenant_row = conn.execute(
            "SELECT tenant_id FROM chargeopt.dispatch_recommendations WHERE id = %s",
            (recommendation_id,),
        ).fetchone()
        if tenant_row is None:
            raise KeyError(f"Unknown recommendation_id: {recommendation_id}")
        tenant_id = str(tenant_row[0])
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        cursor = conn.execute(
            """
                UPDATE chargeopt.dispatch_recommendations
                SET status = %s,
                    reviewed_by = %s,
                    reviewed_at = now(),
                    review_reason = %s,
                    updated_at = now()
                WHERE id = %s
                """,
            (status, actor, reason, recommendation_id),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown recommendation_id: {recommendation_id}")
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), %s, 'dispatch.status_changed', %s, %s)
                """,
            (f"au-{uuid4().hex}", tenant_id, actor, recommendation_id, reason or f"Status changed to {status}."),
        )
    invalidate_repository_cache()
    return {"id": recommendation_id, "status": status}


def persist_roi_simulation(
    station_id: str | None,
    roi: dict,
    inputs: dict,
    scope_tenant_id: str | None = None,
) -> int:
    """Persist an ROI simulation and return its database ID."""
    from psycopg.types.json import Json

    with get_connection() as conn, conn.transaction():
        tenant_id = _tenant_for_station(conn, station_id) if station_id else "t-001"
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        row = conn.execute(
            """
                INSERT INTO chargeopt.roi_simulations (
                    station_id, capacity_kwh, power_kw, capex,
                    annual_demand_savings, annual_arbitrage, annual_vpp_revenue,
                    annual_degradation_cost, annual_maintenance, annual_net_benefit,
                    payback_years, irr_percent, npv_10y, recommendation, inputs
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
            (
                station_id,
                roi["capacity_kwh"],
                roi["power_kw"],
                roi["capex"],
                roi["annual_demand_savings"],
                roi["annual_arbitrage"],
                roi["annual_vpp_revenue"],
                roi["annual_degradation_cost"],
                roi["annual_maintenance"],
                roi["annual_net_benefit"],
                roi["payback_years"],
                roi["irr"],
                roi["npv_10y"],
                roi["recommendation"],
                Json(inputs),
            ),
        ).fetchone()
        simulation_id = int(row[0])
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), 'system', 'roi.persisted', %s, %s)
                """,
            (f"au-{uuid4().hex}", tenant_id, station_id or "portfolio", f"ROI simulation {simulation_id} persisted."),
        )
    invalidate_repository_cache()
    return simulation_id


# ---------------------------------------------------------------------------
# Auth, protocol, task, approval, optimization, and settlement operations
# ---------------------------------------------------------------------------


def authenticate_user(email: str, password: str) -> dict[str, object]:
    token = None
    with get_connection() as conn, conn.transaction():
        row = conn.execute(
            """
            SELECT id, tenant_id, email, display_name, role, password_salt, password_hash
            FROM chargeopt.users
            WHERE lower(email) = lower(%s) AND active = true
            """,
            (email,),
        ).fetchone()
        if row is None or not verify_password(password, row[5], row[6]):
            raise PermissionError("Invalid email or password.")
        _set_tenant_context(conn, row[1])
        token = new_session_token()
        expires_at = session_expiry()
        conn.execute(
            "INSERT INTO chargeopt.sessions (token_hash, user_id, expires_at) VALUES (%s, %s, %s)",
            (hash_token(token), row[0], expires_at),
        )
        conn.execute("UPDATE chargeopt.users SET last_login_at = now() WHERE id = %s", (row[0],))
        principal = Principal(row[0], row[1], row[4], row[3], "bearer")
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'auth.login', %s, 'User login succeeded.')
            """,
            (f"au-{uuid4().hex}", row[1], row[2], row[0]),
        )
    return {"access_token": token, "expires_at": expires_at, "principal": principal}


def principal_from_session(token: str) -> Principal | None:
    with get_connection() as conn:
        _set_tenant_context(conn, None)
        row = conn.execute(
            """
            SELECT u.id, u.tenant_id, u.display_name, u.role
            FROM chargeopt.sessions s
            JOIN chargeopt.users u ON u.id = s.user_id
            WHERE s.token_hash = %s
              AND s.revoked_at IS NULL
              AND s.expires_at > now()
              AND u.active = true
            """,
            (hash_token(token),),
        ).fetchone()
    if row is None:
        return None
    return Principal(row[0], row[1], row[3], row[2], "bearer")


def persist_protocol_message(
    tenant_id: str,
    protocol: str,
    station_id: str,
    device_id: str | None,
    external_id: str,
    message_type: str,
    payload: dict,
    scope_tenant_id: str | None = None,
) -> dict[str, object]:
    from psycopg.types.json import Json

    with get_connection() as conn, conn.transaction():
        tenant_id = _tenant_for_station(conn, station_id)
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        if device_id is None:
            device_row = conn.execute(
                "SELECT id, tenant_id, station_id FROM chargeopt.devices WHERE protocol = %s AND external_id = %s",
                (protocol, external_id),
            ).fetchone()
            if device_row is None:
                device_id = f"dev-{uuid4().hex}"
                conn.execute(
                    """
                    INSERT INTO chargeopt.devices (id, tenant_id, station_id, protocol, external_id, status, last_seen_at)
                    VALUES (%s, %s, %s, %s, %s, 'online', now())
                    """,
                    (device_id, tenant_id, station_id, protocol, external_id),
                )
            else:
                device_id = str(device_row[0])
                _ensure_tenant_allowed(scope_tenant_id, str(device_row[1]))
                if str(device_row[1]) != tenant_id or str(device_row[2]) != station_id:
                    raise PermissionError("Device belongs to another station or tenant.")
        else:
            device_row = conn.execute(
                """
                SELECT tenant_id, station_id, protocol, external_id
                FROM chargeopt.devices
                WHERE id = %s
                """,
                (device_id,),
            ).fetchone()
            if device_row is None:
                raise KeyError(f"Unknown device_id: {device_id}")
            _ensure_tenant_allowed(scope_tenant_id, str(device_row[0]))
            if (
                str(device_row[0]) != tenant_id
                or str(device_row[1]) != station_id
                or str(device_row[2]) != protocol
                or str(device_row[3]) != external_id
            ):
                raise PermissionError("Device identity does not match station, protocol, or external_id.")
            conn.execute(
                "UPDATE chargeopt.devices SET status = 'online', last_seen_at = now() WHERE id = %s", (device_id,)
            )
        row = conn.execute(
            """
            INSERT INTO chargeopt.protocol_messages (
                tenant_id, device_id, station_id, protocol, direction, message_type, payload, status
            )
            VALUES (%s, %s, %s, %s, 'inbound', %s, %s, 'accepted')
            RETURNING id
            """,
            (tenant_id, device_id, station_id, protocol, message_type, Json(payload)),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'protocol.message_received', %s, %s)
            """,
            (f"au-{uuid4().hex}", tenant_id, f"{protocol}:{external_id}", device_id, message_type),
        )
    invalidate_repository_cache()
    return {"id": int(row[0]), "tenant_id": tenant_id, "device_id": device_id}


def enqueue_task(
    tenant_id: str,
    station_id: str | None,
    device_id: str | None,
    task_type: str,
    payload: dict,
    priority: int = 100,
    idempotency_key: str | None = None,
    scope_tenant_id: str | None = None,
) -> dict[str, object]:
    from psycopg.types.json import Json

    task_id = f"tsk-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        if station_id is not None:
            tenant_id = _tenant_for_station(conn, station_id)
        if device_id is not None:
            device_row = conn.execute(
                "SELECT tenant_id, station_id FROM chargeopt.devices WHERE id = %s",
                (device_id,),
            ).fetchone()
            if device_row is None:
                raise KeyError(f"Unknown device_id: {device_id}")
            device_tenant = str(device_row[0])
            device_station = str(device_row[1])
            _ensure_tenant_allowed(scope_tenant_id, device_tenant)
            if station_id is not None and station_id != device_station:
                raise PermissionError("Device belongs to another station.")
            if station_id is None:
                station_id = device_station
                tenant_id = device_tenant
            if tenant_id != device_tenant:
                raise PermissionError("Device belongs to another tenant.")
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        row = conn.execute(
            """
            INSERT INTO chargeopt.task_queue (
                id, tenant_id, station_id, device_id, task_type, priority, idempotency_key, payload
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (idempotency_key) DO UPDATE SET updated_at = now()
            RETURNING id, tenant_id, station_id, device_id, task_type, status, priority, payload, result
                      , attempts, max_attempts, lease_expires_at, locked_by, last_error
            """,
            (task_id, tenant_id, station_id, device_id, task_type, priority, idempotency_key, Json(payload)),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), 'system', 'task.enqueued', %s, %s)
            """,
            (f"au-{uuid4().hex}", row[1], row[0], task_type),
        )
    return _task_row_to_dict(row)


def _task_row_to_dict(row) -> dict[str, object]:
    task = {
        "id": row[0],
        "tenant_id": row[1],
        "station_id": row[2],
        "device_id": row[3],
        "task_type": row[4],
        "status": row[5],
        "priority": int(row[6]),
        "payload": row[7] or {},
        "result": row[8] or {},
    }
    if len(row) > 9:
        task.update(
            {
                "attempts": int(row[9]),
                "max_attempts": int(row[10]),
                "lease_expires_at": row[11],
                "locked_by": row[12],
                "last_error": row[13],
            }
        )
    return task


def claim_next_task(
    tenant_id: str,
    worker_id: str,
    task_types: list[str] | None = None,
    lease_seconds: int = 300,
) -> dict[str, object] | None:
    """Atomically claim the next due task for an async worker."""
    filters = [
        "scheduled_at <= now()",
        "(status = 'queued' OR (status = 'running' AND lease_expires_at IS NOT NULL AND lease_expires_at < now()))",
        "attempts < max_attempts",
    ]
    params: list[object] = []
    if tenant_id != "*":
        filters.insert(0, "tenant_id = %s")
        params.append(tenant_id)
    if task_types:
        filters.append("task_type = ANY(%s)")
        params.append(task_types)
    params.extend([lease_seconds, worker_id])
    query = f"""
        WITH candidate AS (
            SELECT id
            FROM chargeopt.task_queue
            WHERE {" AND ".join(filters)}
            ORDER BY priority ASC, scheduled_at ASC, created_at ASC
            FOR UPDATE SKIP LOCKED
            LIMIT 1
        )
        UPDATE chargeopt.task_queue tq
        SET status = 'running',
            attempts = tq.attempts + 1,
            locked_at = now(),
            lease_expires_at = now() + (%s * interval '1 second'),
            locked_by = %s,
            last_error = NULL,
            updated_at = now()
        FROM candidate
        WHERE tq.id = candidate.id
        RETURNING tq.id, tq.tenant_id, tq.station_id, tq.device_id, tq.task_type, tq.status,
                  tq.priority, tq.payload, tq.result, tq.attempts, tq.max_attempts,
                  tq.lease_expires_at, tq.locked_by, tq.last_error
    """
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, None if tenant_id == "*" else tenant_id)
        row = conn.execute(query, tuple(params)).fetchone()
        if row is None:
            return None
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'task.claimed', %s, %s)
            """,
            (f"au-{uuid4().hex}", row[1], worker_id, row[0], f"lease_seconds={lease_seconds}"),
        )
    return _task_row_to_dict(row)


def complete_task(
    task_id: str,
    tenant_id: str,
    worker_id: str,
    status: str,
    result: dict,
    error: str | None = None,
    retry_delay_seconds: int = 60,
) -> dict[str, object]:
    """Complete or retry a worker task with bounded retry semantics."""
    from psycopg.types.json import Json

    with get_connection() as conn, conn.transaction():
        row = conn.execute(
            """
            SELECT tenant_id, attempts, max_attempts, status, locked_by
            FROM chargeopt.task_queue
            WHERE id = %s
            """,
            (task_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        task_tenant = str(row[0])
        if tenant_id != "*" and tenant_id != task_tenant:
            raise PermissionError("Task belongs to another tenant.")
        _set_tenant_context(conn, task_tenant)
        locked_by = row[4]
        if locked_by is not None and locked_by != worker_id:
            raise PermissionError("Task lease is owned by another worker.")

        if status == "succeeded":
            next_status = "completed"
        elif status == "cancelled":
            next_status = "cancelled"
        elif int(row[1]) < int(row[2]):
            next_status = "queued"
        else:
            next_status = "failed"

        task = conn.execute(
            """
            UPDATE chargeopt.task_queue
            SET status = %s,
                result = %s,
                last_error = %s,
                locked_at = NULL,
                lease_expires_at = NULL,
                locked_by = NULL,
                scheduled_at = CASE WHEN %s = 'queued' THEN now() + (%s * interval '1 second') ELSE scheduled_at END,
                completed_at = CASE WHEN %s IN ('completed', 'failed', 'cancelled') THEN now() ELSE completed_at END,
                updated_at = now()
            WHERE id = %s
            RETURNING id, tenant_id, station_id, device_id, task_type, status, priority, payload,
                      result, attempts, max_attempts, lease_expires_at, locked_by, last_error
            """,
            (
                next_status,
                Json(result),
                error,
                next_status,
                retry_delay_seconds,
                next_status,
                task_id,
            ),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'task.completed', %s, %s)
            """,
            (f"au-{uuid4().hex}", task_tenant, worker_id, task_id, f"{status}->{next_status}"),
        )
    invalidate_repository_cache()
    return _task_row_to_dict(task)


def reap_expired_tasks(tenant_id: str, actor: str) -> dict[str, int]:
    """Requeue or fail tasks whose worker leases expired."""
    tenant_filter = ""
    params: tuple[object, ...] = ()
    if tenant_id != "*":
        tenant_filter = "AND tenant_id = %s"
        params = (tenant_id,)
    with get_connection() as conn, conn.transaction():
        _set_tenant_context(conn, None if tenant_id == "*" else tenant_id)
        rows = conn.execute(
            f"""
            UPDATE chargeopt.task_queue
            SET status = CASE WHEN attempts >= max_attempts THEN 'failed' ELSE 'queued' END,
                last_error = COALESCE(last_error, 'worker lease expired'),
                locked_at = NULL,
                lease_expires_at = NULL,
                locked_by = NULL,
                completed_at = CASE WHEN attempts >= max_attempts THEN now() ELSE completed_at END,
                updated_at = now()
            WHERE true
              {tenant_filter}
              AND status = 'running'
              AND lease_expires_at IS NOT NULL
              AND lease_expires_at < now()
            RETURNING tenant_id, status
            """,
            params,
        ).fetchall()
        requeued = sum(1 for row in rows if row[1] == "queued")
        failed = sum(1 for row in rows if row[1] == "failed")
        if rows:
            audit_tenant = tenant_id if tenant_id != "*" else rows[0][0]
            conn.execute(
                """
                INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
                VALUES (%s, %s, now(), %s, 'task.reaped', 'task_queue', %s)
                """,
                (f"au-{uuid4().hex}", audit_tenant, actor, f"requeued={requeued}; failed={failed}"),
            )
    invalidate_repository_cache()
    return {"requeued": requeued, "failed": failed, "total": len(rows)}


def persist_revenue_proof(
    tenant_id: str | None,
    station_id: str | None,
    diagnostics: dict,
    created_by: str,
    scope_tenant_id: str | None = None,
) -> str:
    """Persist a revenue-proof snapshot for monthly ROI auditability."""
    from psycopg.types.json import Json

    proof_id = f"rpf-{uuid4().hex}"
    portfolio = diagnostics["portfolio"]
    interval = portfolio["confidence_interval"]
    scope = diagnostics["scope"]
    algorithm = diagnostics["algorithm"]
    with get_connection() as conn, conn.transaction():
        proof_tenant = _tenant_for_proof(conn, tenant_id, station_id)
        _ensure_tenant_allowed(scope_tenant_id, proof_tenant)
        _set_tenant_context(conn, proof_tenant)
        conn.execute(
            """
            INSERT INTO chargeopt.revenue_proof_runs (
                id, tenant_id, station_id, generated_at, algorithm,
                monthly_net_impact, p90_low, p90_high, evidence_window_hours,
                payload, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                proof_id,
                proof_tenant,
                station_id,
                diagnostics["generated_at"],
                algorithm["name"],
                portfolio["monthly_net_impact"],
                interval["p90_low"],
                interval["p90_high"],
                scope["evidence_window_hours"],
                Json(diagnostics),
                created_by,
            ),
        )
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'revenue_proof.persisted', %s, %s)
            """,
            (
                f"au-{uuid4().hex}",
                proof_tenant,
                created_by,
                proof_id,
                f"monthly_net_impact={portfolio['monthly_net_impact']}",
            ),
        )
    return proof_id


def request_dispatch_approval(recommendation_id: str, principal: Principal, reason: str | None) -> dict[str, object]:
    approval_id = f"apv-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        row = conn.execute(
            "SELECT tenant_id FROM chargeopt.dispatch_recommendations WHERE id = %s",
            (recommendation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown recommendation_id: {recommendation_id}")
        tenant_id = str(row[0])
        _ensure_tenant_allowed(None if principal.is_platform_admin else principal.tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        approval = conn.execute(
            """
            INSERT INTO chargeopt.dispatch_approvals (id, tenant_id, recommendation_id, requested_by, reason)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (recommendation_id) DO UPDATE SET reason = EXCLUDED.reason
            RETURNING id, recommendation_id, status
            """,
            (approval_id, tenant_id, recommendation_id, principal.subject, reason),
        ).fetchone()
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'dispatch.approval_requested', %s, %s)
            """,
            (f"au-{uuid4().hex}", tenant_id, principal.subject, recommendation_id, reason or "Approval requested."),
        )
    return {"id": approval[0], "recommendation_id": approval[1], "status": approval[2], "task_id": None}


def review_dispatch_approval(
    recommendation_id: str,
    principal: Principal,
    approved: bool,
    reason: str | None,
) -> dict[str, object]:
    with get_connection() as conn, conn.transaction():
        row = conn.execute(
            """
            SELECT dr.tenant_id, dr.station_id, dr.command_payload
            FROM chargeopt.dispatch_recommendations dr
            WHERE dr.id = %s
            """,
            (recommendation_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"Unknown recommendation_id: {recommendation_id}")
        tenant_id, station_id, command_payload = row[0], row[1], row[2] or {}
        _ensure_tenant_allowed(None if principal.is_platform_admin else principal.tenant_id, str(tenant_id))
        _set_tenant_context(conn, tenant_id)
        status = "approved" if approved else "rejected"
        approval = conn.execute(
            """
            UPDATE chargeopt.dispatch_approvals
            SET status = %s, reviewed_by = %s, reason = %s, reviewed_at = now()
            WHERE recommendation_id = %s
            RETURNING id, recommendation_id, status
            """,
            (status, principal.subject, reason, recommendation_id),
        ).fetchone()
        if approval is None:
            approval = conn.execute(
                """
                INSERT INTO chargeopt.dispatch_approvals (
                    id, tenant_id, recommendation_id, requested_by, reviewed_by, status, reason, reviewed_at
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                RETURNING id, recommendation_id, status
                """,
                (
                    f"apv-{uuid4().hex}",
                    tenant_id,
                    recommendation_id,
                    principal.subject,
                    principal.subject,
                    status,
                    reason,
                ),
            ).fetchone()
        task_id = None
        if approved:
            task = conn.execute(
                """
                INSERT INTO chargeopt.task_queue (id, tenant_id, station_id, task_type, priority, payload)
                VALUES (%s, %s, %s, 'dispatch.execute', 10, %s)
                RETURNING id
                """,
                (f"tsk-{uuid4().hex}", tenant_id, station_id, JsonCompat(command_payload)),
            ).fetchone()
            task_id = task[0]
            conn.execute(
                "UPDATE chargeopt.dispatch_recommendations SET status = 'approved', reviewed_by = %s, reviewed_at = now() WHERE id = %s",
                (principal.subject, recommendation_id),
            )
        else:
            conn.execute(
                "UPDATE chargeopt.dispatch_recommendations SET status = 'rejected', reviewed_by = %s, reviewed_at = now(), review_reason = %s WHERE id = %s",
                (principal.subject, reason, recommendation_id),
            )
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'dispatch.approval_reviewed', %s, %s)
            """,
            (f"au-{uuid4().hex}", tenant_id, principal.subject, recommendation_id, status),
        )
    invalidate_repository_cache()
    return {"id": approval[0], "recommendation_id": approval[1], "status": approval[2], "task_id": task_id}


def JsonCompat(payload: dict):
    from psycopg.types.json import Json

    return Json(payload)


def record_edge_receipt(
    tenant_id: str,
    task_id: str,
    station_id: str | None,
    device_id: str | None,
    status: str,
    payload: dict,
    scope_tenant_id: str | None = None,
) -> dict[str, str]:
    from psycopg.types.json import Json

    receipt_id = f"rcp-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        task_row = conn.execute(
            "SELECT tenant_id, station_id, device_id FROM chargeopt.task_queue WHERE id = %s",
            (task_id,),
        ).fetchone()
        if task_row is None:
            raise KeyError(f"Unknown task_id: {task_id}")
        tenant_id = str(task_row[0])
        task_station_id = str(task_row[1]) if task_row[1] is not None else None
        task_device_id = str(task_row[2]) if task_row[2] is not None else None
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        if station_id is not None and task_station_id is not None and station_id != task_station_id:
            raise PermissionError("Receipt station_id does not match task station_id.")
        if device_id is not None and task_device_id is not None and device_id != task_device_id:
            raise PermissionError("Receipt device_id does not match task device_id.")
        if station_id is not None:
            station_tenant = _tenant_for_station(conn, station_id)
            if station_tenant != tenant_id:
                raise PermissionError("Receipt station belongs to another tenant.")
        if device_id is not None:
            device_row = conn.execute(
                "SELECT tenant_id, station_id FROM chargeopt.devices WHERE id = %s",
                (device_id,),
            ).fetchone()
            if device_row is None:
                raise KeyError(f"Unknown device_id: {device_id}")
            if str(device_row[0]) != tenant_id:
                raise PermissionError("Receipt device belongs to another tenant.")
            if station_id is not None and str(device_row[1]) != station_id:
                raise PermissionError("Receipt device belongs to another station.")
        _set_tenant_context(conn, tenant_id)
        conn.execute(
            """
            INSERT INTO chargeopt.edge_command_receipts (id, tenant_id, task_id, station_id, device_id, status, payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (receipt_id, tenant_id, task_id, station_id, device_id, status, Json(payload)),
        )
        task_status = (
            "completed" if status == "succeeded" else "failed" if status in {"failed", "rolled_back"} else "running"
        )
        conn.execute(
            """
            UPDATE chargeopt.task_queue
            SET status = %s, result = %s, completed_at = CASE WHEN %s IN ('completed', 'failed') THEN now() ELSE completed_at END,
                updated_at = now()
            WHERE id = %s
            """,
            (
                task_status,
                Json({"edge_status": status, "receipt_id": receipt_id, "payload": payload}),
                task_status,
                task_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), 'edge-gateway', 'edge.receipt', %s, %s)
            """,
            (f"au-{uuid4().hex}", tenant_id, task_id, status),
        )
    return {"id": receipt_id, "task_id": task_id, "status": status}


def persist_optimization_run(
    tenant_id: str,
    scope: str,
    objective: str,
    horizon_hours: int,
    solver: str,
    objective_value: float,
    inputs: dict,
    outputs: dict,
    created_by: str,
    scope_tenant_id: str | None = None,
) -> str:
    from psycopg.types.json import Json

    run_id = f"opt-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        conn.execute(
            """
            INSERT INTO chargeopt.optimization_runs (
                id, tenant_id, scope, objective, horizon_hours, solver, status,
                objective_value, inputs, outputs, created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, 'completed', %s, %s, %s, %s)
            """,
            (
                run_id,
                tenant_id,
                scope,
                objective,
                horizon_hours,
                solver,
                objective_value,
                Json(inputs),
                Json(outputs),
                created_by,
            ),
        )
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'optimization.completed', %s, %s)
            """,
            (f"au-{uuid4().hex}", tenant_id, created_by, run_id, f"{solver}:{objective_value}"),
        )
    return run_id


def settle_vpp_event(
    event_id: str,
    baseline_kw: float,
    delivered_kw: float,
    settled_by: str,
    evidence: dict,
    scope_tenant_id: str | None = None,
) -> dict[str, object]:
    from psycopg.types.json import Json

    settlement_id = f"set-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        tenant_id = _tenant_for_vpp_event(conn, event_id)
        _ensure_tenant_allowed(scope_tenant_id, tenant_id)
        _set_tenant_context(conn, tenant_id)
        event = conn.execute(
            "SELECT duration_minutes, incentive_per_kwh FROM chargeopt.vpp_events WHERE id = %s",
            (event_id,),
        ).fetchone()
        duration_hours = float(event[0]) / 60
        incentive = float(event[1])
        performance = min(1.2, delivered_kw / max(1, baseline_kw))
        gross = delivered_kw * duration_hours * incentive
        penalty = 0 if performance >= 0.9 else gross * (0.9 - performance)
        net = max(0, gross - penalty)
        conn.execute(
            """
            INSERT INTO chargeopt.vpp_settlements (
                id, tenant_id, event_id, baseline_kw, delivered_kw, performance_score,
                gross_revenue, penalty, net_revenue, evidence, settled_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                settlement_id,
                tenant_id,
                event_id,
                baseline_kw,
                delivered_kw,
                performance,
                gross,
                penalty,
                net,
                Json(evidence),
                settled_by,
            ),
        )
        conn.execute("UPDATE chargeopt.vpp_events SET status = 'settled' WHERE id = %s", (event_id,))
        conn.execute(
            """
            INSERT INTO chargeopt.audit_entries (id, tenant_id, timestamp, actor, action, target, detail)
            VALUES (%s, %s, now(), %s, 'vpp.settled', %s, %s)
            """,
            (f"au-{uuid4().hex}", tenant_id, settled_by, event_id, f"net={round(net, 2)}"),
        )
    invalidate_repository_cache()
    return {
        "id": settlement_id,
        "event_id": event_id,
        "performance_score": round(performance, 4),
        "gross_revenue": round(gross, 2),
        "penalty": round(penalty, 2),
        "net_revenue": round(net, 2),
    }
