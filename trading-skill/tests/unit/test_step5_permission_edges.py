from trading_skill.trade_permission import (
    EventEntryState,
    TradePermissionBlocker,
    TradePermissionState,
    evaluate_trade_permission,
)


def _candidate(**overrides):
    row = {
        "timeframe": "daily",
        "signal_id": "sig-daily-2b",
        "signal_type": "SECOND_BUY",
        "state": "READY",
        "executable_candidate": True,
    }
    row.update(overrides)
    return row


def _evaluate(technical, **overrides):
    kwargs = {
        "quality_status": "PASS",
        "quality_deep_analysis_eligible": True,
        "event_state": EventEntryState.CLEAR,
        "authority_stop_defined": True,
        "execution_stop_defined": True,
        "account_context_known": True,
        "account_allows_security": True,
        "portfolio_context_known": True,
        "portfolio_allows_new_risk": True,
    }
    kwargs.update(overrides)
    return evaluate_trade_permission(technical, **kwargs)


def test_unknown_quality_is_context_required_not_fake_hard_reject():
    decision = _evaluate(
        {"best_executable_candidate": _candidate(), "dominant_current_buy": _candidate()},
        quality_status="UNKNOWN",
        quality_deep_analysis_eligible=False,
    )
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.QUALITY_REVIEW_REQUIRED in decision.blockers
    assert TradePermissionBlocker.QUALITY_REJECTED not in decision.blockers


def test_pass_with_inconsistent_deep_eligibility_requires_review_instead_of_hard_reject():
    decision = _evaluate(
        {"best_executable_candidate": _candidate(), "dominant_current_buy": _candidate()},
        quality_status="PASS",
        quality_deep_analysis_eligible=False,
    )
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert decision.blockers == (TradePermissionBlocker.QUALITY_REVIEW_REQUIRED,)


def test_malformed_best_candidate_is_not_trusted_just_because_field_exists():
    malformed = _candidate(state="WAIT_LOWER_CONFIRMATION", executable_candidate=False)
    decision = _evaluate({"best_executable_candidate": malformed, "dominant_current_buy": malformed})
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.new_entry_allowed is False


def test_lower_timeframe_candidate_is_not_trusted_even_if_marked_ready():
    fake_lower = _candidate(timeframe="120m")
    decision = _evaluate({"best_executable_candidate": fake_lower, "dominant_current_buy": fake_lower})
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.new_entry_allowed is False


def test_daily_first_buy_waits_and_never_becomes_prepare_entry():
    fake_daily = _candidate(
        timeframe="daily",
        signal_type="FIRST_BUY",
        state="WAIT_STANDARD_SECOND_BUY",
        executable_candidate=False,
    )
    decision = _evaluate({"best_executable_candidate": None, "dominant_current_buy": fake_daily})
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.new_entry_allowed is False
