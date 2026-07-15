from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from chargeopt.operations_assurance import live_market_readiness, record_shadow_day, run_assurance_checks
from chargeopt.vpp_trading import build_market_adapter


@contextmanager
def _connection_context(conn):
    yield conn


def _transaction_conn():
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda _self: _self
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def _cursor(*, one=None, all_rows=None):
    cursor = MagicMock()
    cursor.fetchone.return_value = one
    cursor.fetchall.return_value = all_rows or []
    return cursor


def test_completed_shadow_day_is_qualified_and_hashed():
    conn = _transaction_conn()
    conn.execute.side_effect = [
        _cursor(),
        _cursor(one=None),
        _cursor(one=(288, 286, 2, 6)),
        _cursor(one=(6, 0)),
        _cursor(one=(0,)),
        _cursor(one=(0,)),
        _cursor(one=(0,)),
        _cursor(one=(0,)),
        _cursor(),
    ]
    yesterday = datetime.now(UTC).date() - timedelta(days=1)
    with patch("chargeopt.operations_assurance.get_connection", return_value=_connection_context(conn)):
        result = record_shadow_day("t-1", yesterday)

    assert result["qualified"] is True
    assert len(result["evidence_hash"]) == 64
    assert result["qualification_reasons"]["automation_cycles_gte_250"] is True


def test_shadow_evidence_rejects_incomplete_day():
    with pytest.raises(ValueError, match="completed UTC day"):
        record_shadow_day("t-1", datetime.now(UTC).date())


def test_live_readiness_requires_all_30_consecutive_days_and_external_inputs():
    conn = _transaction_conn()
    today = datetime.now(UTC).date()
    days = [(today - timedelta(days=offset), True, f"hash-{offset}") for offset in range(1, 31)]
    connection = (
        "mc-1",
        "CN-MARKET",
        "participant-1",
        "live",
        True,
        "verified",
        datetime.now(UTC) + timedelta(days=90),
        "verified",
        datetime.now(UTC) - timedelta(days=1),
        {"certificate_subject": "provided-by-market"},
    )
    conn.execute.side_effect = [_cursor(), _cursor(one=connection), _cursor(all_rows=days)]
    with patch("chargeopt.operations_assurance.get_connection", return_value=_connection_context(conn)):
        result = live_market_readiness("t-1")

    assert result["ready"] is True
    assert result["shadow_qualified_days"] == 30
    assert result["blockers"] == []


def test_live_readiness_fails_closed_without_market_connection():
    conn = _transaction_conn()
    conn.execute.side_effect = [_cursor(), _cursor(one=None), _cursor(all_rows=[])]
    with patch("chargeopt.operations_assurance.get_connection", return_value=_connection_context(conn)):
        result = live_market_readiness("t-1")
    assert result == {"ready": False, "blockers": ["market_connection_missing"], "shadow_qualified_days": 0}


def test_live_market_adapter_fails_closed_without_readiness_evidence():
    with pytest.raises(RuntimeError, match="readiness gate blocked"):
        build_market_adapter(
            {
                "mode": "live",
                "adapter": "signed_rest",
                "enabled": True,
                "credential_ref": "MARKET",
                "base_url": "https://market.example",
                "live_readiness": {"ready": False, "blockers": ["thirty_consecutive_shadow_days"]},
            }
        )


def test_assurance_checks_record_healthy_slo_measurements():
    global_conn = _transaction_conn()
    global_conn.execute.side_effect = [_cursor(), _cursor(all_rows=[("t-1",)])]
    tenant_conn = _transaction_conn()
    tenant_conn.execute.side_effect = [
        _cursor(),
        _cursor(one=(datetime.now(UTC) - timedelta(seconds=30),)),
        _cursor(one=(0,)),
        _cursor(one=(0,)),
        _cursor(),
        _cursor(),
        _cursor(),
    ]
    contexts = [_connection_context(global_conn), _connection_context(tenant_conn)]
    with patch("chargeopt.operations_assurance.get_connection", side_effect=contexts):
        result = run_assurance_checks("test-assurance")

    assert result["status"] == "healthy"
    assert result["tenants"][0]["healthy"] is True
    assert tenant_conn.execute.call_count == 7


@pytest.mark.asyncio
async def test_live_readiness_api(client):
    response = await client.get("/api/vpp/trading/live-readiness")
    assert response.status_code == 200
    assert response.json() == {"ready": False, "blockers": ["database_required"], "shadow_qualified_days": 0}
