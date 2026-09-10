from datetime import datetime, timezone

from trading_skill.decision import Action
from trading_skill.position import add_tranche, create_trade, map_sell_scope
from trading_skill.sizing import StopCandidate, StopLevel, StopType, TrancheRole


def _stop(level):
    return StopCandidate("s", level, StopType.BUY_POINT_INVALIDATION, 900, "structure")


def _trade_with_core_and_confirmation():
    trade = create_trade("000001", datetime(2026, 9, 10, tzinfo=timezone.utc))
    trade = add_tranche(
        trade,
        value=10000,
        entry_price=10,
        role=TrancheRole.CONFIRMATION,
        management_level=StopLevel.L30,
        entry_signal_id="confirm",
        parent_structure_id=None,
        stop=_stop(StopLevel.L30),
    )
    trade = add_tranche(
        trade,
        value=20000,
        entry_price=10,
        role=TrancheRole.CORE,
        management_level=StopLevel.LD,
        entry_signal_id="core",
        parent_structure_id=None,
        stop=_stop(StopLevel.LD),
    )
    return trade


def test_weekly_first_sell_is_reduce_core_when_core_is_actually_cut():
    scope = map_sell_scope(_trade_with_core_and_confirmation(), timeframe="weekly", sell_class=1)
    assert scope.action is Action.REDUCE_CORE
    assert any(fraction == 0.5 for _, fraction in scope.reduction_fractions)


def test_120m_second_sell_is_reduce_core_when_matrix_touches_core():
    scope = map_sell_scope(_trade_with_core_and_confirmation(), timeframe="120m", sell_class=2)
    assert scope.action is Action.REDUCE_CORE


def test_30m_first_sell_stays_tactical_when_core_is_untouched():
    scope = map_sell_scope(_trade_with_core_and_confirmation(), timeframe="30m", sell_class=1)
    assert scope.action is Action.REDUCE_TACTICAL


def test_full_matrix_exit_is_labeled_exit_only_when_every_current_tranche_is_fully_removed():
    scope = map_sell_scope(_trade_with_core_and_confirmation(), timeframe="weekly", sell_class=3)
    assert scope.action is Action.EXIT
