"""Tests for chargeopt/db.py — mocking the psycopg pool."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _reset_pool():
    """Ensure the module-level pool is None before and after each test."""
    import chargeopt.db as db_module

    db_module._pool = None
    yield
    db_module._pool = None


# ---------------------------------------------------------------------------
# init_pool
# ---------------------------------------------------------------------------


def test_init_pool_no_op_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from chargeopt.config import get_settings

    get_settings.cache_clear()
    import chargeopt.db as db_module

    db_module.init_pool()
    assert db_module._pool is None
    get_settings.cache_clear()


def test_init_pool_creates_pool(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    from chargeopt.config import get_settings

    get_settings.cache_clear()

    mock_pool = MagicMock()
    with patch("chargeopt.db.ConnectionPool", return_value=mock_pool) as MockCP:
        import chargeopt.db as db_module

        db_module.init_pool()
        assert db_module._pool is mock_pool
        MockCP.assert_called_once()

    get_settings.cache_clear()


def test_init_pool_idempotent(monkeypatch):
    """Calling init_pool twice does not create a second pool."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    from chargeopt.config import get_settings

    get_settings.cache_clear()

    mock_pool = MagicMock()
    with patch("chargeopt.db.ConnectionPool", return_value=mock_pool) as MockCP:
        import chargeopt.db as db_module

        db_module.init_pool()
        db_module.init_pool()
        assert MockCP.call_count == 1

    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# close_pool
# ---------------------------------------------------------------------------


def test_close_pool_closes_and_clears(monkeypatch):
    import chargeopt.db as db_module

    mock_pool = MagicMock()
    db_module._pool = mock_pool
    db_module.close_pool()
    mock_pool.close.assert_called_once()
    assert db_module._pool is None


def test_close_pool_noop_when_none():
    import chargeopt.db as db_module

    db_module._pool = None
    db_module.close_pool()  # should not raise


# ---------------------------------------------------------------------------
# get_connection
# ---------------------------------------------------------------------------


def test_get_connection_raises_without_pool():
    import chargeopt.db as db_module

    db_module._pool = None
    with pytest.raises(RuntimeError, match="not initialised"):
        with db_module.get_connection():
            pass


def test_get_connection_yields_conn():
    import chargeopt.db as db_module

    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.connection.return_value.__enter__ = lambda s: mock_conn
    mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
    db_module._pool = mock_pool

    with db_module.get_connection() as conn:
        assert conn is mock_conn


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


def test_health_check_no_pool():
    import chargeopt.db as db_module

    db_module._pool = None
    result = db_module.health_check()
    assert result["db"] == "disabled"
    assert result["pool_available"] is None


def test_health_check_with_pool():
    import chargeopt.db as db_module

    mock_conn = MagicMock()
    mock_pool = MagicMock()
    mock_pool.get_stats.return_value = {"pool_available": 5, "pool_size": 10}
    mock_pool.connection.return_value.__enter__ = lambda s: mock_conn
    mock_pool.connection.return_value.__exit__ = MagicMock(return_value=False)
    db_module._pool = mock_pool

    result = db_module.health_check()
    assert result["db"] == "ok"
    assert result["pool_available"] == 5
    assert result["pool_size"] == 10
    mock_conn.execute.assert_called_once_with("SELECT 1")
