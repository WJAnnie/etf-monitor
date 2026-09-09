from trading_skill.fundamental_decision import combine_fundamental_decision
from trading_skill.fundamental_quality import (
    CompanyQuality,
    EvidenceCoverage,
    FundamentalStatus,
    GrowthState,
    RiskLevel,
    StockFundamentalAssessment,
    ValuationState,
)
from trading_skill.industry_fundamental_evidence import (
    EvidenceFamily,
    SpecializedCoverage,
    SpecializedEvidenceAssessment,
    SpecializedQuality,
)


def phase_a(*, status=FundamentalStatus.WATCH, quality=CompanyQuality.GOOD, risk=RiskLevel.LOW, coverage=EvidenceCoverage.PARTIAL, hard=()):
    return StockFundamentalAssessment(
        status=status,
        profile="测试",
        company_quality=quality,
        growth_state=GrowthState.STABLE,
        valuation_state=ValuationState.REASONABLE,
        risk_level=risk,
        evidence_coverage=coverage,
        latest_period=None,
        previous_period=None,
        visible_period_count=2,
        positive_evidence=(),
        warnings=(),
        hard_risks=tuple(hard),
        followups=(),
        rationale=(),
    )


def special(family, *, quality=SpecializedQuality.STRONG, coverage=SpecializedCoverage.FULL, can_upgrade=True, hard=(), warnings=()):
    return SpecializedEvidenceAssessment(
        profile="测试",
        family=family,
        quality=quality,
        coverage=coverage,
        positive_evidence=("专属证据正向",),
        warnings=tuple(warnings),
        hard_risks=tuple(hard),
        missing_evidence=(),
        metrics={},
        can_upgrade_watch=can_upgrade,
    )


def test_phase_a_reject_can_never_be_overridden_by_plugin():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.REJECT, hard=("明确硬伤",)),
        special(EvidenceFamily.ORDER_MANUFACTURING),
    )
    assert out.status is FundamentalStatus.REJECT
    assert out.deep_analysis_eligible is False


def test_specialized_hard_risk_rejects_even_phase_a_pass():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.PASS),
        special(EvidenceFamily.CONSUMER_CASHFLOW, hard=("专属硬风险",)),
    )
    assert out.status is FundamentalStatus.REJECT


def test_financial_watch_can_upgrade_when_specialized_metrics_are_sufficient():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.WATCH, quality=CompanyQuality.UNKNOWN),
        special(EvidenceFamily.FINANCIAL),
    )
    assert out.status is FundamentalStatus.PASS


def test_order_manufacturing_watch_can_upgrade_with_good_common_and_specialized_quality():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.WATCH, quality=CompanyQuality.GOOD),
        special(EvidenceFamily.ORDER_MANUFACTURING),
    )
    assert out.status is FundamentalStatus.PASS


def test_rnd_stays_watch_until_external_pipeline_or_product_evidence_exists():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.PASS),
        special(EvidenceFamily.RND, can_upgrade=False),
    )
    assert out.status is FundamentalStatus.WATCH


def test_cyclical_stays_watch_until_price_cost_cycle_evidence_exists():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.PASS),
        special(EvidenceFamily.CYCLICAL, can_upgrade=False),
    )
    assert out.status is FundamentalStatus.WATCH


def test_limited_industry_evidence_cannot_pass():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.PASS),
        special(EvidenceFamily.ORDER_MANUFACTURING, quality=SpecializedQuality.INSUFFICIENT, coverage=SpecializedCoverage.LIMITED, can_upgrade=False),
    )
    assert out.status is FundamentalStatus.WATCH


def test_high_phase_a_risk_is_not_washed_away_by_positive_plugin():
    out = combine_fundamental_decision(
        phase_a(status=FundamentalStatus.WATCH, risk=RiskLevel.HIGH),
        special(EvidenceFamily.ORDER_MANUFACTURING),
    )
    assert out.status is FundamentalStatus.WATCH
