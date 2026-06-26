"""Centralised application configuration via pydantic-settings.

All settings can be overridden by environment variables or a .env file.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )

    # ── Application ──────────────────────────────────────────────────────────
    app_name: str = "ChargeOpt OS"
    app_version: str = "0.2.0"
    environment: Literal["development", "staging", "production"] = "development"
    debug: bool = False

    # ── Server ────────────────────────────────────────────────────────────────
    host: str = "0.0.0.0"
    port: int = Field(default=8000, ge=1, le=65535)
    workers: int = Field(default=1, ge=1, le=32)
    log_level: Literal["debug", "info", "warning", "error", "critical"] = "info"

    # ── Database ──────────────────────────────────────────────────────────────
    database_url: str | None = None
    db_pool_min: int = Field(default=2, ge=1)
    db_pool_max: int = Field(default=10, ge=1)
    db_connect_timeout: int = Field(default=10, ge=1)
    skip_db_migration: bool = Field(default=False, alias="CHARGEOPT_SKIP_DB_MIGRATION")

    # ── Security ──────────────────────────────────────────────────────────────
    api_key: str | None = None
    api_key_header: str = "X-API-Key"
    cors_origins: str = "*"
    cors_allow_credentials: bool = False

    # ── Rate Limiting ─────────────────────────────────────────────────────────
    rate_limit_per_minute: int = Field(default=120, ge=1)

    # ── Observability ─────────────────────────────────────────────────────────
    metrics_enabled: bool = True
    request_id_header: str = "X-Request-Id"

    @property
    def cors_origins_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    @property
    def use_db(self) -> bool:
        return self.database_url is not None


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
