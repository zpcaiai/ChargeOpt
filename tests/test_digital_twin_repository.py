from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from chargeopt.digital_twin import build_default_topology, calibrate_twin_model
from chargeopt.digital_twin_repository import (
    activate_topology_version,
    create_topology_version,
    persist_calibration,
    transition_maintenance_action,
)


def _connection_context(conn):
    context = MagicMock()
    context.__enter__ = lambda _self: conn
    context.__exit__ = MagicMock(return_value=False)
    conn.transaction.return_value.__enter__ = lambda _self: _self
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return context


def _cursor(*, one=None, all_rows=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = one
    cursor.fetchall.return_value = all_rows or []
    return cursor


def test_create_topology_version_writes_assets_relationships_and_audit(repo):
    station = repo.stations[0]
    topology = build_default_topology(station)
    conn = MagicMock()

    def execute(sql, params=None):
        if "SELECT 1 FROM chargeopt.stations" in sql:
            return _cursor(one=(1,))
        if "COALESCE(max(version),0)+1" in sql:
            return _cursor(one=(1,))
        return _cursor()

    conn.execute.side_effect = execute
    with patch(
        "chargeopt.digital_twin_repository.get_connection",
        return_value=_connection_context(conn),
    ):
        result = create_topology_version("t-001", station.id, topology, "engineer", "t-001")

    assert result["status"] == "validated"
    assert result["version"] == 1
    assert result["validation"]["valid"] is True
    assert conn.execute.call_count > len(topology["assets"])


def test_create_topology_rejects_invalid_graph_before_database():
    invalid = {
        "assets": [{"asset_key": "station", "asset_type": "station", "name": "Station"}],
        "relationships": [
            {
                "source_asset_key": "station",
                "target_asset_key": "missing",
                "relationship_type": "contains",
            }
        ],
    }

    with pytest.raises(ValueError, match="Invalid topology"):
        create_topology_version("t-001", "st-1", invalid, "engineer", "t-001")


def test_activate_topology_retires_previous_version_and_audits():
    conn = MagicMock()
    activated = (
        "top-1",
        "st-1",
        2,
        "active",
        "a" * 64,
        "2026-07-18T00:00:00+00:00",
        "2026-07-18T00:00:00+00:00",
    )
    conn.execute.side_effect = [
        _cursor(),
        _cursor(one=("st-1", 2, "validated", {"valid": True}, "a" * 64)),
        _cursor(),
        _cursor(one=activated),
        _cursor(),
    ]
    with patch(
        "chargeopt.digital_twin_repository.get_connection",
        return_value=_connection_context(conn),
    ):
        result = activate_topology_version("t-001", "top-1", "approver", "t-001")

    assert result["status"] == "active"
    assert result["version"] == 2


def test_persist_calibration_records_active_topology_and_quality_gate():
    result = calibrate_twin_model(
        [float(index) for index in range(24)],
        [float(index) * 1.02 + 2 for index in range(24)],
    )
    conn = MagicMock()
    conn.execute.side_effect = [
        _cursor(),
        _cursor(one=(1,)),
        _cursor(one=("top-active",)),
        _cursor(),
        _cursor(),
    ]
    with patch(
        "chargeopt.digital_twin_repository.get_connection",
        return_value=_connection_context(conn),
    ):
        persisted = persist_calibration(
            "t-001",
            "st-1",
            "model-v1",
            result,
            "engineer",
            "t-001",
        )

    assert persisted["id"].startswith("cal-")
    assert persisted["status"] == "passed"


def test_maintenance_transition_enforces_state_machine():
    conn = MagicMock()
    completed = ("mnt-1", "st-1", "completed", "tech", "operator", {"fixed": True}, "2026-07-18")
    conn.execute.side_effect = [
        _cursor(),
        _cursor(one=("in_progress",)),
        _cursor(one=completed),
        _cursor(),
    ]
    with patch(
        "chargeopt.digital_twin_repository.get_connection",
        return_value=_connection_context(conn),
    ):
        result = transition_maintenance_action(
            "t-001",
            "mnt-1",
            "completed",
            "operator",
            assigned_to="tech",
            outcome={"fixed": True},
            scope_tenant_id="t-001",
        )

    assert result["status"] == "completed"
    assert result["outcome"] == {"fixed": True}


def test_maintenance_transition_rejects_terminal_state():
    conn = MagicMock()
    conn.execute.side_effect = [_cursor(), _cursor(one=("completed",))]
    with (
        patch(
            "chargeopt.digital_twin_repository.get_connection",
            return_value=_connection_context(conn),
        ),
        pytest.raises(ValueError, match="Illegal maintenance transition"),
    ):
        transition_maintenance_action(
            "t-001",
            "mnt-1",
            "cancelled",
            "operator",
            scope_tenant_id="t-001",
        )
