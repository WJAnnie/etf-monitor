from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import Timeframe
from trading_skill.shadow import (
    aggregate_m15,
    aggregate_weekly,
    analyze_structure,
    infer_tick_size,
    parse_market_time,
)

TZ = ZoneInfo("Asia/Shanghai")


def row(ts, o, h, l, c, v=100):
    return {"time": ts, "open": o, "high": h, "low": l, "close": c, "volume": v, "amount": 0}


def test_yahoo_m15_timestamp_is_normalized_from_start_to_end():
    dt = parse_market_time("2026-09-08 09:30", market="CN", timeframe="m15", source="yahoo")
    assert dt.hour == 9 and dt.minute == 45
    assert dt.tzinfo is not None


def test_m30_aggregation_does_not_bridge_lunch_break():
    rows = [
        row("2026-09-08 09:45", 10, 11, 9, 10.5),
        row("2026-09-08 10:00", 10.5, 12, 10, 11.5),
        row("2026-09-08 10:15", 11.5, 12, 11, 11.8),
        row("2026-09-08 10:30", 11.8, 13, 11.5, 12.8),
        row("2026-09-08 13:15", 12.8, 13, 12, 12.3),
        row("2026-09-08 13:30", 12.3, 12.8, 12.1, 12.7),
    ]
    out = aggregate_m15(rows, market="CN", source="sina", group_size=2)
    assert len(out) == 3
    assert out[0]["open"] == 10
    assert out[0]["close"] == 11.5
    assert out[-1]["open"] == 12.8
    assert out[-1]["close"] == 12.7


def test_m120_requires_eight_contiguous_m15_bars():
    start = datetime(2026, 9, 8, 9, 45, tzinfo=TZ)
    rows = []
    for i in range(9):
        ts = start + timedelta(minutes=15 * i)
        rows.append(row(ts.strftime("%Y-%m-%d %H:%M"), 10 + i, 11 + i, 9 + i, 10.5 + i))
    out = aggregate_m15(rows, market="CN", source="sina", group_size=8)
    assert len(out) == 1
    assert out[0]["open"] == 10
    assert out[0]["close"] == 17.5


def test_current_week_is_provisional_before_friday():
    rows = [
        row("2026-09-07", 10, 11, 9, 10.5),
        row("2026-09-08", 10.5, 12, 10, 11.5),
    ]
    weekly = aggregate_weekly(rows, market="CN")
    assert len(weekly) == 1
    assert weekly[0]["_complete"] is False


def test_etf_tick_size_is_one_mill():
    symbol = {"name": "电池ETF", "sources": {"m15": "sina"}, "market": "CN", "tx_symbol": "sh561160"}
    assert infer_tick_size(symbol, field="m15") == Decimal("0.001")


def test_proxy_yahoo_uses_etf_tick_size():
    symbol = {
        "name": "中证港股通科技指数",
        "sources": {"daily": "yahoo"},
        "yahoo_symbol_is_proxy": True,
        "market": "CN_INDEX",
    }
    assert infer_tick_size(symbol, field="daily") == Decimal("0.001")


def test_real_structure_pipeline_accepts_complete_tz_aware_bars():
    bars = []
    base = datetime(2026, 8, 1, 15, 0, tzinfo=TZ)
    for i in range(20):
        low = 100 + i
        high = low + 5
        bars.append(
            RawBar.make(
                symbol="X",
                timeframe=Timeframe.DAILY,
                timestamp=base + timedelta(days=i),
                open=low + 1,
                high=high,
                low=low,
                close=high - 1,
                volume=1000 + i,
                source="shadow-test",
            )
        )
    result = analyze_structure(tuple(bars), tick_size=Decimal("0.01"))
    assert result["status"] == "OK"
    assert result["raw_bars"] == 20
    assert "strokes" in result
