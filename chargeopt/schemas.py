"""Pydantic response schemas for ChargeOpt API v1.

Defines typed output models so FastAPI generates accurate OpenAPI docs and
validates serialisation at the boundary.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel

# ---------------------------------------------------------------------------
# RFC 7807 Problem Details (used for all error responses)
# ---------------------------------------------------------------------------


class ProblemDetail(BaseModel):
    """RFC 7807 problem details object."""

    type: str = "about:blank"
    title: str
    status: int
    detail: str | None = None
    instance: str | None = None

    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Pagination envelope
# ---------------------------------------------------------------------------


class PageMeta(BaseModel):
    total: int
    limit: int
    offset: int


class Page(BaseModel):
    meta: PageMeta
    items: list[Any]


# ---------------------------------------------------------------------------
# Shared primitives
# ---------------------------------------------------------------------------


class TariffInfo(BaseModel):
    name: str


class AlertOut(BaseModel):
    id: str
    station_id: str
    timestamp: str
    priority: str
    title: str
    detail: str
    acknowledged: bool


class AuditEntryOut(BaseModel):
    id: str
    timestamp: str
    actor: str
    action: str
    target: str
    detail: str


# ---------------------------------------------------------------------------
# Station
# ---------------------------------------------------------------------------


class StationSummary(BaseModel):
    id: str
    name: str
    type: str
    address: str
    lat: float
    lng: float
    dispatch_mode: str
    connectors: int
    transformer_capacity_kw: float
    storage_capacity_kwh: float
    storage_power_kw: float
    pv_capacity_kw: float
    current_power_kw: float
    current_load_kw: float
    storage_soc: float
    storage_power_kw_now: float
    queue_length: int
    occupied_connectors: int
    connector_utilization: float
    storage_utilization: float
    today_energy_kwh: float
    today_revenue: float
    grid_cost: float
    demand_charge_day: float
    today_margin: float
    margin_rate: float
    demand_peak_kw: float
    demand_headroom_kw: float
    peak_cut_opportunity_kw: float
    monthly_savings_potential: float
    vpp_capacity_kw: float
    health_score: float
    alert_count: int
    tariff: str


class StationListResponse(BaseModel):
    stations: list[StationSummary]


class TelemetryRow(BaseModel):
    time: str
    label: str
    load_kw: float
    grid_kw: float
    pv_kw: float
    storage_power_kw: float
    storage_soc: float
    queue_length: int
    occupied: int
    energy_kwh: float
    revenue: float
    price: float
    period: str


class StoragePlanRow(BaseModel):
    label: str
    action: Literal["charge", "discharge", "hold"]
    power_kw: float
    soc: float
    reason: str


class PricingSuggestion(BaseModel):
    label: str
    strategy: str
    service_fee_delta: str
    expected_queue: int
    note: str


class StationDetailResponse(BaseModel):
    station: StationSummary
    telemetry: list[TelemetryRow]
    forecast: list[dict[str, Any]]
    storage_plan: list[StoragePlanRow]
    pricing: list[PricingSuggestion]
    alerts: list[AlertOut]
    recommendations: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class PortfolioTotals(BaseModel):
    station_count: int
    today_energy_kwh: float
    today_revenue: float
    today_margin: float
    current_power_kw: float
    queue_length: int
    demand_peak_kw: float
    monthly_savings_potential: float
    vpp_capacity_kw: float
    portfolio_health: float
    gross_margin_rate: float


class TenantInfo(BaseModel):
    id: str
    name: str
    plan: str


class OverviewResponse(BaseModel):
    tenant: TenantInfo
    generated_at: str
    totals: PortfolioTotals
    stations: list[StationSummary]
    dispatch: dict[str, Any]
    vpp: dict[str, Any]
    portfolio_series: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


class DispatchSummary(BaseModel):
    count: int
    high_risk: int
    estimated_daily_value: float


class DispatchResponse(BaseModel):
    mode: str
    approval_required: bool
    recommendations: list[dict[str, Any]]
    summary: DispatchSummary


# ---------------------------------------------------------------------------
# VPP
# ---------------------------------------------------------------------------


class VppResponse(BaseModel):
    event: dict[str, Any]
    total_adjustable_kw: float
    reliable_capacity_kw: float
    expected_revenue: float
    resources: list[dict[str, Any]]
    allocations: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# ROI
# ---------------------------------------------------------------------------


class RoiResponse(BaseModel):
    capacity_kwh: float
    power_kw: float
    capex: float
    annual_demand_savings: float
    annual_arbitrage: float
    annual_vpp_revenue: float
    annual_degradation_cost: float
    annual_maintenance: float
    annual_net_benefit: float
    payback_years: float
    npv_10y: float
    irr: float
    recommendation: Literal["invest", "review"]


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------


class AuditResponse(BaseModel):
    audit: list[AuditEntryOut]
    meta: PageMeta


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "unhealthy"]
    version: str
    db: str
    pool_available: int | None = None
    pool_size: int | None = None
