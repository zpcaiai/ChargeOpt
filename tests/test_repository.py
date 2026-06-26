"""Tests for the repository layer.

Covers:
- In-memory fallback when DATABASE_URL is absent
- DB error → graceful fallback to in-memory fixtures
- TTL cache: second call within TTL returns same object
- Cache invalidation works
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from chargeopt.data import Repository


@pytest.fixture(autouse=True)
def _clear_cache():
    """Reset the repository cache before every test."""
    from chargeopt.repository import invalidate_repository_cache

    invalidate_repository_cache()
    yield
    invalidate_repository_cache()


def test_returns_in_memory_when_no_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from chargeopt.config import get_settings

    get_settings.cache_clear()
    from chargeopt.repository import load_repository_from_db

    repo = load_repository_from_db()
    assert isinstance(repo, Repository)
    assert len(repo.stations) > 0
    get_settings.cache_clear()


def test_fallback_on_db_error(monkeypatch):
    """If PostgreSQL raises, we still get in-memory data."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake:fake@localhost/fake")
    from chargeopt.config import get_settings

    get_settings.cache_clear()
    from chargeopt.repository import load_repository_from_db

    with patch("chargeopt.repository._load_from_postgres", side_effect=RuntimeError("connection refused")):
        repo = load_repository_from_db()

    assert isinstance(repo, Repository)
    assert len(repo.stations) > 0
    get_settings.cache_clear()


def test_ttl_cache_returns_same_object(monkeypatch):
    """Two calls within the TTL window return the identical cached object (DB mode)."""
    from unittest.mock import patch

    from chargeopt.data import load_repository

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    from chargeopt.config import get_settings

    get_settings.cache_clear()

    fixed_repo = load_repository()
    from chargeopt.repository import load_repository_from_db

    with patch("chargeopt.repository._load_from_postgres", return_value=fixed_repo):
        r1 = load_repository_from_db()
        r2 = load_repository_from_db()

    assert r1 is r2
    get_settings.cache_clear()


def test_cache_invalidation(monkeypatch):
    """After invalidate_repository_cache(), the next call produces a fresh object."""
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from chargeopt.config import get_settings

    get_settings.cache_clear()
    from chargeopt.repository import invalidate_repository_cache, load_repository_from_db

    r1 = load_repository_from_db()
    invalidate_repository_cache()
    r2 = load_repository_from_db()
    assert r1 is not r2
    get_settings.cache_clear()
