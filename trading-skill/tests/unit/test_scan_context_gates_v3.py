from datetime import datetime
from zoneinfo import ZoneInfo

import scripts.collect_candidate_bars_v2 as bars_v2
from scripts.run_full_a_scan_v2 import _independent_context_blockers


CN = ZoneInfo("Asia/Shanghai")


def _analysis():
    return {
        "timeframes": {
            key: {
                "status": "OK",
                "technical": {"confirmation": "NEUTRAL"} if key == "5m" else None,
            }
            for key in ("weekly", "daily", "120m", "30m", "5m")
        }
    }


def _symbol(event_risk="NORMAL", event_complete=True, industry_complete=True):
    return {
        "fundamental_prefilter": {"eligible": True},
        "industry_event_risk": event_risk,
        "industry_event_context_complete": event_complete,
        "industry_context_complete": industry_complete,
    }


def test_missing_industry_event_context_is_not_silently_normal():
    blockers = _independent_context_blockers(
        {},
        _symbol(event_risk="UNKNOWN", event_complete=False),
        _analysis(),
        parents_ok=True,
        signal_type="SECOND_BUY",
        authority_signal={"structural_price_ticks": 1000},
    )
    assert "INDUSTRY_EVENT_CONTEXT_UNAVAILABLE" in blockers
    assert "INDUSTRY_MAJOR_NEGATIVE_EVENT" not in blockers


def test_single_major_negative_high_risk_blocks_but_caution_only_keeps_a_label():
    high = _independent_context_blockers(
        {},
        _symbol(event_risk="HIGH"),
        _analysis(),
        parents_ok=True,
        signal_type="SECOND_BUY",
        authority_signal={"structural_price_ticks": 1000},
    )
    caution = _independent_context_blockers(
        {},
        _symbol(event_risk="CAUTION"),
        _analysis(),
        parents_ok=True,
        signal_type="SECOND_BUY",
        authority_signal={"structural_price_ticks": 1000},
    )
    assert "INDUSTRY_MAJOR_NEGATIVE_EVENT" in high
    assert "INDUSTRY_MAJOR_NEGATIVE_EVENT" not in caution
    assert "INDUSTRY_EVENT_CONTEXT_UNAVAILABLE" not in caution


def test_unresolved_cross_market_real_industry_context_blocks_new_entry():
    blockers = _independent_context_blockers(
        {},
        _symbol(industry_complete=False),
        _analysis(),
        parents_ok=True,
        signal_type="SECOND_BUY",
        authority_signal={"structural_price_ticks": 1000},
    )
    assert "INDUSTRY_CONTEXT_INCOMPLETE" in blockers


def test_candidate_bar_collection_preserves_industry_risk_and_priority_metadata(monkeypatch):
    daily = [{"time": "2026-09-10 15:00:00"}] * 500
    m5 = [{"time": "2026-09-10 14:45:00"}] * 500
    m30 = [{"time": "2026-09-10 14:30:00"}] * 480
    m120 = [{"time": "2026-09-10 15:00:00"}] * 100

    monkeypatch.setattr(bars_v2, "fetch_daily_v2", lambda code, market: (daily, "daily", []))
    monkeypatch.setattr(bars_v2, "fetch_m5_v2", lambda code, market, now: (m5, "m5", []))
    monkeypatch.setattr(bars_v2, "fetch_m30_v2", lambda code, market, now: (m30, "m30", []))
    monkeypatch.setattr(bars_v2, "normalize_daily", lambda rows, now, source: rows)
    monkeypatch.setattr(bars_v2, "aggregate_m30_to_m120", lambda rows: m120)
    monkeypatch.setattr(bars_v2, "aggregate_weekly", lambda rows, now: [])

    source = {
        "code": "600000",
        "name": "测试股",
        "market": 1,
        "industry_code": "CROSS_MARKET",
        "industry_name": "半导体设备",
        "actual_industry_name": "半导体设备",
        "candidate_route": "跨行业结构补充",
        "industry_context_complete": True,
        "industry_context_note": "resolved",
        "industry_event_risk": "CAUTION",
        "industry_event_score": -2,
        "industry_event_context_complete": True,
        "daily_priority_score": 88.5,
        "fundamental_prefilter": {"eligible": True, "deep_scan_eligible": True},
    }
    result = bars_v2.collect_one(source, datetime(2026, 9, 10, 15, 0, tzinfo=CN))
    assert result["actual_industry_name"] == "半导体设备"
    assert result["candidate_route"] == "跨行业结构补充"
    assert result["industry_context_complete"] is True
    assert result["industry_event_risk"] == "CAUTION"
    assert result["industry_event_score"] == -2
    assert result["industry_event_context_complete"] is True
    assert result["daily_priority_score"] == 88.5
