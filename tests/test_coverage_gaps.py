"""Targeted tests to cover remaining uncovered lines.

Covers:
- domain.py: midnight-crossing TariffPeriod.contains; TariffPlan fallback paths
- logging_config.py: json_logs=True branch
- analytics.py: dispatch branch for queue_length>=4 and storage_soc<32 low-price
- repository.py: all internal _load_* functions via mocked connection
"""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# domain.py — midnight-crossing TariffPeriod (line 35)
# ---------------------------------------------------------------------------


def test_tariff_period_midnight_crossing():
    from chargeopt.domain import TariffPeriod

    # 22:00 – 06:00 (crosses midnight)
    p = TariffPeriod("night", 22, 6, 0.3)
    assert p.contains(23) is True   # after 22
    assert p.contains(0) is True    # midnight
    assert p.contains(5) is True    # before 6
    assert p.contains(6) is False   # end exclusive
    assert p.contains(12) is False  # daytime


# ---------------------------------------------------------------------------
# domain.py — TariffPlan fallback (lines 50, 56): no period matches
# ---------------------------------------------------------------------------


def test_tariff_plan_price_at_falls_back_to_last():
    from chargeopt.domain import TariffPeriod, TariffPlan

    # Only one period covering 8–22; hour=0 matches nothing → falls back
    p = TariffPeriod("peak", 8, 22, 1.5)
    plan = TariffPlan("tp", "Test", (p,), 20.0, 0.05)
    # Hour 0 is outside 8–22 so falls back to last period
    assert plan.price_at(0) == pytest.approx(1.5)


def test_tariff_plan_period_name_at_falls_back_to_last():
    from chargeopt.domain import TariffPeriod, TariffPlan

    p = TariffPeriod("peak", 8, 22, 1.5)
    plan = TariffPlan("tp", "Test", (p,), 20.0, 0.05)
    assert plan.period_name_at(0) == "peak"


# ---------------------------------------------------------------------------
# logging_config.py — json_logs=True branch (line 24)
# ---------------------------------------------------------------------------


def test_configure_logging_json_mode():
    from chargeopt.logging_config import configure_logging

    configure_logging(log_level="warning", json_logs=True)
    import logging

    assert logging.getLogger().level == logging.WARNING


# ---------------------------------------------------------------------------
# analytics.py — dispatch branches not triggered by fixture data
# Lines 274 (queue_length >= 4) and 286 (storage_soc < 32 and low price)
# ---------------------------------------------------------------------------


def _make_station(**overrides):
    from chargeopt.domain import Station

    defaults = dict(
        id="st-t",
        tenant_id="t-1",
        region_id="r-1",
        name="Test",
        station_type="ultra_fast",
        address="X",
        latitude=0.0,
        longitude=0.0,
        transformer_capacity_kw=1000.0,
        charger_count=10,
        connector_count=20,
        max_connector_power_kw=250.0,
        storage_capacity_kwh=1200.0,
        storage_power_kw=600.0,
        pv_capacity_kw=200.0,
        tariff_plan_id="tp-1",
        monthly_opex=50000.0,
        reliability_score=0.97,
        dispatch_mode="auto",
    )
    return Station(**{**defaults, **overrides})


def _make_telemetry(station_id, **overrides):
    from chargeopt.domain import TelemetryPoint

    base = dict(
        station_id=station_id,
        timestamp=datetime(2024, 1, 1, 14, tzinfo=timezone.utc),
        load_kw=500.0,
        pv_kw=100.0,
        grid_kw=400.0,
        storage_power_kw=0.0,
        storage_soc=0.6,
        connector_occupied=10,
        queue_length=0,
        sessions=10,
        energy_kwh=5.0,
        revenue=300.0,
        alert_count=0,
    )
    return TelemetryPoint(**{**base, **overrides})


def _make_repo(station, points):
    """Build a minimal Repository-like mock."""
    from chargeopt.domain import TariffPeriod, TariffPlan, Tenant, Region, VppEvent, AuditEntry

    tenant = Tenant("t-1", "ACME", "enterprise")
    region = Region("r-1", "Shanghai", "SGCC")
    periods = (
        TariffPeriod("valley", 0, 8, 0.4),
        TariffPeriod("peak", 8, 22, 1.2),
        TariffPeriod("off-peak", 22, 24, 0.6),
    )
    tariff = TariffPlan("tp-1", "TOU", periods, 20.0, 0.06)
    event = VppEvent(
        "vpp-1", "t-1", "DR", datetime(2024, 1, 1, 18, tzinfo=timezone.utc),
        60, 2000.0, 0.15, "pending"
    )

    mock = MagicMock()
    mock.stations = (station,)
    mock.tenants = (tenant,)
    mock.regions = (region,)
    mock.tariff_plans = (tariff,)
    mock.vpp_events = (event,)
    mock.audit = ()
    mock.station_points.return_value = points
    mock.tariff_for.return_value = tariff
    mock.station_alerts.return_value = []
    return mock


