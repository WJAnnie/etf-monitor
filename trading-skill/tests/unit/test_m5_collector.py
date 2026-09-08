from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from portfolio_market_data_5m import (  # noqa: E402
    build_summary,
    normalize_complete_m5,
    provider_order,
)

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 8, 14, 12, tzinfo=TZ)


def row(t: str, value: float = 10.0) -> dict:
    return {
        "time": t,
        "open": value,
        "high": value + 1,
        "low": value - 1,
        "close": value + 0.2,
        "volume": 100,
        "amount": 0,
    }


def test_yahoo_start_timestamp_becomes_interval_end():
    rows = normalize_complete_m5(
        [row("2026-09-08 09:30:00")],
        source="yahoo",
        market="CN",
        now=NOW,
    )
    assert rows[0]["time"] == "2026-09-08 09:35:00"


def test_yahoo_incomplete_bar_is_dropped():
    now = datetime(2026, 9, 8, 14, 2, tzinfo=TZ)
    rows = normalize_complete_m5(
        [row("2026-09-08 14:00:00")],
        source="yahoo",
        market="CN",
        now=now,
    )
    assert rows == []


def test_end_timestamp_provider_keeps_completed_bar():
    rows = normalize_complete_m5(
        [row("2026-09-08 14:00:00")],
        source="sina",
        market="CN",
        now=NOW,
    )
    assert rows[0]["time"] == "2026-09-08 14:00:00"


def test_lunch_break_timestamp_is_rejected():
    rows = normalize_complete_m5(
        [row("2026-09-08 12:05:00")],
        source="sina",
        market="CN",
        now=NOW,
    )
    assert rows == []


def test_proxy_symbols_prefer_real_symbol_sources_before_yahoo():
    symbol = {"yahoo_symbol_is_proxy": True}
    assert provider_order(symbol) == ["tencent", "sina", "eastmoney", "yahoo"]


def test_summary_distinguishes_fresh_from_cache():
    rows = [row(f"2026-09-08 13:{minute:02d}:00") for minute in range(5, 60, 5)]
    # Enough history is not material to this assertion, so repeat distinct historical timestamps.
    historical = []
    for day in range(1, 6):
        for minute in range(5, 60, 5):
            historical.append(row(f"2026-09-0{day} 13:{minute:02d}:00"))
    # 240 unique session bars across earlier days + today's bars.
    all_rows = []
    base_days = [1, 2, 3, 4, 7, 8]
    for d in base_days:
        for hour, start, end in ((9, 35, 60), (10, 0, 60), (11, 0, 31), (13, 5, 60), (14, 0, 13)):
            for minute in range(start, end, 5):
                if hour == 9 and minute >= 60:
                    continue
                all_rows.append(row(f"2026-09-{d:02d} {hour:02d}:{minute:02d}:00"))
    # The exact fixture length may vary, but pad with valid prior-session keys if needed.
    i = 0
    while len(all_rows) < 240:
        all_rows.insert(0, row(f"2026-08-{20 + (i // 48):02d} 09:{35 + (i % 5) * 5:02d}:00"))
        i += 1
    all_rows = all_rows[-240:]
    all_rows[-1] = row("2026-09-08 14:10:00")

    fresh = {
        "key": "A", "name": "A", "market": "CN", "proxy_for": "A", "proxy_note": "",
        "m5": all_rows, "source": "sina", "warnings": [], "errors": [], "using_cache": False,
    }
    cached = dict(fresh, key="B", name="B", source="cache", using_cache=True, errors=["live failed"])
    summary = build_summary([fresh, cached], now=NOW)
    assert summary["symbols"]["A"]["fresh_for_analysis"] is True
    assert summary["symbols"]["B"]["fresh_for_analysis"] is False
