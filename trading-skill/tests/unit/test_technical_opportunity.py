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


def test_standard_second_buy_with_aligned_context_is_ready_without_score():
    item = evaluate_technical_opportunity(Timeframe.M120, context())
    assert item.state is TechnicalOpportunityState.READY
    assert item.executable_candidate is True
    assert item.signal_type.value == "SECOND_BUY"


def test_parent_caution_is_not_hard_block_when_lower_structure_is_aligned():
    item = evaluate_technical_opportunity(Timeframe.DAILY, context(kind="THIRD_BUY", parent="CAUTION"))
    assert item.state is TechnicalOpportunityState.READY_WITH_CAUTION
    assert item.executable_candidate is True


def test_history_limit_blocks_readiness_before_lower_confirmation():
    item = evaluate_technical_opportunity(Timeframe.M30, context(history=False, lower="ALIGNED"))
    assert item.state is TechnicalOpportunityState.HISTORY_LIMITED
    assert item.executable_candidate is False


def test_parent_current_sell_conflict_is_distinct_from_parent_structure():
    item = evaluate_technical_opportunity(
        Timeframe.M120,
        context(parent="SUPPORTIVE", parent_sell=True, lower="ALIGNED"),
    )
    assert item.state is TechnicalOpportunityState.PARENT_SIGNAL_CONFLICT
    assert item.parent_structure_state == "SUPPORTIVE"


def test_parent_structural_block_and_missing_parent_evidence_are_separate_states():
    blocked = evaluate_technical_opportunity(Timeframe.M120, context(parent="BLOCKED"))
    unresolved = evaluate_technical_opportunity(Timeframe.M120, context(parent="UNRESOLVED"))
    assert blocked.state is TechnicalOpportunityState.PARENT_STRUCTURE_BLOCKED
    assert unresolved.state is TechnicalOpportunityState.PARENT_STRUCTURE_UNRESOLVED


def test_lower_sell_waits_for_pullback_without_invalidating_primary_signal():
    item = evaluate_technical_opportunity(Timeframe.M120, context(lower="WAITING_PULLBACK"))
    assert item.state is TechnicalOpportunityState.WAIT_PULLBACK
    assert item.executable_candidate is False


def test_lower_mixed_or_unresolved_waits_for_confirmation():
    mixed = evaluate_technical_opportunity(Timeframe.M120, context(lower="MIXED"))
    unresolved = evaluate_technical_opportunity(Timeframe.M120, context(lower="UNRESOLVED"))
    assert mixed.state is TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION
    assert unresolved.state is TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION


def test_first_buy_permissions_remain_distinct_by_timeframe():
    daily = evaluate_technical_opportunity(Timeframe.DAILY, context(kind="FIRST_BUY"))
    m120 = evaluate_technical_opportunity(Timeframe.M120, context(kind="FIRST_BUY"))
    m30 = evaluate_technical_opportunity(Timeframe.M30, context(kind="FIRST_BUY"))
    assert daily.state is TechnicalOpportunityState.WAIT_STANDARD_SECOND_BUY
    assert m120.state is TechnicalOpportunityState.PREPARE_FIRST_BUY
    assert m30.state is TechnicalOpportunityState.OBSERVE_FIRST_BUY
    assert not any(item.executable_candidate for item in (daily, m120, m30))


def test_five_minute_signal_can_never_become_primary_opportunity():
    item = evaluate_technical_opportunity(Timeframe.M5, context())
    assert item.state is TechnicalOpportunityState.NOT_PRIMARY_TIMEFRAME
    assert item.executable_candidate is False


def test_class2_is_reported_as_metadata_and_does_not_change_permission_state():
    plain = evaluate_technical_opportunity(Timeframe.M120, context())
    class2 = evaluate_technical_opportunity(
        Timeframe.M120,
        context(class2=("STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY")),
    )
    assert class2.state is plain.state is TechnicalOpportunityState.READY
    assert class2.class2_types == ("STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY")


def test_dominant_structure_and_best_executable_candidate_are_not_the_same_concept():
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
    assert executable is not None and executable.timeframe is Timeframe.M120
    assert executable.state is TechnicalOpportunityState.READY


def test_no_ready_opportunity_returns_no_executable_candidate_instead_of_forcing_one():
    opportunities = build_technical_opportunities(
        {
            "daily": context(kind="THIRD_BUY", lower="UNRESOLVED"),
            "120m": context(kind="SECOND_BUY", lower="WAITING_PULLBACK"),
        }
    )
    assert dominant_current_buy(opportunities).timeframe is Timeframe.DAILY
    assert best_executable_candidate(opportunities) is None
