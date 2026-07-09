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
_cached_repo: Repository | None = None
_cache_expires_at: float = 0.0


def invalidate_repository_cache() -> None:
    """Force the next call to load_repository_from_db to re-query the DB."""
    global _cached_repo, _cache_expires_at
    with _cache_lock:
        _cached_repo = None
        _cache_expires_at = 0.0


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def load_repository_from_db() -> Repository:
    """Return a Repository, using a short TTL cache to avoid per-request DB queries.

    Falls back to in-memory fixtures when DATABASE_URL is absent.
    """
    global _cached_repo, _cache_expires_at

    settings = get_settings()
    if not settings.use_db:
        logger.debug("No DATABASE_URL – using in-memory repository.")
        return load_repository()

    now = time.monotonic()
    with _cache_lock:
        if _cached_repo is not None and now < _cache_expires_at:
            return _cached_repo

    try:
        repo = _load_from_postgres()
    except Exception:
        logger.exception("Failed to load repository from PostgreSQL – falling back to in-memory fixtures.")
        repo = load_repository()

    with _cache_lock:
        _cached_repo = repo
        _cache_expires_at = time.monotonic() + _CACHE_TTL_SECONDS

    return repo


# ---------------------------------------------------------------------------
# Internal PostgreSQL loader
# ---------------------------------------------------------------------------


def _load_from_postgres() -> Repository:
    with get_connection() as conn:
        tenants = _load_tenants(conn)
        regions = _load_regions(conn)
        tariff_plans = _load_tariff_plans(conn)
        stations = _load_stations(conn)
        telemetry = _load_telemetry(conn)
        alerts = _load_alerts(conn)
        vpp_events = _load_vpp_events(conn)
        audit = _load_audit(conn)

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


def _load_tenants(conn) -> list[Tenant]:
    rows = conn.execute("SELECT id, name, plan FROM chargeopt.tenants ORDER BY id").fetchall()
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


def _load_stations(conn) -> list[Station]:
    rows = conn.execute(
        """
        SELECT id, tenant_id, region_id, name, station_type, address,
               latitude, longitude, transformer_capacity_kw, charger_count,
               connector_count, max_connector_power_kw, storage_capacity_kwh,
               storage_power_kw, pv_capacity_kw, tariff_plan_id,
               monthly_opex, reliability_score, dispatch_mode
        FROM chargeopt.stations ORDER BY id
        """
    ).fetchall()
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


def _load_telemetry(conn) -> list[TelemetryPoint]:
    rows = conn.execute(
        """
        SELECT station_id, timestamp, load_kw, pv_kw, grid_kw,
               storage_power_kw, storage_soc, connector_occupied,
               queue_length, sessions, energy_kwh, revenue, alert_count
        FROM chargeopt.telemetry_points
        ORDER BY station_id, timestamp
        """
    ).fetchall()
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


def _load_alerts(conn) -> list[Alert]:
    rows = conn.execute(
        "SELECT id, station_id, timestamp, priority, title, detail, acknowledged"
        " FROM chargeopt.alerts ORDER BY timestamp DESC"
    ).fetchall()
    return [Alert(r[0], r[1], _to_dt(r[2]), r[3], r[4], r[5], bool(r[6])) for r in rows]


def _load_vpp_events(conn) -> list[VppEvent]:
    rows = conn.execute(
        "SELECT id, tenant_id, title, start_at, duration_minutes, requested_kw, incentive_per_kwh, status"
        " FROM chargeopt.vpp_events ORDER BY start_at DESC"
    ).fetchall()
    return [VppEvent(r[0], r[1], r[2], _to_dt(r[3]), int(r[4]), float(r[5]), float(r[6]), r[7]) for r in rows]


def _load_audit(conn) -> list[AuditEntry]:
    rows = conn.execute(
        "SELECT id, timestamp, actor, action, target, detail"
        " FROM chargeopt.audit_entries ORDER BY timestamp DESC LIMIT 200"
    ).fetchall()
    return [AuditEntry(r[0], _to_dt(r[1]), r[2], r[3], r[4], r[5]) for r in rows]


