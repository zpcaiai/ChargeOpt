"""First-deployment administrator onboarding tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from chargeopt.auth import Principal, validate_password_strength


def test_shared_password_policy_rejects_weak_passwords():
    with pytest.raises(ValueError):
        validate_password_strength("weak-password")
    validate_password_strength("Industrial-Admin-2026!")


@pytest.mark.asyncio
async def test_bootstrap_status_is_closed_without_database(client):
    response = await client.get("/api/auth/bootstrap-status")
    assert response.status_code == 200
    assert response.json() == {
        "available": False,
        "initialized": False,
        "configured": False,
        "recovery_available": False,
    }


@pytest.mark.asyncio
async def test_bootstrap_admin_creates_session_with_deployment_key(client, monkeypatch):
    from chargeopt import config as cfg

    setup_key = "deployment-key-with-32-characters"
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setenv("INITIAL_ADMIN_TOKEN", setup_key)
    cfg.get_settings.cache_clear()
    principal = Principal("usr-new", "t-001", "tenant_admin", "Site Administrator", "bearer")
    login_result = {
        "access_token": "session-token",
        "expires_at": datetime.now(UTC) + timedelta(hours=12),
        "principal": principal,
    }
    payload = {
        "display_name": "Site Administrator",
        "email": "admin@example.com",
        "password": "Industrial-Admin-2026!",
        "setup_key": setup_key,
    }
    try:
        with (
            patch("chargeopt.app.bootstrap_tenant_admin", return_value="usr-new") as create_admin,
            patch("chargeopt.app.authenticate_user", return_value=login_result),
        ):
            response = await client.post("/api/auth/bootstrap-admin", json=payload)
    finally:
        cfg.get_settings.cache_clear()

    assert response.status_code == 201
    assert response.json()["access_token"] == "session-token"
    assert response.json()["principal"]["role"] == "tenant_admin"
    create_admin.assert_called_once_with(
        "t-001",
        "admin@example.com",
        "Site Administrator",
        "Industrial-Admin-2026!",
        recovery_id=None,
    )


@pytest.mark.asyncio
async def test_bootstrap_admin_allows_configured_one_time_recovery(client, monkeypatch):
    from chargeopt import config as cfg

    setup_key = "deployment-key-with-32-characters"
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setenv("INITIAL_ADMIN_TOKEN", setup_key)
    monkeypatch.setenv("ADMIN_RECOVERY_ID", "recovery-2026-07-20-random")
    cfg.get_settings.cache_clear()
    principal = Principal("usr-recovered", "t-001", "tenant_admin", "Recovered Admin", "bearer")
    login_result = {
        "access_token": "session-token",
        "expires_at": datetime.now(UTC) + timedelta(hours=12),
        "principal": principal,
    }
    payload = {
        "display_name": "Recovered Admin",
        "email": "recovered@example.com",
        "password": "Industrial-Recovery-2026!",
        "setup_key": setup_key,
    }
    try:
        with (
            patch("chargeopt.app.bootstrap_tenant_admin", return_value="usr-recovered") as recover_admin,
            patch("chargeopt.app.authenticate_user", return_value=login_result),
        ):
            response = await client.post("/api/auth/bootstrap-admin", json=payload)
    finally:
        cfg.get_settings.cache_clear()

    assert response.status_code == 201
    recover_admin.assert_called_once_with(
        "t-001",
        "recovered@example.com",
        "Recovered Admin",
        "Industrial-Recovery-2026!",
        recovery_id="recovery-2026-07-20-random",
    )


@pytest.mark.asyncio
async def test_bootstrap_admin_rejects_wrong_deployment_key(client, monkeypatch):
    from chargeopt import config as cfg

    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")
    monkeypatch.setenv("INITIAL_ADMIN_TOKEN", "deployment-key-with-32-characters")
    cfg.get_settings.cache_clear()
    try:
        response = await client.post(
            "/api/auth/bootstrap-admin",
            json={
                "display_name": "Site Administrator",
                "email": "admin@example.com",
                "password": "Industrial-Admin-2026!",
                "setup_key": "incorrect-deployment-key-value",
            },
        )
    finally:
        cfg.get_settings.cache_clear()

    assert response.status_code == 401
