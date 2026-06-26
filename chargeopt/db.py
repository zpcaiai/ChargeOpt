"""PostgreSQL connection pool management.

Uses psycopg3 connection pool.  Falls back gracefully when DATABASE_URL
is absent (in-memory / Vercel serverless mode).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import Generator
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import psycopg
    from psycopg_pool import ConnectionPool

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
    if _pool is not None:
        return
    from psycopg_pool import ConnectionPool  # lazy – requires libpq
    _pool = ConnectionPool(
        conninfo=settings.database_url,  # type: ignore[arg-type]
        min_size=settings.db_pool_min,
        max_size=settings.db_pool_max,
        open=True,
        kwargs={
            "connect_timeout": settings.db_connect_timeout,
            "options": "-c search_path=chargeopt,public",
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
    """Yield a checked-out connection from the pool."""
    if _pool is None:
        raise RuntimeError("Connection pool is not initialised.  Call init_pool() first.")
    with _pool.connection() as conn:
        yield conn


def health_check() -> dict[str, object]:
    """Return a health dict; raises if the DB is unreachable."""
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
