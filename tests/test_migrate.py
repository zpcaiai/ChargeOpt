"""Tests for the database migration script."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


def test_skip_when_flag_set(monkeypatch, capsys):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("CHARGEOPT_SKIP_DB_MIGRATION", "1")
    from scripts.migrate import main

    main()
    captured = capsys.readouterr()
    assert "Skipping" in captured.out


def test_exits_without_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("CHARGEOPT_SKIP_DB_MIGRATION", raising=False)
    from scripts.migrate import main

    with pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code != 0


def test_applies_migrations(monkeypatch, tmp_path):
    """Simulate migration execution with a mock psycopg connection."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    # Write a dummy migration file
    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_test.sql").write_text("SELECT 1;", encoding="utf-8")

    # Patch the migrations path
    monkeypatch.setattr("scripts.migrate.MIGRATIONS", mig_dir)

    # Build mock psycopg connection that returns "not yet applied"
    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = None  # migration not yet applied

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)
    mock_conn.transaction.return_value.__enter__ = lambda s: s
    mock_conn.transaction.return_value.__exit__ = MagicMock(return_value=False)

    with patch("psycopg.connect", return_value=mock_conn):
        import importlib

        from scripts import migrate as m

        importlib.reload(m)
        m.main()


def test_skips_already_applied(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("DATABASE_URL", "postgresql://fake/fake")

    mig_dir = tmp_path / "migrations"
    mig_dir.mkdir()
    (mig_dir / "001_test.sql").write_text("SELECT 1;", encoding="utf-8")
    monkeypatch.setattr("scripts.migrate.MIGRATIONS", mig_dir)

    mock_cursor = MagicMock()
    mock_cursor.fetchone.return_value = (1,)  # already applied

    mock_conn = MagicMock()
    mock_conn.execute.return_value = mock_cursor
    mock_conn.__enter__ = lambda s: s
    mock_conn.__exit__ = MagicMock(return_value=False)

    with patch("psycopg.connect", return_value=mock_conn):
        import importlib

        from scripts import migrate as m

        importlib.reload(m)
        m.main()

    out = capsys.readouterr().out
    assert "skip" in out.lower() or "already" in out.lower()
