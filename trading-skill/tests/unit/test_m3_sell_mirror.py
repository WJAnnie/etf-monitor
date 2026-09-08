from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_skill.chan.center import Center
from trading_skill.chan.divergence import MacdLegEvidence, StructuralLeg, evaluate_trend_divergence
from trading_skill.chan.signals import (
    LowerMove,
    ReversalAnchor,
    first_buy_or_sell,
    new_second_sell_tracker,
    new_third_sell_tracker,
    overlap_signals,
    second_sell_step,
    third_sell_step,
)
from trading_skill.chan.trend import classify_trend, complete_trend
from trading_skill.domain.enums import (
    CenterState,
    ChanSignalType,
    Direction,
    SecondSellTrackerState,
    ThirdSellTrackerState,
    Timeframe,
    TrendClassification,
)

TZ = ZoneInfo("Asia/Shanghai")
T0 = datetime(2026, 9, 8, 10, 0, tzinfo=TZ)


def center(n, dd, gg, zd, zg):
    return Center(
        id=f"c{n}", symbol="X", source_timeframe=Timeframe.DAILY, level_rank=1,
        state=CenterState.CONFIRMED, seed_motion_ids=(f"a{n}", f"b{n}", f"d{n}"),
        motion_ids=(f"a{n}", f"b{n}", f"d{n}"), zd_ticks=zd, zg_ticks=zg,
        dd_ticks=dd, gg_ticks=gg, d_ticks=dd, g_ticks=gg,
        structural_start_timestamp=T0 + timedelta(days=n * 3),
        structural_end_timestamp=T0 + timedelta(days=n * 3 + 2),
        confirmation_timestamp=T0 + timedelta(days=n * 3 + 2), revision=1,
    )


def leg(name, direction, low, high, day):
    return StructuralLeg(
        name, direction, 1, low, high,
        T0 + timedelta(days=day - 2), T0 + timedelta(days=day), T0 + timedelta(days=day),
    )


def move(name, direction, low, high, day, completed=True):
    return LowerMove(
        name, direction, 0, low, high,
        T0 + timedelta(days=day), T0 + timedelta(days=day), completed,
    )


def test_m3_confirmed_top_divergence_and_first_sell():
    c1 = center(0, 100, 200, 120, 180)
    c2 = center(1, 240, 330, 260, 310)
    trend = classify_trend((c1, c2), symbol="X").trend
    assert trend.current_classification is TrendClassification.UPTREND
    trend = complete_trend(
        trend,
        lower_level_opposite_turn_completed=True,
        direct_extension_exists=False,
        confirmation_timestamp=T0 + timedelta(days=10),
    )
    b = leg("b", Direction.UP, 220, 340, 5)
    c = leg("c", Direction.UP, 250, 370, 8)
    div, result = evaluate_trend_divergence(
        trend, b, c,
        MacdLegEvidence("b", 20, 8, 7, 6),
        MacdLegEvidence("c", 10, 5, 4, 4),
        lower_level_turn_completed=True,
        as_of=T0 + timedelta(days=10),
    )
    assert result.valid
    signal, signal_result = first_buy_or_sell(
        symbol="X", trend=trend, divergence=div,
        structural_price_ticks=370, structural_timestamp=c.structural_end_timestamp,
    )
    assert signal_result.valid
    assert signal.standard_types == (ChanSignalType.FIRST_SELL,)
    assert signal.side == "SELL"


def test_m3_second_sell_is_structural_not_anchor_price_rule():
    anchor = ReversalAnchor("ra-s", "X", Direction.DOWN, 1, 200, T0, True)
    tracker = new_second_sell_tracker(anchor)
    tracker, signal = second_sell_step(tracker, move("down", Direction.DOWN, 150, 200, 1))
    assert signal is None and tracker.state is SecondSellTrackerState.WAIT_REBOUND
    tracker, signal = second_sell_step(tracker, move("reb", Direction.UP, 170, 205, 2))
    assert tracker.state is SecondSellTrackerState.SECOND_SELL_CONFIRMED
    assert signal.standard_types == (ChanSignalType.SECOND_SELL,)
    assert signal.structural_price_ticks == 205
    assert signal.side == "SELL"


def test_m3_third_sell_equal_zd_valid_one_tick_above_invalid():
    c = center(0, 100, 200, 120, 180)
    tracker = new_third_sell_tracker(c)
    tracker, _ = third_sell_step(tracker, c, move("dep", Direction.DOWN, 60, 119, 1))
    tracker, signal = third_sell_step(tracker, c, move("ret", Direction.UP, 80, 120, 2))
    assert tracker.state is ThirdSellTrackerState.THIRD_SELL_CONFIRMED
    assert signal.standard_types == (ChanSignalType.THIRD_SELL,)
    assert signal.structural_price_ticks == 120

    tracker2 = new_third_sell_tracker(c)
    tracker2, _ = third_sell_step(tracker2, c, move("dep2", Direction.DOWN, 60, 119, 1))
    tracker2, signal2 = third_sell_step(tracker2, c, move("ret2", Direction.UP, 80, 121, 2))
    assert tracker2.state is ThirdSellTrackerState.THIRD_SELL_FAILED
    assert signal2 is None


def test_m3_second_and_third_sell_can_overlap():
    anchor = ReversalAnchor("ra-s", "X", Direction.DOWN, 1, 220, T0, True)
    second = new_second_sell_tracker(anchor)
    second, _ = second_sell_step(second, move("down", Direction.DOWN, 140, 220, 1))
    second, s2 = second_sell_step(second, move("reb", Direction.UP, 160, 190, 2))

    c = center(0, 100, 200, 120, 180)
    third = new_third_sell_tracker(c)
    third, _ = third_sell_step(third, c, move("dep", Direction.DOWN, 60, 119, 1))
    third, s3 = third_sell_step(third, c, move("ret", Direction.UP, 80, 120, 2))

    merged = overlap_signals(s2, s3)
    assert set(merged.standard_types) == {ChanSignalType.SECOND_SELL, ChanSignalType.THIRD_SELL}
    assert merged.side == "SELL"
