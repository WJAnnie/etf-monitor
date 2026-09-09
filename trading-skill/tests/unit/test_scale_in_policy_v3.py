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
        "price_not_below_position_cost": True,
    }
    kwargs.update(overrides)
    return scale_in_decision(**kwargs)


def test_120m_standard_second_buy_adds_confirmation_tranche():
    decision = _decision()
    assert decision.allowed is True
    assert decision.role is TrancheRole.CONFIRMATION
    assert decision.remaining_capacity_fraction == 0.40


def test_30m_standard_buy_adds_tactical_only_once_then_trend_add():
    first = _decision(timeframe=Timeframe.M30)
    assert first.role is TrancheRole.TACTICAL
    second = _decision(
        timeframe=Timeframe.M30,
        existing_roles=(TrancheRole.TEST, TrancheRole.TACTICAL),
    )
    assert second.role is TrancheRole.TREND_ADD
    third = _decision(
        timeframe=Timeframe.M30,
        existing_roles=(TrancheRole.TEST, TrancheRole.TACTICAL, TrancheRole.TREND_ADD),
    )
    assert third.allowed is False


def test_daily_standard_buy_can_build_core_tranche():
    decision = _decision(timeframe=Timeframe.DAILY, signal_type=ChanSignalType.THIRD_BUY)
    assert decision.allowed is True
    assert decision.role is TrancheRole.CORE


def test_first_buy_and_5m_cannot_trigger_scale_in():
    assert _decision(signal_type=ChanSignalType.FIRST_BUY).allowed is False
    assert _decision(timeframe=Timeframe.M5).allowed is False


def test_scale_in_never_bypasses_risk_context_or_protection():
    assert _decision(risk_level=2).allowed is False
    assert _decision(opportunity_grade="C").allowed is False
    assert _decision(context_valid=False).allowed is False
    assert _decision(protection_not_loosened=False).allowed is False


def test_scale_in_cannot_mechanically_average_down():
    decision = _decision(price_not_below_position_cost=False)
    assert decision.allowed is False
    assert "摊低成本" in decision.reason
