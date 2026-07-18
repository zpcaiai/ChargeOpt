from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from chargeopt.advanced_ems_repository import list_ems_evidence, persist_ems_evidence


def _connection_context(conn):
    context = MagicMock()
    context.__enter__ = lambda _self: conn
    context.__exit__ = MagicMock(return_value=False)
    conn.transaction.return_value.__enter__ = lambda _self: _self
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return context


def _cursor(*, one=None, rows=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = one
    cursor.fetchall.return_value = rows or []
    return cursor


def _persist(conn, input_hash: str = "a" * 64):
    with patch(
        "chargeopt.advanced_ems_repository.get_connection",
        return_value=_connection_context(conn),
    ):
        return persist_ems_evidence(
            "t-001",
            "st-hq-hongqiao",
            "forecast",
            "adaptive-conformal-ensemble-v2",
            "observed",
            input_hash,
            {"horizon": 24},
            {"algorithm": "adaptive-conformal-ensemble-v2", "input_hash": input_hash},
            "forecast-idempotency-001",
            "operator-1",
            "t-001",
        )


def test_persist_ems_evidence_inserts_append_only_record_and_audit():
    conn = MagicMock()
    conn.execute.side_effect = [_cursor(), _cursor(one=("t-001",)), _cursor(), _cursor(), _cursor()]

    result = _persist(conn)

    assert result["id"].startswith("ems-")
    assert result["persisted"] is True
    assert result["replayed"] is False
    sql = "\n".join(str(call.args[0]) for call in conn.execute.call_args_list)
    assert "INSERT INTO chargeopt.ems_evidence_runs" in sql
    assert "INSERT INTO chargeopt.audit_entries" in sql


def test_persist_ems_evidence_replays_same_idempotent_result():
    conn = MagicMock()
    created_at = datetime(2026, 7, 18, tzinfo=UTC)
    saved = {"algorithm": "adaptive-conformal-ensemble-v2", "input_hash": "a" * 64}
    conn.execute.side_effect = [
        _cursor(),
        _cursor(one=("t-001",)),
        _cursor(one=("ems-existing", "a" * 64, saved, created_at)),
    ]

    result = _persist(conn)

    assert result["id"] == "ems-existing"
    assert result["replayed"] is True
    assert result["result"] == saved


def test_persist_ems_evidence_rejects_idempotency_hash_conflict():
    conn = MagicMock()
    conn.execute.side_effect = [
        _cursor(),
        _cursor(one=("t-001",)),
        _cursor(one=("ems-existing", "b" * 64, {}, datetime.now(UTC))),
    ]

    with pytest.raises(ValueError, match="different EMS inputs"):
        _persist(conn)


def test_list_ems_evidence_returns_tenant_scoped_rows():
    conn = MagicMock()
    created_at = datetime(2026, 7, 18, tzinfo=UTC)
    row = (
        "ems-1",
        "st-hq-hongqiao",
        "dispatch",
        "wasserstein-radius-robust-cvar-milp-mpc-v1",
        "completed",
        "observed",
        "c" * 64,
        {"objective_value": 10.2},
        "operator-1",
        created_at,
    )
    conn.execute.side_effect = [_cursor(), _cursor(rows=[row])]
    with patch(
        "chargeopt.advanced_ems_repository.get_connection",
        return_value=_connection_context(conn),
    ):
        result = list_ems_evidence("t-001", evidence_type="dispatch", scope_tenant_id="t-001")

    assert result == [
        {
            "id": "ems-1",
            "station_id": "st-hq-hongqiao",
            "evidence_type": "dispatch",
            "algorithm_version": "wasserstein-radius-robust-cvar-milp-mpc-v1",
            "status": "completed",
            "evidence_class": "observed",
            "input_hash": "c" * 64,
            "result": {"objective_value": 10.2},
            "created_by": "operator-1",
            "created_at": created_at.isoformat(),
        }
    ]


def test_repository_denies_cross_tenant_before_opening_database():
    with (
        patch("chargeopt.advanced_ems_repository.get_connection") as connection,
        pytest.raises(PermissionError, match="another tenant"),
    ):
        list_ems_evidence("t-002", scope_tenant_id="t-001")
    connection.assert_not_called()
