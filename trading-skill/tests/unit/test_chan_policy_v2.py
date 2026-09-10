from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_skill.chan.center import Center
from trading_skill.chan.signals import ChanSignal
from trading_skill.chan_policy_v2 import (
    TimeframeRole,
    classify_second_buy_extensions,
    timeframe_role,
    validate_standard_second_buy,
    validate_standard_second_sell,
)
from trading_skill.domain.enums import CenterState, ChanSignalType, SignalState, Timeframe
from trading_skill.domain.models import stable_id


CN = ZoneInfo("Asia/Shanghai")
T0 = datetime(2026, 9, 1, 15, 0, tzinfo=CN)


def signal(
    signal_id: str,
    kind: ChanSignalType,
    *,
    price: int,
    when: datetime,
    anchor_ids=(),
    evidence_ids=(),
    level=1,
):
    return ChanSignal(
        id=signal_id,
        symbol="000001",
        standard_types=(kind,),
        extended_types=(),
        side="BUY" if "BUY" in kind.value else "SELL",
        level_rank=level,
        timeframe="daily",
        state=SignalState.CONFIRMED,
        structural_price_ticks=price,
        structural_timestamp=when,
        confirmation_timestamp=when,
        anchor_ids=tuple(anchor_ids),
        evidence_ids=tuple(evidence_ids),
    )


def center(*, zg=995, level=1):
    return Center(
        id="c1",
        symbol="000001",
        source_timeframe=Timeframe.DAILY,
        level_rank=level,
        state=CenterState.CONFIRMED,
        seed_motion_ids=("a", "b", "c"),
        motion_ids=("a", "b", "c"),
        zd_ticks=980,
        zg_ticks=zg,
        dd_ticks=970,
        gg_ticks=1010,
        d_ticks=970,
        g_ticks=1010,
        structural_start_timestamp=T0 - timedelta(days=10),
        structural_end_timestamp=T0 - timedelta(days=2),
        confirmation_timestamp=T0 - timedelta(days=2),
    )


def test_timeframe_roles_are_non_overlapping():
    assert timeframe_role(Timeframe.WEEKLY) is TimeframeRole.STRATEGIC_CONTEXT
    assert timeframe_role(Timeframe.DAILY) is TimeframeRole.ENTRY_AUTHORITY
    assert timeframe_role(Timeframe.M120) is TimeframeRole.STRUCTURAL_CONFIRMATION
    assert timeframe_role(Timeframe.M30) is TimeframeRole.EXECUTION_SETUP
    assert timeframe_role(Timeframe.M5) is TimeframeRole.EXECUTION_TRIGGER


def test_standard_second_buy_equal_first_buy_low_is_valid_boundary():
    first = signal("f1", ChanSignalType.FIRST_BUY, price=1000, when=T0)
    second = signal(
        "s2",
        ChanSignalType.SECOND_BUY,
        price=1000,
        when=T0 + timedelta(days=3),
        anchor_ids=(stable_id("anchor", first.id),),
        evidence_ids=("first-up", "pullback"),
    )
    validation = validate_standard_second_buy(second, (first, second))
    assert validation.valid
    assert validation.first_buy == first


def test_standard_second_buy_below_first_buy_low_is_invalid():
    first = signal("f1", ChanSignalType.FIRST_BUY, price=1000, when=T0)
    second = signal(
        "s2",
        ChanSignalType.SECOND_BUY,
        price=999,
        when=T0 + timedelta(days=3),
        anchor_ids=(stable_id("anchor", first.id),),
        evidence_ids=("first-up", "pullback"),
    )
    validation = validate_standard_second_buy(second, (first, second))
    assert not validation.valid
    assert "SECOND_BUY_BROKE_FIRST_BUY_LOW" in validation.reason_codes


def test_class2_labels_are_extended_only_and_can_overlap():
    first = signal("f1", ChanSignalType.FIRST_BUY, price=1000, when=T0)
    second = signal(
        "s2",
        ChanSignalType.SECOND_BUY,
        price=1002,
        when=T0 + timedelta(days=3),
        anchor_ids=(stable_id("anchor", first.id),),
        evidence_ids=("first-up", "shared-return"),
    )
    third = signal(
        "t3",
        ChanSignalType.THIRD_BUY,
        price=1002,
        when=T0 + timedelta(days=3),
        evidence_ids=("departure", "shared-return"),
    )
    annotation = classify_second_buy_extensions(second, all_signals=(first, second, third), centers=(center(),))
    assert annotation.standard_type is ChanSignalType.SECOND_BUY
    assert ChanSignalType.STRONG_CLASS2_BUY in annotation.extended_types
    assert ChanSignalType.CENTER_CLASS2_BUY in annotation.extended_types
    assert second.standard_types == (ChanSignalType.SECOND_BUY,)


def test_second_sell_mirror_rejects_rebound_above_first_sell_high():
    first = signal("fs1", ChanSignalType.FIRST_SELL, price=1200, when=T0)
    second_ok = signal(
        "ss2a",
        ChanSignalType.SECOND_SELL,
        price=1200,
        when=T0 + timedelta(days=3),
        anchor_ids=(stable_id("anchor", first.id),),
    )
    second_bad = signal(
        "ss2b",
        ChanSignalType.SECOND_SELL,
        price=1201,
        when=T0 + timedelta(days=3),
        anchor_ids=(stable_id("anchor", first.id),),
    )
    assert validate_standard_second_sell(second_ok, (first, second_ok))
    assert not validate_standard_second_sell(second_bad, (first, second_bad))
