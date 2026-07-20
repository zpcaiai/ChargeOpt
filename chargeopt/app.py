"""ChargeOpt OS – production FastAPI application.

Provides:
- All analytics API endpoints
- API-Key authentication (optional when api_key is not set)
- CORS, request-ID propagation, structured access logging
- Rate limiting via slowapi
- /health and /metrics (Prometheus) endpoints
- Graceful startup/shutdown with connection-pool lifecycle
"""

import hmac
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog
from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request, Response, Security, status
from fastapi.exception_handlers import request_validation_exception_handler  # noqa: F401
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.security.api_key import APIKeyHeader
from prometheus_client import CONTENT_TYPE_LATEST, Counter, Gauge, Histogram, generate_latest
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from .advanced_ems import (
    ALGORITHMS as EMS_ALGORITHMS,
)
from .advanced_ems import (
    FoundationForecastClient,
    calibrated_ensemble_forecast,
    coordinate_portfolio_admm,
    evaluate_offline_policy,
    project_three_phase_distflow,
    solve_distributionally_robust_mpc,
    train_conservative_fitted_q,
)
from .advanced_ems_repository import list_ems_evidence, persist_ems_evidence
from .analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail, station_summary
from .auth import ROLE_PERMISSIONS, Principal, development_principal, has_permission, static_api_key_principal
from .config import get_settings
from .db import close_pool, health_check, init_pool
from .digital_twin import (
    assess_field_qualification,
    build_default_topology,
    build_twin_snapshot,
    calibrate_twin_model,
    compare_trajectories,
    diagnose_twin,
    estimate_causal_uplift,
    estimate_station_state,
    normalize_measurement,
    run_fault_injection_suite,
    simulate_station,
    twin_aware_station,
)
from .digital_twin_repository import (
    activate_topology_version,
    create_topology_version,
    get_topology,
    list_maintenance_actions,
    load_measurements,
    load_qualification_evidence,
    persist_calibration,
    persist_causal_study,
    persist_diagnostics,
    persist_measurements,
    persist_simulation,
    persist_state_estimate,
    record_qualification_evidence,
    transition_maintenance_action,
)
from .grid_ems import (
    GRID_EMS_ALGORITHMS,
    aggregate_ev_flexibility,
    assess_n_minus_one_security,
    estimate_battery_degradation,
    solve_secure_rolling_dispatch,
)
from .logging_config import configure_logging
from .mlops import evaluate_model, list_models, promote_model, register_model
from .operations_assurance import live_market_readiness, record_shadow_day, run_assurance_checks
from .optimizer import solve_dispatch_optimization
from .protocols import normalize_protocol_message
from .repository import (
    acknowledge_alert,
    authenticate_user,
    claim_next_task,
    complete_task,
    enqueue_task,
    ingest_telemetry,
    load_repository_from_db,
    persist_dispatch_recommendations,
    persist_optimization_run,
    persist_protocol_message,
    persist_revenue_proof,
    persist_roi_simulation,
    principal_from_session,
    reap_expired_tasks,
    record_edge_receipt,
    request_dispatch_approval,
    review_dispatch_approval,
    settle_vpp_event,
    update_dispatch_status,
)
from .revenue_intelligence import build_revenue_diagnostics
from .schemas import (
    AlertAcknowledgeRequest,
    AlertAcknowledgeResponse,
    AuditResponse,
    CircuitBreakerRequest,
    CircuitBreakerResponse,
    DispatchApprovalRequest,
    DispatchApprovalResponse,
    DispatchGenerateRequest,
    DispatchGenerateResponse,
    DispatchResponse,
    DispatchStatusRequest,
    DispatchStatusResponse,
    EdgeReceiptRequest,
    EdgeReceiptResponse,
    EmsBatteryDegradationRequest,
    EmsCoordinationRequest,
    EmsDispatchRequest,
    EmsFlexibilityRequest,
    EmsForecastRequest,
    EmsNetworkProjectionRequest,
    EmsNetworkSecurityRequest,
    EmsOfflinePolicyRequest,
    EmsResponse,
    EmsSecureDispatchRequest,
    HealthResponse,
    LoginRequest,
    LoginResponse,
    MarketTradeResponse,
    MarketTradeWebhookRequest,
    ModelEvaluationRequest,
    ModelEvaluationResponse,
    ModelRegisterRequest,
    ModelResponse,
    OptimizationRunRequest,
    OptimizationRunResponse,
    OverviewResponse,
    PrincipalOut,
    ProblemDetail,
    ProtocolMessageRequest,
    ProtocolMessageResponse,
    ReadinessResponse,
    RevenueDiagnosticResponse,
    RevenueProofRunRequest,
    RevenueProofRunResponse,
    RoiResponse,
    RoiSimulationPersistedResponse,
    RoiSimulationRequest,
    SettlementActionResponse,
    SettlementApprovalRequest,
    SettlementDisputeRequest,
    SettlementDisputeResolutionRequest,
    SettlementExportRequest,
    SettlementPaymentRequest,
    SettlementReversalRequest,
    StationDetailResponse,
    StationListResponse,
    TaskClaimRequest,
    TaskClaimResponse,
    TaskCompleteRequest,
    TaskCreateRequest,
    TaskReapRequest,
    TaskReapResponse,
    TaskResponse,
    TelemetryIngestRequest,
    TelemetryIngestResponse,
    TwinCalibrationRequest,
    TwinCausalStudyRequest,
    TwinFaultInjectionRequest,
    TwinMaintenanceTransitionRequest,
    TwinMeasurementBatchRequest,
    TwinOptimizationRequest,
    TwinQualificationEvidenceRequest,
    TwinResponse,
    TwinSimulationRequest,
    TwinTopologyCreateRequest,
    TwinTrajectoryComparisonRequest,
    VppAutomationRunRequest,
    VppAutomationRunResponse,
    VppMeterIntervalRequest,
    VppMeterIntervalResponse,
    VppResponse,
    VppSettlementBatchRequest,
    VppSettlementBatchResponse,
    VppSettlementRequest,
    VppSettlementResponse,
    VppTradingDashboardResponse,
)
from .vpp_automation import run_all_automation_cycles, run_automation_cycle
from .vpp_operations import run_operational_maintenance
from .vpp_repository import (
    approve_settlement_batch,
    create_settlement_batch,
    dispute_settlement_batch,
    export_settlement_batch,
    ingest_meter_interval,
    mark_settlement_paid,
    record_trade_fill,
    resolve_settlement_dispute,
    reverse_settlement_batch,
    set_circuit_breaker,
    trading_dashboard,
)
from .vpp_trading import verify_market_webhook

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

REQUEST_COUNT = Counter(
    "chargeopt_http_requests_total",
    "Total HTTP requests",
    ["method", "endpoint", "status_code"],
)
REQUEST_LATENCY = Histogram(
    "chargeopt_http_request_duration_seconds",
    "HTTP request latency",
    ["method", "endpoint"],
)
DB_POOL_AVAILABLE = Gauge("chargeopt_db_pool_available", "DB pool connections available")
DB_POOL_SIZE = Gauge("chargeopt_db_pool_size", "DB pool total size")
ACTIVE_STATIONS_GAUGE = Gauge("chargeopt_active_stations_total", "Number of stations in the repository")

# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------

limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(use_lifespan: bool = True) -> FastAPI:
    s = get_settings()
    configure_logging(
        log_level=s.log_level,
        json_logs=s.is_production,
    )

    @asynccontextmanager
    async def _lifespan(application: FastAPI):
        init_pool()
        logger.info("ChargeOpt startup complete", environment=s.environment)
        yield
        close_pool()
        logger.info("ChargeOpt shutdown complete")

    app = FastAPI(
        title=s.app_name,
        version=s.app_version,
        lifespan=_lifespan if use_lifespan else None,
        docs_url="/docs" if not s.is_production else None,
        redoc_url="/redoc" if not s.is_production else None,
        openapi_url="/openapi.json" if not s.is_production else None,
        default_response_class=JSONResponse,
    )

    # -- RFC 7807 error handlers ---------------------------------------------
    @app.exception_handler(404)
    async def _not_found_handler(request: Request, exc) -> JSONResponse:
        return JSONResponse(
            status_code=404,
            content=ProblemDetail(
                title="Not Found",
                status=404,
                detail=str(exc.detail) if hasattr(exc, "detail") else "Resource not found.",
                instance=str(request.url.path),
            ).model_dump(),
            media_type="application/problem+json",
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content=ProblemDetail(
                title="Validation Error",
                status=422,
                detail=str(exc.errors()),
                instance=str(request.url.path),
            ).model_dump(),
            media_type="application/problem+json",
        )

    @app.exception_handler(500)
    async def _server_error_handler(request: Request, exc) -> JSONResponse:
        logger.error(
            "Unhandled server error",
            path=str(request.url.path),
            error_type=type(exc).__name__,
            exc_info=s.is_production,
        )
        return JSONResponse(
            status_code=500,
            content=ProblemDetail(
                title="Internal Server Error",
                status=500,
                detail="An unexpected error occurred.",
                instance=str(request.url.path),
            ).model_dump(),
            media_type="application/problem+json",
        )

    # -- Rate limiter error handler ------------------------------------------
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
        return JSONResponse(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            content={"error": "rate_limit_exceeded", "detail": str(exc)},
        )

    # -- CORS ----------------------------------------------------------------
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_origins_list,
        allow_credentials=s.cors_allow_credentials,
        allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
        allow_headers=["*"],
        expose_headers=[s.request_id_header],
    )

    # -- Security headers ----------------------------------------------------
    @app.middleware("http")
    async def _security_headers_middleware(request: Request, call_next):
        response: Response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = "geolocation=(), microphone=(), camera=()"
        if s.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    # -- Request ID + structured access logging + metrics --------------------
    @app.middleware("http")
    async def _observability_middleware(request: Request, call_next):
        request_id = request.headers.get(s.request_id_header) or str(uuid.uuid4())
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            method=request.method,
            path=request.url.path,
        )
        t0 = time.perf_counter()
        try:
            response: Response = await call_next(request)
        except Exception as exc:
            logger.error("Unhandled exception", error_type=type(exc).__name__, exc_info=s.is_production)
            raise
        elapsed = time.perf_counter() - t0
        response.headers[s.request_id_header] = request_id
        logger.info(
            "request",
            status_code=response.status_code,
            duration_ms=round(elapsed * 1000, 1),
        )
        if s.metrics_enabled:
            endpoint = request.url.path
            REQUEST_COUNT.labels(request.method, endpoint, response.status_code).inc()
            REQUEST_LATENCY.labels(request.method, endpoint).observe(elapsed)
        return response

    # -- Register routers ----------------------------------------------------
    v1 = _build_v1_router(s)
    app.include_router(v1, prefix="/api/v1")
    # Backward-compatible aliases (no version prefix)
    app.include_router(v1, prefix="/api", include_in_schema=False)
    _register_ops_routes(app, s)

    return app


# ---------------------------------------------------------------------------
# Security dependency
# ---------------------------------------------------------------------------

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def _principal_out(principal: Principal) -> PrincipalOut:
    permissions = ROLE_PERMISSIONS[principal.role]
    return PrincipalOut(
        subject=principal.subject,
        tenant_id=principal.tenant_id,
        role=principal.role,
        display_name=principal.display_name,
        auth_type=principal.auth_type,
        permissions=sorted(permissions),
    )


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token


def resolve_principal(
    api_key: str | None = Security(_api_key_header),
    authorization: str | None = Header(default=None),
) -> Principal:
    settings = get_settings()
    bearer = _extract_bearer(authorization)
    if bearer and settings.use_db:
        principal = principal_from_session(bearer)
        if principal is not None:
            return principal

    if settings.api_key is not None:
        provided = api_key or ""
        if hmac.compare_digest(provided.encode(), settings.api_key.encode()):
            return static_api_key_principal()

    if not settings.is_production and settings.api_key is None:
        return development_principal()

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or missing credentials.",
        headers={"WWW-Authenticate": "Bearer"},
    )


PrincipalDep = Depends(resolve_principal)


def require_permission(permission: str):
    def _dependency(principal: Principal = PrincipalDep) -> Principal:
        if not has_permission(principal, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")
        return principal

    return _dependency


def require_write_permission(permission: str):
    def _dependency(principal: Principal = PrincipalDep) -> Principal:
        if not has_permission(principal, permission):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Insufficient permissions.")
        return principal

    return _dependency


AuthDep = Depends(require_permission("station:read"))
DispatchWriteDep = Depends(require_write_permission("dispatch:write"))
DispatchApproveDep = Depends(require_write_permission("dispatch:approve"))
TelemetryWriteDep = Depends(require_write_permission("telemetry:write"))
DeviceWriteDep = Depends(require_write_permission("device:write"))
TaskWriteDep = Depends(require_write_permission("task:write"))
VppSettleDep = Depends(require_write_permission("vpp:settle"))
VppTradeDep = Depends(require_write_permission("vpp:trade"))
VppMeterWriteDep = Depends(require_write_permission("vpp:meter:write"))
VppOperateDep = Depends(require_write_permission("vpp:operate"))
ModelWriteDep = Depends(require_write_permission("model:write"))
ModelApproveDep = Depends(require_write_permission("model:approve"))
TwinReadDep = Depends(require_permission("twin:read"))
TwinWriteDep = Depends(require_write_permission("twin:write"))
TwinApproveDep = Depends(require_write_permission("twin:approve"))
EmsReadDep = Depends(require_permission("ems:read"))
EmsWriteDep = Depends(require_write_permission("ems:write"))
AuditReadDep = Depends(require_permission("audit:read"))


def _tenant_scope(principal: Principal) -> str | None:
    return None if principal.is_platform_admin else principal.tenant_id


def _worker_tenant_scope(principal: Principal) -> str:
    return "*" if principal.is_platform_admin else principal.tenant_id or "t-001"


def _twin_tenant(principal: Principal, requested_tenant: str | None, station_tenant: str | None) -> str:
    target = requested_tenant or principal.tenant_id or station_tenant
    if target is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="tenant_id is required")
    if not principal.is_platform_admin and target != principal.tenant_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-tenant access is denied.")
    if station_tenant is not None and target != station_tenant:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Station belongs to another tenant.")
    return target


def _require_twin_database(settings: Any) -> None:
    if not settings.use_db:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Digital-twin evidence persistence requires DATABASE_URL.",
        )


# ---------------------------------------------------------------------------
# Ops routes (health + metrics – no auth, no version prefix)
# ---------------------------------------------------------------------------


def _update_gauges() -> None:
    """Refresh business-level Prometheus gauges from live state."""
    try:
        db_status = health_check()
        DB_POOL_AVAILABLE.set(db_status.get("pool_available") or 0)
        DB_POOL_SIZE.set(db_status.get("pool_size") or 0)
    except Exception:
        pass
    try:
        repo = load_repository_from_db()
        ACTIVE_STATIONS_GAUGE.set(len(repo.stations))
    except Exception:
        pass


