from datetime import datetime
from zoneinfo import ZoneInfo

from trading_skill.a_share_fundamentals import evaluate_prefilter


def _row(report_date: str, revenue: float, profit: float, roe: float, eps: float = 1.0):
    return {
        "REPORTDATE": report_date,
        "NOTICE_DATE": report_date,
        "YSTZ": revenue,
        "SJLTZ": profit,
        "WEIGHTAVG_ROE": roe,
        "XSMLL": 30,
        "BASIC_EPS": eps,
        "MGJYXJJE": 1.1,
    }


def test_prefilter_requires_visible_annual_and_interim():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = evaluate_prefilter([_row("2025-12-31", 10, 12, 12)], as_of=now, pe=20, pb=2)
    assert not result.eligible
    assert "缺少已披露中报" in result.reasons


def test_prefilter_passes_healthy_company():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 18, 22, 16), _row("2025-12-31", 12, 15, 14)]
    result = evaluate_prefilter(rows, as_of=now, pe=28, pb=4)
    assert result.eligible
    assert result.grade in {"A", "B"}


def test_prefilter_rejects_profit_collapse():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 5, -40, 10), _row("2025-12-31", 8, 10, 11)]
    result = evaluate_prefilter(rows, as_of=now, pe=15, pb=2)
    assert not result.eligible
    assert "净利润同比明显下滑" in result.reasons
