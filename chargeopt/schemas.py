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


ProtocolName = Literal[
    "ocpp",
    "ocpp16",
    "ocpp201",
    "ocpp21",
    "iso15118",
    "modbus",
    "modbus_tcp",
    "modbus_rtu",
    "mqtt",
    "bacnet_ip",
    "opc_ua",
    "iec61850",
    "iec104",
    "dlt645",
    "cjt188",
]


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
    idempotency_key: str | None = Field(default=None, min_length=8, max_length=240)


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


class EmsForecastRequest(BaseModel):
    station_id: str
    history_kw: list[float] | None = Field(default=None, min_length=12, max_length=100000)
    horizon: int = Field(default=24, ge=1, le=672)
    interval_minutes: Literal[5, 15, 30, 60] = 60
    coverage: Literal[0.8] = 0.8
    scenario_count: int = Field(default=24, ge=4, le=256)
    random_seed: int = 17
    use_foundation_model: bool = False
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsDispatchRequest(EmsForecastRequest):
    prices: list[float] | None = Field(default=None, min_length=1, max_length=672)
    initial_soc: float | None = Field(default=None, gt=0, le=1)
    soh: float = Field(default=0.95, gt=0, le=1)
    temperature_c: float = Field(default=25, ge=-30, le=90)
    risk_alpha: float = Field(default=0.95, gt=0.5, lt=1)
    risk_weight: float = Field(default=0.25, ge=0, le=1000)
    demand_charge_per_kw: float | None = Field(default=None, ge=0)
    reserve_soc: float = Field(default=0.32, ge=0.2, le=0.92)


class EmsNetworkProjectionRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str | None = None
    evidence_class: Literal["synthetic", "replay", "shadow", "observed"] = "replay"
    network: dict[str, Any]
    proposals: list[dict[str, Any]] = Field(min_length=1, max_length=10000)
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsCoordinationRequest(BaseModel):
    tenant_id: str | None = None
    resources: list[dict[str, Any]] = Field(min_length=2, max_length=10000)
    target_kw: float
    rho: float = Field(default=1, gt=0, le=10000)
    tolerance: float = Field(default=0.0001, gt=0, le=0.1)
    max_iterations: int = Field(default=500, ge=10, le=10000)
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsOfflinePolicyRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str | None = None
    transitions: list[dict[str, Any]] = Field(min_length=12, max_length=200000)
    actions_kw: list[float] = Field(min_length=2, max_length=101)
    evaluation_state: list[float] = Field(min_length=1, max_length=1000)
    safety_constraints: dict[str, float]
    conservative_penalty: float = Field(default=0.2, ge=0, le=1000)
    max_mahalanobis: float = Field(default=4, gt=0, le=1000)
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsEvSession(BaseModel):
    session_id: str = Field(min_length=1, max_length=160)
    arrival_step: int = Field(default=0, ge=0, le=671)
    departure_step: int = Field(ge=1, le=672)
    required_energy_kwh: float = Field(ge=0, le=5000)
    delivered_energy_kwh: float = Field(default=0, ge=0, le=5000)
    max_charge_kw: float = Field(gt=0, le=2000)
    efficiency: float = Field(default=0.94, ge=0.8, le=1)


class EmsFlexibilityRequest(BaseModel):
    station_id: str
    sessions: list[EmsEvSession] = Field(min_length=1, max_length=10000)
    horizon: int = Field(default=24, ge=1, le=672)
    interval_minutes: Literal[5, 15, 30, 60] = 15
    evidence_class: Literal["synthetic", "replay", "shadow", "observed"] = "replay"
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsSecureDispatchRequest(EmsForecastRequest):
    sessions: list[EmsEvSession] = Field(min_length=1, max_length=250)
    prices: list[float] = Field(min_length=1, max_length=168)
    initial_soc: float | None = Field(default=None, gt=0, le=1)
    soh: float = Field(default=0.95, gt=0, le=1)
    temperature_c: float = Field(default=25, ge=-30, le=90)
    carbon_intensity_kg_per_kwh: list[float] | None = Field(default=None, min_length=1, max_length=168)
    carbon_price_per_kg: float = Field(default=0, ge=0, le=10000)
    reserve_up_prices: list[float] | None = Field(default=None, min_length=1, max_length=168)
    reserve_down_prices: list[float] | None = Field(default=None, min_length=1, max_length=168)
    reserve_duration_minutes: int = Field(default=15, ge=1, le=240)
    contingencies: list[dict[str, Any]] = Field(default_factory=list, max_length=256)
    risk_alpha: float = Field(default=0.95, gt=0.5, lt=1)
    risk_weight: float = Field(default=0.25, ge=0, le=1000)
    demand_charge_per_kw: float = Field(default=0, ge=0)
    reserve_soc: float = Field(default=0.3, ge=0.2, le=0.92)
    allow_service_restoration: bool = True


class EmsNetworkSecurityRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str | None = None
    network: dict[str, Any]
    intervals: list[dict[str, Any]] = Field(min_length=1, max_length=672)
    contingencies: list[dict[str, Any]] = Field(min_length=1, max_length=256)
    evidence_class: Literal["synthetic", "replay", "shadow", "observed"] = "replay"
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsBatteryDegradationRequest(BaseModel):
    station_id: str
    soc_series: list[float] = Field(min_length=3, max_length=100000)
    temperature_c: float | list[float] = 25
    interval_minutes: Literal[5, 15, 30, 60] = 15
    soh: float = Field(default=0.95, gt=0, le=1)
    replacement_cost: float | None = Field(default=None, ge=0)
    evidence_class: Literal["synthetic", "replay", "shadow", "observed"] = "replay"
    idempotency_key: str = Field(min_length=8, max_length=300)


class EmsResponse(BaseModel):
    model_config = {"extra": "allow"}


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


class SettlementApprovalRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class SettlementDisputeRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)


class SettlementDisputeResolutionRequest(BaseModel):
    resolution: str = Field(min_length=3, max_length=4000)
    accepted: bool = True


class SettlementExportRequest(BaseModel):
    format: Literal["csv", "json"] = "csv"
    destination: str = Field(min_length=3, max_length=500)


class SettlementPaymentRequest(BaseModel):
    payment_reference: str = Field(min_length=3, max_length=240)


class SettlementReversalRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=2000)
    external_reference: str | None = Field(default=None, max_length=240)


class SettlementActionResponse(BaseModel):
    id: str
    status: str
    event_hash: str
    dispute_id: str | None = None
    export_id: str | None = None
    adjustment_id: str | None = None
    payment_reference: str | None = None
    format: str | None = None
    destination: str | None = None
    content_hash: str | None = None
    row_count: int | None = None
    content: str | None = None
    amount: float | None = None


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
# MLOps lifecycle
# ---------------------------------------------------------------------------


class ModelRegisterRequest(BaseModel):
    tenant_id: str | None = Field(default=None, min_length=1, max_length=120)
    scope: str = Field(min_length=2, max_length=120)
    version: str = Field(min_length=1, max_length=80)
    algorithm: str = Field(min_length=2, max_length=160)
    artifact_uri: str = Field(min_length=8, max_length=1000)
    artifact_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_data_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    training_window_start: datetime
    training_window_end: datetime
    metrics: dict[str, float] = Field(default_factory=dict)


class ModelEvaluationRequest(BaseModel):
    dataset_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    actual: list[float] = Field(min_length=8, max_length=100000)
    p10: list[float] = Field(min_length=8, max_length=100000)
    p50: list[float] = Field(min_length=8, max_length=100000)
    p90: list[float] = Field(min_length=8, max_length=100000)
    reference_metrics: dict[str, float] | None = None


class ModelResponse(BaseModel):
    id: str
    tenant_id: str
    scope: str
    version: str
    algorithm: str
    artifact_uri: str
    artifact_sha256: str
    training_data_hash: str
    training_window_start: datetime
    training_window_end: datetime
    status: str
    metrics: dict[str, Any]
    created_by: str
    approved_by: str | None = None
    approved_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ModelEvaluationResponse(BaseModel):
    id: str
    model_id: str
    metrics: dict[str, float]
    quality_gate: dict[str, Any]


# ---------------------------------------------------------------------------
# Charging-station digital twin
# ---------------------------------------------------------------------------


EvidenceClass = Literal["synthetic", "replay", "shadow", "observed", "field_qualified"]


class TwinAssetInput(BaseModel):
    asset_key: str = Field(min_length=1, max_length=160)
    asset_type: Literal[
        "station",
        "transformer",
        "bus",
        "meter",
        "charger",
        "connector",
        "pcs",
        "battery_system",
        "battery_rack",
        "battery_pack",
        "pv_inverter",
        "sensor",
        "gateway",
    ]
    name: str = Field(min_length=1, max_length=240)
    manufacturer: str | None = Field(default=None, max_length=160)
    model: str | None = Field(default=None, max_length=160)
    serial_number: str | None = Field(default=None, max_length=240)
    rated_power_kw: float | None = Field(default=None, ge=0)
    rated_energy_kwh: float | None = Field(default=None, ge=0)
    attributes: dict[str, Any] = Field(default_factory=dict)


class TwinRelationshipInput(BaseModel):
    source_asset_key: str = Field(min_length=1, max_length=160)
    target_asset_key: str = Field(min_length=1, max_length=160)
    relationship_type: Literal["contains", "feeds", "meters", "controls", "communicates_with", "measures"]
    attributes: dict[str, Any] = Field(default_factory=dict)


class TwinTopologyCreateRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str
    assets: list[TwinAssetInput] = Field(min_length=1, max_length=10000)
    relationships: list[TwinRelationshipInput] = Field(default_factory=list, max_length=30000)


