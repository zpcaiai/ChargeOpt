from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from chargeopt.mlops import evaluate_quantile_forecast, promote_model, quality_gate, register_model


def test_quantile_evaluation_and_quality_gate_rejects_overwide_interval():
    actual = [100, 110, 120, 130, 140, 150, 160, 170, 180, 190]
    p50 = [value + (-2 if index % 2 else 2) for index, value in enumerate(actual)]
    metrics = evaluate_quantile_forecast(actual, [value - 12 for value in p50], p50, [value + 12 for value in p50])
    gate = quality_gate(metrics, {"wape": metrics["wape"] * 0.9})

    assert metrics["wape"] < 0.02
    assert metrics["coverage_80"] == 1
    assert gate["passed"] is False
    assert gate["checks"]["coverage_80_between_70_and_98pct"] is False


def test_quantile_evaluation_rejects_invalid_contract():
    with pytest.raises(ValueError, match="equal lengths"):
        evaluate_quantile_forecast([1, 2], [0], [1], [2])
    with pytest.raises(ValueError, match="p10"):
        evaluate_quantile_forecast([1], [2], [1], [3])


@contextmanager
def _connection_context(conn):
    yield conn


def _transaction_conn():
    conn = MagicMock()
    conn.transaction.return_value.__enter__ = lambda _self: _self
    conn.transaction.return_value.__exit__ = MagicMock(return_value=False)
    return conn


def test_model_promotion_enforces_maker_checker():
    conn = _transaction_conn()
    model_cursor = MagicMock()
    model_cursor.fetchone.return_value = ("mdl-1", "load_forecast", "shadow", "alice")
    conn.execute.side_effect = [MagicMock(), model_cursor]

    with (
        patch("chargeopt.mlops.get_connection", return_value=_connection_context(conn)),
        pytest.raises(PermissionError, match="cannot approve"),
    ):
        promote_model("mdl-1", "t-1", "alice", "t-1")


def test_model_promotion_requires_passed_latest_evaluation():
    conn = _transaction_conn()
    model_cursor = MagicMock()
    model_cursor.fetchone.return_value = ("mdl-1", "load_forecast", "shadow", "alice")
    evaluation_cursor = MagicMock()
    evaluation_cursor.fetchone.return_value = ({"passed": False},)
    conn.execute.side_effect = [MagicMock(), model_cursor, evaluation_cursor]

    with (
        patch("chargeopt.mlops.get_connection", return_value=_connection_context(conn)),
        pytest.raises(ValueError, match="did not pass"),
    ):
        promote_model("mdl-1", "t-1", "bob", "t-1")


def test_register_model_persists_hash_lineage():
    conn = _transaction_conn()
    now = datetime.now(UTC)
    row = (
        "mdl-1",
        "t-1",
        "load_forecast",
        "1.0.0",
        "quantile-ensemble",
        "s3://models/model.bin",
        "a" * 64,
        "b" * 64,
        now - timedelta(days=30),
        now - timedelta(days=1),
        "candidate",
        {},
        "alice",
        None,
        None,
        now,
        now,
    )
    insert = MagicMock()
    insert.fetchone.return_value = row
    conn.execute.side_effect = [MagicMock(), insert, MagicMock()]
    payload = {
        "scope": "load_forecast",
        "version": "1.0.0",
        "algorithm": "quantile-ensemble",
        "artifact_uri": "s3://models/model.bin",
        "artifact_sha256": "a" * 64,
        "training_data_hash": "b" * 64,
        "training_window_start": now - timedelta(days=30),
        "training_window_end": now - timedelta(days=1),
        "metrics": {},
    }
    with patch("chargeopt.mlops.get_connection", return_value=_connection_context(conn)):
        result = register_model("t-1", payload, "alice", "t-1")

    assert result["artifact_sha256"] == "a" * 64
    assert result["training_data_hash"] == "b" * 64
    assert result["status"] == "candidate"


@pytest.mark.asyncio
async def test_mlops_api_lifecycle_contract(client):
    now = datetime.now(UTC)
    model = {
        "id": "mdl-1",
        "tenant_id": "t-001",
        "scope": "portfolio_load_forecast",
        "version": "2026.07.1",
        "algorithm": "gradient-boosted-quantile-ensemble",
        "artifact_uri": "s3://models/forecast/model.bin",
        "artifact_sha256": "a" * 64,
        "training_data_hash": "b" * 64,
        "training_window_start": now - timedelta(days=90),
        "training_window_end": now - timedelta(days=1),
        "status": "candidate",
        "metrics": {},
        "created_by": "dev-admin",
        "approved_by": None,
        "approved_at": None,
        "created_at": now,
        "updated_at": now,
    }
    with patch("chargeopt.app.register_model", return_value=model) as register:
        response = await client.post(
            "/api/models",
            json={
                "tenant_id": "t-001",
                "scope": model["scope"],
                "version": model["version"],
                "algorithm": model["algorithm"],
                "artifact_uri": model["artifact_uri"],
                "artifact_sha256": "a" * 64,
                "training_data_hash": "b" * 64,
                "training_window_start": (now - timedelta(days=90)).isoformat(),
                "training_window_end": (now - timedelta(days=1)).isoformat(),
            },
        )
    assert response.status_code == 201
    assert register.call_args.args[0] == "t-001"

    evaluation = {
        "id": "eval-1",
        "model_id": "mdl-1",
        "metrics": {"wape": 0.1},
        "quality_gate": {"passed": True},
    }
    values = list(range(100, 108))
    with patch("chargeopt.app.evaluate_model", return_value=evaluation):
        response = await client.post(
            "/api/models/mdl-1/evaluations?tenant_id=t-001",
            json={
                "dataset_hash": "c" * 64,
                "actual": values,
                "p10": [value - 10 for value in values],
                "p50": values,
                "p90": [value + 10 for value in values],
            },
        )
    assert response.status_code == 201
    assert response.json()["quality_gate"]["passed"] is True
