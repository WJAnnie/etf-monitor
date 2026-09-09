from __future__ import annotations

from scripts.run_full_a_scan_v3 import _enforce_first_buy_permission


def test_120m_first_buy_is_prepare_only_when_no_hard_blocker():
    candidate = {"signal": "FIRST_BUY", "timeframe": "120m", "blockers": [], "action": "BUY_TRANCHE_1", "push": True}
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "PREPARE_BUY"
    assert candidate["push"] is True


def test_30m_first_buy_is_observation_not_full_execution():
    candidate = {"signal": "FIRST_BUY", "timeframe": "30m", "blockers": [], "action": "BUY_TRANCHE_1", "push": True}
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "OBSERVE"
    assert candidate["push"] is False


def test_first_buy_never_bypasses_fundamental_or_risk_blocker():
    for blocker in ("FUNDAMENTAL_VETO", "RISK_TOO_HIGH", "DATA_INCOMPLETE", "PARENT_CONTEXT_INVALID"):
        candidate = {"signal": "FIRST_BUY", "timeframe": "120m", "blockers": [blocker], "action": "OBSERVE", "push": False}
        _enforce_first_buy_permission(candidate)
        assert candidate["action"] == "OBSERVE"
        assert candidate["push"] is False
        assert "硬阻断" in candidate["recent_signal_note"]
