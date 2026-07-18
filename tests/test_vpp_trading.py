from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

import pytest

from chargeopt.data import load_repository
from chargeopt.vpp_trading import (
    SandboxMarketAdapter,
    calculate_trade_settlement,
    evaluate_order_risk,
    optimize_bid_blocks,
    probabilistic_portfolio_forecast,
    validate_order_transition,
    verify_market_webhook,
)


def _policy(**overrides):
    return {
        "id": "risk-1",
        "version": 1,
        "max_order_kw": 5000,
        "max_daily_energy_kwh": 30000,
        "max_open_orders": 20,
        "min_confidence": 0.9,
        "reserve_margin": 0.2,
        "min_price_per_kwh": 0.1,
        "max_price_per_kwh": 8,
        "max_telemetry_age_seconds": 900,
        "auto_trade_enabled": True,
    } | overrides


def test_probabilistic_forecast_and_bid_optimizer_respect_capacity():
    repo = load_repository()
    now = max(point.timestamp for point in repo.telemetry).astimezone(UTC)
    forecast = probabilistic_portfolio_forecast(repo, "t-001", horizon_hours=4, now=now)
    assert forecast["algorithm"] == "adaptive-conformal-ensemble-v2"
    assert len(forecast["portfolio"]) == 16
    assert 0.5 <= forecast["calibration_score"] <= 0.99
    assert all(row["p10_grid_kw"] <= row["p50_grid_kw"] <= row["p90_grid_kw"] for row in forecast["portfolio"])

    bids = optimize_bid_blocks(repo, "t-001", forecast, _policy())
    assert bids
    for bid in bids:
        assert bid["quantity_kw"] <= 5000
        assert sum(item["target_kw"] for item in bid["allocation"]) <= bid["quantity_kw"] + 0.01
        assert bid["limit_price_per_kwh"] >= 0.1
        assert bid["cvar_shortfall_kw"] >= bid["var_shortfall_kw"]
        assert bid["risk_scenario_count"] == forecast["scenario_count"]
        assert bid["risk_algorithm"] == "empirical-capacity-shortfall-cvar-v1"


def test_risk_engine_fails_closed_for_stale_data_and_breaker():
    now = datetime.now(UTC)
    order = {
        "quantity_kw": 1000,
        "delivery_start": now.isoformat(),
        "delivery_end": (now + timedelta(hours=1)).isoformat(),
        "limit_price_per_kwh": 0.8,
        "confidence": 0.95,
    }
    approved = evaluate_order_risk(
        order,
        _policy(),
        open_order_count=0,
        committed_energy_kwh=0,
        circuit_state="closed",
        telemetry_age_seconds=60,
    )
    assert approved["approved"] is True

    rejected = evaluate_order_risk(
        order,
        _policy(),
        open_order_count=0,
        committed_energy_kwh=0,
        circuit_state="open",
        telemetry_age_seconds=901,
    )
    assert rejected["approved"] is False
    assert "circuit_breaker_open" in rejected["reasons"]
    assert "telemetry_stale" in rejected["reasons"]


def test_order_state_machine_rejects_illegal_transitions():
    validate_order_transition("draft", "ready")
    validate_order_transition("submitted", "partially_filled")
    with pytest.raises(ValueError, match="invalid market order transition"):
        validate_order_transition("ready", "filled")
    with pytest.raises(ValueError):
        validate_order_transition("filled", "cancelled")


def test_sandbox_adapter_is_deterministic_and_idempotent():
    adapter = SandboxMarketAdapter()
    first = adapter.submit_order({"client_order_id": "co-1"})
    second = adapter.submit_order({"client_order_id": "co-1"})
    assert first.accepted is True
    assert first.market_order_id == second.market_order_id
    assert adapter.cancel_order({"market_order_id": first.market_order_id}).status == "cancelled"


def test_settlement_uses_interval_energy_and_evidence_hashes():
    start = datetime(2026, 1, 1, tzinfo=UTC)
    trade = {
        "quantity_kw": 1000,
        "price_per_kwh": 1.2,
        "delivery_start": start,
        "delivery_end": start + timedelta(hours=1),
    }
    intervals = [
        {
            "interval_start": start + timedelta(minutes=15 * index),
            "interval_end": start + timedelta(minutes=15 * (index + 1)),
            "delivered_kw": 900,
            "quality": "measured",
            "evidence_hash": f"hash-{index}",
        }
        for index in range(4)
    ]
    result = calculate_trade_settlement(trade, intervals, imbalance_price_per_kwh=0.8, penalty_rate=0.25)
    assert result["committed_kwh"] == 1000
    assert result["delivered_kwh"] == 900
    assert result["performance_score"] == 0.9
    assert result["gross_revenue"] == 1080
    assert result["net_revenue"] < result["gross_revenue"]
    assert len(result["evidence"]["evidence_root_hash"]) == 64


def test_market_webhook_signature_replay_window():
    body = b'{"market_trade_id":"trade-1"}'
    timestamp = str(int(datetime.now(UTC).timestamp()))
    signature = hmac.new(b"secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert verify_market_webhook(body, timestamp, signature, "secret") is True
    assert verify_market_webhook(body, timestamp, "bad", "secret") is False
    stale = str(int((datetime.now(UTC) - timedelta(minutes=10)).timestamp()))
    stale_signature = hmac.new(b"secret", stale.encode() + b"." + body, hashlib.sha256).hexdigest()
    assert verify_market_webhook(body, stale, stale_signature, "secret") is False
