import json

import pytest

from scripts.analyze_step5_trade_permission import _context_for_symbol, _load_risk_context
from trading_skill.trade_permission import EventEntryState, TradePermissionState, evaluate_trade_permission


def test_risk_context_rejects_string_boolean(tmp_path):
    path = tmp_path / "risk.json"
    path.write_text(json.dumps({"account_context_known": "false"}), encoding="utf-8")
    with pytest.raises(ValueError, match="JSON boolean"):
        _load_risk_context(path)


def test_symbol_override_rejects_numeric_boolean(tmp_path):
    path = tmp_path / "risk.json"
    path.write_text(
        json.dumps({"symbols": {"1:600000:STOCK": {"portfolio_context_known": 1}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="JSON boolean"):
        _load_risk_context(path)


def test_valid_global_and_symbol_boolean_context_is_applied(tmp_path):
    path = tmp_path / "risk.json"
    path.write_text(
        json.dumps(
            {
                "account_context_known": True,
                "account_allows_security": True,
                "portfolio_context_known": True,
                "portfolio_allows_new_risk": True,
                "symbols": {"1:600000:STOCK": {"portfolio_allows_new_risk": False}},
            }
        ),
        encoding="utf-8",
    )
    payload = _load_risk_context(path)
    context = _context_for_symbol(payload, {"code": "600000", "market": 1, "security_type": "STOCK"})
    assert context["account_context_known"] is True
    assert context["account_allows_security"] is True
    assert context["portfolio_context_known"] is True
    assert context["portfolio_allows_new_risk"] is False


def _permission_for(candidate):
    return evaluate_trade_permission(
        {"best_executable_candidate": candidate, "dominant_current_buy": candidate},
        quality_status="PASS",
        quality_deep_analysis_eligible=True,
        event_state=EventEntryState.CLEAR,
        structural_stop_defined=True,
        account_context_known=True,
        account_allows_security=True,
        portfolio_context_known=True,
        portfolio_allows_new_risk=True,
    )


def test_ready_candidate_on_5m_is_not_trusted_by_step5a():
    decision = _permission_for(
        {
            "timeframe": "5m",
            "signal_id": "bad",
            "signal_type": "SECOND_BUY",
            "state": "READY",
            "executable_candidate": True,
        }
    )
    assert decision.state is TradePermissionState.WAIT_TECHNICAL


def test_ready_first_buy_is_not_trusted_as_standard_entry():
    decision = _permission_for(
        {
            "timeframe": "120m",
            "signal_id": "bad",
            "signal_type": "FIRST_BUY",
            "state": "READY",
            "executable_candidate": True,
        }
    )
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
