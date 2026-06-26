"""Unit tests for domain model boundary conditions."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from chargeopt.domain import (
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


def _station(**overrides) -> Station:
    defaults = dict(
        id="st-test",
        tenant_id="t-test",
        region_id="r-test",
        name="Test Station",
        station_type="ultra_fast",
        address="1 Test Rd",
        latitude=31.2,
        longitude=121.5,
        transformer_capacity_kw=1000.0,
        charger_count=10,
        connector_count=20,
        max_connector_power_kw=250.0,
        storage_capacity_kwh=1200.0,
        storage_power_kw=600.0,
        pv_capacity_kw=200.0,
        tariff_plan_id="tp-default",
        monthly_opex=50000.0,
        reliability_score=0.97,
        dispatch_mode="auto",
    )
    return Station(**{**defaults, **overrides})


class TestTariffPeriod:
    def test_fields_stored(self):
        p = TariffPeriod("peak", 8, 22, 1.2)
        assert p.name == "peak"
        assert p.start_hour == 8
        assert p.end_hour == 22
        assert p.energy_price_per_kwh == pytest.approx(1.2)

    def test_zero_price_allowed(self):
        p = TariffPeriod("free", 0, 6, 0.0)
        assert p.energy_price_per_kwh == 0.0


class TestStation:
    def test_default_fields(self):
        s = _station()
        assert s.id == "st-test"
        assert s.storage_capacity_kwh == pytest.approx(1200.0)
        assert s.reliability_score == pytest.approx(0.97)

    def test_zero_storage(self):
        s = _station(storage_capacity_kwh=0.0, storage_power_kw=0.0)
        assert s.storage_capacity_kwh == 0.0

    def test_dispatch_mode_values(self):
        for mode in ("auto", "manual", "observe"):
            s = _station(dispatch_mode=mode)
            assert s.dispatch_mode == mode


class TestTelemetryPoint:
    def test_fields(self):
        ts = datetime(2024, 1, 1, 12, tzinfo=UTC)
        t = TelemetryPoint("st-x", ts, 500.0, 100.0, 400.0, -200.0, 0.65, 8, 2, 10, 800.0, 1200.0, 0)
        assert t.station_id == "st-x"
        assert t.storage_soc == pytest.approx(0.65)
        assert t.alert_count == 0


class TestAlert:
    def test_acknowledged_default(self):
        ts = datetime.now(UTC)
        a = Alert("al-1", "st-x", ts, "high", "Overload", "Load > 100%", False)
        assert a.acknowledged is False

    def test_priority_values(self):
        ts = datetime.now(UTC)
        for priority in ("low", "medium", "high", "critical"):
            a = Alert("al-1", "st-x", ts, priority, "T", "D", False)
            assert a.priority == priority


class TestVppEvent:
    def test_fields(self):
        ts = datetime.now(UTC)
        e = VppEvent("vpp-1", "t-1", "DR Event", ts, 60, 2000.0, 0.15, "pending")
        assert e.requested_kw == pytest.approx(2000.0)
        assert e.incentive_per_kwh == pytest.approx(0.15)


class TestAuditEntry:
    def test_fields(self):
        ts = datetime.now(UTC)
        entry = AuditEntry("au-1", ts, "system", "dispatch", "st-x", "Charged 100 kWh")
        assert entry.actor == "system"
        assert entry.action == "dispatch"


class TestTenant:
    def test_fields(self):
        t = Tenant("t-1", "ACME Energy", "enterprise")
        assert t.plan == "enterprise"


class TestRegion:
    def test_fields(self):
        r = Region("r-1", "Shanghai", "SGCC")
        assert r.grid_operator == "SGCC"


class TestTariffPlan:
    def test_empty_periods(self):
        tp = TariffPlan("tp-1", "Flat", (), 15.0, 0.05)
        assert len(tp.periods) == 0
        assert tp.demand_charge_per_kw_month == pytest.approx(15.0)

    def test_multiple_periods(self):
        periods = (
            TariffPeriod("off-peak", 0, 8, 0.4),
            TariffPeriod("peak", 8, 22, 1.2),
            TariffPeriod("off-peak2", 22, 24, 0.4),
        )
        tp = TariffPlan("tp-2", "TOU", periods, 20.0, 0.06)
        assert len(tp.periods) == 3
