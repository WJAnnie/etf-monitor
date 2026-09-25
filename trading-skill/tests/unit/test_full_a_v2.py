from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts.collect_candidate_bars_v2 import aggregate_m30_to_m120, normalize_complete_m30
from scripts.run_full_a_scan_v2 import (
    _nested_labels,
    execution_maturity_v2,
    resolve_execution_context_v2,
)
from trading_skill.domain.enums import ChanSignalType, Timeframe


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


def fake_signal(kind, *, when=None, price_ticks=1000, side="BUY"):
    when = when or datetime(2026, 9, 9, 13, 30, tzinfo=CN)
    return SimpleNamespace(
        structural_price_ticks=price_ticks,
        structural_timestamp=when - timedelta(minutes=5),
        confirmation_timestamp=when,
        standard_types=(kind,),
        side=side,
    )


def fake_result(timeframe, *signals):
    return SimpleNamespace(timeframe=timeframe, signals=tuple(signals), status="OK")


def test_recent_second_buy_can_remain_prepare_with_small_extension():
    signal = fake_signal(ChanSignalType.SECOND_BUY)
    assert execution_maturity_v2(signal, 10.30) == "TRIGGERED"
    assert execution_maturity_v2(signal, 10.70) == "PREPARE"
    assert execution_maturity_v2(signal, 10.90) == "WATCH"


def test_first_buy_window_is_tighter_than_second_buy():
    signal = fake_signal(ChanSignalType.FIRST_BUY)
    assert execution_maturity_v2(signal, 10.50) == "PREPARE"
    assert execution_maturity_v2(signal, 10.70) == "WATCH"


def test_120m_setup_requires_30m_then_5m_structural_trigger_for_buy():
    as_of = datetime(2026, 9, 9, 14, 0, tzinfo=CN)
    setup = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(hours=2))
    m30_buy = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(minutes=40))
    m5_buy = fake_signal(ChanSignalType.FIRST_BUY, when=as_of - timedelta(minutes=10))
    results = {
        Timeframe.M30: fake_result(Timeframe.M30, m30_buy),
        Timeframe.M5: fake_result(Timeframe.M5, m5_buy),
    }
    ctx = resolve_execution_context_v2(
        results,
        primary_tf=Timeframe.M120,
        setup_signal=setup,
        as_of=as_of,
        current_price=10.20,
    )
    assert ctx["maturity"] == "TRIGGERED"
    assert ctx["confirmation_signal"] is m30_buy
    assert ctx["execution_signal"] is m5_buy
    assert ctx["execution_timeframe"] is Timeframe.M5


def test_120m_setup_without_5m_buy_is_prepare_not_triggered():
    as_of = datetime(2026, 9, 9, 14, 0, tzinfo=CN)
    setup = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(hours=2))
    m30_buy = fake_signal(ChanSignalType.THIRD_BUY, when=as_of - timedelta(minutes=30))
    results = {
        Timeframe.M30: fake_result(Timeframe.M30, m30_buy),
        Timeframe.M5: fake_result(Timeframe.M5),
    }
    ctx = resolve_execution_context_v2(
        results,
        primary_tf=Timeframe.M120,
        setup_signal=setup,
        as_of=as_of,
        current_price=10.20,
    )
    assert ctx["maturity"] == "PREPARE"
    assert ctx["execution_signal"] is None
    assert "WAIT_5M_EXECUTION_BUY" in ctx["reason_codes"]


def test_later_5m_sell_invalidates_execution_buy():
    as_of = datetime(2026, 9, 9, 14, 0, tzinfo=CN)
    setup = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(hours=2))
    m30_buy = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(minutes=40))
    m5_buy = fake_signal(ChanSignalType.FIRST_BUY, when=as_of - timedelta(minutes=20))
    m5_sell = fake_signal(
        ChanSignalType.FIRST_SELL,
        when=as_of - timedelta(minutes=5),
        side="SELL",
    )
    results = {
        Timeframe.M30: fake_result(Timeframe.M30, m30_buy),
        Timeframe.M5: fake_result(Timeframe.M5, m5_buy, m5_sell),
    }
    ctx = resolve_execution_context_v2(
        results,
        primary_tf=Timeframe.M120,
        setup_signal=setup,
        as_of=as_of,
        current_price=10.20,
    )
    assert ctx["maturity"] == "PREPARE"
    assert ctx["execution_signal"] is None


def test_daily_setup_cannot_skip_120m_and_30m_chain_even_if_5m_has_buy():
    as_of = datetime(2026, 9, 9, 14, 0, tzinfo=CN)
    setup = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(days=1))
    m5_buy = fake_signal(ChanSignalType.SECOND_BUY, when=as_of - timedelta(minutes=10))
    results = {
        Timeframe.M120: fake_result(Timeframe.M120),
        Timeframe.M30: fake_result(Timeframe.M30),
        Timeframe.M5: fake_result(Timeframe.M5, m5_buy),
    }
    ctx = resolve_execution_context_v2(
        results,
        primary_tf=Timeframe.DAILY,
        setup_signal=setup,
        as_of=as_of,
        current_price=10.20,
    )
    assert ctx["maturity"] == "WATCH"
    assert ctx["execution_signal"] is None
    assert "WAIT_120M_CHILD_STRUCTURE" in ctx["reason_codes"]


def test_parent_second_buy_plus_lower_first_buy_is_labeled_as_level_nesting_not_new_standard_buy():
    setup = fake_signal(ChanSignalType.SECOND_BUY)
    lower = fake_signal(ChanSignalType.FIRST_BUY)
    labels = _nested_labels(setup, None, lower)
    assert "标准二买" in labels
    assert "二买级别嵌套：次级别一买执行" in labels
