from conftest import BASE, make_bar
from trading_skill.chan.fractal import detect_fractals
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.enums import FractalType


def process(bars, tick):
    v = validate_raw_bars(bars, tick)
    assert v.result.valid
    p = process_inclusions(v.bars)
    assert p.result.valid
    return p.bars


def test_standard_top(tick):
    bars = [make_bar(0, 9, 10, 8, 9), make_bar(1, 11.5, 13, 11, 12), make_bar(2, 10, 11, 9, 10), make_bar(3, 11, 12, 10, 11)]
    f = detect_fractals(process(bars, tick)).fractals
    assert f[0].type == FractalType.TOP


def test_standard_bottom(tick):
    bars = [make_bar(0, 11, 12, 10, 11), make_bar(1, 9, 10, 7, 8), make_bar(2, 10, 11, 9, 10), make_bar(3, 11, 12, 10, 11)]
    f = detect_fractals(process(bars, tick)).fractals
    assert f[0].type == FractalType.BOTTOM


def test_equal_edge_not_standard(tick):
    from decimal import Decimal
    from trading_skill.domain.bar import ProcessedBar
    from trading_skill.domain.enums import Direction, Timeframe
    def pb(i, high, low):
        return ProcessedBar(id=f"pb{i}", symbol="TEST", timeframe=Timeframe.M30, start_timestamp=BASE, end_timestamp=BASE, open_ticks=low, high_ticks=high, low_ticks=low, close_ticks=low, volume=Decimal("1"), amount=Decimal("1"), raw_bar_count=1, source_raw_bar_ids=(f"r{i}",), extreme_high_raw_bar_ids=(f"r{i}",), extreme_low_raw_bar_ids=(f"r{i}",), direction_context=Direction.UP, is_complete=True)
    out = detect_fractals((pb(0,1200,800), pb(1,1200,1000), pb(2,1100,900)))
    assert not any(x.center_index == 1 for x in out.fractals)
    assert 1 in out.equal_edge_windows


def test_unfinished_third_bar_cannot_confirm(tick):
    bars = [make_bar(0, 9, 10, 8, 9), make_bar(1, 11.5, 13, 11, 12), make_bar(2, 10, 11, 9, 10, complete=False), make_bar(3, 11, 12, 10, 11)]
    f = detect_fractals(process(bars, tick)).fractals
    assert not any(x.center_index == 1 for x in f)
