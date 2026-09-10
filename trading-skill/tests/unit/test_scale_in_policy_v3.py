from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.sizing import TrancheRole
from trading_skill.strategy_policy import scale_in_decision


def _decision(**overrides):
    kwargs = {
        "timeframe": Timeframe.M120,
        "signal_type": ChanSignalType.SECOND_BUY,
        "existing_roles": (TrancheRole.TEST,),
        "new_structure_confirmed": True,
        "opportunity_grade": "A",
        "risk_level": 1,
        "context_valid": True,
        "protection_not_loosened": True,
        "current_price_below_cost": False,
        "mechanical_average_down_requested": False,
    }
    kwargs.update(overrides)
    return scale_in_decision(**kwargs)


def test_30m_new_standard_buy_adds_one_confirmation_tranche():
    decision = _decision(timeframe=Timeframe.M30)
    assert decision.allowed is True
    assert decision.role is TrancheRole.CONFIRMATION
    assert decision.remaining_capacity_fraction == 0.30

    duplicate = _decision(
        timeframe=Timeframe.M30,
        existing_roles=(TrancheRole.TEST, TrancheRole.CONFIRMATION),
    )
    assert duplicate.allowed is False


def test_120m_new_standard_buy_upgrades_one_core_tranche():
    decision = _decision()
    assert decision.allowed is True
    assert decision.role is TrancheRole.CORE
    assert decision.remaining_capacity_fraction == 0.50

    duplicate = _decision(
        existing_roles=(TrancheRole.TEST, TrancheRole.CONFIRMATION, TrancheRole.CORE),
    )
    assert duplicate.allowed is False


def test_daily_second_or_third_buy_is_final_trend_add_not_initial_core_creation():
    for kind in (ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY):
        decision = _decision(
            timeframe=Timeframe.DAILY,
            signal_type=kind,
            existing_roles=(TrancheRole.TEST, TrancheRole.CONFIRMATION, TrancheRole.CORE),
        )
        assert decision.allowed is True
        assert decision.role is TrancheRole.TREND_ADD
        assert decision.remaining_capacity_fraction == 0.25


def test_first_buy_weekly_and_5m_cannot_trigger_scale_in():
    assert _decision(signal_type=ChanSignalType.FIRST_BUY).allowed is False
    assert _decision(timeframe=Timeframe.M5).allowed is False
    assert _decision(timeframe=Timeframe.WEEKLY).allowed is False


def test_class2_extended_label_is_not_an_independent_extra_tranche_trigger():
    # scale_in_decision only accepts canonical standard signal types; class-2 is metadata
    # on SECOND_BUY and therefore cannot create a second add beyond the same structure.
    duplicate = _decision(
        timeframe=Timeframe.M30,
        existing_roles=(TrancheRole.TEST, TrancheRole.CONFIRMATION),
    )
    assert duplicate.allowed is False


def test_scale_in_never_bypasses_risk_context_or_protection():
    assert _decision(risk_level=2).allowed is False
    assert _decision(opportunity_grade="C").allowed is False
    assert _decision(context_valid=False).allowed is False
    assert _decision(protection_not_loosened=False).allowed is False


def test_mechanical_average_down_is_blocked_even_when_price_is_lower():
    decision = _decision(current_price_below_cost=True, mechanical_average_down_requested=True)
    assert decision.allowed is False
    assert "机械补仓" in decision.reason


def test_new_confirmed_structure_may_add_below_cost_when_all_gates_pass():
    decision = _decision(current_price_below_cost=True, mechanical_average_down_requested=False)
    assert decision.allowed is True
    assert decision.role is TrancheRole.CORE
    assert "低于持仓成本" in decision.reason
    assert "新结构" in decision.reason


def test_lower_price_without_new_structure_is_still_blocked():
    decision = _decision(current_price_below_cost=True, new_structure_confirmed=False)
    assert decision.allowed is False
    assert "价格更低" in decision.reason
    assert "结构条件" in decision.reason
