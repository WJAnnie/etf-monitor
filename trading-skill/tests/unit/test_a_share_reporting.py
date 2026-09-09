from datetime import datetime
from zoneinfo import ZoneInfo

from trading_skill.a_share_reporting import (
    DeliveryStatus,
    evaluate_delivery_gate,
    translate_candidate,
)

CN_TZ = ZoneInfo("Asia/Shanghai")


def _symbol(m5_time: str, *, daily_complete: bool = True) -> dict:
    date = m5_time[:10]
    return {
        "5m": [{"time": m5_time, "close": 10.0}],
        "daily": [{"time": date, "close": 10.0, "_complete": daily_complete}],
    }


def test_candidate_user_fields_are_chinese():
    translated = translate_candidate(
        {
            "timeframe": "30m",
            "signal": "SECOND_BUY",
            "action": "BUY_TRANCHE_1",
            "technical": "SUPPORT",
            "opportunity": "A",
            "risk": "L1",
            "volume_price": "成交量:MILD_EXPAND",
            "reason": "风电行业筛选通过 + 30mSECOND_BUY",
        }
    )
    assert translated["timeframe"] == "30分钟"
    assert translated["signal"] == "二买"
    assert translated["action"] == "第一笔买入"
    assert translated["technical"] == "偏支持"
    assert translated["opportunity"] == "高质量机会"
    assert translated["risk"] == "预警"
    assert translated["volume_price"] == "成交量：温和放量"
    assert "30分钟二买" in translated["reason"]


def test_1430_gate_requires_current_session_coverage():
    bars = {
        "generated_at": datetime(2026, 9, 9, 14, 31, tzinfo=CN_TZ).isoformat(),
        "symbols": [_symbol("2026-09-09 14:30:00") for _ in range(5)],
    }
    gate = evaluate_delivery_gate(bars, stage="14:30盘中扫描")
    assert gate.status is DeliveryStatus.READY
    assert gate.current_count == 5


def test_old_data_is_treated_as_holiday_and_not_pushed():
    bars = {
        "generated_at": datetime(2026, 9, 9, 14, 31, tzinfo=CN_TZ).isoformat(),
        "symbols": [_symbol("2026-09-08 15:00:00") for _ in range(4)],
    }
    gate = evaluate_delivery_gate(bars, stage="14:30盘中扫描")
    assert gate.status is DeliveryStatus.HOLIDAY_SKIP


def test_partial_market_data_is_blocked():
    bars = {
        "generated_at": datetime(2026, 9, 9, 14, 51, tzinfo=CN_TZ).isoformat(),
        "symbols": [
            _symbol("2026-09-09 14:50:00"),
            _symbol("2026-09-09 14:50:00"),
            _symbol("2026-09-09 14:50:00"),
            _symbol("2026-09-08 15:00:00"),
            _symbol("2026-09-08 15:00:00"),
        ],
    }
    gate = evaluate_delivery_gate(bars, stage="14:50收盘前扫描")
    assert gate.status is DeliveryStatus.DATA_INCOMPLETE
    assert gate.current_count == 3


def test_close_gate_requires_completed_daily_bar():
    bars = {
        "generated_at": datetime(2026, 9, 9, 16, 30, tzinfo=CN_TZ).isoformat(),
        "symbols": [
            _symbol("2026-09-09 15:00:00", daily_complete=True),
            _symbol("2026-09-09 15:00:00", daily_complete=True),
            _symbol("2026-09-09 15:00:00", daily_complete=True),
            _symbol("2026-09-09 15:00:00", daily_complete=False),
            _symbol("2026-09-09 15:00:00", daily_complete=False),
        ],
    }
    gate = evaluate_delivery_gate(bars, stage="16:30收盘确认")
    assert gate.status is DeliveryStatus.DATA_INCOMPLETE
