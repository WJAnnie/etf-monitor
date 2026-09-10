from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_skill.chan.center import Center, CenterMotion
from trading_skill.chan.signals import ChanSignal
from trading_skill.chan_extensions import annotate_second_buy_variants, class2_buy_types, is_class2_buy
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import CenterState, ChanSignalType, Direction, SignalState, Timeframe
from trading_skill.production_chan import ProductionChanResult, _directional_leg, analyze_production_chan

BASE = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)


def _signal(kind: ChanSignalType, *, price: int, when: datetime = BASE) -> ChanSignal:
    return ChanSignal(
        id=f"sig-{kind.value}-{price}",
        symbol="X",
        standard_types=(kind,),
        extended_types=(),
        side="BUY",
        level_rank=1,
        timeframe="30m",
        state=SignalState.CONFIRMED,
        structural_price_ticks=price,
        structural_timestamp=when,
        confirmation_timestamp=when,
        anchor_ids=(),
        evidence_ids=(),
    )


def _center(*, zg: int = 150) -> Center:
    return Center(
        id="c1", symbol="X", source_timeframe=Timeframe.M30, level_rank=1,
        state=CenterState.CONFIRMED, seed_motion_ids=("a", "b", "c"), motion_ids=("a", "b", "c"),
        zd_ticks=120, zg_ticks=zg, dd_ticks=100, gg_ticks=180, d_ticks=100, g_ticks=180,
        structural_start_timestamp=BASE - timedelta(hours=5),
        structural_end_timestamp=BASE - timedelta(hours=2),
        confirmation_timestamp=BASE - timedelta(hours=2),
    )


def _result(signals, centers=()) -> ProductionChanResult:
    return ProductionChanResult(
        "OK", Timeframe.M30, 100, 100, 90, 10, 6, 4, 4, tuple(centers),
        None, None, None, None, tuple(signals), None, 10.0, (),
    )


def test_center_class2_buy_is_explicit_extension_not_standard_type():
    second = _signal(ChanSignalType.SECOND_BUY, price=160)
    annotated = annotate_second_buy_variants(_result((second,), (_center(),)))
    signal = annotated.signals[0]
    assert signal.standard_types == (ChanSignalType.SECOND_BUY,)
    assert class2_buy_types(signal) == (ChanSignalType.CENTER_CLASS2_BUY,)
    assert is_class2_buy(signal)
    assert ChanSignalType.CENTER_CLASS2_BUY not in signal.standard_types


def test_second_buy_inside_center_is_not_center_class2():
    second = _signal(ChanSignalType.SECOND_BUY, price=149)
    signal = annotate_second_buy_variants(_result((second,), (_center(),))).signals[0]
    assert not is_class2_buy(signal)


def test_strong_class2_requires_standard_second_and_third_same_structure():
    second = _signal(ChanSignalType.SECOND_BUY, price=160)
    third = _signal(ChanSignalType.THIRD_BUY, price=160)
    annotated = annotate_second_buy_variants(_result((second, third)))
    updated = next(s for s in annotated.signals if ChanSignalType.SECOND_BUY in s.standard_types)
    assert ChanSignalType.STRONG_CLASS2_BUY in class2_buy_types(updated)
    assert ChanSignalType.STRONG_CLASS2_BUY not in updated.standard_types


def test_class2_annotation_never_creates_standard_second_buy():
    third = _signal(ChanSignalType.THIRD_BUY, price=160)
    signal = annotate_second_buy_variants(_result((third,), (_center(),))).signals[0]
    assert signal.standard_types == (ChanSignalType.THIRD_BUY,)
    assert not is_class2_buy(signal)


def _motion(i: int, direction: Direction, low: int, high: int) -> CenterMotion:
    return CenterMotion(
        id=f"m{i}", source_timeframe=Timeframe.M30, level_rank=0, direction=direction,
        low_ticks=low, high_ticks=high,
        structural_start_timestamp=BASE + timedelta(minutes=i * 30),
        structural_end_timestamp=BASE + timedelta(minutes=(i + 1) * 30),
        confirmation_timestamp=BASE + timedelta(minutes=(i + 1) * 30),
    )


def test_divergence_leg_rejects_merged_multiple_same_direction_motions():
    motions = (
        _motion(0, Direction.DOWN, 80, 120),
        _motion(1, Direction.UP, 90, 115),
        _motion(2, Direction.DOWN, 70, 110),
    )
    assert _directional_leg(motions, direction=Direction.DOWN, level_rank=1, prefix="c") is None


def test_divergence_leg_accepts_one_explicit_completed_motion():
    motion = _motion(0, Direction.DOWN, 80, 120)
    leg = _directional_leg((motion,), direction=Direction.DOWN, level_rank=1, prefix="c")
    assert leg is not None
    assert leg.low_ticks == 80 and leg.high_ticks == 120


def test_mixed_price_basis_is_blocked_before_chan_structure():
    bars = (
        RawBar.make(symbol="X", timeframe=Timeframe.M30, timestamp=BASE, open=10, high=11, low=9, close=10, volume=100, adjustment="forward"),
        RawBar.make(symbol="X", timeframe=Timeframe.M30, timestamp=BASE + timedelta(minutes=30), open=10, high=12, low=10, close=11, volume=100, adjustment="raw"),
        RawBar.make(symbol="X", timeframe=Timeframe.M30, timestamp=BASE + timedelta(minutes=60), open=11, high=12, low=9, close=10, volume=100, adjustment="raw"),
    )
    result = analyze_production_chan(bars, tick_size=Decimal("0.01"), as_of=BASE + timedelta(hours=2))
    assert result.status == "DATA_INCOMPLETE"
    assert "MIXED_PRICE_BASIS" in result.issues
