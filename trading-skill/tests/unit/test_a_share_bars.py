from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_skill.a_share_bars import aggregate_m5, aggregate_weekly

TZ = ZoneInfo("Asia/Shanghai")


def _m5(start: datetime, count: int):
    rows = []
    for i in range(count):
        end = start + timedelta(minutes=5 * i)
        rows.append(
            {
                "time": end.strftime("%Y-%m-%d %H:%M:%S"),
                "open": 10 + i * 0.01,
                "close": 10.01 + i * 0.01,
                "high": 10.02 + i * 0.01,
                "low": 9.99 + i * 0.01,
                "volume": 100 + i,
                "amount": 1000 + i,
                "_complete": True,
            }
        )
    return rows


def test_six_real_5m_bars_make_one_30m_bar():
    rows = _m5(datetime(2026, 9, 8, 9, 35, tzinfo=TZ), 6)
    aggregated = aggregate_m5(rows, bars_per_group=6)
    assert len(aggregated) == 1
    assert aggregated[0]["time"].endswith("10:00:00")
    assert aggregated[0]["_source"] == "aggregate_real_5m"


def test_24_real_5m_bars_make_one_120m_bar():
    rows = _m5(datetime(2026, 9, 8, 9, 35, tzinfo=TZ), 24)
    aggregated = aggregate_m5(rows, bars_per_group=24)
    assert len(aggregated) == 1
    assert aggregated[0]["time"].endswith("11:30:00")


def test_partial_group_is_not_promoted_to_completed_30m():
    rows = _m5(datetime(2026, 9, 8, 13, 5, tzinfo=TZ), 5)
    assert aggregate_m5(rows, bars_per_group=6) == []


def test_aggregation_never_bridges_lunch_break():
    morning = _m5(datetime(2026, 9, 8, 11, 15, tzinfo=TZ), 4)
    afternoon = _m5(datetime(2026, 9, 8, 13, 5, tzinfo=TZ), 2)
    assert aggregate_m5(morning + afternoon, bars_per_group=6) == []


def test_current_week_is_provisional_before_friday_close():
    daily = [
        {"time": "2026-09-07", "open": 10, "close": 10.1, "high": 10.2, "low": 9.9, "volume": 100},
        {"time": "2026-09-08", "open": 10.1, "close": 10.2, "high": 10.3, "low": 10.0, "volume": 120},
    ]
    weekly = aggregate_weekly(daily, now=datetime(2026, 9, 8, 16, 30, tzinfo=TZ))
    assert len(weekly) == 1
    assert weekly[0]["_complete"] is False
