from trading_skill.structural_stop import resolve_authority_stop, resolve_execution_stop
from trading_skill.trade_permission import (
    EntryMode,
    EventEntryState,
    TradePermissionBlocker,
    TradePermissionState,
    evaluate_event_entry_state,
    evaluate_trade_permission,
)


def _candidate(state="READY", timeframe="daily", signal_type="SECOND_BUY", signal_id="sig-daily"):
    return {
        "timeframe": timeframe,
        "signal_id": signal_id,
        "signal_type": signal_type,
        "state": state,
        "executable_candidate": state in {"READY", "READY_WITH_CAUTION"},
    }


def _technical(*, executable=None, dominant=None):
    return {"best_executable_candidate": executable, "dominant_current_buy": dominant}


def _permission(technical, **overrides):
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


def test_no_daily_second_buy_execution_chain_never_manufactures_permission():
    decision = _permission(
        _technical(dominant=_candidate(state="CONTINUATION_ONLY", signal_type="THIRD_BUY"))
    )
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.entry_mode is EntryMode.NONE
    assert decision.new_entry_allowed is False
    assert decision.blockers == ()


def test_daily_second_buy_ready_passes_all_independent_gates():
    decision = _permission(_technical(executable=_candidate()))
    assert decision.state is TradePermissionState.ELIGIBLE
    assert decision.entry_mode is EntryMode.STANDARD
    assert decision.new_entry_allowed is True
    assert decision.selected_timeframe == "daily"
    assert decision.signal_type == "SECOND_BUY"


def test_ready_with_weekly_caution_keeps_caution_without_blocking():
    decision = _permission(_technical(executable=_candidate(state="READY_WITH_CAUTION")))
    assert decision.state is TradePermissionState.ELIGIBLE_WITH_CAUTION
    assert decision.new_entry_allowed is True
    assert decision.cautions


def test_120m_first_buy_never_creates_test_entry_permission():
    first_buy = _candidate(
        state="NOT_PRIMARY_TIMEFRAME", timeframe="120m", signal_type="FIRST_BUY", signal_id="sig-120-first"
    )
    decision = _permission(_technical(dominant=first_buy))
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.entry_mode is EntryMode.NONE
    assert decision.new_entry_allowed is False


def test_120m_or_30m_standard_buy_cannot_be_smuggled_as_best_executable_candidate():
    for timeframe in ("120m", "30m"):
        decision = _permission(_technical(executable=_candidate(timeframe=timeframe)))
        assert decision.state is TradePermissionState.WAIT_TECHNICAL
        assert decision.new_entry_allowed is False


def test_daily_third_buy_is_continuation_not_fresh_entry():
    decision = _permission(_technical(executable=_candidate(signal_type="THIRD_BUY")))
    assert decision.state is TradePermissionState.WAIT_TECHNICAL
    assert decision.new_entry_allowed is False


def test_quality_watch_requires_review_instead_of_automatic_risk_discount():
    decision = _permission(_technical(executable=_candidate()), quality_status="WATCH")
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.QUALITY_REVIEW_REQUIRED in decision.blockers


def test_quality_reject_is_hard_blocker_even_when_technical_ready():
    decision = _permission(
        _technical(executable=_candidate()), quality_status="REJECT", quality_deep_analysis_eligible=False
    )
    assert decision.state is TradePermissionState.BLOCKED
    assert TradePermissionBlocker.QUALITY_REJECTED in decision.blockers


def test_major_negative_event_blocks_new_entry_but_positive_does_not_create_permission():
    major_negative = [{"importance": "重大", "impact": "利空"}]
    assert evaluate_event_entry_state(major_negative, data_complete=True) is EventEntryState.BLOCK_NEW_ENTRY
    decision = _permission(_technical(executable=_candidate()), event_state=EventEntryState.BLOCK_NEW_ENTRY)
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


