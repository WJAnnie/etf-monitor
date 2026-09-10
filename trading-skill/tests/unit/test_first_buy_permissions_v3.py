from __future__ import annotations

from scripts.run_full_a_scan_v3 import _enforce_first_buy_permission


def _candidate(*, timeframe="120m", blockers=None, opportunity="A", maturity="TRIGGERED", action="BUY_TRANCHE_1", push=True):
    return {
        "signal": "FIRST_BUY",
        "timeframe": timeframe,
        "blockers": list(blockers or []),
        "opportunity": opportunity,
        "execution_maturity": maturity,
        "action": action,
        "push": push,
    }


def test_120m_first_buy_is_observation_not_prepare_entry_even_when_mature():
    candidate = _candidate()
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "OBSERVE"
    assert candidate["push"] is False
    assert candidate["buy_amount"] is None
    assert candidate["buy_quantity"] is None
    assert "不是新开仓授权周期" in candidate["recent_signal_note"]


def test_120m_first_buy_grade_c_stays_observation_and_never_revives_trade():
    candidate = _candidate(opportunity="C", action="OBSERVE", push=False)
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "OBSERVE"
    assert candidate["push"] is False


def test_120m_first_buy_maturity_cannot_create_permission_at_any_distance():
    for maturity in ("TRIGGERED", "PREPARE", "WATCH", "NOT_READY"):
        candidate = _candidate(maturity=maturity)
        _enforce_first_buy_permission(candidate)
        assert candidate["action"] == "OBSERVE"
        assert candidate["push"] is False
        assert "不能单独准备或建立新仓" in candidate["recent_signal_note"]


def test_30m_first_buy_is_observation_not_full_execution():
    candidate = _candidate(timeframe="30m")
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "OBSERVE"
    assert candidate["push"] is False
    assert "不是新开仓授权周期" in candidate["recent_signal_note"]


def test_daily_first_buy_always_waits_for_standard_second_buy():
    candidate = _candidate(timeframe="daily", blockers=["DAILY_FIRST_BUY_WAIT_2B"], action="WAIT_2B", push=False)
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "WAIT_2B"
    assert candidate["push"] is False
    assert "等待标准二买" in candidate["recent_signal_note"]
    assert "低周期" not in candidate["recent_signal_note"] or "不得提前创造新开仓资格" in candidate["recent_signal_note"]


def test_first_buy_never_bypasses_fundamental_or_risk_blocker():
    for blocker in ("FUNDAMENTAL_VETO", "RISK_TOO_HIGH", "DATA_INCOMPLETE", "PARENT_CONTEXT_INVALID"):
        candidate = _candidate(blockers=[blocker], action="OBSERVE", push=False)
        _enforce_first_buy_permission(candidate)
        assert candidate["action"] == "OBSERVE"
        assert candidate["push"] is False
        assert "硬阻断" in candidate["recent_signal_note"]
