from trading_skill.trade_permission import (
    EventEntryState,
    TradePermissionBlocker,
    TradePermissionState,
    evaluate_trade_permission,
)


def _candidate(**overrides):
    row = {
        "timeframe": "120m",
        "signal_id": "sig-1",
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
        "structural_stop_defined": True,
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


def test_prepare_first_buy_must_really_be_120m_first_buy():
    fake_daily = _candidate(
        timeframe="daily",
        signal_type="FIRST_BUY",
        state="PREPARE_FIRST_BUY",
        executable_candidate=False,
    )
    decision = _evaluate({"best_executable_candidate": None, "dominant_current_buy": fake_daily})
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.new_entry_allowed is False
