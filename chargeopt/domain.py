from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Literal


Priority = Literal["low", "medium", "high", "critical"]
DispatchMode = Literal["observe", "recommend", "semi_auto", "auto"]


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    plan: str


@dataclass(frozen=True)
class Region:
    id: str
    name: str
    grid_operator: str


@dataclass(frozen=True)
class TariffPeriod:
    name: str
    start_hour: int
    end_hour: int
    energy_price_per_kwh: float

    def contains(self, hour: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= hour < self.end_hour
        return hour >= self.start_hour or hour < self.end_hour


@dataclass(frozen=True)
class TariffPlan:
    id: str
    name: str
    periods: tuple[TariffPeriod, ...]
    demand_charge_per_kw_month: float
    service_fee_per_kwh: float

    def price_at(self, hour: int) -> float:
        for period in self.periods:
            if period.contains(hour):
                return period.energy_price_per_kwh
        return self.periods[-1].energy_price_per_kwh

    def period_name_at(self, hour: int) -> str:
        for period in self.periods:
            if period.contains(hour):
                return period.name
        return self.periods[-1].name


@dataclass(frozen=True)
class Station:
    id: str
    tenant_id: str
    region_id: str
    name: str
    station_type: str
    address: str
    latitude: float
    longitude: float
    transformer_capacity_kw: float
    charger_count: int
    connector_count: int
    max_connector_power_kw: float
    storage_capacity_kwh: float
    storage_power_kw: float
    pv_capacity_kw: float
    tariff_plan_id: str
    monthly_opex: float
    reliability_score: float
    dispatch_mode: DispatchMode


@dataclass(frozen=True)
class TelemetryPoint:
    station_id: str
    timestamp: datetime
    load_kw: float
    pv_kw: float
    grid_kw: float
    storage_power_kw: float
    storage_soc: float
    connector_occupied: int
    queue_length: int
    sessions: int
    energy_kwh: float
    revenue: float
    alert_count: int


@dataclass(frozen=True)
class Alert:
    id: str
    station_id: str
    timestamp: datetime
    priority: Priority
    title: str
    detail: str
    acknowledged: bool


@dataclass(frozen=True)
class VppEvent:
    id: str
    tenant_id: str
    title: str
    start: datetime
    duration_minutes: int
    requested_kw: float
    incentive_per_kwh: float
    status: Literal["draft", "pending_approval", "active", "settled"]


@dataclass(frozen=True)
class AuditEntry:
    id: str
    timestamp: datetime
    actor: str
    action: str
    target: str
    detail: str
