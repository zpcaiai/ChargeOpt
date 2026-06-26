from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta

from .domain import Alert, AuditEntry, Region, Station, TariffPeriod, TariffPlan, TelemetryPoint, Tenant, VppEvent


@dataclass(frozen=True)
class Repository:
    tenants: tuple[Tenant, ...]
    regions: tuple[Region, ...]
    tariff_plans: tuple[TariffPlan, ...]
    stations: tuple[Station, ...]
    telemetry: tuple[TelemetryPoint, ...]
    alerts: tuple[Alert, ...]
    vpp_events: tuple[VppEvent, ...]
    audit: tuple[AuditEntry, ...]

    def tariff_for(self, station: Station) -> TariffPlan:
        return next(plan for plan in self.tariff_plans if plan.id == station.tariff_plan_id)

    def station_points(self, station_id: str) -> list[TelemetryPoint]:
        return [point for point in self.telemetry if point.station_id == station_id]

    def station_alerts(self, station_id: str) -> list[Alert]:
        return [alert for alert in self.alerts if alert.station_id == station_id]


def load_repository() -> Repository:
    now = datetime.now().replace(minute=0, second=0, microsecond=0)
    start = now - timedelta(hours=23)

    tenants = (Tenant("t-001", "华东超充能源集团", "Enterprise"),)
    regions = (
        Region("r-sh", "上海城市群", "华东电网"),
        Region("r-js", "苏南物流走廊", "华东电网"),
    )
    tariff_plans = (
        TariffPlan(
            "tariff-sh-industrial",
            "上海工商业尖峰平谷",
            (
                TariffPeriod("valley", 0, 7, 0.39),
                TariffPeriod("flat", 7, 10, 0.82),
                TariffPeriod("peak", 10, 15, 1.27),
                TariffPeriod("flat", 15, 18, 0.86),
                TariffPeriod("peak", 18, 22, 1.34),
                TariffPeriod("valley", 22, 24, 0.43),
            ),
            demand_charge_per_kw_month=42.0,
            service_fee_per_kwh=0.58,
        ),
        TariffPlan(
            "tariff-js-logistics",
            "江苏物流园区两部制",
            (
                TariffPeriod("valley", 0, 8, 0.36),
                TariffPeriod("flat", 8, 11, 0.78),
                TariffPeriod("peak", 11, 14, 1.18),
                TariffPeriod("flat", 14, 17, 0.78),
                TariffPeriod("peak", 17, 21, 1.22),
                TariffPeriod("valley", 21, 24, 0.41),
            ),
            demand_charge_per_kw_month=38.0,
            service_fee_per_kwh=0.52,
        ),
    )
    stations = (
        Station(
            "st-hq-hongqiao",
            "t-001",
            "r-sh",
            "虹桥枢纽超充站",
            "urban_ultrafast",
            "上海市闵行区申虹路",
            31.194,
            121.318,
            transformer_capacity_kw=2600,
            charger_count=18,
            connector_count=36,
            max_connector_power_kw=480,
            storage_capacity_kwh=1200,
            storage_power_kw=600,
            pv_capacity_kw=180,
            tariff_plan_id="tariff-sh-industrial",
            monthly_opex=168000,
            reliability_score=0.96,
            dispatch_mode="recommend",
        ),
        Station(
            "st-wg-waigaoqiao",
            "t-001",
            "r-sh",
            "外高桥物流重卡站",
            "heavy_truck_depot",
            "上海市浦东新区港城路",
            31.343,
            121.600,
            transformer_capacity_kw=4200,
            charger_count=24,
            connector_count=48,
            max_connector_power_kw=600,
            storage_capacity_kwh=2200,
            storage_power_kw=1000,
            pv_capacity_kw=320,
            tariff_plan_id="tariff-sh-industrial",
            monthly_opex=236000,
            reliability_score=0.92,
            dispatch_mode="semi_auto",
        ),
        Station(
            "st-sz-industrial",
            "t-001",
            "r-js",
            "苏州园区光储充站",
            "pv_storage_charging",
            "苏州市工业园区星湖街",
            31.299,
            120.706,
            transformer_capacity_kw=1800,
            charger_count=12,
            connector_count=24,
            max_connector_power_kw=360,
            storage_capacity_kwh=900,
            storage_power_kw=450,
            pv_capacity_kw=520,
            tariff_plan_id="tariff-js-logistics",
            monthly_opex=112000,
            reliability_score=0.98,
            dispatch_mode="recommend",
        ),
    )

    telemetry: list[TelemetryPoint] = []
    for station_index, station in enumerate(stations):
        tariff = next(plan for plan in tariff_plans if plan.id == station.tariff_plan_id)
        soc = 0.54 + station_index * 0.08
        for index in range(24):
            timestamp = start + timedelta(hours=index)
            hour = timestamp.hour
            load_factor = _load_factor(hour, station.station_type)
            pv_factor = max(0.0, math.sin((hour - 6) / 12 * math.pi))
            pv_kw = station.pv_capacity_kw * pv_factor * (0.72 + 0.08 * math.sin(index + station_index))
            base_load = station.transformer_capacity_kw * load_factor
            ripple = station.transformer_capacity_kw * 0.035 * math.sin(index * 1.7 + station_index)
            load_kw = max(station.transformer_capacity_kw * 0.12, base_load + ripple)
            price = tariff.price_at(hour)
            if price < 0.5 and soc < 0.86:
                storage_power_kw = min(station.storage_power_kw, station.transformer_capacity_kw * 0.13)
                soc += storage_power_kw / station.storage_capacity_kwh * 0.88
            elif price > 1.0 and load_kw > station.transformer_capacity_kw * 0.58 and soc > 0.24:
                storage_power_kw = -min(station.storage_power_kw, load_kw * 0.22)
                soc += storage_power_kw / station.storage_capacity_kwh / 0.9
            else:
                storage_power_kw = 0.0
            soc = min(0.92, max(0.18, soc))
            grid_kw = max(0.0, load_kw + max(storage_power_kw, 0) + min(storage_power_kw, 0) - pv_kw)
            occupied = min(
                station.connector_count, max(1, round(station.connector_count * min(0.96, load_factor + 0.12)))
            )
            queue = max(0, round((load_factor - 0.72) * 24 + (station_index == 1) * 2))
            sessions = max(1, round(occupied * (0.58 + load_factor * 0.22)))
            energy_kwh = load_kw * 0.91
            revenue = energy_kwh * (price + tariff.service_fee_per_kwh)
            alert_count = 1 if grid_kw > station.transformer_capacity_kw * 0.9 or queue >= 8 else 0
            telemetry.append(
                TelemetryPoint(
                    station.id,
                    timestamp,
                    round(load_kw, 2),
                    round(pv_kw, 2),
                    round(grid_kw, 2),
                    round(storage_power_kw, 2),
                    round(soc, 3),
                    occupied,
                    queue,
                    sessions,
                    round(energy_kwh, 2),
                    round(revenue, 2),
                    alert_count,
                )
            )

    alerts = (
        Alert(
            "al-001",
            "st-hq-hongqiao",
            now - timedelta(hours=2),
            "high",
            "需量峰值接近阈值",
            "18:00-19:00 grid load reached 91% of transformer capacity.",
            False,
        ),
        Alert(
            "al-002",
            "st-wg-waigaoqiao",
            now - timedelta(hours=4),
            "medium",
            "重卡排队时间偏高",
            "Queue is forecast to exceed target service level during evening peak.",
            False,
        ),
        Alert(
            "al-003",
            "st-sz-industrial",
            now - timedelta(hours=8),
            "low",
            "光伏预测偏差",
            "PV output was 12% below forecast because of cloud cover.",
            True,
        ),
    )
    vpp_events = (
        VppEvent(
            "vpp-20260625-01",
            "t-001",
            "华东晚高峰削减响应",
            now.replace(hour=18),
            90,
            2200,
            2.35,
            "pending_approval",
        ),
    )
    audit = (
        AuditEntry(
            "au-001",
            now - timedelta(hours=5),
            "system",
            "forecast.generated",
            "tenant:t-001",
            "24h load forecast generated for 3 stations.",
        ),
        AuditEntry(
            "au-002",
            now - timedelta(hours=3),
            "operator.li",
            "dispatch.reviewed",
            "st-wg-waigaoqiao",
            "Approved storage reserve floor at 28% for evening VPP event.",
        ),
        AuditEntry(
            "au-003",
            now - timedelta(hours=1),
            "system",
            "roi.simulated",
            "st-hq-hongqiao",
            "Simulated 1.2MWh storage case with VPP revenue enabled.",
        ),
    )
    return Repository(tenants, regions, tariff_plans, stations, tuple(telemetry), alerts, vpp_events, audit)


def _load_factor(hour: int, station_type: str) -> float:
    commuter_peak = 0.42 * math.exp(-((hour - 19) ** 2) / 18)
    lunch_peak = 0.18 * math.exp(-((hour - 12) ** 2) / 10)
    night_base = 0.22 if hour < 7 or hour > 22 else 0.31
    if station_type == "heavy_truck_depot":
        logistics = 0.38 * math.exp(-((hour - 5) ** 2) / 12) + 0.36 * math.exp(-((hour - 21) ** 2) / 12)
        return min(0.95, night_base + logistics + 0.1)
    if station_type == "pv_storage_charging":
        workday = 0.26 * math.exp(-((hour - 10) ** 2) / 18) + 0.29 * math.exp(-((hour - 17) ** 2) / 18)
        return min(0.9, night_base + workday)
    return min(0.94, night_base + commuter_peak + lunch_peak)
