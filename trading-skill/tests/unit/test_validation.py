from conftest import make_bar
from trading_skill.data.validate import validate_raw_bars


def test_validation_valid(tick):
    out = validate_raw_bars([make_bar(0, 10, 11, 9, 10.5), make_bar(1, 10.5, 12, 10, 11)], tick)
    assert out.result.valid
    assert out.bars[0].high_ticks == 1100


def test_duplicate_timestamp_rejected(tick):
    b = make_bar(0, 10, 11, 9, 10)
    out = validate_raw_bars([b, b], tick)
    assert not out.result.valid
    assert "DUPLICATE_TIMESTAMP" in out.result.reason_codes


def test_invalid_ohlc_rejected(tick):
    b = make_bar(0, 10, 9, 8, 10)
    out = validate_raw_bars([b], tick)
    assert not out.result.valid
    assert "INVALID_OHLC_HIGH" in out.result.reason_codes