def _to_dt(value: object) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


# ---------------------------------------------------------------------------
# Write-path operations
# ---------------------------------------------------------------------------


def append_audit(actor: str, action: str, target: str, detail: str) -> str:
    """Persist an audit entry and return its generated ID."""
    audit_id = f"au-{uuid4().hex}"
    with get_connection() as conn, conn.transaction():
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail)
                VALUES (%s, now(), %s, %s, %s, %s)
                """,
            (audit_id, actor, action, target, detail),
        )
    invalidate_repository_cache()
    return audit_id


def ingest_telemetry(payload: dict) -> dict[str, object]:
    """Upsert a telemetry point with idempotency tracking."""
    station_id = payload["station_id"]
    timestamp = payload["timestamp"]
    timestamp_key = timestamp.isoformat() if isinstance(timestamp, datetime) else str(timestamp)
    idempotency_key = payload.get("idempotency_key") or f"{station_id}:{timestamp_key}"
    actor = payload.get("actor") or "edge-gateway"

    with get_connection() as conn, conn.transaction():
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
                INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail)
                VALUES (%s, now(), %s, 'telemetry.ingested', %s, %s)
                """,
            (
                f"au-{uuid4().hex}",
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


def acknowledge_alert(alert_id: str, actor: str) -> dict[str, object]:
    """Acknowledge an alert and audit the action."""
    with get_connection() as conn, conn.transaction():
        cursor = conn.execute(
            "UPDATE chargeopt.alerts SET acknowledged = true WHERE id = %s",
            (alert_id,),
        )
        if cursor.rowcount != 1:
            raise KeyError(f"Unknown alert_id: {alert_id}")
        conn.execute(
            """
                INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail)
                VALUES (%s, now(), %s, 'alert.acknowledged', %s, 'Alert acknowledged.')
                """,
            (f"au-{uuid4().hex}", actor, alert_id),
        )
    invalidate_repository_cache()
    return {"id": alert_id, "acknowledged": True}


def persist_dispatch_recommendations(recommendations: list[dict], actor: str) -> int:
    """Persist generated dispatch recommendations without overwriting review status."""
    from psycopg.types.json import Json

    with get_connection() as conn, conn.transaction():
        for item in recommendations:
            conn.execute(
                """
                    INSERT INTO chargeopt.dispatch_recommendations (
                        id, station_id, title, risk, action, value, dispatch_window,
                        mode, approval, rationale, command_payload
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
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
                INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail)
                VALUES (%s, now(), %s, 'dispatch.generated', 'dispatch_recommendations', %s)
                """,
            (f"au-{uuid4().hex}", actor, f"Persisted {len(recommendations)} dispatch recommendations."),
        )
    invalidate_repository_cache()
    return len(recommendations)


def update_dispatch_status(recommendation_id: str, status: str, actor: str, reason: str | None) -> dict[str, str]:
    """Approve/reject/execute a persisted dispatch recommendation."""
    allowed = {"pending", "approved", "rejected", "executed", "failed", "rolled_back"}
    if status not in allowed:
        raise ValueError(f"Invalid dispatch status: {status}")

    with get_connection() as conn, conn.transaction():
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
                INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail)
                VALUES (%s, now(), %s, 'dispatch.status_changed', %s, %s)
                """,
            (f"au-{uuid4().hex}", actor, recommendation_id, reason or f"Status changed to {status}."),
        )
    invalidate_repository_cache()
    return {"id": recommendation_id, "status": status}


def persist_roi_simulation(station_id: str | None, roi: dict, inputs: dict) -> int:
    """Persist an ROI simulation and return its database ID."""
    from psycopg.types.json import Json

    with get_connection() as conn, conn.transaction():
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
                INSERT INTO chargeopt.audit_entries (id, timestamp, actor, action, target, detail)
                VALUES (%s, now(), 'system', 'roi.persisted', %s, %s)
                """,
            (f"au-{uuid4().hex}", station_id or "portfolio", f"ROI simulation {simulation_id} persisted."),
        )
    invalidate_repository_cache()
    return simulation_id
