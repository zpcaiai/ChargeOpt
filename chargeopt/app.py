"""ChargeOpt OS – production FastAPI application.

Provides:
- All analytics API endpoints
- API-Key authentication (optional when api_key is not set)
- CORS, request-ID propagation, structured access logging
- Rate limiting via slowapi
- /health and /metrics (Prometheus) endpoints
- Graceful startup/shutdown with connection-pool lifecycle
"""

from __future__ import annotations

import hmac
import time
import uuid
from contextlib import asynccontextmanager
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, Response, Security, status
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
from .config import get_settings
from .db import close_pool, health_check, init_pool
from .logging_config import configure_logging
from .repository import load_repository_from_db
from .schemas import (
    AuditResponse,
    DispatchResponse,
    HealthResponse,
    OverviewResponse,
    ProblemDetail,
    RoiResponse,
    StationDetailResponse,
    StationListResponse,
    VppResponse,
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
        allow_methods=["GET", "OPTIONS"],
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


def require_api_key(api_key: str | None = Security(_api_key_header)) -> None:
    """Dependency: reject requests without a valid API key when one is configured."""
    configured = get_settings().api_key
    if configured is None:
        return  # Security disabled – development mode
    # Use constant-time comparison to prevent timing-oracle attacks.
    provided = api_key or ""
    if not hmac.compare_digest(provided.encode(), configured.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


AuthDep = Annotated[None, Depends(require_api_key)]

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

    @router.get("/overview", response_model=OverviewResponse)
    @limiter.limit(rl)
    async def _overview(request: Request, _auth: AuthDep) -> Any:
        repo = load_repository_from_db()
        return build_overview(repo)

    @router.get("/stations", response_model=StationListResponse)
    @limiter.limit(rl)
    async def _stations(request: Request, _auth: AuthDep) -> Any:
        repo = load_repository_from_db()
        return {"stations": [station_summary(repo, station) for station in repo.stations]}

    @router.get("/stations/{station_id}", response_model=StationDetailResponse)
    @limiter.limit(rl)
    async def _station_detail(request: Request, station_id: str, _auth: AuthDep) -> Any:
        repo = load_repository_from_db()
        try:
            return station_detail(repo, station_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/dispatch", response_model=DispatchResponse)
    @limiter.limit(rl)
    async def _dispatch(request: Request, _auth: AuthDep) -> Any:
        repo = load_repository_from_db()
        return build_dispatch(repo)

    @router.get("/vpp", response_model=VppResponse)
    @limiter.limit(rl)
    async def _vpp(request: Request, _auth: AuthDep) -> Any:
        repo = load_repository_from_db()
        return build_vpp(repo)

    @router.get("/roi", response_model=RoiResponse)
    @limiter.limit(rl)
    async def _roi(
        request: Request,
        _auth: AuthDep,
        capacity_kwh: float = Query(default=1200.0, gt=0),
        power_kw: float = Query(default=600.0, gt=0),
        capex_per_kwh: float = Query(default=1150.0, gt=0),
        vpp: bool = Query(default=True),
    ) -> Any:
        repo = load_repository_from_db()
        return simulate_roi(repo, capacity_kwh, power_kw, capex_per_kwh, vpp)

    @router.get("/audit", response_model=AuditResponse)
    @limiter.limit(rl)
    async def _audit(
        request: Request,
        _auth: AuthDep,
        limit: int = Query(default=50, ge=1, le=200),
        offset: int = Query(default=0, ge=0),
    ) -> Any:
        repo = load_repository_from_db()
        all_entries = list(repo.audit)
        page = all_entries[offset : offset + limit]
        return {
            "audit": [entry.__dict__ | {"timestamp": entry.timestamp.isoformat(timespec="seconds")} for entry in page],
            "meta": {"total": len(all_entries), "limit": limit, "offset": offset},
        }

    return router


