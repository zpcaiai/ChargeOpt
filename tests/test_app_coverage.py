"""Additional app.py coverage tests.

Covers:
- Lifespan startup/shutdown
- 500 error handler
- Rate-limit handler
- HSTS header in production mode
- Observability middleware exception path
- _update_gauges exception branches
- /health unhealthy (DB raises)
- /metrics disabled
- __main__.main()
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient


# ---------------------------------------------------------------------------
# Helper to build an app with specific env settings
# ---------------------------------------------------------------------------


def _make_client(extra_env: dict):
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    for k, v in extra_env.items():
        os.environ[k] = v
    from chargeopt.app import create_app

    app = create_app()
    cfg.get_settings.cache_clear()
    for k in extra_env:
        os.environ.pop(k, None)
    return app


# ---------------------------------------------------------------------------
# Lifespan  (startup + shutdown)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lifespan_calls_init_and_close_pool():
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()

    with patch("chargeopt.app.init_pool") as mock_init, patch("chargeopt.app.close_pool") as mock_close:
        from chargeopt.app import create_app
        from contextlib import asynccontextmanager
        import anyio

        app = create_app()
        # Drive the full lifespan (startup → yield → shutdown)
        async with app.router.lifespan_context(app):
            pass

        mock_init.assert_called_once()
        mock_close.assert_called_once()

    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# 500 error handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_500_handler_returns_problem_detail():
    from chargeopt.app import create_app
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    app = create_app()

    @app.get("/test-500-trigger")
    async def _boom():
        raise RuntimeError("forced error")

    # raise_app_exceptions=False lets httpx return the 500 response instead of raising
    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/test-500-trigger")

    assert resp.status_code == 500
    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Rate-limit handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rate_limit_handler():
    """Verify the RateLimitExceeded exception handler returns 429 with expected body."""
    from slowapi.errors import RateLimitExceeded
    from slowapi.wrappers import Limit
    from chargeopt.app import create_app
    from chargeopt import config as cfg
    from starlette.datastructures import URL
    from unittest.mock import MagicMock

    cfg.get_settings.cache_clear()
    app = create_app()

    # Build a minimal mock Limit so RateLimitExceeded can be constructed
    mock_limit = MagicMock()
    mock_limit.error_message = None  # slowapi uses this in __str__
    exc = RateLimitExceeded(mock_limit)

    # Find the registered handler for RateLimitExceeded
    handler = app.exception_handlers.get(RateLimitExceeded)
    assert handler is not None, "RateLimitExceeded handler not registered"

    mock_request = MagicMock()
    mock_request.url = URL("http://test/some-path")
    response = await handler(mock_request, exc)

    assert response.status_code == 429
    import json
    body = json.loads(response.body)
    assert body["error"] == "rate_limit_exceeded"
    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# HSTS header in production mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hsts_header_in_production():
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    os.environ["ENVIRONMENT"] = "production"
    os.environ["CHARGEOPT_SKIP_DB_MIGRATION"] = "1"
    try:
        from chargeopt.app import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health")
        assert "strict-transport-security" in resp.headers
    finally:
        os.environ.pop("ENVIRONMENT", None)
        os.environ.pop("CHARGEOPT_SKIP_DB_MIGRATION", None)
        cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Middleware exception path (call_next raises)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_observability_middleware_reraises_exceptions():
    from chargeopt.app import create_app
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    app = create_app()

    @app.get("/test-raise-mid")
    async def _raise():
        raise ValueError("boom")

    async with AsyncClient(
        transport=ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as ac:
        resp = await ac.get("/test-raise-mid")

    assert resp.status_code == 500
    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# _update_gauges — exception swallowing branches
# ---------------------------------------------------------------------------


def test_update_gauges_swallows_db_exception():
    from chargeopt.app import _update_gauges

    with patch("chargeopt.app.health_check", side_effect=RuntimeError("no pool")):
        _update_gauges()  # must not raise


def test_update_gauges_swallows_repo_exception():
    from chargeopt.app import _update_gauges

    with (
        patch("chargeopt.app.health_check", return_value={"pool_available": 0, "pool_size": 0}),
        patch("chargeopt.app.load_repository_from_db", side_effect=RuntimeError("no repo")),
    ):
        _update_gauges()  # must not raise


# ---------------------------------------------------------------------------
# /health — DB raises → 503
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_returns_503_when_db_raises():
    from chargeopt.app import create_app
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    app = create_app()

    with patch("chargeopt.app.health_check", side_effect=RuntimeError("db down")):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/health")

    assert resp.status_code == 503
    assert resp.json()["status"] == "unhealthy"
    cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /metrics — disabled
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_disabled_returns_404():
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    os.environ["METRICS_ENABLED"] = "false"
    try:
        from chargeopt.app import create_app

        app = create_app()
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.get("/metrics")
        assert resp.status_code == 404
    finally:
        os.environ.pop("METRICS_ENABLED", None)
        cfg.get_settings.cache_clear()


# ---------------------------------------------------------------------------
# /metrics enabled path (lines 278-280)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_metrics_enabled_returns_prometheus_text(client):
    """Cover the _update_gauges + generate_latest + Response path."""
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert b"chargeopt" in resp.content or b"#" in resp.content


# ---------------------------------------------------------------------------
# __main__.main()
# ---------------------------------------------------------------------------


def test_main_calls_uvicorn_run():
    from chargeopt import config as cfg

    cfg.get_settings.cache_clear()
    with patch("chargeopt.__main__.uvicorn.run") as mock_run:
        from chargeopt.__main__ import main

        main()
        mock_run.assert_called_once()
        call_kwargs = mock_run.call_args
        assert call_kwargs[0][0] == "chargeopt.app:app"
    cfg.get_settings.cache_clear()


def test_main_name_guard():
    """Cover the `if __name__ == '__main__': main()` guard (line 24)."""
    import runpy

    with patch("chargeopt.__main__.main") as mock_main:
        runpy.run_module("chargeopt.__main__", run_name="__main__", alter_sys=True)
        mock_main.assert_called_once()
