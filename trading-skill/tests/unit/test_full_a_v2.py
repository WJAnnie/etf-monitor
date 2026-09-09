from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts.collect_candidate_bars_v2 import aggregate_m30_to_m120, normalize_complete_m30
from scripts.run_full_a_scan_v2 import execution_maturity_v2
from trading_skill.domain.enums import ChanSignalType


CN = ZoneInfo("Asia/Shanghai")


def m30(time, open_, close, high, low):
    return {"time": time, "open": open_, "close": close, "high": high, "low": low, "volume": 1, "amount": 1}


def test_long_history_m30_builds_two_m120_bars_per_full_day():
    rows = [
        m30("2026-09-08 10:00:00", 10, 10.1, 10.2, 9.9),
        m30("2026-09-08 10:30:00", 10.1, 10.2, 10.3, 10.0),
        m30("2026-09-08 11:00:00", 10.2, 10.3, 10.4, 10.1),
        m30("2026-09-08 11:30:00", 10.3, 10.4, 10.5, 10.2),
        m30("2026-09-08 13:30:00", 10.4, 10.5, 10.6, 10.3),
        m30("2026-09-08 14:00:00", 10.5, 10.6, 10.7, 10.4),
        m30("2026-09-08 14:30:00", 10.6, 10.7, 10.8, 10.5),
        m30("2026-09-08 15:00:00", 10.7, 10.8, 10.9, 10.6),
    ]
    normalized = normalize_complete_m30(rows, now=datetime(2026, 9, 8, 16, 0, tzinfo=CN), source="测试")
    m120 = aggregate_m30_to_m120(normalized)
    assert len(m120) == 2
    assert m120[0]["time"].endswith("11:30:00")
    assert m120[1]["time"].endswith("15:00:00")


def fake_signal(kind):
    return SimpleNamespace(structural_price_ticks=1000, standard_types=(kind,))


def test_recent_second_buy_can_remain_prepare_with_small_extension():
    signal = fake_signal(ChanSignalType.SECOND_BUY)
    assert execution_maturity_v2(signal, 10.30) == "TRIGGERED"
    assert execution_maturity_v2(signal, 10.70) == "PREPARE"
    assert execution_maturity_v2(signal, 10.90) == "WATCH"


def test_first_buy_window_is_tighter_than_second_buy():
    signal = fake_signal(ChanSignalType.FIRST_BUY)
    assert execution_maturity_v2(signal, 10.50) == "PREPARE"
    assert execution_maturity_v2(signal, 10.70) == "WATCH"
