from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from chargeopt.vpp_repository import _assert_settlement_transition, approve_settlement_batch


@contextmanager
def _connection_context(conn):
    yield conn


def test_settlement_state_machine_rejects_skipped_states():
    with pytest.raises(ValueError, match="review -> paid"):
        _assert_settlement_transition("review", "paid")
    with pytest.raises(ValueError, match="reversed -> approved"):
        _assert_settlement_transition("reversed", "approved")


def test_settlement_approval_enforces_maker_checker():
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda _self: _self
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    batch = {"id": "stb-1", "status": "review", "created_by": "alice"}
    with (
        patch("chargeopt.vpp_repository.get_connection", return_value=_connection_context(conn)),
        patch("chargeopt.vpp_repository._settlement_batch_for_update", return_value=batch),
        pytest.raises(PermissionError, match="cannot approve"),
    ):
        approve_settlement_batch("t-1", "stb-1", "alice", "self approval")
