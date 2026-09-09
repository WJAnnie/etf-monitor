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


def test_120m_first_buy_is_prepare_only_when_grade_and_maturity_allow():
    candidate = _candidate()
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "PREPARE_BUY"
    assert candidate["push"] is True
    assert "仅进入准备" in candidate["recent_signal_note"]


def test_120m_first_buy_cannot_revive_grade_c_trade():
    candidate = _candidate(opportunity="C", action="OBSERVE", push=False)
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "OBSERVE"
    assert candidate["push"] is False
    assert "机会等级仅C" in candidate["recent_signal_note"]


def test_120m_first_buy_cannot_revive_distant_or_immature_trade():
    for maturity in ("WATCH", "NOT_READY"):
        candidate = _candidate(maturity=maturity, action="OBSERVE", push=False)
        _enforce_first_buy_permission(candidate)
        assert candidate["action"] == "OBSERVE"
        assert candidate["push"] is False
        assert "执行尚未成熟" in candidate["recent_signal_note"] or "离买点过远" in candidate["recent_signal_note"]


def test_30m_first_buy_is_observation_not_full_execution():
    candidate = _candidate(timeframe="30m")
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "OBSERVE"
    assert candidate["push"] is False


def test_daily_first_buy_always_waits_for_standard_second_buy():
    candidate = _candidate(timeframe="daily", blockers=["DAILY_FIRST_BUY_WAIT_2B"], action="WAIT_2B", push=False)
    _enforce_first_buy_permission(candidate)
    assert candidate["action"] == "WAIT_2B"
    assert candidate["push"] is False
    assert "等待标准二买" in candidate["recent_signal_note"]


def test_first_buy_never_bypasses_fundamental_or_risk_blocker():
    for blocker in ("FUNDAMENTAL_VETO", "RISK_TOO_HIGH", "DATA_INCOMPLETE", "PARENT_CONTEXT_INVALID"):
        candidate = _candidate(blockers=[blocker], action="OBSERVE", push=False)
        _enforce_first_buy_permission(candidate)
        assert candidate["action"] == "OBSERVE"
        assert candidate["push"] is False
        assert "硬阻断" in candidate["recent_signal_note"]