def _register_ops_routes(app: FastAPI, s: Any) -> None:
    @app.get("/health", tags=["ops"], response_model=HealthResponse, include_in_schema=False)
    async def _health():
        try:
            db_status = health_check()
        except Exception as exc:
            return JSONResponse(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                content={"status": "unhealthy", "detail": str(exc)},
            )
        _update_gauges()
        return {"status": "ok", "version": s.app_version, **db_status}

    @app.get("/ready", tags=["ops"], response_model=ReadinessResponse, include_in_schema=False)
    async def _ready():
        checks = {
            "database_configured": s.use_db or not s.is_production,
            "database_reachable": True,
            "debug_disabled": not s.debug,
            "cors_credentials_safe": not (s.cors_allow_credentials and "*" in s.cors_origins_list),
        }
        failures: list[str] = []
        if s.use_db:
            try:
                db_status = health_check()
                checks["database_reachable"] = db_status.get("db") == "ok"
            except Exception:
                checks["database_reachable"] = False
        if s.is_production and not s.use_db:
            failures.append("DATABASE_URL is required in production.")
        if not checks["database_reachable"]:
            failures.append("Database health check failed.")
        if s.debug:
            failures.append("DEBUG must be false outside local development.")
        if not checks["cors_credentials_safe"]:
            failures.append("CORS cannot allow credentials with wildcard origins.")
        ready = all(checks.values())
        payload = {
            "status": "ready" if ready else "not_ready",
            "version": s.app_version,
            "checks": checks,
            "failures": failures,
        }
        if not ready:
            return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=payload)
        return payload

    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    async def _metrics():
        if not s.metrics_enabled:
            raise HTTPException(status_code=404, detail="Metrics disabled.")
        _update_gauges()
        data = generate_latest()
        return Response(content=data, media_type=CONTENT_TYPE_LATEST)

    @app.get("/api/cron/vpp-cycle", tags=["ops"], include_in_schema=False)
    async def _vpp_cron(authorization: str | None = Header(default=None)):
        if not s.cron_secret or not hmac.compare_digest(authorization or "", f"Bearer {s.cron_secret}"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron credentials.")
        if not s.vpp_automation_enabled:
            return {"status": "disabled", "tenant_count": 0, "results": []}
        cycles = run_all_automation_cycles(trigger_source="production-scheduler")
        operations = run_operational_maintenance("production-scheduler")
        return {
            "status": "degraded"
            if cycles["status"] == "degraded" or operations["status"] == "degraded"
            else "completed",
            "cycles": cycles,
            "operations": operations,
        }

    @app.get("/api/cron/assurance", tags=["ops"], include_in_schema=False)
    async def _assurance_cron(authorization: str | None = Header(default=None)):
        if not s.cron_secret or not hmac.compare_digest(authorization or "", f"Bearer {s.cron_secret}"):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid cron credentials.")
        assurance = run_assurance_checks("production-assurance")
        evidence_date = datetime.now(UTC).date() - timedelta(days=1)
        evidence = [
            record_shadow_day(row["tenant_id"], evidence_date, "production-assurance") for row in assurance["tenants"]
        ]
        return {"status": assurance["status"], "assurance": assurance, "shadow_evidence": evidence}


# ---------------------------------------------------------------------------
# Versioned API router  (/api/v1/...)
# ---------------------------------------------------------------------------


def _build_v1_router(s: Any) -> APIRouter:
    router = APIRouter(tags=["v1"])
    rl = f"{s.rate_limit_per_minute}/minute"

    def _ems_target_tenant(principal: Principal, requested: str | None = None) -> str:
        tenant_id = requested or principal.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="tenant_id is required for a platform-wide principal.",
            )
        if not principal.is_platform_admin and tenant_id != principal.tenant_id:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Cross-tenant EMS access is denied.")
        return tenant_id

    def _ems_station(repo: Any, station_id: str) -> Any:
        station = next((item for item in repo.stations if item.id == station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {station_id}")
        return station

    def _ems_result_with_evidence(
        principal: Principal,
        tenant_id: str,
        station_id: str | None,
        evidence_type: str,
        evidence_class: str,
        request_payload: dict[str, Any],
        result: dict[str, Any],
        idempotency_key: str,
    ) -> dict[str, Any]:
        if not s.use_db:
            return {
                **result,
                "evidence": {"id": None, "persisted": False, "replayed": False, "reason": "database_disabled"},
            }
        stored = persist_ems_evidence(
            tenant_id,
            station_id,
            evidence_type,
            result["algorithm"],
            evidence_class,
            result["input_hash"],
            request_payload,
            result,
            idempotency_key,
            principal.subject,
            _tenant_scope(principal),
        )
        persisted_result = stored["result"] if stored["replayed"] else result
        return {
            **persisted_result,
            "evidence": {
                "id": stored["id"],
                "persisted": True,
                "replayed": stored["replayed"],
                "created_at": stored.get("created_at"),
            },
        }

    @router.post("/auth/login", response_model=LoginResponse)
    @limiter.limit(rl)
    async def _login(request: Request, body: LoginRequest) -> Any:
        try:
            result = authenticate_user(body.email, body.password)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        principal = result["principal"]
        return {
            "access_token": result["access_token"],
            "token_type": "bearer",
            "expires_at": result["expires_at"],
            "principal": _principal_out(principal),
        }

    @router.get("/auth/me", response_model=PrincipalOut)
    @limiter.limit(rl)
    async def _me(request: Request, principal: Principal = PrincipalDep) -> Any:
        return _principal_out(principal)

    @router.get("/overview", response_model=OverviewResponse)
    @limiter.limit(rl)
    async def _overview(request: Request, _auth: Principal = AuthDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        return build_overview(repo)

    @router.get("/stations", response_model=StationListResponse)
    @limiter.limit(rl)
    async def _stations(request: Request, _auth: Principal = AuthDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        return {"stations": [station_summary(repo, station) for station in repo.stations]}

    @router.get("/stations/{station_id}", response_model=StationDetailResponse)
    @limiter.limit(rl)
    async def _station_detail(request: Request, station_id: str, _auth: Principal = AuthDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        try:
            return station_detail(repo, station_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/digital-twin/stations/{station_id}", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _twin_snapshot(request: Request, station_id: str, _auth: Principal = TwinReadDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {station_id}")
        evidence_class = "synthetic"
        if s.use_db:
            evidence_class = "observed"
            tenant_id = _twin_tenant(_auth, None, station.tenant_id)
            qualification = assess_field_qualification(
                load_qualification_evidence(tenant_id, station_id, _tenant_scope(_auth))
            )
            if qualification["ready"]:
                evidence_class = "field_qualified"
        snapshot = build_twin_snapshot(repo, station_id, evidence_class=evidence_class)
        snapshot["persisted"] = False
        return snapshot

    @router.get("/digital-twin/stations/{station_id}/topology", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _twin_topology(request: Request, station_id: str, _auth: Principal = TwinReadDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {station_id}")
        if s.use_db:
            tenant_id = _twin_tenant(_auth, None, station.tenant_id)
            topology = get_topology(tenant_id, station_id, scope_tenant_id=_tenant_scope(_auth))
            if topology:
                return topology
        return build_default_topology(station) | {"status": "synthetic_default"}

    @router.post(
        "/digital-twin/topologies",
        response_model=TwinResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _create_twin_topology(
        request: Request,
        body: TwinTopologyCreateRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        _require_twin_database(s)
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == body.station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}")
        tenant_id = _twin_tenant(_auth, body.tenant_id, station.tenant_id)
        try:
            return create_topology_version(
                tenant_id,
                body.station_id,
                {
                    "station_id": body.station_id,
                    "assets": [item.model_dump() for item in body.assets],
                    "relationships": [item.model_dump() for item in body.relationships],
                },
                _auth.subject,
                _tenant_scope(_auth),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/digital-twin/topologies/{topology_id}/activate", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _activate_twin_topology(
        request: Request,
        topology_id: str,
        tenant_id: str | None = Query(default=None),
        _auth: Principal = TwinApproveDep,
    ) -> Any:
        _require_twin_database(s)
        target_tenant = _twin_tenant(_auth, tenant_id, _auth.tenant_id)
        try:
            return activate_topology_version(target_tenant, topology_id, _auth.subject, _tenant_scope(_auth))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/digital-twin/measurements",
        response_model=TwinResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    @limiter.limit(rl)
    async def _ingest_twin_measurements(
        request: Request,
        body: TwinMeasurementBatchRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        _require_twin_database(s)
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == body.station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}")
        tenant_id = _twin_tenant(_auth, body.tenant_id, station.tenant_id)
        try:
            normalized = [
                normalize_measurement(
                    item.model_dump() | {"station_id": body.station_id},
                    station=station,
                    received_at=item.received_at,
                )
                for item in body.measurements
            ]
            ingest_result = persist_measurements(
                tenant_id,
                body.station_id,
                normalized,
                _auth.subject,
                _tenant_scope(_auth),
            )
            history = load_measurements(
                tenant_id,
                body.station_id,
                limit=5000,
                scope_tenant_id=_tenant_scope(_auth),
            )
            topology = get_topology(tenant_id, body.station_id, scope_tenant_id=_tenant_scope(_auth))
            snapshot = estimate_station_state(
                station,
                history,
                evidence_class="observed",
                topology_version=topology["topology_hash"] if topology else None,
            )
            state_result = persist_state_estimate(
                tenant_id,
                body.station_id,
                snapshot,
                _auth.subject,
                _tenant_scope(_auth),
            )
            diagnosis = diagnose_twin(station, snapshot)
            diagnostic_result = persist_diagnostics(
                tenant_id,
                body.station_id,
                diagnosis,
                _auth.subject,
                _tenant_scope(_auth),
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return {
            "ingest": ingest_result,
            "state": snapshot,
            "state_persistence": state_result,
            "diagnostics": diagnosis,
            "diagnostic_persistence": diagnostic_result,
        }

    @router.post(
        "/digital-twin/simulations",
        response_model=TwinResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _run_twin_simulation(
        request: Request,
        body: TwinSimulationRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == body.station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}")
        tenant_id = _twin_tenant(_auth, body.tenant_id, station.tenant_id)
        evidence_class = body.evidence_class
        if not s.use_db and evidence_class not in {"synthetic", "replay"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="A database-backed evidence trail is required for shadow, observed, or field-qualified simulation.",
            )
        simulation = simulate_station(
            station,
            body.initial_state,
            body.schedule,
            interval_minutes=body.interval_minutes,
            evidence_class=evidence_class,
            random_seed=body.random_seed,
        )
        diagnosis = diagnose_twin(
            station,
            {
                "estimated_at": datetime.now(UTC).isoformat(),
                "trust_score": 1.0 if evidence_class == "field_qualified" else 0.75,
                "balance_residual_kw": 0,
                "transformer_headroom_kw": station.transformer_capacity_kw - simulation["metrics"]["max_grid_kw"],
                "autonomy_gate": {"allowed": evidence_class == "field_qualified", "reasons": []},
                "contract": simulation["contract"],
            },
            simulation,
        )
        if not s.use_db:
            return {"persisted": False, **simulation, "diagnostics": diagnosis}
        result = persist_simulation(
            tenant_id,
            body.station_id,
            simulation,
            body.model_dump(mode="json"),
            body.scenario_type,
            body.idempotency_key,
            _auth.subject,
            _tenant_scope(_auth),
        )
        persist_diagnostics(
            tenant_id,
            body.station_id,
            diagnosis,
            _auth.subject,
            _tenant_scope(_auth),
        )
        return {**result, "persisted": True, "diagnostics": diagnosis}

    @router.post(
        "/digital-twin/causal-studies",
        response_model=TwinResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _run_twin_causal_study(
        request: Request,
        body: TwinCausalStudyRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        if not s.use_db and body.evidence_class not in {"synthetic", "replay"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Observed causal claims require database-backed evidence.",
            )
        tenant_id = _twin_tenant(_auth, body.tenant_id, _auth.tenant_id or "t-001")
        result = estimate_causal_uplift(
            [item.model_dump(mode="json") for item in body.observations],
            evidence_class=body.evidence_class,
            estimand=body.estimand,
        )
        if not s.use_db:
            return {"persisted": False, **result}
        return {
            "persisted": True,
            **persist_causal_study(
                tenant_id,
                body.station_id,
                result,
                _auth.subject,
                _tenant_scope(_auth),
            ),
        }

    @router.post(
        "/digital-twin/calibrations",
        response_model=TwinResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _calibrate_twin(
        request: Request,
        body: TwinCalibrationRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == body.station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}")
        tenant_id = _twin_tenant(_auth, body.tenant_id, station.tenant_id)
        if not s.use_db and body.evidence_class not in {"synthetic", "replay"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Observed calibration requires database-backed evidence.",
            )
        try:
            result = calibrate_twin_model(
                body.predicted,
                body.observed,
                evidence_class=body.evidence_class,
                model_scope=body.model_scope,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        if not s.use_db:
            return {"persisted": False, **result}
        return {
            "persisted": True,
            **persist_calibration(
                tenant_id,
                body.station_id,
                body.model_version,
                result,
                _auth.subject,
                _tenant_scope(_auth),
            ),
        }

    @router.post("/digital-twin/trajectory-comparisons", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _compare_twin_trajectory(
        request: Request,
        body: TwinTrajectoryComparisonRequest,
        _auth: Principal = TwinReadDep,
    ) -> Any:
        try:
            return compare_trajectories(body.predicted, body.observed, fields=tuple(body.fields))
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/digital-twin/stations/{station_id}/maintenance", response_model=list[TwinResponse])
    @limiter.limit(rl)
    async def _twin_maintenance_queue(
        request: Request,
        station_id: str,
        tenant_id: str | None = Query(default=None),
        _auth: Principal = TwinReadDep,
    ) -> Any:
        _require_twin_database(s)
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {station_id}")
        target_tenant = _twin_tenant(_auth, tenant_id, station.tenant_id)
        return list_maintenance_actions(target_tenant, station_id, _tenant_scope(_auth))

    @router.post("/digital-twin/maintenance/{action_id}/transition", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _transition_twin_maintenance(
        request: Request,
        action_id: str,
        body: TwinMaintenanceTransitionRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        _require_twin_database(s)
        target_tenant = _twin_tenant(_auth, body.tenant_id, _auth.tenant_id)
        try:
            return transition_maintenance_action(
                target_tenant,
                action_id,
                body.status,
                _auth.subject,
                assigned_to=body.assigned_to,
                outcome=body.outcome,
                scope_tenant_id=_tenant_scope(_auth),
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/digital-twin/commissioning/fault-injection", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _run_twin_fault_injection(
        request: Request,
        body: TwinFaultInjectionRequest,
        _auth: Principal = TwinApproveDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == body.station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}")
        tenant_id = _twin_tenant(_auth, body.tenant_id, station.tenant_id)
        result = run_fault_injection_suite(station)
        if not s.use_db:
            return {"persisted": False, **result}
        evidence = record_qualification_evidence(
            tenant_id,
            body.station_id,
            datetime.now(UTC).date(),
            "fault_injection",
            result["qualified"],
            result,
            _auth.subject,
            _tenant_scope(_auth),
        )
        return {"persisted": True, **result, "qualification_evidence": evidence}

    @router.post("/digital-twin/optimization", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _run_twin_optimization(
        request: Request,
        body: TwinOptimizationRequest,
        _auth: Principal = TwinWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = next((item for item in repo.stations if item.id == body.station_id), None)
        if station is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}")
        evidence_class = "synthetic"
        qualification = {"ready": False, "blockers": ["database_required"]}
        if s.use_db:
            tenant_id = _twin_tenant(_auth, None, station.tenant_id)
            qualification = assess_field_qualification(
                load_qualification_evidence(tenant_id, body.station_id, _tenant_scope(_auth))
            )
            evidence_class = "field_qualified" if qualification["ready"] else "observed"
        snapshot_payload = build_twin_snapshot(repo, body.station_id, evidence_class=evidence_class)
        snapshot = snapshot_payload["state"]
        if body.mode == "auto" and evidence_class != "field_qualified":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"message": "Autonomous dispatch requires field qualification.", "qualification": qualification},
            )
        try:
            derated = twin_aware_station(station, snapshot)
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        twin_repo = replace(
            repo,
            stations=tuple(derated if item.id == station.id else item for item in repo.stations),
        )
        result = solve_dispatch_optimization(
            twin_repo,
            _tenant_scope(_auth),
            body.station_id,
            body.horizon_hours,
            body.objective,
        )
        return {
            **result,
            "mode": body.mode,
            "twin_state": snapshot,
            "qualification": qualification,
            "safety_gate": {
                "allowed": body.mode == "recommend" or qualification["ready"],
                "storage_power_derating": round(derated.storage_power_kw / max(1, station.storage_power_kw), 6),
                "storage_capacity_derating": round(
                    derated.storage_capacity_kwh / max(1, station.storage_capacity_kwh), 6
                ),
            },
        }

    @router.post(
        "/digital-twin/qualification/evidence",
        response_model=TwinResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _record_twin_qualification(
        request: Request,
        body: TwinQualificationEvidenceRequest,
        _auth: Principal = TwinApproveDep,
    ) -> Any:
        _require_twin_database(s)
        tenant_id = _twin_tenant(_auth, body.tenant_id, _auth.tenant_id or "t-001")
        result = record_qualification_evidence(
            tenant_id,
            body.station_id,
            body.evidence_date.date(),
            body.category,
            body.qualified,
            body.evidence,
            _auth.subject,
            _tenant_scope(_auth),
        )
        evidence = load_qualification_evidence(tenant_id, body.station_id, _tenant_scope(_auth))
        return {**result, "qualification": assess_field_qualification(evidence)}

    @router.get("/digital-twin/qualification", response_model=TwinResponse)
    @limiter.limit(rl)
    async def _twin_qualification(
        request: Request,
        station_id: str | None = Query(default=None),
        tenant_id: str | None = Query(default=None),
        _auth: Principal = TwinReadDep,
    ) -> Any:
        if not s.use_db:
            return {
                "ready": False,
                "qualified_shadow_days": 0,
                "blockers": ["database_required", "real_field_evidence_required"],
            }
        target_tenant = _twin_tenant(_auth, tenant_id, _auth.tenant_id or "t-001")
        evidence = load_qualification_evidence(target_tenant, station_id, _tenant_scope(_auth))
        return {**assess_field_qualification(evidence), "evidence_count": len(evidence)}

    @router.get("/dispatch", response_model=DispatchResponse)
    @limiter.limit(rl)
    async def _dispatch(request: Request, _auth: Principal = AuthDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        return build_dispatch(repo)

    @router.get("/vpp", response_model=VppResponse)
    @limiter.limit(rl)
    async def _vpp(request: Request, _auth: Principal = AuthDep) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        return build_vpp(repo)

    @router.get("/roi", response_model=RoiResponse)
    @limiter.limit(rl)
    async def _roi(
        request: Request,
        _auth: Principal = AuthDep,
        capacity_kwh: float = Query(default=1200.0, gt=0),
        power_kw: float = Query(default=600.0, gt=0),
        capex_per_kwh: float = Query(default=1150.0, gt=0),
        vpp: bool = Query(default=True),
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        return simulate_roi(repo, capacity_kwh, power_kw, capex_per_kwh, vpp)

    @router.get("/revenue-diagnostics", response_model=RevenueDiagnosticResponse)
    @limiter.limit(rl)
    async def _revenue_diagnostics(
        request: Request,
        _auth: Principal = AuthDep,
        station_id: str | None = Query(default=None),
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        try:
            return build_revenue_diagnostics(repo, station_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post(
        "/revenue-diagnostics/runs",
        response_model=RevenueProofRunResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _persist_revenue_diagnostic_run(
        request: Request,
        body: RevenueProofRunRequest,
        _auth: Principal = DispatchWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        try:
            diagnostics = build_revenue_diagnostics(repo, body.station_id)
            proof_id = persist_revenue_proof(
                _tenant_scope(_auth),
                body.station_id,
                diagnostics,
                body.created_by or _auth.subject,
                scope_tenant_id=_tenant_scope(_auth),
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"id": proof_id, **diagnostics}

    @router.get("/audit", response_model=AuditResponse)
    @limiter.limit(rl)
    async def _audit(
        request: Request,
        _auth: Principal = AuditReadDep,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        all_entries = list(repo.audit)
        page = all_entries[offset : offset + limit]
        return {
            "audit": [entry.__dict__ | {"timestamp": entry.timestamp.isoformat(timespec="seconds")} for entry in page],
            "meta": {"total": len(all_entries), "limit": limit, "offset": offset},
        }

    @router.post("/telemetry", response_model=TelemetryIngestResponse, status_code=status.HTTP_202_ACCEPTED)
    @limiter.limit(rl)
    async def _ingest_telemetry(
        request: Request, body: TelemetryIngestRequest, _auth: Principal = TelemetryWriteDep
    ) -> Any:
        try:
            payload = body.model_dump()
            payload["actor"] = _auth.subject
            return ingest_telemetry(payload, _tenant_scope(_auth))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.post(
        "/alerts/{alert_id}/acknowledge",
        response_model=AlertAcknowledgeResponse,
    )
    @limiter.limit(rl)
    async def _acknowledge_alert(
        request: Request,
        alert_id: str,
        body: AlertAcknowledgeRequest,
        _auth: Principal = DispatchWriteDep,
    ) -> Any:
        try:
            return acknowledge_alert(alert_id, body.actor or _auth.subject, _tenant_scope(_auth))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.post(
        "/dispatch/recommendations/generate",
        response_model=DispatchGenerateResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _generate_dispatch_recommendations(
        request: Request,
        body: DispatchGenerateRequest,
        _auth: Principal = DispatchWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        dispatch = build_dispatch(repo)
        recommendations = dispatch["recommendations"]
        try:
            generated = persist_dispatch_recommendations(recommendations, body.actor or _auth.subject)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"generated": generated, "recommendations": recommendations}

    @router.patch(
        "/dispatch/recommendations/{recommendation_id}",
        response_model=DispatchStatusResponse,
    )
    @limiter.limit(rl)
    async def _update_dispatch_recommendation(
        request: Request,
        recommendation_id: str,
        body: DispatchStatusRequest,
        _auth: Principal = DispatchWriteDep,
    ) -> Any:
        try:
            return update_dispatch_status(
                recommendation_id,
                body.status,
                body.actor or _auth.subject,
                body.reason,
                _tenant_scope(_auth),
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.post(
        "/roi/simulations",
        response_model=RoiSimulationPersistedResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _persist_roi_simulation(
        request: Request, body: RoiSimulationRequest, _auth: Principal = DispatchWriteDep
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        roi = simulate_roi(repo, body.capacity_kwh, body.power_kw, body.capex_per_kwh, body.vpp)
        try:
            simulation_id = persist_roi_simulation(body.station_id, roi, body.model_dump(), _tenant_scope(_auth))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"id": simulation_id, **roi}

    @router.post(
        "/protocols/{protocol}/messages",
        response_model=ProtocolMessageResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    @limiter.limit(rl)
    async def _protocol_message(
        request: Request,
        protocol: str,
        body: ProtocolMessageRequest,
        _auth: Principal = DeviceWriteDep,
    ) -> Any:
        normalized = normalize_protocol_message(protocol, body.message_type, body.payload)
        try:
            message = persist_protocol_message(
                _auth.tenant_id or "t-001",
                protocol,
                body.station_id,
                body.device_id,
                body.external_id,
                body.message_type,
                body.payload | {"normalized": normalized},
                scope_tenant_id=_tenant_scope(_auth),
                idempotency_key=body.idempotency_key,
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        telemetry_ingested = False
        if normalized.get("kind") == "telemetry":
            payload = {
                "station_id": body.station_id,
                "timestamp": normalized.get("timestamp"),
                "load_kw": normalized.get("load_kw", body.payload.get("load_kw", 0)),
                "pv_kw": normalized.get("pv_kw", body.payload.get("pv_kw", 0)),
                "grid_kw": normalized.get("grid_kw", body.payload.get("grid_kw", normalized.get("load_kw", 0))),
                "storage_power_kw": body.payload.get("storage_power_kw", 0),
                "storage_soc": normalized.get("storage_soc", body.payload.get("storage_soc", 0.5)),
                "connector_occupied": body.payload.get("connector_occupied", 0),
                "queue_length": body.payload.get("queue_length", 0),
                "sessions": body.payload.get("sessions", 0),
                "energy_kwh": normalized.get("energy_kwh", body.payload.get("energy_kwh", 0)),
                "revenue": body.payload.get("revenue", 0),
                "alert_count": body.payload.get("alert_count", 0),
                "idempotency_key": body.idempotency_key
                or f"{protocol}:{body.external_id}:{body.message_type}:{normalized.get('timestamp')}",
                "actor": _auth.subject,
            }
            try:
                ingest_telemetry(payload, _tenant_scope(_auth))
            except KeyError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
            except PermissionError as exc:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            telemetry_ingested = True
        return {
            "id": message["id"],
            "protocol": protocol,
            "station_id": body.station_id,
            "device_id": message["device_id"],
            "status": "accepted",
            "telemetry_ingested": telemetry_ingested,
            "task_id": None,
        }

    @router.post("/tasks", response_model=TaskResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit(rl)
    async def _create_task(request: Request, body: TaskCreateRequest, _auth: Principal = TaskWriteDep) -> Any:
        try:
            return enqueue_task(
                _auth.tenant_id or "t-001",
                body.station_id,
                body.device_id,
                body.task_type,
                body.payload,
                body.priority,
                body.idempotency_key,
                scope_tenant_id=_tenant_scope(_auth),
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post("/tasks/claim", response_model=TaskClaimResponse)
    @limiter.limit(rl)
    async def _claim_task(request: Request, body: TaskClaimRequest, _auth: Principal = TaskWriteDep) -> Any:
        try:
            task = claim_next_task(
                _worker_tenant_scope(_auth),
                body.worker_id,
                body.task_types,
                body.lease_seconds,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        return {"task": task}

    @router.post("/tasks/reap-expired", response_model=TaskReapResponse)
    @limiter.limit(rl)
    async def _reap_tasks(request: Request, body: TaskReapRequest, _auth: Principal = TaskWriteDep) -> Any:
        try:
            return reap_expired_tasks(_worker_tenant_scope(_auth), body.actor or _auth.subject)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.post("/tasks/{task_id}/complete", response_model=TaskResponse)
    @limiter.limit(rl)
    async def _complete_task(
        request: Request,
        task_id: str,
        body: TaskCompleteRequest,
        _auth: Principal = TaskWriteDep,
    ) -> Any:
        try:
            return complete_task(
                task_id,
                _worker_tenant_scope(_auth),
                body.worker_id,
                body.status,
                body.result,
                body.error,
                body.retry_delay_seconds,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.post(
        "/dispatch/recommendations/{recommendation_id}/approval",
        response_model=DispatchApprovalResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _request_dispatch_approval(
        request: Request,
        recommendation_id: str,
        body: DispatchApprovalRequest,
        _auth: Principal = DispatchWriteDep,
    ) -> Any:
        try:
            return request_dispatch_approval(recommendation_id, _auth, body.reason)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post(
        "/dispatch/recommendations/{recommendation_id}/approve",
        response_model=DispatchApprovalResponse,
    )
    @limiter.limit(rl)
    async def _approve_dispatch(
        request: Request,
        recommendation_id: str,
        body: DispatchApprovalRequest,
        _auth: Principal = DispatchApproveDep,
    ) -> Any:
        try:
            return review_dispatch_approval(recommendation_id, _auth, True, body.reason)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post(
        "/dispatch/recommendations/{recommendation_id}/reject",
        response_model=DispatchApprovalResponse,
    )
    @limiter.limit(rl)
    async def _reject_dispatch(
        request: Request,
        recommendation_id: str,
        body: DispatchApprovalRequest,
        _auth: Principal = DispatchApproveDep,
    ) -> Any:
        try:
            return review_dispatch_approval(recommendation_id, _auth, False, body.reason)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post("/edge/receipts", response_model=EdgeReceiptResponse, status_code=status.HTTP_202_ACCEPTED)
    @limiter.limit(rl)
    async def _edge_receipt(request: Request, body: EdgeReceiptRequest, _auth: Principal = DeviceWriteDep) -> Any:
        try:
            return record_edge_receipt(
                _auth.tenant_id or "t-001",
                body.task_id,
                body.station_id,
                body.device_id,
                body.status,
                body.payload,
                scope_tenant_id=_tenant_scope(_auth),
                idempotency_key=body.idempotency_key,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post("/optimization/runs", response_model=OptimizationRunResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit(rl)
    async def _optimization_run(
        request: Request, body: OptimizationRunRequest, _auth: Principal = DispatchWriteDep
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        try:
            result = solve_dispatch_optimization(
                repo, _tenant_scope(_auth), body.station_id, body.horizon_hours, body.objective
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        persist_tenant_id = _auth.tenant_id or "t-001"
        if body.station_id is not None:
            station = next((item for item in repo.stations if item.id == body.station_id), None)
            if station is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown station_id: {body.station_id}"
                )
            persist_tenant_id = station.tenant_id
        run_id = persist_optimization_run(
            persist_tenant_id,
            body.station_id or "portfolio",
            body.objective,
            body.horizon_hours,
            result["solver"],
            result["objective_value"],
            result["inputs"],
            {"dispatch_plan": result["dispatch_plan"], "constraints": result["constraints"]},
            _auth.subject,
            scope_tenant_id=_tenant_scope(_auth),
        )
        return {"id": run_id, **result}

    @router.get("/ems/capabilities", response_model=EmsResponse)
    @limiter.limit(rl)
    async def _ems_capabilities(request: Request, _auth: Principal = EmsReadDep) -> Any:
        foundation_model_error = None
        try:
            foundation_model_configured = FoundationForecastClient.from_environment() is not None
        except RuntimeError as exc:
            foundation_model_configured = False
            foundation_model_error = str(exc)
        return {
            "algorithms": {**EMS_ALGORITHMS, **GRID_EMS_ALGORITHMS},
            "foundation_model_configured": foundation_model_configured,
            "foundation_model_configuration_valid": foundation_model_error is None,
            "foundation_model_configuration_error": foundation_model_error,
            "persistence_enabled": s.use_db,
            "control_mode": "recommendation_and_shadow_only",
            "field_control_available": False,
            "field_control_requirements": [
                "approved dispatch recommendation",
                "active digital-twin qualification",
                "device command task",
                "edge gateway receipt",
            ],
            "network_certificate_scope": "radial phase-decoupled LinDistFlow; external AC study required",
        }

    @router.get("/ems/evidence", response_model=EmsResponse)
    @limiter.limit(rl)
    async def _ems_evidence(
        request: Request,
        tenant_id: str | None = Query(default=None),
        evidence_type: str | None = Query(default=None),
        station_id: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=1000),
        _auth: Principal = EmsReadDep,
    ) -> Any:
        target_tenant = _ems_target_tenant(_auth, tenant_id)
        supported_evidence_types = {
            "forecast",
            "dispatch",
            "network_projection",
            "portfolio_coordination",
            "offline_policy_evaluation",
            "flexibility_envelope",
            "security_constrained_dispatch",
            "network_security_assessment",
            "battery_degradation_assessment",
        }
        if evidence_type is not None and evidence_type not in supported_evidence_types:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="Unsupported EMS evidence_type.",
            )
        if not s.use_db:
            return {"runs": [], "persistence_enabled": False}
        try:
            runs = list_ems_evidence(
                target_tenant,
                evidence_type=evidence_type,
                station_id=station_id,
                limit=limit,
                scope_tenant_id=_tenant_scope(_auth),
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        return {"runs": runs, "persistence_enabled": True}

    @router.post("/ems/forecasts", response_model=EmsResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit("20/minute")
    async def _ems_forecast(
        request: Request,
        body: EmsForecastRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = _ems_station(repo, body.station_id)
        points = sorted(repo.station_points(station.id), key=lambda item: item.timestamp)
        history = body.history_kw or [float(point.grid_kw) for point in points]
        evidence_class = "replay" if body.history_kw is not None else "observed" if s.use_db else "synthetic"
        external_predictions = None
        external_metadata = None
        if body.use_foundation_model:
            try:
                client = FoundationForecastClient.from_environment()
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            if client is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The time-series foundation model is not configured.",
                )
            try:
                external = client.forecast(history, body.horizon, body.interval_minutes)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            external_predictions = {external["name"]: external["p50"]}
            external_metadata = {external["name"]: {"model_version": external["model_version"]}}
        try:
            result = calibrated_ensemble_forecast(
                history,
                horizon=body.horizon,
                interval_minutes=body.interval_minutes,
                coverage=body.coverage,
                scenario_count=body.scenario_count,
                seed=body.random_seed,
                external_predictions=external_predictions,
                external_metadata=external_metadata,
                evidence_class=evidence_class,
            )
            return _ems_result_with_evidence(
                _auth,
                station.tenant_id,
                station.id,
                "forecast",
                evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/ems/dispatch-runs", response_model=EmsResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit("12/minute")
    async def _ems_dispatch(
        request: Request,
        body: EmsDispatchRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = _ems_station(repo, body.station_id)
        points = sorted(repo.station_points(station.id), key=lambda item: item.timestamp)
        if not points and body.history_kw is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Station telemetry history is empty.")
        history = body.history_kw or [float(point.grid_kw) for point in points]
        evidence_class = "replay" if body.history_kw is not None else "observed" if s.use_db else "synthetic"
        external_predictions = None
        external_metadata = None
        if body.use_foundation_model:
            try:
                client = FoundationForecastClient.from_environment()
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            if client is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The time-series foundation model is not configured.",
                )
            try:
                external = client.forecast(history, body.horizon, body.interval_minutes)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            external_predictions = {external["name"]: external["p50"]}
            external_metadata = {external["name"]: {"model_version": external["model_version"]}}
        try:
            forecast = calibrated_ensemble_forecast(
                history,
                horizon=body.horizon,
                interval_minutes=body.interval_minutes,
                coverage=body.coverage,
                scenario_count=body.scenario_count,
                seed=body.random_seed,
                external_predictions=external_predictions,
                external_metadata=external_metadata,
                evidence_class=evidence_class,
            )
            tariff = repo.tariff_for(station)
            prices = body.prices or [
                float(tariff.price_at(datetime.fromisoformat(row["at"]).hour)) for row in forecast["rows"]
            ]
            initial_soc = (
                body.initial_soc if body.initial_soc is not None else float(points[-1].storage_soc) if points else 0.5
            )
            demand_charge = (
                body.demand_charge_per_kw
                if body.demand_charge_per_kw is not None
                else float(tariff.demand_charge_per_kw_month) / 30
            )
            result = solve_distributionally_robust_mpc(
                station,
                forecast,
                prices=prices,
                initial_soc=initial_soc,
                soh=body.soh,
                temperature_c=body.temperature_c,
                risk_alpha=body.risk_alpha,
                risk_weight=body.risk_weight,
                demand_charge_per_kw=demand_charge,
                reserve_soc=body.reserve_soc,
            )
            result["evidence_class"] = evidence_class
            result["forecast_evidence"] = forecast
            return _ems_result_with_evidence(
                _auth,
                station.tenant_id,
                station.id,
                "dispatch",
                evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/ems/flexibility-envelopes", response_model=EmsResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit("20/minute")
    async def _ems_flexibility_envelope(
        request: Request,
        body: EmsFlexibilityRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = _ems_station(repo, body.station_id)
        try:
            result = aggregate_ev_flexibility(
                [item.model_dump() for item in body.sessions],
                horizon=body.horizon,
                interval_minutes=body.interval_minutes,
            )
            result["evidence_class"] = body.evidence_class
            return _ems_result_with_evidence(
                _auth,
                station.tenant_id,
                station.id,
                "flexibility_envelope",
                body.evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post(
        "/ems/security-constrained-dispatch-runs",
        response_model=EmsResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit("8/minute")
    async def _ems_security_constrained_dispatch(
        request: Request,
        body: EmsSecureDispatchRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = _ems_station(repo, body.station_id)
        points = sorted(repo.station_points(station.id), key=lambda item: item.timestamp)
        if not points and body.history_kw is None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Station telemetry history is empty.")
        history = body.history_kw or [float(point.grid_kw) for point in points]
        evidence_class = "replay" if body.history_kw is not None else "observed" if s.use_db else "synthetic"
        external_predictions = None
        external_metadata = None
        if body.use_foundation_model:
            try:
                foundation_client = FoundationForecastClient.from_environment()
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            if foundation_client is None:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="The time-series foundation model is not configured.",
                )
            try:
                external = foundation_client.forecast(history, body.horizon, body.interval_minutes)
            except RuntimeError as exc:
                raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
            external_predictions = {external["name"]: external["p50"]}
            external_metadata = {external["name"]: {"model_version": external["model_version"]}}
        try:
            forecast = calibrated_ensemble_forecast(
                history,
                horizon=body.horizon,
                interval_minutes=body.interval_minutes,
                coverage=body.coverage,
                scenario_count=body.scenario_count,
                seed=body.random_seed,
                external_predictions=external_predictions,
                external_metadata=external_metadata,
                evidence_class=evidence_class,
            )
            initial_soc = (
                body.initial_soc if body.initial_soc is not None else float(points[-1].storage_soc) if points else 0.5
            )
            result = solve_secure_rolling_dispatch(
                station,
                forecast,
                [item.model_dump() for item in body.sessions],
                prices=body.prices,
                initial_soc=initial_soc,
                soh=body.soh,
                temperature_c=body.temperature_c,
                carbon_intensity_kg_per_kwh=body.carbon_intensity_kg_per_kwh,
                carbon_price_per_kg=body.carbon_price_per_kg,
                reserve_up_prices=body.reserve_up_prices,
                reserve_down_prices=body.reserve_down_prices,
                reserve_duration_minutes=body.reserve_duration_minutes,
                contingencies=body.contingencies,
                risk_alpha=body.risk_alpha,
                risk_weight=body.risk_weight,
                demand_charge_per_kw=body.demand_charge_per_kw,
                reserve_soc=body.reserve_soc,
                allow_service_restoration=body.allow_service_restoration,
            )
            result["evidence_class"] = evidence_class
            result["forecast_evidence"] = forecast
            return _ems_result_with_evidence(
                _auth,
                station.tenant_id,
                station.id,
                "security_constrained_dispatch",
                evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/ems/network-security-assessments",
        response_model=EmsResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit("12/minute")
    async def _ems_network_security_assessment(
        request: Request,
        body: EmsNetworkSecurityRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        target_tenant = _ems_target_tenant(_auth, body.tenant_id)
        repo = load_repository_from_db(target_tenant)
        station_ids = {station.id for station in repo.stations}
        proposal_ids = {
            str(item.get("station_id")) for interval in body.intervals for item in (interval.get("proposals") or [])
        }
        if body.station_id is not None and body.station_id not in station_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown tenant station_id: {body.station_id}",
            )
        if not proposal_ids <= station_ids:
            unknown = sorted(proposal_ids - station_ids)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown tenant station_ids: {', '.join(unknown)}",
            )
        try:
            result = assess_n_minus_one_security(body.network, body.intervals, body.contingencies)
            result["evidence_class"] = body.evidence_class
            return _ems_result_with_evidence(
                _auth,
                target_tenant,
                body.station_id,
                "network_security_assessment",
                body.evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/ems/battery-degradation-assessments",
        response_model=EmsResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit("20/minute")
    async def _ems_battery_degradation_assessment(
        request: Request,
        body: EmsBatteryDegradationRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        repo = load_repository_from_db(_tenant_scope(_auth))
        station = _ems_station(repo, body.station_id)
        replacement_cost = (
            body.replacement_cost if body.replacement_cost is not None else float(station.storage_capacity_kwh) * 1450
        )
        try:
            result = estimate_battery_degradation(
                body.soc_series,
                interval_minutes=body.interval_minutes,
                storage_capacity_kwh=float(station.storage_capacity_kwh),
                replacement_cost=replacement_cost,
                soh=body.soh,
                temperature_c=body.temperature_c,
            )
            result["evidence_class"] = body.evidence_class
            return _ems_result_with_evidence(
                _auth,
                station.tenant_id,
                station.id,
                "battery_degradation_assessment",
                body.evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/ems/network-projections", response_model=EmsResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit("20/minute")
    async def _ems_network_projection(
        request: Request,
        body: EmsNetworkProjectionRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        target_tenant = _ems_target_tenant(_auth, body.tenant_id)
        repo = load_repository_from_db(target_tenant)
        station_ids = {station.id for station in repo.stations}
        proposal_ids = {str(item.get("station_id")) for item in body.proposals}
        if body.station_id is not None and body.station_id not in station_ids:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown tenant station_id: {body.station_id}",
            )
        if not proposal_ids <= station_ids:
            unknown = sorted(proposal_ids - station_ids)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown tenant station_ids: {', '.join(unknown)}",
            )
        try:
            result = project_three_phase_distflow(body.network, body.proposals)
            result["evidence_class"] = body.evidence_class
            return _ems_result_with_evidence(
                _auth,
                target_tenant,
                body.station_id,
                "network_projection",
                body.evidence_class,
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/ems/portfolio-coordination", response_model=EmsResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit("20/minute")
    async def _ems_portfolio_coordination(
        request: Request,
        body: EmsCoordinationRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        target_tenant = _ems_target_tenant(_auth, body.tenant_id)
        repo = load_repository_from_db(target_tenant)
        station_ids = {station.id for station in repo.stations}
        resource_ids = {str(item.get("station_id")) for item in body.resources}
        if not resource_ids <= station_ids:
            unknown = sorted(resource_ids - station_ids)
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown tenant station_ids: {', '.join(unknown)}",
            )
        try:
            result = coordinate_portfolio_admm(
                body.resources,
                body.target_kw,
                rho=body.rho,
                tolerance=body.tolerance,
                max_iterations=body.max_iterations,
            )
            result["evidence_class"] = "replay"
            result["execution_authorized"] = False
            result["control_boundary"] = "allocation recommendation only"
            return _ems_result_with_evidence(
                _auth,
                target_tenant,
                None,
                "portfolio_coordination",
                "replay",
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/ems/offline-policy/evaluations", response_model=EmsResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit("6/minute")
    async def _ems_offline_policy(
        request: Request,
        body: EmsOfflinePolicyRequest,
        _auth: Principal = EmsWriteDep,
    ) -> Any:
        target_tenant = _ems_target_tenant(_auth, body.tenant_id)
        if body.station_id is not None:
            repo = load_repository_from_db(target_tenant)
            _ems_station(repo, body.station_id)
        try:
            model = train_conservative_fitted_q(
                body.transitions,
                body.actions_kw,
                conservative_penalty=body.conservative_penalty,
            )
            evaluation = evaluate_offline_policy(
                model,
                body.evaluation_state,
                body.safety_constraints,
                max_mahalanobis=body.max_mahalanobis,
            )
            result = {
                **evaluation,
                "algorithm": model["algorithm"],
                "input_hash": evaluation["input_hash"],
                "evidence_class": "shadow",
                "model": model,
                "execution_authorized": False,
            }
            return _ems_result_with_evidence(
                _auth,
                target_tenant,
                body.station_id,
                "offline_policy_evaluation",
                "shadow",
                body.model_dump(),
                result,
                body.idempotency_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/models", response_model=list[ModelResponse])
    @limiter.limit(rl)
    async def _models(
        request: Request,
        tenant_id: str | None = Query(default=None),
        _auth: Principal = ModelWriteDep,
    ) -> Any:
        target_tenant = tenant_id or _auth.tenant_id
        if target_tenant is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="tenant_id is required")
        try:
            return list_models(target_tenant, _tenant_scope(_auth))
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post("/models", response_model=ModelResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit(rl)
    async def _register_model(request: Request, body: ModelRegisterRequest, _auth: Principal = ModelWriteDep) -> Any:
        target_tenant = body.tenant_id or _auth.tenant_id
        if target_tenant is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="tenant_id is required")
        try:
            return register_model(
                target_tenant, body.model_dump(exclude={"tenant_id"}), _auth.subject, _tenant_scope(_auth)
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post(
        "/models/{model_id}/evaluations",
        response_model=ModelEvaluationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _evaluate_model(
        request: Request,
        model_id: str,
        body: ModelEvaluationRequest,
        tenant_id: str | None = Query(default=None),
        _auth: Principal = ModelWriteDep,
    ) -> Any:
        target_tenant = tenant_id or _auth.tenant_id
        if target_tenant is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="tenant_id is required")
        try:
            return evaluate_model(model_id, target_tenant, body.model_dump(), _auth.subject, _tenant_scope(_auth))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/models/{model_id}/promote", response_model=ModelResponse)
    @limiter.limit(rl)
    async def _promote_model(
        request: Request,
        model_id: str,
        tenant_id: str | None = Query(default=None),
        _auth: Principal = ModelApproveDep,
    ) -> Any:
        target_tenant = tenant_id or _auth.tenant_id
        if target_tenant is None:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="tenant_id is required")
        try:
            return promote_model(model_id, target_tenant, _auth.subject, _tenant_scope(_auth))
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/vpp/settlements", response_model=VppSettlementResponse, status_code=status.HTTP_201_CREATED)
    @limiter.limit(rl)
    async def _vpp_settlement(request: Request, body: VppSettlementRequest, _auth: Principal = VppSettleDep) -> Any:
        try:
            return settle_vpp_event(
                body.event_id,
                body.baseline_kw,
                body.delivered_kw,
                body.settled_by or _auth.subject,
                body.evidence,
                scope_tenant_id=_tenant_scope(_auth),
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.get("/vpp/trading/dashboard", response_model=VppTradingDashboardResponse)
    @limiter.limit(rl)
    async def _vpp_trading_dashboard(request: Request, _auth: Principal = AuthDep) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        if not s.use_db:
            return {
                "generated_at": datetime.now(UTC),
                "connection": {
                    "id": "local-sandbox",
                    "market_code": "LOCAL-SANDBOX",
                    "participant_id": tenant_id,
                    "adapter": "sandbox",
                    "mode": "sandbox",
                    "enabled": False,
                },
                "risk_policy": {
                    "id": "local-read-only",
                    "name": "Local read-only guardrails",
                    "version": 1,
                    "auto_trade_enabled": False,
                    "auto_dispatch_enabled": False,
                },
                "circuit_breaker": {"state": "closed", "reason": "local_read_only"},
                "metrics": {
                    "submitted_kw_24h": 0,
                    "filled_kw_24h": 0,
                    "failed_orders_24h": 0,
                    "risk_rejections_24h": 0,
                    "open_orders": 0,
                    "committed_energy_kwh": 0,
                },
                "orders": [],
                "automation_runs": [],
                "settlements": [],
            }
        try:
            return trading_dashboard(tenant_id)
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.get("/vpp/trading/live-readiness")
    @limiter.limit(rl)
    async def _vpp_live_readiness(request: Request, _auth: Principal = AuthDep) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        if not s.use_db:
            return {"ready": False, "blockers": ["database_required"], "shadow_qualified_days": 0}
        return live_market_readiness(tenant_id)

    @router.post(
        "/vpp/trading/automation/run",
        response_model=VppAutomationRunResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    @limiter.limit("12/minute")
    async def _run_vpp_automation(
        request: Request,
        body: VppAutomationRunRequest,
        _auth: Principal = VppTradeDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return run_automation_cycle(
                tenant_id,
                trigger_source=body.trigger_source,
                actor=_auth.subject,
                max_orders_per_cycle=s.vpp_max_orders_per_cycle,
            )
        except (ValueError, KeyError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        except RuntimeError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/trades",
        response_model=MarketTradeResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _record_vpp_trade(
        request: Request,
        body: MarketTradeWebhookRequest,
        _auth: Principal = VppTradeDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return record_trade_fill(
                tenant_id,
                body.order_id,
                body.market_trade_id,
                body.quantity_kw,
                body.price_per_kwh,
                body.traded_at,
                _auth.subject,
                body.payload,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/market-webhook",
        response_model=MarketTradeResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    @limiter.limit(rl)
    async def _market_trade_webhook(
        request: Request,
        x_chargeopt_tenant: str = Header(),
        x_chargeopt_timestamp: str = Header(),
        x_chargeopt_signature: str = Header(),
    ) -> Any:
        raw_body = await request.body()
        if not s.market_webhook_secret or not verify_market_webhook(
            raw_body,
            x_chargeopt_timestamp,
            x_chargeopt_signature,
            s.market_webhook_secret,
        ):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid market webhook signature.")
        try:
            body = MarketTradeWebhookRequest.model_validate_json(raw_body)
            return record_trade_fill(
                x_chargeopt_tenant,
                body.order_id,
                body.market_trade_id,
                body.quantity_kw,
                body.price_per_kwh,
                body.traded_at,
                "market-webhook",
                body.payload,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/meter-intervals",
        response_model=VppMeterIntervalResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    @limiter.limit(rl)
    async def _vpp_meter_interval(
        request: Request,
        body: VppMeterIntervalRequest,
        _auth: Principal = VppMeterWriteDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        payload = body.model_dump()
        payload["payload"] = body.payload
        try:
            return ingest_meter_interval(tenant_id, payload, _auth.subject)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches",
        response_model=VppSettlementBatchResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @limiter.limit(rl)
    async def _vpp_settlement_batch(
        request: Request,
        body: VppSettlementBatchRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        if body.period_end <= body.period_start:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="period_end must be later than period_start"
            )
        try:
            return create_settlement_batch(
                tenant_id,
                body.market_code,
                body.period_start,
                body.period_end,
                _auth.subject,
                imbalance_price_per_kwh=body.imbalance_price_per_kwh,
                penalty_rate=body.penalty_rate,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches/{batch_id}/approve",
        response_model=SettlementActionResponse,
    )
    @limiter.limit(rl)
    async def _approve_vpp_settlement(
        request: Request,
        batch_id: str,
        body: SettlementApprovalRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return approve_settlement_batch(tenant_id, batch_id, _auth.subject, body.reason)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches/{batch_id}/dispute",
        response_model=SettlementActionResponse,
    )
    @limiter.limit(rl)
    async def _dispute_vpp_settlement(
        request: Request,
        batch_id: str,
        body: SettlementDisputeRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return dispute_settlement_batch(tenant_id, batch_id, _auth.subject, body.reason)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches/{batch_id}/resolve-dispute",
        response_model=SettlementActionResponse,
    )
    @limiter.limit(rl)
    async def _resolve_vpp_settlement_dispute(
        request: Request,
        batch_id: str,
        body: SettlementDisputeResolutionRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return resolve_settlement_dispute(
                tenant_id, batch_id, _auth.subject, body.resolution, accepted=body.accepted
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches/{batch_id}/export",
        response_model=SettlementActionResponse,
    )
    @limiter.limit(rl)
    async def _export_vpp_settlement(
        request: Request,
        batch_id: str,
        body: SettlementExportRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return export_settlement_batch(tenant_id, batch_id, _auth.subject, body.format, body.destination)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches/{batch_id}/paid",
        response_model=SettlementActionResponse,
    )
    @limiter.limit(rl)
    async def _mark_vpp_settlement_paid(
        request: Request,
        batch_id: str,
        body: SettlementPaymentRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return mark_settlement_paid(tenant_id, batch_id, _auth.subject, body.payment_reference)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post(
        "/vpp/trading/settlement-batches/{batch_id}/reverse",
        response_model=SettlementActionResponse,
    )
    @limiter.limit(rl)
    async def _reverse_vpp_settlement(
        request: Request,
        batch_id: str,
        body: SettlementReversalRequest,
        _auth: Principal = VppSettleDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        try:
            return reverse_settlement_batch(tenant_id, batch_id, _auth.subject, body.reason, body.external_reference)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/vpp/trading/circuit-breaker", response_model=CircuitBreakerResponse)
    @limiter.limit("12/minute")
    async def _set_vpp_circuit_breaker(
        request: Request,
        body: CircuitBreakerRequest,
        _auth: Principal = VppOperateDep,
    ) -> Any:
        tenant_id = _auth.tenant_id
        if tenant_id is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="A tenant context is required."
            )
        return set_circuit_breaker(tenant_id, body.state, body.reason, _auth.subject, body.reset_after)

    return router


app = create_app()
