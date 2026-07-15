from datetime import UTC, datetime
from unittest.mock import patch

from chargeopt.vpp_automation import run_automation_cycle


def test_live_readiness_gate_skips_before_forecast_or_order_creation():
    context = {
        "circuit_breaker": {"state": "closed"},
        "connection": {
            "mode": "live",
            "live_readiness": {"ready": False, "blockers": ["market_certificate_verified_and_valid"]},
        },
    }
    with (
        patch("chargeopt.vpp_automation.claim_automation_cycle", return_value="run-1"),
        patch("chargeopt.vpp_automation.get_trading_context", return_value=context),
        patch("chargeopt.vpp_automation.finish_automation_cycle") as finish,
        patch("chargeopt.vpp_automation.load_repository_from_db") as load_repo,
    ):
        result = run_automation_cycle("t-001", now=datetime(2026, 7, 15, 12, tzinfo=UTC))

    assert result["status"] == "skipped"
    assert result["reason"] == "live_market_readiness_gate"
    assert result["orders_created"] == 0
    load_repo.assert_not_called()
    finish.assert_called_once_with(
        "t-001",
        "run-1",
        "skipped",
        {"reason": "live_market_readiness_gate", "blockers": ["market_certificate_verified_and_valid"]},
    )