def test_both_daily_authority_and_5m_execution_stops_are_required():
    missing_authority = _permission(
        _technical(executable=_candidate()), authority_stop_defined=False, execution_stop_defined=True
    )
    assert missing_authority.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.STRUCTURAL_STOP_UNDEFINED in missing_authority.blockers

    missing_execution = _permission(
        _technical(executable=_candidate()), authority_stop_defined=True, execution_stop_defined=False
    )
    assert missing_execution.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.EXECUTION_STOP_UNDEFINED in missing_execution.blockers


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
    decision = _permission(_technical(executable=_candidate()), portfolio_context_known=False)
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert TradePermissionBlocker.PORTFOLIO_CONTEXT_UNAVAILABLE in decision.blockers


def test_multiple_failures_are_preserved_instead_of_collapsed_into_one_score():
    decision = _permission(
        _technical(executable=_candidate()),
        quality_status="WATCH",
        event_state=EventEntryState.UNKNOWN,
        authority_stop_defined=False,
        execution_stop_defined=False,
        account_context_known=False,
        portfolio_context_known=False,
    )
    assert decision.state is TradePermissionState.CONTEXT_REQUIRED
    assert set(decision.blockers) == {
        TradePermissionBlocker.QUALITY_REVIEW_REQUIRED,
        TradePermissionBlocker.EVENT_CONTEXT_UNAVAILABLE,
        TradePermissionBlocker.STRUCTURAL_STOP_UNDEFINED,
        TradePermissionBlocker.EXECUTION_STOP_UNDEFINED,
        TradePermissionBlocker.ACCOUNT_CONTEXT_UNAVAILABLE,
        TradePermissionBlocker.PORTFOLIO_CONTEXT_UNAVAILABLE,
    }


def _structure(latest_5m=10.0, with_5m_buy=True, with_later_sell=False):
    five_signals = []
    if with_5m_buy:
        five_signals.append(
            {
                "id": "sig-5m",
                "side": "BUY",
                "types": ["SECOND_BUY"],
                "structural_price_ticks": 960,
                "confirmation_timestamp": "2026-09-09T10:00:00+08:00",
            }
        )
    if with_later_sell:
        five_signals.append(
            {
                "id": "sig-5m-sell",
                "side": "SELL",
                "types": ["FIRST_SELL"],
                "structural_price_ticks": 980,
                "confirmation_timestamp": "2026-09-09T11:00:00+08:00",
            }
        )
    return {
        "tick_size": "0.01",
        "chan": {
            "daily": {
                "latest_close": 10.0,
                "signals": [
                    {
                        "id": "sig-daily",
                        "side": "BUY",
                        "types": ["SECOND_BUY"],
                        "structural_price_ticks": 920,
                        "structural_timestamp": "2026-09-08T15:00:00+08:00",
                        "confirmation_timestamp": "2026-09-09T15:00:00+08:00",
                    }
                ],
            },
            "5m": {"latest_close": latest_5m, "signals": five_signals},
        },
    }


def test_dual_stop_resolver_keeps_daily_authority_and_5m_execution_identity_separate():
    structure = _structure()
    authority = resolve_authority_stop(structure, _candidate())
    execution = resolve_execution_stop(structure, _candidate())
    assert authority.valid_for_new_entry is True
    assert authority.timeframe == "daily"
    assert authority.signal_id == "sig-daily"
    assert authority.stop_price == 9.2
    assert execution.valid_for_new_entry is True
    assert execution.timeframe == "5m"
    assert execution.signal_id == "sig-5m"
    assert execution.authority_signal_id == "sig-daily"
    assert execution.stop_price == 9.59


def test_5m_indicator_cannot_substitute_for_formal_buy_and_newer_sell_invalidates_execution_stop():
    no_buy = resolve_execution_stop(_structure(with_5m_buy=False), _candidate())
    assert no_buy.valid_for_new_entry is False
    assert "正式5分钟BUY" in no_buy.reason

    later_sell = resolve_execution_stop(_structure(with_later_sell=True), _candidate())
    assert later_sell.valid_for_new_entry is False
    assert "更新SELL" in later_sell.reason
