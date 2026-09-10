from datetime import datetime
from zoneinfo import ZoneInfo

from trading_skill.a_share_fundamentals import evaluate_prefilter, industry_metric_policy


def _row(report_date: str, revenue: float, profit: float, roe: float, eps: float = 1.0, **extra):
    row = {
        "REPORTDATE": report_date,
        "NOTICE_DATE": report_date,
        "YSTZ": revenue,
        "SJLTZ": profit,
        "WEIGHTAVG_ROE": roe,
        "XSMLL": 30,
        "BASIC_EPS": eps,
        "MGJYXJJE": 1.1,
    }
    row.update(extra)
    return row


def test_prefilter_can_use_visible_annual_when_latest_quarter_is_not_available():
    now = datetime(2026, 3, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    result = evaluate_prefilter([_row("2025-12-31", 10, 12, 12)], as_of=now, pe=20, pb=2)
    assert result.latest is not None
    assert result.latest.report_date == "2025-12-31"
    assert "缺少已披露中报" not in result.reasons


def test_prefilter_uses_latest_visible_quarter_not_only_interim():
    now = datetime(2026, 11, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        _row("2026-09-30", 20, 25, 17, TOTALOPERATEREVE=1000, RESEARCH_EXPENSE=120, FINANCIAL_DETAIL_STATUS="COMPLETE"),
        _row("2026-06-30", 18, 22, 16, TOTALOPERATEREVE=900, RESEARCH_EXPENSE=90, FINANCIAL_DETAIL_STATUS="COMPLETE"),
        _row("2025-12-31", 12, 15, 14, TOTALOPERATEREVE=1600, RESEARCH_EXPENSE=150, FINANCIAL_DETAIL_STATUS="COMPLETE"),
    ]
    result = evaluate_prefilter(rows, as_of=now, pe=28, pb=4, industry_name="半导体设备")
    assert result.latest is not None
    assert result.latest.report_date == "2026-09-30"
    assert result.eligible
    assert result.industry_policy == "GROWTH_TECH"
    assert result.industry_evidence_status == "COMPLETE"
    assert "研发强度" in result.metric_focus
    assert any("研发" in reason for reason in result.industry_evidence_reasons)


def test_prefilter_rejects_profit_collapse():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 5, -40, 10), _row("2025-12-31", 8, 10, 11)]
    result = evaluate_prefilter(rows, as_of=now, pe=15, pb=2, industry_name="通用制造")
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


def test_order_driven_missing_detail_blocks_direct_entry_but_keeps_observation_grade():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 20, 22, 16), _row("2025-12-31", 12, 15, 14)]
    result = evaluate_prefilter(rows, as_of=now, pe=25, pb=2, industry_name="船舶制造")
    assert result.grade in {"A", "B", "C"}
    assert not result.eligible
    assert result.industry_evidence_status == "BASE_ONLY"
    assert "行业专属财务证据缺失，禁止直接新开仓，仅保留结构观察" in result.reasons


def test_order_driven_contract_liability_growth_is_real_decision_evidence():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        _row("2026-06-30", 20, 22, 16, CONTRACT_LIAB=130, FIXED_ASSET=500, CIP=120, FINANCIAL_DETAIL_STATUS="COMPLETE"),
        _row("2025-12-31", 12, 15, 14, CONTRACT_LIAB=100, FIXED_ASSET=480, CIP=100, FINANCIAL_DETAIL_STATUS="COMPLETE"),
    ]
    result = evaluate_prefilter(rows, as_of=now, pe=25, pb=2, industry_name="船舶制造")
    assert result.eligible
    assert result.industry_evidence_status == "COMPLETE"
    assert any("合同负债" in reason for reason in result.industry_evidence_reasons)


def test_bank_asset_quality_can_hard_veto_even_when_generic_growth_is_not_used():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        _row("2026-06-30", -30, 8, 12, BLDKBBL=6.0, HXYJBCZL=10.0, FINANCIAL_DETAIL_STATUS="COMPLETE"),
        _row("2025-12-31", -20, 5, 11, BLDKBBL=1.5, HXYJBCZL=9.5, FINANCIAL_DETAIL_STATUS="COMPLETE"),
    ]
    result = evaluate_prefilter(rows, as_of=now, pe=6, pb=0.8, industry_name="股份制银行")
    assert result.hard_fail
    assert not result.eligible
    assert "营收同比明显下滑" not in result.reasons
    assert any("不良贷款率" in reason for reason in result.industry_evidence_reasons)


def test_utility_debt_ratio_is_used_instead_of_only_generic_growth():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        _row("2026-06-30", 8, 10, 9, ZCFZL=84, FIXED_ASSET=1000, CIP=300, FINANCIAL_DETAIL_STATUS="COMPLETE"),
        _row("2025-12-31", 7, 9, 9, ZCFZL=78, FIXED_ASSET=950, CIP=250, FINANCIAL_DETAIL_STATUS="COMPLETE"),
    ]
    result = evaluate_prefilter(rows, as_of=now, pe=15, pb=1.5, industry_name="电力公用事业")
    assert result.industry_policy == "UTILITY"
    assert any("资产负债率" in reason for reason in result.industry_evidence_reasons)
