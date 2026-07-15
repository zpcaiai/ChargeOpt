"""PostgreSQL connection pool management.

Uses psycopg3 connection pool.  Falls back gracefully when DATABASE_URL
is absent (in-memory / Vercel serverless mode).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import Any

try:  # optional at import time for environments without DB extras
    from psycopg_pool import ConnectionPool
except Exception:  # pragma: no cover - exercised when DB extras are absent
    ConnectionPool = None  # type: ignore[assignment]

from .config import get_settings

logger = logging.getLogger(__name__)

_pool: Any | None = None


def init_pool() -> None:
    """Initialise the module-level connection pool.  Call once at startup."""
    global _pool
    settings = get_settings()
    if not settings.use_db:
        logger.info("DATABASE_URL not set – running in in-memory mode.")
        return
    if settings.is_serverless:
        logger.info("Serverless runtime detected – using direct pooled-endpoint connections.")
        return
    if _pool is not None:
        return
    if ConnectionPool is None:
        raise RuntimeError("psycopg_pool is required when DATABASE_URL is configured.")
    _pool = ConnectionPool(
        conninfo=settings.database_url,  # type: ignore[arg-type]
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        open=True,
        kwargs={
            "connect_timeout": settings.db_connect_timeout,
        },
    )
    logger.info("PostgreSQL connection pool ready (min=%d, max=%d)", settings.db_pool_min, settings.db_pool_max)


def close_pool() -> None:
    """Drain and close the pool – call on application shutdown."""
    global _pool
    if _pool is not None:
        _pool.close()
        _pool = None
        logger.info("PostgreSQL connection pool closed.")


@contextlib.contextmanager
def get_connection() -> Generator:
    """Yield a connection under the non-owner application RLS role."""
    settings = get_settings()
    if settings.use_db and settings.is_serverless:
        import psycopg

        with psycopg.connect(
            settings.database_url,
            connect_timeout=settings.db_connect_timeout,
        ) as conn:
            conn.execute("SET ROLE chargeopt_app")
            try:
                yield conn
            finally:
                conn.execute("RESET ROLE")
        return
    if _pool is None:
        # Vercel serverless does not run ASGI lifespan reliably for every cold
        # start, so initialise lazily on first DB use as a safety net.
        init_pool()
    if _pool is None:
        raise RuntimeError("Connection pool is not initialised.  Call init_pool() first.")
    with _pool.connection() as conn:
        conn.execute("SET ROLE chargeopt_app")
        try:
            yield conn
        finally:
            conn.execute("RESET ROLE")


def health_check() -> dict[str, object]:
    """Return a health dict; raises if the DB is unreachable."""
    settings = get_settings()
    if settings.use_db and settings.is_serverless:
        with get_connection() as conn:
            conn.execute("SELECT 1")
        return {"db": "ok", "pool_available": None, "pool_size": None}
    if _pool is None:
        init_pool()
    if _pool is None:
        return {"db": "disabled", "pool_available": None}
    stats = _pool.get_stats()
    with get_connection() as conn:
        conn.execute("SELECT 1")
    return {
        "db": "ok",
        "pool_available": stats.get("pool_available"),
        "pool_size": stats.get("pool_size"),
    }
