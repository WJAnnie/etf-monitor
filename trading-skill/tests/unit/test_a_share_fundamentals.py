from datetime import datetime
from zoneinfo import ZoneInfo

from trading_skill.a_share_fundamentals import evaluate_prefilter, industry_metric_policy


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


def test_prefilter_can_use_visible_annual_when_latest_quarter_is_not_available():
    now = datetime(2026, 3, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = evaluate_prefilter([_row("2025-12-31", 10, 12, 12)], as_of=now, pe=20, pb=2)
    assert result.latest is not None
    assert result.latest.report_date == "2025-12-31"
    assert "缺少已披露中报" not in result.reasons


def test_prefilter_uses_latest_visible_quarter_not_only_interim():
    now = datetime(2026, 11, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        _row("2026-09-30", 20, 25, 17),
        _row("2026-06-30", 18, 22, 16),
        _row("2025-12-31", 12, 15, 14),
    ]
    result = evaluate_prefilter(rows, as_of=now, pe=28, pb=4, industry_name="半导体设备")
    assert result.latest is not None
    assert result.latest.report_date == "2026-09-30"
    assert result.eligible
    assert result.industry_policy == "GROWTH_TECH"
    assert "研发强度" in result.metric_focus


def test_prefilter_rejects_profit_collapse():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 5, -40, 10), _row("2025-12-31", 8, 10, 11)]
    result = evaluate_prefilter(rows, as_of=now, pe=15, pb=2, industry_name="消费电子")
    assert not result.eligible
    assert "净利润同比明显下滑" in result.reasons


def test_asset_heavy_industry_focus_contains_orders_fixed_assets_and_cip():
    policy = industry_metric_policy("船舶制造")
    assert policy.key == "ORDER_DRIVEN_MANUFACTURING"
    assert "新签订单" in policy.metric_focus
    assert "固定资产" in policy.metric_focus
    assert "在建工程" in policy.metric_focus


def test_bank_uses_pb_not_generic_growth_valuation_focus():
    policy = industry_metric_policy("股份制银行")
    assert policy.key == "BANK"
    assert policy.valuation_focus[0] == "PB"
    assert "净息差NIM" in policy.metric_focus
