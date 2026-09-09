from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_skill.chan.segment import NormalizedSegment
from trading_skill.domain.enums import ChanSignalType, Direction, Timeframe
from trading_skill.production_chan import build_center_lifecycle


TZ = ZoneInfo("Asia/Shanghai")


def _segment(index: int, direction: Direction, low: int, high: int) -> NormalizedSegment:
    start = datetime(2026, 9, 1, 9, 30, tzinfo=TZ) + timedelta(minutes=30 * index)
    end = start + timedelta(minutes=30)
    return NormalizedSegment(
        id=f"seg-{index}",
        source_segment_id=f"raw-{index}",
        direction=direction,
        low_ticks=low,
        high_ticks=high,
        structural_start_timestamp=start,
        structural_end_timestamp=end,
        confirmation_timestamp=end,
    )


def test_center_lifecycle_emits_third_buy_on_up_departure_and_first_return_outside():
    segments = (
        _segment(0, Direction.DOWN, 100, 120),
        _segment(1, Direction.UP, 105, 115),
        _segment(2, Direction.DOWN, 108, 125),
        # seed center core = [108, 115]
        _segment(3, Direction.UP, 118, 135),
        _segment(4, Direction.DOWN, 115, 130),
    )
    result = build_center_lifecycle(segments, symbol="600000", timeframe=Timeframe.M30)
    types = [kind for signal in result.signals for kind in signal.standard_types]
    assert ChanSignalType.THIRD_BUY in types
    assert result.centers[0].zd_ticks == 108
    assert result.centers[0].zg_ticks == 115


def test_center_extension_does_not_recalculate_seed_core():
    segments = (
        _segment(0, Direction.DOWN, 100, 120),
        _segment(1, Direction.UP, 105, 115),
        _segment(2, Direction.DOWN, 108, 125),
        _segment(3, Direction.UP, 110, 114),
    )
    result = build_center_lifecycle(segments, symbol="600000", timeframe=Timeframe.M30)
    center = result.centers[0]
    assert center.zd_ticks == 108
    assert center.zg_ticks == 115
    assert center.extension_count == 1
