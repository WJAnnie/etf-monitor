from trading_skill.decision import OpportunityGrade
from trading_skill.domain.enums import Timeframe
from trading_skill.position_policy_v2 import (
    AddGate,
    ExitIntent,
    add_permission,
    protection_can_tighten,
    sell_policy,
    staged_position_policy,
    take_profit_contract,
)
from trading_skill.sizing import StopLevel, TrancheRole


def _rules(grade=OpportunityGrade.A):
    return {rule.role: rule for rule in staged_position_policy(grade).rules}


def test_staged_position_fractions_sum_to_one_for_tradeable_grades():
    for grade in (OpportunityGrade.S, OpportunityGrade.A, OpportunityGrade.B):
        policy = staged_position_policy(grade)
        assert policy.total_fraction == 1.0
        assert policy.rules[0].role is TrancheRole.TEST
        assert policy.rules[0].target_fraction > 0
        assert all(not rule.may_average_down for rule in policy.rules)


def test_c_grade_has_no_position_plan():
    assert staged_position_policy(OpportunityGrade.C).rules == ()


def test_add_gates_follow_30m_120m_daily_progression():
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


def test_low_timeframe_sell_cannot_liquidate_daily_core():
    for timeframe in (Timeframe.M5, Timeframe.M30, Timeframe.M120):
        policy = sell_policy(timeframe, 1)
        assert TrancheRole.CORE not in policy.affected_roles
        assert policy.intent is not ExitIntent.EXIT_ALL


def test_daily_second_sell_is_partial_core_reduction_not_full_exit():
    policy = sell_policy(Timeframe.DAILY, 2)
    assert policy.intent is ExitIntent.REDUCE_CORE
    core = next(item for item in policy.reductions if item.role is TrancheRole.CORE)
    assert core.fraction_of_role == 0.50
    assert policy.intent is not ExitIntent.EXIT_ALL


def test_daily_third_sell_and_weekly_failure_exit_all():
    daily = sell_policy(Timeframe.DAILY, 3)
    weekly = sell_policy(Timeframe.WEEKLY, 1)
    assert daily.intent is ExitIntent.EXIT_ALL
    assert weekly.intent is ExitIntent.EXIT_ALL
    assert all(item.fraction_of_role == 1.0 for item in daily.reductions)
    assert all(item.fraction_of_role == 1.0 for item in weekly.reductions)


def test_profit_protection_is_one_way_and_requires_new_structure():
    assert protection_can_tighten(current_stop_ticks=1000, proposed_stop_ticks=1010, new_confirmed_structure=True)
    assert not protection_can_tighten(current_stop_ticks=1000, proposed_stop_ticks=990, new_confirmed_structure=True)
    assert not protection_can_tighten(current_stop_ticks=1000, proposed_stop_ticks=1010, new_confirmed_structure=False)


def test_take_profit_is_structural_not_fixed_percent_primary():
    policy = staged_position_policy(OpportunityGrade.A)
    assert not policy.fixed_percent_take_profit_primary
    contract = " ".join(take_profit_contract())
    assert "固定盈利百分比" in contract
    assert "日线二卖" in contract
    assert "日线三卖" in contract
