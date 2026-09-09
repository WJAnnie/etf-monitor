from trading_skill.position import BreakState, Protection, evaluate_protection_break
from trading_skill.sizing import StopLevel


def _protection():
    return Protection("t1", StopLevel.L120, 1000, "center-1")


def test_lower_timeframe_break_only_warns_for_120m_protection():
    result = evaluate_protection_break(
        _protection(), observed_level=StopLevel.L5, low_ticks=980, close_ticks=985, bar_complete=True
    )
    assert result.state is BreakState.LOWER_LEVEL_WARNING
    assert result.confirmed_failure is False
    assert result.exit_managed_tranche is False


def test_incomplete_same_timeframe_bar_cannot_confirm_stop():
    result = evaluate_protection_break(
        _protection(), observed_level=StopLevel.L120, low_ticks=980, close_ticks=985, bar_complete=False
    )
    assert result.state is BreakState.LOWER_LEVEL_WARNING
    assert result.confirmed_failure is False


def test_same_timeframe_wick_break_that_closes_back_above_is_warning_only():
    result = evaluate_protection_break(
        _protection(), observed_level=StopLevel.L120, low_ticks=990, close_ticks=1005, bar_complete=True
    )
    assert result.state is BreakState.WICK_BREAK
    assert result.confirmed_failure is False


def test_same_timeframe_completed_close_break_exits_managed_tranche():
    result = evaluate_protection_break(
        _protection(), observed_level=StopLevel.L120, low_ticks=980, close_ticks=990, bar_complete=True
    )
    assert result.state is BreakState.CLOSE_BREAK
    assert result.confirmed_failure is True
    assert result.exit_managed_tranche is True


def test_failed_reclaim_is_stronger_confirmed_failure():
    result = evaluate_protection_break(
        _protection(),
        observed_level=StopLevel.L120,
        low_ticks=970,
        close_ticks=990,
        bar_complete=True,
        reclaim_attempt_completed=True,
        reclaim_close_ticks=995,
    )
    assert result.state is BreakState.BREAK_AND_FAILED_RECLAIM
    assert result.confirmed_failure is True
    assert result.exit_managed_tranche is True
