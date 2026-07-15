"""Tests for the database migration script."""

from __future__ import annotations

from pathlib import Path
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


def test_force_rls_migration_covers_tenant_write_tables():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "006_force_rls.sql").read_text(encoding="utf-8")

    assert "ALTER TABLE IF EXISTS chargeopt.task_queue FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE IF EXISTS chargeopt.edge_command_receipts FORCE ROW LEVEL SECURITY" in sql
    assert "ALTER TABLE IF EXISTS chargeopt.revenue_proof_runs FORCE ROW LEVEL SECURITY" in sql


def test_vpp_trading_migration_has_full_tenant_and_audit_controls():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "007_vpp_trading_platform.sql").read_text(
        encoding="utf-8"
    )

    for table in (
        "market_orders",
        "market_order_events",
        "market_trades",
        "delivery_schedules",
        "vpp_meter_intervals",
        "vpp_settlement_batches",
        "vpp_circuit_breakers",
        "vpp_automation_runs",
    ):
        assert f"chargeopt.{table}" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql
    assert "prevent_order_event_mutation" in sql
    assert "idempotency_key text NOT NULL" in sql


def test_default_credentials_are_disabled_by_followup_migration():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "008_disable_default_credentials.sql").read_text(
        encoding="utf-8"
    )

    assert "SET active = false" in sql
    assert "password_salt = 'chargeopt-demo-salt-v1'" in sql
    assert "SET revoked_at = now()" in sql


def test_reliable_operations_migration_is_fail_closed():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "009_reliable_vpp_operations.sql").read_text(
        encoding="utf-8"
    )

    assert "event_key text" in sql
    assert "recovered running task without lease" in sql
    assert "vpp_operational_heartbeats" in sql
    assert "NULLIF(current_setting('chargeopt.tenant_id', true), '') IS NOT NULL" in sql
    assert "COALESCE(current_setting('chargeopt.tenant_id', true), '*')" not in sql


def test_mlops_migration_has_registry_quality_evidence_and_rls():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "010_mlops_registry.sql").read_text(encoding="utf-8")

    assert "model_registry" in sql
    assert "model_evaluations" in sql
    assert "idx_chargeopt_active_model_scope" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql


def test_settlement_workflow_migration_is_immutable_and_auditable():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "011_settlement_workflow.sql").read_text(
        encoding="utf-8"
    )

    for table in (
        "vpp_settlement_events",
        "vpp_settlement_disputes",
        "vpp_settlement_exports",
        "vpp_settlement_adjustments",
    ):
        assert f"chargeopt.{table}" in sql
    assert "prevent_settlement_ledger_mutation" in sql
    assert "FORCE ROW LEVEL SECURITY" in sql


def test_operational_assurance_migration_requires_real_live_inputs():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "012_operational_assurance.sql").read_text(
        encoding="utf-8"
    )

    assert "market_certificate_status" in sql
    assert "trading_qualification_status" in sql
    assert "device_credentials_attested_at" in sql
    assert "shadow_run_evidence" in sql
    assert "recovery_drills" in sql
    assert "prevent_shadow_evidence_mutation" in sql


def test_ingress_receipts_have_durable_idempotency_indexes():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "014_ingress_receipt_idempotency.sql").read_text(
        encoding="utf-8"
    )

    assert "protocol_messages" in sql
    assert "edge_command_receipts" in sql
    assert "idempotency_key" in sql
    assert "CREATE UNIQUE INDEX" in sql


def test_application_role_cannot_bypass_rls():
    sql = (Path(__file__).resolve().parents[1] / "migrations" / "013_application_rls_role.sql").read_text(
        encoding="utf-8"
    )

    assert "NOBYPASSRLS" in sql
    assert "GRANT chargeopt_app TO CURRENT_USER" in sql
    assert "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES" in sql
