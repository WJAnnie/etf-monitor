from datetime import datetime
from zoneinfo import ZoneInfo

from trading_skill.fundamental_quality import (
    CompanyQuality,
    EvidenceCoverage,
    FundamentalStatus,
    GrowthState,
    ValuationState,
    assess_stock_fundamentals,
    visible_periods,
)


AS_OF = datetime(2026, 9, 9, 15, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def report(date, notice, *, revenue=20, profit=20, roe=15, margin=30, eps=1.0, cash=0.9):
    return {
        "REPORTDATE": date,
        "NOTICE_DATE": notice,
        "YSTZ": revenue,
        "SJLTZ": profit,
        "WEIGHTAVG_ROE": roe,
        "XSMLL": margin,
        "BASIC_EPS": eps,
        "MGJYXJJE": cash,
    }


def healthy_history():
    return [
        report("2026-06-30", "2026-08-20", revenue=22, profit=26, roe=16, margin=34, eps=1.2, cash=1.1),
        report("2026-03-31", "2026-04-25", revenue=14, profit=17, roe=12, margin=33, eps=0.55, cash=0.5),
        report("2025-12-31", "2026-03-20", revenue=12, profit=15, roe=14, margin=32, eps=1.8, cash=1.6),
        report("2025-09-30", "2025-10-25", revenue=10, profit=12, roe=11, margin=31, eps=1.2, cash=1.0),
    ]


def test_no_longer_requires_both_annual_and_interim_to_have_any_assessment():
    assessment = assess_stock_fundamentals(
        [report("2026-06-30", "2026-08-20")],
        as_of=AS_OF,
        industry_name="医疗器械",
        pe=30,
        pb=4,
    )
    assert assessment.latest_period is not None
    assert assessment.visible_period_count == 1
    assert assessment.status is not FundamentalStatus.REJECT
    assert assessment.evidence_coverage is EvidenceCoverage.LIMITED


def test_future_report_is_never_used():
    rows = healthy_history() + [report("2026-09-30", "2026-10-25", revenue=80, profit=100)]
    periods = visible_periods(rows, as_of=AS_OF)
    assert periods[0].report_date == "2026-06-30"
    assert all(period.report_date != "2026-09-30" for period in periods)


def test_high_valuation_is_tag_not_standalone_veto():
    assessment = assess_stock_fundamentals(
        healthy_history(),
        as_of=AS_OF,
        industry_name="半导体设备",
        pe=120,
        pb=10,
    )
    assert assessment.status is FundamentalStatus.PASS
    assert assessment.company_quality in {CompanyQuality.GOOD, CompanyQuality.EXCELLENT}
    assert assessment.growth_state in {GrowthState.ACCELERATING, GrowthState.IMPROVING}
    assert assessment.valuation_state is ValuationState.VERY_HIGH
    assert any("不单独否决" in item for item in assessment.rationale)


def test_research_stage_innovation_drug_is_not_rejected_for_negative_eps_or_pe():
    rows = [
        report("2026-06-30", "2026-08-20", revenue=28, profit=-18, roe=-3, margin=75, eps=-0.4, cash=-0.5),
        report("2026-03-31", "2026-04-25", revenue=20, profit=-25, roe=-4, margin=72, eps=-0.3, cash=-0.4),
    ]
    assessment = assess_stock_fundamentals(
        rows,
        as_of=AS_OF,
        industry_name="创新药",
        pe=-1,
        pb=5,
    )
    assert assessment.status is FundamentalStatus.WATCH
    assert not assessment.hard_risks
    assert assessment.valuation_state is ValuationState.NOT_APPLICABLE
    assert any("负EPS" in item for item in assessment.rationale)


def test_bank_cannot_be_passed_by_generic_manufacturing_metrics():
    assessment = assess_stock_fundamentals(
        healthy_history(),
        as_of=AS_OF,
        industry_name="银行",
        pe=6,
        pb=0.65,
    )
    assert assessment.status is FundamentalStatus.WATCH
    assert assessment.company_quality is CompanyQuality.UNKNOWN
    assert assessment.valuation_state is ValuationState.LOW
    assert any("金融行业" in warning for warning in assessment.warnings)
    assert any("净息差" in item for item in assessment.followups)


def test_clear_accounting_deterioration_can_reject_standard_company():
    rows = [
        report("2026-06-30", "2026-08-20", revenue=-45, profit=-70, roe=-5, margin=-2, eps=-0.8, cash=-1.0),
        report("2026-03-31", "2026-04-25", revenue=-25, profit=-40, roe=-2, margin=3, eps=-0.3, cash=-0.4),
    ]
    assessment = assess_stock_fundamentals(
        rows,
        as_of=AS_OF,
        industry_name="家电",
        pe=-1,
        pb=1.2,
    )
    assert assessment.status is FundamentalStatus.REJECT
    assert assessment.hard_risks


def test_order_driven_industry_uses_statement_evidence_instead_of_profit_only():
    detailed = {
        "current": {"debt_asset_ratio_pct": 55, "operating_cash_flow": 100},
        "changes": {
            "contract_liabilities_change_pct": 35,
            "accounts_receivable_change_pct": 10,
            "inventory_change_pct": 8,
        },
    }
    assessment = assess_stock_fundamentals(
        healthy_history(),
        as_of=AS_OF,
        industry_name="电网设备",
        pe=35,
        pb=4,
        detailed_metrics=detailed,
        industry_state="TRENDING",
    )
    assert assessment.status is FundamentalStatus.PASS
    assert assessment.evidence_coverage is EvidenceCoverage.FULL
    assert any("合同负债增长" in item for item in assessment.positive_evidence)