def test_dispatch_demand_peak_guard_branch():
    """Cover analytics.py line 262: demand_headroom_kw < transformer_capacity_kw * 0.12."""
    from chargeopt.analytics import build_dispatch

    # transformer_capacity_kw=1000, so threshold = 120 kW
    # demand_headroom = transformer_capacity_kw - peak_grid_kw
    # set grid_kw high (950) so headroom = 50 < 120
    station = _make_station(transformer_capacity_kw=1000.0)
    points = [
        _make_telemetry(
            station.id,
            timestamp=datetime(2024, 1, 1, h, tzinfo=timezone.utc),
            grid_kw=950.0,  # high load → low headroom
        )
        for h in range(24)
    ]
    repo = _make_repo(station, points)

    result = build_dispatch(repo)
    titles = {r["title"] for r in result["recommendations"]}
    assert "Demand peak guard" in titles


def test_dispatch_queue_relief_branch():
    """Cover analytics.py line 274: queue_length >= 4."""
    from chargeopt.analytics import build_dispatch

    station = _make_station()
    # 24 points, current (last) has queue_length=5 → triggers queue relief
    points = [_make_telemetry(station.id, timestamp=datetime(2024, 1, 1, h, tzinfo=timezone.utc))
              for h in range(24)]
    points[-1] = _make_telemetry(station.id, timestamp=datetime(2024, 1, 1, 23, tzinfo=timezone.utc),
                                 queue_length=5, grid_kw=400.0)
    repo = _make_repo(station, points)

    result = build_dispatch(repo)
    titles = {r["title"] for r in result["recommendations"]}
    assert "Queue relief pricing" in titles


def test_dispatch_storage_recharge_branch():
    """Cover analytics.py line 286: storage_soc < 32 AND low price hour."""
    from chargeopt.analytics import build_dispatch

    station = _make_station()
    # current point: low SOC at hour 3 (valley price 0.4 < 0.6)
    points = [_make_telemetry(station.id, timestamp=datetime(2024, 1, 1, h, tzinfo=timezone.utc))
              for h in range(24)]
    points[-1] = _make_telemetry(
        station.id,
        timestamp=datetime(2024, 1, 1, 3, tzinfo=timezone.utc),
        storage_soc=0.25,   # 25% < 32%
        grid_kw=400.0,
    )
    repo = _make_repo(station, points)

    result = build_dispatch(repo)
    titles = {r["title"] for r in result["recommendations"]}
    assert "Storage recharge" in titles


# ---------------------------------------------------------------------------
# repository.py — internal _load_* functions via mocked connection
# ---------------------------------------------------------------------------


def _make_mock_conn(table_data: dict) -> MagicMock:
    """Return a connection mock where conn.execute(sql).fetchall() returns table_data[sql_key]."""
    conn = MagicMock()

    def execute_side_effect(sql, *args, **kwargs):
        cursor = MagicMock()
        sql_strip = sql.strip()
        for key, rows in table_data.items():
            if key in sql_strip:
                cursor.fetchall.return_value = rows
                return cursor
        cursor.fetchall.return_value = []
        return cursor

    conn.execute.side_effect = execute_side_effect
    return conn


def test_load_from_postgres_all_loaders():
    """Exercise _load_from_postgres (lines 90-110) and all _load_* sub-functions."""
    from chargeopt.repository import _load_from_postgres

    now = datetime(2024, 1, 1, 14, tzinfo=timezone.utc)

    table_data = {
        "tenants": [("t-1", "ACME", "enterprise")],
        "regions": [("r-1", "Shanghai", "SGCC")],
        "tariff_plans": [("tp-1", "TOU", 20.0, 0.06)],
        "tariff_periods": [("tp-1", "peak", 8, 22, 1.2)],
        "stations": [(
            "st-1", "t-1", "r-1", "Station A", "ultra_fast", "1 Rd",
            31.2, 121.5, 1000.0, 10, 20, 250.0, 1200.0, 600.0, 200.0,
            "tp-1", 50000.0, 0.97, "auto"
        )],
        "telemetry_points": [(
            "st-1", now, 500.0, 100.0, 400.0, 0.0, 0.6, 10, 2, 10, 5.0, 300.0, 0
        )],
        "alerts": [("al-1", "st-1", now, "high", "Test", "Detail", False)],
        "vpp_events": [("vpp-1", "t-1", "DR", now, 60, 2000.0, 0.15, "pending")],
        "audit_entries": [("au-1", now, "system", "dispatch", "st-1", "ok")],
    }

    mock_conn = _make_mock_conn(table_data)
    mock_ctx = MagicMock()
    mock_ctx.__enter__ = lambda s: mock_conn
    mock_ctx.__exit__ = MagicMock(return_value=False)

    with patch("chargeopt.repository.get_connection", return_value=mock_ctx):
        repo = _load_from_postgres()

    assert len(repo.tenants) == 1
    assert repo.tenants[0].id == "t-1"
    assert len(repo.stations) == 1
    assert repo.stations[0].id == "st-1"
    assert len(repo.telemetry) == 1
    assert len(repo.alerts) == 1
    assert len(repo.vpp_events) == 1
    assert len(repo.audit) == 1


def test_to_dt_string_branch():
    """Cover repository._to_dt with a string value (not datetime)."""
    from chargeopt.repository import _to_dt

    result = _to_dt("2024-01-01T14:00:00")
    assert isinstance(result, datetime)
    assert result.year == 2024


def test_to_dt_datetime_branch():
    """Cover repository._to_dt with a datetime value (passthrough)."""
    from chargeopt.repository import _to_dt

    dt = datetime(2024, 6, 1, 12, tzinfo=timezone.utc)
    result = _to_dt(dt)
    assert result is dt
