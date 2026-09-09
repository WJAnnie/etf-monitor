from trading_skill.history_policy import classify_history_counts, primary_history_gate
from scripts.run_full_a_scan_v3 import _enforce_history_policy


def test_history_quality_marks_full_long_term_evidence():
    quality = classify_history_counts(daily=1200, weekly=250, m120=400, m30=1600, m5=1200)
    assert quality.long_term_complete is True
    assert quality.long_term_tier == "完整长期证据"
    assert quality.daily_primary_ok is True
    assert quality.m120_primary_ok is True
    assert quality.m30_primary_ok is True


def test_history_quality_allows_30m_for_newer_stock_without_pretending_long_term_complete():
    quality = classify_history_counts(daily=168, weekly=36, m120=338, m30=1352, m5=1200)
    assert quality.long_term_complete is False
    assert quality.long_term_tier == "长期证据不足"
    assert quality.daily_primary_ok is False
    assert quality.m120_primary_ok is False
    assert quality.m30_primary_ok is True
    ok, _ = primary_history_gate("30m", quality)
    assert ok is True


def test_daily_primary_is_observation_when_longer_context_is_insufficient():
    candidate = {"timeframe": "daily", "push": True, "action": "BUY_TRANCHE_1", "blockers": []}
    symbol = {
        "history_quality": classify_history_counts(
            daily=168, weekly=36, m120=338, m30=1352, m5=1200
        ).to_dict()
    }
    _enforce_history_policy(candidate, symbol)
    assert candidate["push"] is False
    assert candidate["action"] == "OBSERVE"
    assert "HISTORY_CONTEXT_INCOMPLETE" in candidate["blockers"]


def test_120m_primary_requires_some_daily_and_weekly_context():
    quality = classify_history_counts(daily=130, weekly=45, m120=400, m30=1600, m5=1200)
    ok, _ = primary_history_gate("120m", quality)
    assert ok is True

    weak = classify_history_counts(daily=130, weekly=30, m120=400, m30=1600, m5=1200)
    ok, _ = primary_history_gate("120m", weak)
    assert ok is False


def test_5m_can_never_become_primary_entry_from_history_gate():
    quality = classify_history_counts(daily=1200, weekly=250, m120=400, m30=1600, m5=1200)
    ok, reason = primary_history_gate("5m", quality)
    assert ok is False
    assert "不是正式主买点" in reason
