"""Tests for the configuration layer."""

from __future__ import annotations


def test_default_settings():
    from chargeopt.config import Settings

    s = Settings()
    assert s.environment == "development"
    assert s.port == 8000
    assert s.use_db is False
    assert s.is_production is False


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/db")
    monkeypatch.setenv("API_KEY", "my-secret")
    from chargeopt.config import Settings

    s = Settings()
    assert s.is_production is True
    assert s.port == 9000
    assert s.use_db is True
    assert s.api_key == "my-secret"


def test_cors_origins_parsed_from_string(monkeypatch):
    monkeypatch.setenv("CORS_ORIGINS", "https://app.example.com,https://admin.example.com")
    from chargeopt.config import Settings

    s = Settings()
    origins = s.cors_origins_list
    assert "https://app.example.com" in origins
    assert len(origins) == 2


def test_skip_migration_alias_resolves(monkeypatch):
    """CHARGEOPT_SKIP_DB_MIGRATION env var must set skip_db_migration=True."""
    monkeypatch.setenv("CHARGEOPT_SKIP_DB_MIGRATION", "1")
    from chargeopt.config import Settings

    s = Settings()
    assert s.skip_db_migration is True


def test_log_level_and_debug_fields(monkeypatch):
    monkeypatch.setenv("LOG_LEVEL", "debug")
    monkeypatch.setenv("DEBUG", "true")
    from chargeopt.config import Settings

    s = Settings()
    assert s.log_level == "debug"
    assert s.debug is True
