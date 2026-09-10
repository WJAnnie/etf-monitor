from __future__ import annotations

from trading_skill.domain.enums import Timeframe
from trading_skill.technical_opportunity import (
    TechnicalOpportunityState,
    best_executable_candidate,
    build_technical_opportunities,
    dominant_current_buy,
    evaluate_technical_opportunity,
)


def context(
    *,
    kind="SECOND_BUY",
    history=True,
    parent="SUPPORTIVE",
    parent_sell=False,
    lower="ALIGNED",
    class2=(),
):
    return {
        "signal": {
            "signal_id": f"sig-{kind}",
            "standard_types": [kind],
            "extended_types": list(class2),
            "age_completed_bars": 3,
        },
        "class2_types": list(class2),
        "history_eligible": history,
        "history_reason": "历史证据不足" if not history else "历史通过",
        "structural_parent_context": {"state": parent},
        "has_parent_current_sell_conflict": parent_sell,
        "lower_context": {"state": lower},
    }


def test_daily_standard_second_buy_with_aligned_execution_chain_is_ready():
    item = evaluate_technical_opportunity(Timeframe.DAILY, context())
    assert item.state is TechnicalOpportunityState.READY
    assert item.executable_candidate is True
    assert item.signal_type.value == "SECOND_BUY"


def test_weekly_caution_is_not_hard_block_for_daily_second_buy_when_chain_aligned():
    item = evaluate_technical_opportunity(Timeframe.DAILY, context(parent="CAUTION"))
    assert item.state is TechnicalOpportunityState.READY_WITH_CAUTION
    assert item.executable_candidate is True


def test_daily_history_limit_blocks_readiness_before_execution_chain():
    item = evaluate_technical_opportunity(Timeframe.DAILY, context(history=False, lower="ALIGNED"))
    assert item.state is TechnicalOpportunityState.HISTORY_LIMITED
    assert item.executable_candidate is False


def test_daily_parent_current_sell_conflict_is_distinct_from_parent_structure():
    item = evaluate_technical_opportunity(
        Timeframe.DAILY,
        context(parent="SUPPORTIVE", parent_sell=True, lower="ALIGNED"),
    )
    assert item.state is TechnicalOpportunityState.PARENT_SIGNAL_CONFLICT
    assert item.parent_structure_state == "SUPPORTIVE"


def test_daily_parent_structural_block_and_missing_parent_are_separate_states():
    blocked = evaluate_technical_opportunity(Timeframe.DAILY, context(parent="BLOCKED"))
    unresolved = evaluate_technical_opportunity(Timeframe.DAILY, context(parent="UNRESOLVED"))
    assert blocked.state is TechnicalOpportunityState.PARENT_STRUCTURE_BLOCKED
    assert unresolved.state is TechnicalOpportunityState.PARENT_STRUCTURE_UNRESOLVED


def test_lower_sell_waits_for_pullback_without_invalidating_daily_second_buy_definition():
    item = evaluate_technical_opportunity(Timeframe.DAILY, context(lower="WAITING_PULLBACK"))
    assert item.state is TechnicalOpportunityState.WAIT_PULLBACK
    assert item.executable_candidate is False


def test_lower_mixed_or_unresolved_waits_for_confirmation():
    mixed = evaluate_technical_opportunity(Timeframe.DAILY, context(lower="MIXED"))
    unresolved = evaluate_technical_opportunity(Timeframe.DAILY, context(lower="UNRESOLVED"))
    assert mixed.state is TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION
    assert unresolved.state is TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION


def test_first_buy_and_third_buy_have_non_entry_meanings():
    first = evaluate_technical_opportunity(Timeframe.DAILY, context(kind="FIRST_BUY"))
    third = evaluate_technical_opportunity(Timeframe.DAILY, context(kind="THIRD_BUY"))
    assert first.state is TechnicalOpportunityState.WAIT_STANDARD_SECOND_BUY
    assert third.state is TechnicalOpportunityState.CONTINUATION_ONLY
    assert first.executable_candidate is False
    assert third.executable_candidate is False


def test_120m_30m_and_5m_can_never_become_standalone_new_entry_opportunity():
    for timeframe in (Timeframe.M120, Timeframe.M30, Timeframe.M5):
        item = evaluate_technical_opportunity(timeframe, context())
        assert item.state is TechnicalOpportunityState.NOT_PRIMARY_TIMEFRAME
        assert item.executable_candidate is False


def test_class2_is_metadata_on_daily_standard_second_buy_and_does_not_create_extra_permission():
    plain = evaluate_technical_opportunity(Timeframe.DAILY, context())
    class2 = evaluate_technical_opportunity(
        Timeframe.DAILY,
        context(class2=("STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY")),
    )
    assert class2.state is plain.state is TechnicalOpportunityState.READY
    assert class2.executable_candidate is plain.executable_candidate is True
    assert class2.class2_types == ("STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY")


def test_dominant_current_buy_can_exist_without_any_fresh_entry_permission():
    opportunities = build_technical_opportunities(
        {
            "daily": context(kind="FIRST_BUY", lower="UNRESOLVED"),
            "120m": context(kind="SECOND_BUY", lower="ALIGNED"),
            "30m": context(kind="THIRD_BUY", lower="ALIGNED"),
        }
    )
    dominant = dominant_current_buy(opportunities)
    executable = best_executable_candidate(opportunities)
    assert dominant is not None and dominant.timeframe is Timeframe.DAILY
    assert dominant.state is TechnicalOpportunityState.WAIT_STANDARD_SECOND_BUY
    assert executable is None


def test_daily_second_buy_is_only_best_executable_even_if_lower_timeframes_have_formal_buys():
    opportunities = build_technical_opportunities(
        {
            "daily": context(kind="SECOND_BUY", lower="ALIGNED"),
            "120m": context(kind="SECOND_BUY", lower="ALIGNED"),
            "30m": context(kind="THIRD_BUY", lower="ALIGNED"),
        }
    )
    executable = best_executable_candidate(opportunities)
    assert executable is not None
    assert executable.timeframe is Timeframe.DAILY
    assert executable.signal_type.value == "SECOND_BUY"
