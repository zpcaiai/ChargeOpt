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
