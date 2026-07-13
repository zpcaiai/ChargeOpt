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

from .analytics import build_dispatch, build_overview, build_vpp, simulate_roi, station_detail, station_summary
from .auth import ROLE_PERMISSIONS, Principal, development_principal, has_permission, static_api_key_principal
from .config import get_settings
from .db import close_pool, health_check, init_pool
from .logging_config import configure_logging
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
    DispatchApprovalRequest,
    DispatchApprovalResponse,
    DispatchGenerateRequest,
    DispatchGenerateResponse,
    DispatchResponse,
    DispatchStatusRequest,
    DispatchStatusResponse,
    EdgeReceiptRequest,
    EdgeReceiptResponse,
    HealthResponse,
    LoginRequest,
    LoginResponse,
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
    VppResponse,
    VppSettlementRequest,
    VppSettlementResponse,
)

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
        logger.exception("Unhandled server error", path=str(request.url.path))
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
        except Exception:
            logger.exception("Unhandled exception")
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
AuditReadDep = Depends(require_permission("audit:read"))


def _tenant_scope(principal: Principal) -> str | None:
    return None if principal.is_platform_admin else principal.tenant_id


def _worker_tenant_scope(principal: Principal) -> str:
    return "*" if principal.is_platform_admin else principal.tenant_id or "t-001"


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


# ---------------------------------------------------------------------------
# Versioned API router  (/api/v1/...)
# ---------------------------------------------------------------------------


def _build_v1_router(s: Any) -> APIRouter:
    router = APIRouter(tags=["v1"])
    rl = f"{s.rate_limit_per_minute}/minute"

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
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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
            )
        except PermissionError as exc:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
        except (KeyError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
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

    return router


app = create_app()
