from datetime import datetime
from zoneinfo import ZoneInfo

from trading_skill.a_share_fundamentals import evaluate_prefilter, industry_metric_policy


def _row(
    report_date: str,
    revenue: float,
    profit: float,
    roe: float,
    eps: float = 1.0,
    gross_margin: float = 30,
    operating_cash_per_share: float = 1.1,
):
    return {
        "REPORTDATE": report_date,
        "NOTICE_DATE": report_date,
        "YSTZ": revenue,
        "SJLTZ": profit,
        "WEIGHTAVG_ROE": roe,
        "XSMLL": gross_margin,
        "BASIC_EPS": eps,
        "MGJYXJJE": operating_cash_per_share,
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
    assert result.policy_name == "GENERIC_QUALITY"


def test_prefilter_rejects_profit_collapse_for_generic_company():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 5, -40, 10), _row("2025-12-31", 8, 10, 11)]
    result = evaluate_prefilter(rows, as_of=now, pe=15, pb=2)
    assert not result.eligible
    assert "净利润同比明显下滑" in result.reasons


def test_innovation_drug_does_not_use_negative_eps_or_profit_decline_as_single_hard_veto():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [
        _row("2026-06-30", 35, -60, -8, eps=-0.8, gross_margin=72, operating_cash_per_share=-0.2),
        _row("2025-12-31", 20, -45, -6, eps=-0.6, gross_margin=70, operating_cash_per_share=-0.1),
    ]
    result = evaluate_prefilter(
        rows,
        as_of=now,
        pe=None,
        pb=6,
        industry_name="生物制品",
        prospect_theme="创新药",
    )
    assert result.policy_name == "INNOVATION_DRUG_RND"
    assert "净利润同比明显下滑" not in result.reasons
    assert "每股收益非正" not in result.reasons
    assert result.grade in {"A", "B", "C", "D"}
    assert "研发费用率" in result.external_metrics_required


def test_order_driven_industry_keeps_profit_collapse_as_hard_veto_and_exposes_order_kpis():
    now = datetime(2026, 9, 8, tzinfo=ZoneInfo("Asia/Shanghai"))
    rows = [_row("2026-06-30", 12, -50, 8), _row("2025-12-31", 10, 12, 9)]
    result = evaluate_prefilter(
        rows,
        as_of=now,
        pe=35,
        pb=3,
        industry_name="船舶制造",
        prospect_theme="造船与海工",
    )
    assert not result.eligible
    assert result.policy_name == "ORDER_DRIVEN_CAPITAL_GOODS"
    assert "净利润同比明显下滑" in result.reasons
    assert "新签订单" in result.external_metrics_required
    assert "合同负债" in result.external_metrics_required


def test_semiconductor_policy_allows_higher_pe_soft_limit_than_generic():
    generic = industry_metric_policy(industry_name="普通制造")
    semi = industry_metric_policy(prospect_theme="半导体设备与材料")
    assert generic.pe_soft_limit == 100
    assert semi.pe_soft_limit == 150
    assert "研发费用率" in semi.external_metrics
