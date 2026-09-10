from trading_skill.domain.enums import Timeframe
from trading_skill.position_policy_v2 import (
    AddGate,
    CapacityBasis,
    ExitIntent,
    add_permission,
    protection_can_tighten,
    sell_policy,
    staged_position_policy,
    take_profit_contract,
)
from trading_skill.sizing import StopLevel, TrancheRole


def _rules():
    return {rule.role: rule for rule in staged_position_policy().rules}


def test_staged_position_is_not_a_static_grade_based_full_position_plan():
    policy = staged_position_policy()
    rules = _rules()
    assert policy.static_full_position_fraction_plan is False
    assert rules[TrancheRole.TEST].capacity_fraction is None
    assert rules[TrancheRole.TEST].capacity_basis is CapacityBasis.EXPLICIT_TEST_RISK
    assert rules[TrancheRole.CONFIRMATION].capacity_fraction == 0.30
    assert rules[TrancheRole.CORE].capacity_fraction == 0.50
    assert rules[TrancheRole.TREND_ADD].capacity_fraction == 0.25
    assert all(not rule.may_average_down for rule in policy.rules)


def test_add_gates_follow_5m_test_30m_confirmation_120m_core_daily_trend_progression():
    rules = _rules()
    assert rules[TrancheRole.TEST].add_gate is AddGate.INITIAL_EXECUTION_CHAIN
    assert rules[TrancheRole.CONFIRMATION].add_gate is AddGate.NEW_M30_STRUCTURE
    assert rules[TrancheRole.CORE].add_gate is AddGate.NEW_M120_STRUCTURE
    assert rules[TrancheRole.TREND_ADD].add_gate is AddGate.DAILY_TREND_CONTINUATION
    assert rules[TrancheRole.TEST].management_stop_level is StopLevel.L5
    assert rules[TrancheRole.CONFIRMATION].management_stop_level is StopLevel.L30
    assert rules[TrancheRole.CORE].management_stop_level is StopLevel.LD
    assert rules[TrancheRole.TREND_ADD].management_stop_level is StopLevel.L120


def test_failed_structure_can_never_authorize_average_down():
    rule = _rules()[TrancheRole.CONFIRMATION]
    permission = add_permission(
        rule,
        daily_thesis_valid=True,
        new_m30_structure=True,
        current_structure_failed=True,
        risk_allows_add=True,
    )
    assert not permission.allowed
    assert "FAILED_STRUCTURE_CANNOT_BE_AVERAGED_DOWN" in permission.reason_codes


def test_new_structure_is_required_for_later_adds():
    rules = _rules()
    confirm = add_permission(rules[TrancheRole.CONFIRMATION], daily_thesis_valid=True)
    core = add_permission(rules[TrancheRole.CORE], daily_thesis_valid=True)
    trend = add_permission(rules[TrancheRole.TREND_ADD], daily_thesis_valid=True)
    assert not confirm.allowed and "NEW_M30_STRUCTURE_REQUIRED" in confirm.reason_codes
    assert not core.allowed and "NEW_M120_STRUCTURE_REQUIRED" in core.reason_codes
    assert not trend.allowed and "DAILY_TREND_CONTINUATION_REQUIRED" in trend.reason_codes


def _reduction(policy, role):
    return next((item.fraction_of_role for item in policy.reductions if item.role is role), 0.0)


def test_low_timeframe_sells_are_staged_and_do_not_immediately_liquidate_core():
    assert _reduction(sell_policy(Timeframe.M5, 1), TrancheRole.TEST) == 0.50
    assert _reduction(sell_policy(Timeframe.M5, 1), TrancheRole.CORE) == 0.0
    assert _reduction(sell_policy(Timeframe.M30, 1), TrancheRole.CONFIRMATION) == 0.50
    assert _reduction(sell_policy(Timeframe.M30, 1), TrancheRole.CORE) == 0.0
    assert _reduction(sell_policy(Timeframe.M120, 1), TrancheRole.CORE) == 0.0
    assert sell_policy(Timeframe.M120, 1).intent is not ExitIntent.EXIT_ALL


def test_120m_second_and_third_sell_only_begin_partial_core_reduction():
    second = sell_policy(Timeframe.M120, 2)
    third = sell_policy(Timeframe.M120, 3)
    assert second.intent is ExitIntent.REDUCE_CORE
    assert third.intent is ExitIntent.REDUCE_CORE
    assert _reduction(second, TrancheRole.CORE) == 0.25
    assert _reduction(third, TrancheRole.CORE) == 0.50


def test_daily_sells_reduce_core_25_50_then_exit():
    first = sell_policy(Timeframe.DAILY, 1)
    second = sell_policy(Timeframe.DAILY, 2)
    third = sell_policy(Timeframe.DAILY, 3)
    assert first.intent is ExitIntent.REDUCE_CORE
    assert second.intent is ExitIntent.REDUCE_CORE
    assert third.intent is ExitIntent.EXIT_ALL
    assert _reduction(first, TrancheRole.CORE) == 0.25
    assert _reduction(second, TrancheRole.CORE) == 0.50
    assert _reduction(third, TrancheRole.CORE) == 1.00


def test_weekly_first_and_second_sell_are_staged_not_automatic_full_exit():
    first = sell_policy(Timeframe.WEEKLY, 1)
    second = sell_policy(Timeframe.WEEKLY, 2)
    third = sell_policy(Timeframe.WEEKLY, 3)
    assert first.intent is ExitIntent.REDUCE_CORE
    assert second.intent is ExitIntent.REDUCE_CORE
    assert third.intent is ExitIntent.EXIT_ALL
    assert _reduction(first, TrancheRole.CORE) == 0.50
    assert _reduction(second, TrancheRole.CORE) == 0.75
    assert _reduction(third, TrancheRole.CORE) == 1.00


def test_profit_protection_is_one_way_and_requires_new_structure():
    assert protection_can_tighten(current_stop_ticks=1000, proposed_stop_ticks=1010, new_confirmed_structure=True)
    assert not protection_can_tighten(current_stop_ticks=1000, proposed_stop_ticks=990, new_confirmed_structure=True)
    assert not protection_can_tighten(current_stop_ticks=1000, proposed_stop_ticks=1010, new_confirmed_structure=False)


def test_take_profit_is_structural_not_fixed_percent_primary():
    policy = staged_position_policy()
    assert not policy.fixed_percent_take_profit_primary
    contract = " ".join(take_profit_contract())
    assert "固定盈利百分比" in contract
    assert "日线一卖" in contract
    assert "周线一卖" in contract
    assert "周线三卖" in contract
