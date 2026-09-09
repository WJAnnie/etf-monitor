from trading_skill.structural_stop import resolve_structural_stop
from trading_skill.trade_permission import (
    EntryMode,
    EventEntryState,
    TradePermissionBlocker,
    TradePermissionState,
    evaluate_event_entry_state,
    evaluate_trade_permission,
)


def _candidate(state="READY", timeframe="120m", signal_type="SECOND_BUY", signal_id="sig-1"):
    return {
        "timeframe": timeframe,
        "signal_id": signal_id,
        "signal_type": signal_type,
        "state": state,
        "executable_candidate": state in {"READY", "READY_WITH_CAUTION"},
    }


def _technical(*, executable=None, dominant=None):
    return {
        "best_executable_candidate": executable,
        "dominant_current_buy": dominant,
    }


def _permission(technical, **overrides):
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


def test_no_ready_or_test_candidate_waits_without_manufacturing_permission():
    decision = _permission(_technical(dominant=_candidate(state="WAIT_LOWER_CONFIRMATION", timeframe="daily", signal_type="THIRD_BUY")))
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.entry_mode is EntryMode.NONE
    assert decision.new_entry_allowed is False
    assert decision.blockers == ()


def test_standard_ready_passes_all_independent_gates():
    decision = _permission(_technical(executable=_candidate()))
    assert decision.state is TradePermissionState.ELIGIBLE
    assert decision.entry_mode is EntryMode.STANDARD
    assert decision.new_entry_allowed is True


def test_ready_with_parent_caution_keeps_caution_without_blocking():
    decision = _permission(_technical(executable=_candidate(state="READY_WITH_CAUTION")))
    assert decision.state is TradePermissionState.ELIGIBLE_WITH_CAUTION
    assert decision.new_entry_allowed is True
    assert decision.cautions


def test_120m_first_buy_can_only_become_test_entry_permission():
    first_buy = _candidate(state="PREPARE_FIRST_BUY", timeframe="120m", signal_type="FIRST_BUY")
    decision = _permission(_technical(dominant=first_buy))
    assert decision.state is TradePermissionState.TEST_ENTRY_ELIGIBLE
    assert decision.entry_mode is EntryMode.TEST
    assert decision.new_entry_allowed is True
    assert any("试仓" in item for item in decision.cautions)


def test_quality_watch_requires_review_instead_of_automatic_risk_discount():
    decision = _permission(_technical(executable=_candidate()), quality_status="WATCH")
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert decision.new_entry_allowed is False
    assert TradePermissionBlocker.QUALITY_REVIEW_REQUIRED in decision.blockers


def test_quality_reject_is_hard_blocker_even_when_technical_ready():
    decision = _permission(_technical(executable=_candidate()), quality_status="REJECT", quality_deep_analysis_eligible=False)
    assert decision.state is TradePermissionState.BLOCKED
    assert TradePermissionBlocker.QUALITY_REJECTED in decision.blockers


def test_major_negative_event_blocks_new_entry_but_positive_does_not_create_permission():
    major_negative = [{"importance": "重大", "impact": "利空"}]
    assert evaluate_event_entry_state(major_negative, data_complete=True) is EventEntryState.BLOCK_NEW_ENTRY
    decision = _permission(
        _technical(executable=_candidate()),
        event_state=EventEntryState.BLOCK_NEW_ENTRY,
    )
    assert decision.state is TradePermissionState.BLOCKED
    assert TradePermissionBlocker.MAJOR_NEGATIVE_EVENT in decision.blockers

    major_positive = [{"importance": "重大", "impact": "利好"}]
    assert evaluate_event_entry_state(major_positive, data_complete=True) is EventEntryState.CLEAR
    no_tech = _permission(_technical(), event_state=EventEntryState.CLEAR)
    assert no_tech.state is TradePermissionState.WAIT_TECHNICAL


def test_missing_event_feed_is_not_silently_treated_as_clear():
    assert evaluate_event_entry_state([], data_complete=False) is EventEntryState.UNKNOWN
    decision = _permission(_technical(executable=_candidate()), event_state=EventEntryState.UNKNOWN)
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.EVENT_CONTEXT_UNAVAILABLE in decision.blockers


def test_structural_stop_is_required_for_any_new_entry():
    decision = _permission(_technical(executable=_candidate()), structural_stop_defined=False)
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.STRUCTURAL_STOP_UNDEFINED in decision.blockers


def test_account_denial_and_portfolio_full_are_hard_independent_blockers():
    decision = _permission(
        _technical(executable=_candidate()),
        account_allows_security=False,
        portfolio_allows_new_risk=False,
    )
    assert decision.state is TradePermissionState.BLOCKED
    assert TradePermissionBlocker.ACCOUNT_SECURITY_NOT_ALLOWED in decision.blockers
    assert TradePermissionBlocker.PORTFOLIO_RISK_FULL in decision.blockers


def test_unknown_portfolio_capacity_never_defaults_to_available():
    decision = _permission(
        _technical(executable=_candidate()),
        portfolio_context_known=False,
    )
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert decision.new_entry_allowed is False
    assert TradePermissionBlocker.PORTFOLIO_CONTEXT_UNAVAILABLE in decision.blockers


def test_multiple_failures_are_preserved_instead_of_collapsed_into_one_risk_score():
    decision = _permission(
        _technical(executable=_candidate()),
        quality_status="WATCH",
        event_state=EventEntryState.UNKNOWN,
        structural_stop_defined=False,
        account_context_known=False,
        portfolio_context_known=False,
    )
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert set(decision.blockers) == {
        TradePermissionBlocker.QUALITY_REVIEW_REQUIRED,
        TradePermissionBlocker.EVENT_CONTEXT_UNAVAILABLE,
        TradePermissionBlocker.STRUCTURAL_STOP_UNDEFINED,
        TradePermissionBlocker.ACCOUNT_CONTEXT_UNAVAILABLE,
        TradePermissionBlocker.PORTFOLIO_CONTEXT_UNAVAILABLE,
    }


def test_structural_stop_resolves_only_matching_signal_and_must_be_below_latest_close():
    structure = {
        "tick_size": "0.01",
        "chan": {
            "120m": {
                "latest_close": 10.0,
                "signals": [
                    {"id": "old", "structural_price_ticks": 700},
                    {"id": "sig-1", "structural_price_ticks": 920},
                ],
            }
        },
    }
    evidence = resolve_structural_stop(structure, _candidate())
    assert evidence.found is True
    assert evidence.valid_for_new_entry is True
    assert evidence.signal_id == "sig-1"
    assert evidence.stop_price == 9.2

    invalid = resolve_structural_stop(
        {**structure, "chan": {"120m": {"latest_close": 9.0, "signals": [{"id": "sig-1", "structural_price_ticks": 920}]}}},
        _candidate(),
    )
    assert invalid.found is True
    assert invalid.valid_for_new_entry is False
