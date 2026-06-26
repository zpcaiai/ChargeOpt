"""Shared pytest fixtures for ChargeOpt test suite."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from chargeopt.app import create_app
from chargeopt.data import load_repository


@pytest.fixture(scope="session")
def repo():
    """In-memory repository fixture (no DB required)."""
    return load_repository()


@pytest.fixture(scope="session")
def app():
    """FastAPI application instance with DB disabled."""
    return create_app()


@pytest.fixture
async def client(app):
    """Async HTTP test client."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
