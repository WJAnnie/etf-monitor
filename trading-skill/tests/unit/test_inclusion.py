from conftest import make_bar
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.enums import Direction


def run(bars, tick):
    v = validate_raw_bars(bars, tick)
    assert v.result.valid
    return process_inclusions(v.bars)


def test_up_inclusion_merge_uses_max_high_max_low(tick):
    bars = [make_bar(0, 9, 10, 8, 9), make_bar(1, 10, 12, 10, 11), make_bar(2, 11, 13, 9, 12), make_bar(3, 12, 14, 11, 13)]
    out = run(bars, tick)
    assert out.result.valid and len(out.bars) == 3
    merged = out.bars[1]
    assert merged.high_ticks == 1300 and merged.low_ticks == 1000
    assert merged.volume == 200 and merged.raw_bar_count == 2


def test_down_inclusion_merge_uses_min_high_min_low(tick):
    bars = [make_bar(0, 14, 15, 13, 14), make_bar(1, 13, 14, 11, 12), make_bar(2, 12, 15, 10, 11), make_bar(3, 11, 12, 9, 10)]
    out = run(bars, tick)
    assert out.result.valid and len(out.bars) == 3
    assert out.bars[1].high_ticks == 1400 and out.bars[1].low_ticks == 1000


def test_equal_range_is_inclusion(tick):
    bars = [make_bar(0, 9, 10, 8, 9), make_bar(1, 10, 12, 10, 11), make_bar(2, 10, 12, 10, 11), make_bar(3, 12, 13, 11, 12)]
    out = run(bars, tick)
    assert out.result.valid and out.bars[1].raw_bar_count == 2


def test_unresolved_all_inclusion(tick):
    bars = [make_bar(0, 10, 12, 9, 11), make_bar(1, 10.5, 11.5, 9.5, 11)]
    out = run(bars, tick)
    assert not out.result.valid
    assert out.result.reason_codes == ("DIRECTION_UNRESOLVED",)


def test_direction_context_changes(tick):
    bars = [make_bar(0, 9, 10, 8, 9), make_bar(1, 10, 12, 10, 11), make_bar(2, 9, 11, 9, 10)]
    out = run(bars, tick)
    assert out.result.valid
    assert out.bars[-1].direction_context == Direction.DOWN