class TwinMeasurementInput(BaseModel):
    asset_key: str | None = Field(default=None, max_length=160)
    point_code: str = Field(min_length=1, max_length=160)
    value: float
    unit: str = Field(min_length=1, max_length=32)
    source_timestamp: datetime
    received_at: datetime | None = None
    sequence_number: int | None = Field(default=None, ge=0)
    source: str = Field(min_length=1, max_length=160)
    idempotency_key: str | None = Field(default=None, max_length=300)
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class TwinMeasurementBatchRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str
    measurements: list[TwinMeasurementInput] = Field(min_length=1, max_length=5000)


class TwinSimulationRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str
    scenario_type: Literal["replay", "what_if", "shadow", "commissioning"] = "what_if"
    evidence_class: EvidenceClass = "synthetic"
    interval_minutes: int = Field(default=15, ge=1, le=60)
    random_seed: int = 0
    idempotency_key: str = Field(min_length=4, max_length=300)
    initial_state: dict[str, Any] = Field(default_factory=dict)
    schedule: list[dict[str, Any]] = Field(min_length=1, max_length=10000)


class TwinOptimizationRequest(BaseModel):
    station_id: str
    horizon_hours: int = Field(default=24, ge=1, le=168)
    objective: Literal["cost", "revenue", "balanced"] = "balanced"
    mode: Literal["recommend", "auto"] = "recommend"


class TwinCalibrationRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str
    model_scope: str = Field(default="station_power_balance", min_length=3, max_length=160)
    model_version: str = Field(default="electro-thermal-queue-twin-v1", min_length=3, max_length=160)
    evidence_class: EvidenceClass = "observed"
    predicted: list[float] = Field(min_length=1, max_length=100000)
    observed: list[float] = Field(min_length=1, max_length=100000)


class TwinTrajectoryComparisonRequest(BaseModel):
    predicted: list[dict[str, Any]] = Field(min_length=1, max_length=100000)
    observed: list[dict[str, Any]] = Field(min_length=1, max_length=100000)
    fields: list[str] = Field(
        default_factory=lambda: ["grid_kw", "storage_soc", "transformer_temperature_c"],
        min_length=1,
        max_length=20,
    )


class TwinMaintenanceTransitionRequest(BaseModel):
    tenant_id: str | None = None
    status: Literal["in_progress", "completed", "cancelled"]
    assigned_to: str | None = Field(default=None, max_length=240)
    outcome: dict[str, Any] | None = None


class TwinFaultInjectionRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str


class CausalObservation(BaseModel):
    timestamp: datetime | None = None
    treated: bool
    outcome: float
    covariates: dict[str, float]


class TwinCausalStudyRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str | None = None
    evidence_class: EvidenceClass = "observed"
    estimand: str = Field(default="monthly_profit_lift", min_length=3, max_length=160)
    observations: list[CausalObservation] = Field(min_length=1, max_length=100000)


class TwinQualificationEvidenceRequest(BaseModel):
    tenant_id: str | None = None
    station_id: str | None = None
    evidence_date: datetime
    category: Literal[
        "topology",
        "device_attestation",
        "calibration",
        "shadow_day",
        "slo",
        "fault_injection",
        "recovery_drill",
        "approval",
    ]
    qualified: bool
    evidence: dict[str, Any] = Field(default_factory=dict)


class TwinResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Shared charging, storage, campus, and energy-management platform
# ---------------------------------------------------------------------------


class EnergyTopologyCreateRequest(BaseModel):
    tenant_id: str | None = None
    name: str = Field(min_length=1, max_length=240)
    assets: list[dict[str, Any]] = Field(min_length=1, max_length=20000)
    relationships: list[dict[str, Any]] = Field(default_factory=list, max_length=60000)
    points: list[dict[str, Any]] = Field(default_factory=list, max_length=100000)
    constraints: list[dict[str, Any]] = Field(default_factory=list, max_length=100000)


class EnergyDriverProfileRequest(BaseModel):
    tenant_id: str | None = None
    name: str = Field(min_length=1, max_length=240)
    protocol: str = Field(min_length=2, max_length=32)
    version: str = Field(min_length=1, max_length=80)
    security_profile: dict[str, Any]
    transport_profile: dict[str, Any] = Field(default_factory=dict)
    capabilities: dict[str, Any] = Field(default_factory=dict)
    mappings: list[dict[str, Any]] = Field(min_length=1, max_length=10000)


class EnergyComputationRequest(BaseModel):
    tenant_id: str | None = None
    scope_id: str | None = Field(default=None, max_length=240)
    evidence_class: EvidenceClass = "observed"
    idempotency_key: str = Field(min_length=4, max_length=300)
    payload: dict[str, Any]


class EnergyResponse(BaseModel):
    model_config = {"extra": "allow"}


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    status: Literal["ok", "unhealthy"]
    version: str
    revision: str
    db: str
    pool_available: int | None = None
    pool_size: int | None = None


class ReadinessResponse(BaseModel):
    status: Literal["ready", "not_ready"]
    version: str
    checks: dict[str, bool]
    failures: list[str] = Field(default_factory=list)
