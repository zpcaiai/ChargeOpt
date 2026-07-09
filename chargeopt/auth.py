"""Authentication, RBAC, and tenant context helpers."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

Role = Literal["platform_admin", "tenant_admin", "operator", "analyst", "edge_gateway", "auditor"]


@dataclass(frozen=True)
class Principal:
    subject: str
    tenant_id: str | None
    role: Role
    display_name: str
    auth_type: str

    @property
    def is_platform_admin(self) -> bool:
        return self.role == "platform_admin"


ROLE_PERMISSIONS: dict[Role, set[str]] = {
    "platform_admin": {"*"},
    "tenant_admin": {
        "auth:read",
        "station:read",
        "dispatch:read",
        "dispatch:write",
        "dispatch:approve",
        "telemetry:write",
        "vpp:read",
        "vpp:settle",
        "audit:read",
        "device:write",
        "task:write",
    },
    "operator": {
        "station:read",
        "dispatch:read",
        "dispatch:write",
        "dispatch:approve",
        "telemetry:write",
        "vpp:read",
        "device:write",
        "task:write",
    },
    "analyst": {"station:read", "dispatch:read", "vpp:read", "audit:read"},
    "edge_gateway": {"telemetry:write", "device:write", "task:write"},
    "auditor": {"station:read", "dispatch:read", "vpp:read", "audit:read"},
}


def has_permission(principal: Principal, permission: str) -> bool:
    permissions = ROLE_PERMISSIONS[principal.role]
    return "*" in permissions or permission in permissions


def hash_password(password: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), 210_000).hex()


def verify_password(password: str, salt: str, password_hash: str) -> bool:
    return hmac.compare_digest(hash_password(password, salt), password_hash)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def session_expiry(hours: int = 12) -> datetime:
    return datetime.now(UTC) + timedelta(hours=hours)


def development_principal() -> Principal:
    return Principal(
        subject="dev-admin",
        tenant_id="t-001",
        role="platform_admin",
        display_name="Development Admin",
        auth_type="development",
    )


def static_api_key_principal() -> Principal:
    return Principal(
        subject="static-api-key",
        tenant_id=None,
        role="platform_admin",
        display_name="Static API Key",
        auth_type="api_key",
    )
