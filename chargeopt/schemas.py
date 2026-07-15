"""Pydantic response schemas for ChargeOpt API v1.

Defines typed output models so FastAPI generates accurate OpenAPI docs and
validates serialisation at the boundary.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

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
# Auth / RBAC
# ---------------------------------------------------------------------------


class PrincipalOut(BaseModel):
    subject: str
    tenant_id: str | None
    role: str
    display_name: str
    auth_type: str
    permissions: list[str]


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=240)
    password: str = Field(min_length=8, max_length=200)


class LoginResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"]
    expires_at: datetime
    principal: PrincipalOut


# ---------------------------------------------------------------------------
# Write-path request/response models
# ---------------------------------------------------------------------------


class TelemetryIngestRequest(BaseModel):
    station_id: str = Field(min_length=1)
    timestamp: datetime
    load_kw: float = Field(ge=0)
    pv_kw: float = Field(ge=0)
    grid_kw: float = Field(ge=0)
    storage_power_kw: float
    storage_soc: float = Field(ge=0, le=1)
    connector_occupied: int = Field(ge=0)
    queue_length: int = Field(ge=0)
    sessions: int = Field(ge=0)
    energy_kwh: float = Field(ge=0)
    revenue: float = Field(ge=0)
    alert_count: int = Field(ge=0)
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)
    actor: str = Field(default="edge-gateway", min_length=1, max_length=120)


class TelemetryIngestResponse(BaseModel):
    station_id: str
    timestamp: str
    created: bool
    idempotency_key: str


class AlertAcknowledgeRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)


class AlertAcknowledgeResponse(BaseModel):
    id: str
    acknowledged: bool


class DispatchGenerateRequest(BaseModel):
    actor: str = Field(default="system", min_length=1, max_length=120)


class DispatchGenerateResponse(BaseModel):
    generated: int
    recommendations: list[dict[str, Any]]


DispatchStatus = Literal["pending", "approved", "rejected", "executed", "failed", "rolled_back"]


class DispatchStatusRequest(BaseModel):
    status: DispatchStatus
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


class DispatchStatusResponse(BaseModel):
    id: str
    status: DispatchStatus


class RoiSimulationRequest(BaseModel):
    station_id: str | None = None
    capacity_kwh: float = Field(default=1200.0, gt=0)
    power_kw: float = Field(default=600.0, gt=0)
    capex_per_kwh: float = Field(default=1150.0, gt=0)
    vpp: bool = True


class RoiSimulationPersistedResponse(RoiResponse):
    id: int


# ---------------------------------------------------------------------------
# Revenue proof / moat diagnostics
# ---------------------------------------------------------------------------


class RevenueDiagnosticResponse(BaseModel):
    generated_at: str
    scope: dict[str, Any]
    algorithm: dict[str, Any]
    portfolio: dict[str, Any]
    stations: list[dict[str, Any]]
    moat: dict[str, Any]


class RevenueProofRunRequest(BaseModel):
    station_id: str | None = Field(default=None, min_length=1)
    created_by: str | None = Field(default=None, min_length=1, max_length=120)


class RevenueProofRunResponse(RevenueDiagnosticResponse):
    id: str


# ---------------------------------------------------------------------------
# Industrial control-plane models
# ---------------------------------------------------------------------------


ProtocolName = Literal["ocpp", "modbus", "mqtt"]


class ProtocolMessageRequest(BaseModel):
    station_id: str = Field(min_length=1)
    device_id: str | None = Field(default=None, min_length=1)
    external_id: str = Field(min_length=1, max_length=160)
    message_type: str = Field(min_length=1, max_length=120)
    payload: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class ProtocolMessageResponse(BaseModel):
    id: int
    protocol: ProtocolName
    station_id: str
    device_id: str | None
    status: str
    telemetry_ingested: bool = False
    task_id: str | None = None


class TaskCreateRequest(BaseModel):
    station_id: str | None = None
    device_id: str | None = None
    task_type: str = Field(min_length=1, max_length=120)
    priority: int = Field(default=100, ge=1, le=1000)
    payload: dict[str, Any]
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=160)


class TaskResponse(BaseModel):
    id: str
    tenant_id: str
    station_id: str | None
    device_id: str | None
    task_type: str
    status: str
    priority: int
    payload: dict[str, Any]
    result: dict[str, Any]
    attempts: int = 0
    max_attempts: int = 3
    lease_expires_at: datetime | None = None
    locked_by: str | None = None
    last_error: str | None = None


class TaskClaimRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    task_types: list[str] | None = None
    lease_seconds: int = Field(default=300, ge=30, le=3600)


class TaskClaimResponse(BaseModel):
    task: TaskResponse | None = None


class TaskCompleteRequest(BaseModel):
    worker_id: str = Field(min_length=1, max_length=120)
    status: Literal["succeeded", "failed", "cancelled"]
    result: dict[str, Any] = Field(default_factory=dict)
    error: str | None = Field(default=None, max_length=1000)
    retry_delay_seconds: int = Field(default=60, ge=0, le=3600)


class TaskReapRequest(BaseModel):
    actor: str | None = Field(default=None, min_length=1, max_length=120)


class TaskReapResponse(BaseModel):
    requeued: int
    failed: int
    total: int


class DispatchApprovalRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=120)
    reason: str | None = Field(default=None, max_length=500)


class DispatchApprovalResponse(BaseModel):
    id: str
    recommendation_id: str
    status: Literal["pending", "approved", "rejected", "expired"]
    task_id: str | None = None


class EdgeReceiptRequest(BaseModel):
    task_id: str = Field(min_length=1)
    station_id: str | None = None
    device_id: str | None = None
    status: Literal["accepted", "executing", "succeeded", "failed", "rolled_back"]
    payload: dict[str, Any] = Field(default_factory=dict)


class EdgeReceiptResponse(BaseModel):
    id: str
    task_id: str
    status: str


class OptimizationRunRequest(BaseModel):
    station_id: str | None = None
    horizon_hours: int = Field(default=24, ge=1, le=48)
    objective: Literal["cost", "revenue", "balanced"] = "balanced"


class OptimizationRunResponse(BaseModel):
    id: str
    solver: str
    objective: str
    objective_value: float
    dispatch_plan: list[dict[str, Any]]
    constraints: dict[str, Any]


class VppSettlementRequest(BaseModel):
    event_id: str = Field(min_length=1)
    baseline_kw: float = Field(ge=0)
    delivered_kw: float = Field(ge=0)
    settled_by: str | None = Field(default=None, min_length=1, max_length=120)
    evidence: dict[str, Any] = Field(default_factory=dict)


class VppSettlementResponse(BaseModel):
    id: str
    event_id: str
    performance_score: float
    gross_revenue: float
    penalty: float
    net_revenue: float


# ---------------------------------------------------------------------------
# Automated VPP trading
# ---------------------------------------------------------------------------


class VppAutomationRunRequest(BaseModel):
    trigger_source: str = Field(default="operator", min_length=2, max_length=80)


class VppAutomationRunResponse(BaseModel):
    tenant_id: str
    cycle_key: str | None = None
    status: str
    orders_created: int = 0
    model_config = {"extra": "allow"}


class MarketTradeWebhookRequest(BaseModel):
    order_id: str = Field(min_length=3, max_length=120)
    market_trade_id: str = Field(min_length=3, max_length=240)
    quantity_kw: float = Field(gt=0)
    price_per_kwh: float = Field(ge=0)
    traded_at: datetime
    payload: dict[str, Any] = Field(default_factory=dict)


class MarketTradeResponse(BaseModel):
    id: str
    order_id: str
    status: str | None = None
    tasks_created: int
    duplicate: bool = False


class VppMeterIntervalRequest(BaseModel):
    station_id: str
    interval_start: datetime
    interval_end: datetime
    baseline_kw: float = Field(ge=0)
    actual_grid_kw: float = Field(ge=0)
    quality: Literal["measured", "estimated", "substituted", "invalid"] = "measured"
    source: str = Field(min_length=2, max_length=120)
    payload: dict[str, Any] = Field(default_factory=dict)


class VppMeterIntervalResponse(BaseModel):
    id: str
    delivered_kw: float
    evidence_hash: str
    quality: str


class VppSettlementBatchRequest(BaseModel):
    market_code: str = Field(min_length=2, max_length=80)
    period_start: datetime
    period_end: datetime
    imbalance_price_per_kwh: float = Field(default=0.8, ge=0)
    penalty_rate: float = Field(default=0.25, ge=0, le=5)


class VppSettlementBatchResponse(BaseModel):
    id: str
    status: str
    trade_count: int
    gross_revenue: float
    imbalance_cost: float
    penalties: float
    net_revenue: float
    evidence_root_hash: str


class CircuitBreakerRequest(BaseModel):
    state: Literal["closed", "open", "half_open"]
    reason: str = Field(min_length=3, max_length=1000)
    reset_after: datetime | None = None


class CircuitBreakerResponse(BaseModel):
    state: str
    reason: str | None
    failure_count: int
    opened_at: datetime | None = None
    reset_after: datetime | None = None
    updated_by: str
    updated_at: datetime


class VppTradingDashboardResponse(BaseModel):
    generated_at: datetime
    connection: dict[str, Any]
    risk_policy: dict[str, Any]
    circuit_breaker: dict[str, Any]
    metrics: dict[str, Any]
    orders: list[dict[str, Any]]
    automation_runs: list[dict[str, Any]]
    settlements: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "unhealthy"]
    version: str
    db: str
    pool_available: int | None = None
    pool_size: int | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[str, bool]
    failures: list[str] = Field(default_factory=list)
