from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from trading_skill.fundamentals import (
    CompanyArchetype,
    FinancialReport,
    FundamentalAnomaly,
    FundamentalGrade,
    FundamentalInput,
    FundamentalVeto,
    HeatState,
    IndustryLifecycle,
    IndustryProfile,
    LeaderType,
    ReportMetrics,
    ValuationGrade,
    evaluate_fundamentals,
    valuation_grade,
)
from trading_skill.universe import UniverseScanner, universe_input_from_profile

TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 8, 15, 30, tzinfo=TZ)


def industry(lifecycle=IndustryLifecycle.HIGH_GROWTH):
    return IndustryProfile("I1", lifecycle, 88, 75, 62, HeatState.WARMING)


def metrics(**overrides):
    base = dict(
        revenue_growth=20,
        profit_growth=22,
        gross_margin=35,
        roe=18,
        roic=13,
        ocf_to_profit=1.1,
        free_cash_flow_positive=True,
        ar_growth=18,
        inventory_growth=20,
        debt_ratio=35,
        goodwill_ratio=5,
        capex_growth=15,
        related_party_growth=0,
        non_recurring_profit_ratio=5,
    )
    base.update(overrides)
    return ReportMetrics(**base)


def report(name, kind, published_delta_days, **metric_overrides):
    return FinancialReport(
        name,
        kind,
        NOW - timedelta(days=180 if kind == "ANNUAL" else 60),
        NOW + timedelta(days=published_delta_days),
        metrics(**metric_overrides),
    )


def profile_input(**overrides):
    base = dict(
        symbol="X",
        industry=industry(),
        leader_type=LeaderType.BOTH,
        archetype=CompanyArchetype.QUALITY_GROWTH,
        annual_report=report("FY", "ANNUAL", -120, revenue_growth=12, profit_growth=10),
        interim_report=report("HY", "INTERIM", -10),
    )
    base.update(overrides)
    return FundamentalInput(**base)


def test_annual_and_interim_are_hard_data_prerequisites():
    p = evaluate_fundamentals(profile_input(interim_report=None), as_of=NOW, valuation_percentile=40)
    assert not p.data_complete
    assert not p.fundamental_eligible
    assert p.fundamental_grade is FundamentalGrade.E
    assert "INTERIM_REPORT_UNAVAILABLE" in p.reason_codes


def test_future_published_report_is_not_visible_in_replay_as_of():
    p = evaluate_fundamentals(
        profile_input(interim_report=report("HY", "INTERIM", 1)),
        as_of=NOW,
        valuation_percentile=40,
    )
    assert not p.data_complete
    assert p.interim_report_id is None
    assert "INTERIM_REPORT_UNAVAILABLE" in p.reason_codes


def test_hard_veto_blocks_fundamental_eligibility():
    p = evaluate_fundamentals(
        profile_input(abnormal_audit=True),
        as_of=NOW,
        valuation_percentile=40,
    )
    assert not p.fundamental_eligible
    assert FundamentalVeto.ABNORMAL_AUDIT in p.vetoes
    assert "FUNDAMENTAL_HARD_VETO" in p.reason_codes


def test_cash_receivable_and_inventory_anomalies_are_deterministic():
    p = evaluate_fundamentals(
        profile_input(
            interim_report=report(
                "HY", "INTERIM", -10,
                revenue_growth=5,
                ar_growth=40,
                inventory_growth=45,
                ocf_to_profit=0.55,
            )
        ),
        as_of=NOW,
        valuation_percentile=40,
    )
    assert FundamentalAnomaly.AR_GROWTH_TOO_FAST in p.anomalies
    assert FundamentalAnomaly.INVENTORY_GROWTH_TOO_FAST in p.anomalies
    assert FundamentalAnomaly.OCF_PROFIT_DIVERGENCE in p.anomalies


def test_valuation_is_graded_separately_from_fundamental_eligibility():
    p = evaluate_fundamentals(profile_input(), as_of=NOW, valuation_percentile=92)
    assert p.fundamental_eligible
    assert p.valuation_grade is ValuationGrade.E_STRETCHED
    assert valuation_grade(10) is ValuationGrade.A_UNDERVALUED


def test_non_leader_is_filtered_by_universe_not_relabelled_as_fundamental_veto():
    p = evaluate_fundamentals(
        profile_input(leader_type=LeaderType.NON_LEADER),
        as_of=NOW,
        valuation_percentile=40,
    )
    assert p.fundamental_eligible
    item = universe_input_from_profile(p, low_position_score=80)
    scanner = UniverseScanner(max_deep_scan_symbols=5, deep_analyzer=lambda _: "deep")
    record = scanner.scan([item])[0]
    assert not record.universe_eligible
    assert "NON_LEADER" in record.reason_codes
    assert not record.deep_scanned


def test_declining_industry_is_filtered_before_deep_chan_scan():
    p = evaluate_fundamentals(
        profile_input(industry=industry(IndustryLifecycle.DECLINING)),
        as_of=NOW,
        valuation_percentile=20,
    )
    item = universe_input_from_profile(p, low_position_score=90)
    calls = []
    record = UniverseScanner(max_deep_scan_symbols=5, deep_analyzer=lambda x: calls.append(x.symbol)).scan([item])[0]
    assert not record.universe_eligible
    assert "INDUSTRY_INELIGIBLE" in record.reason_codes
    assert calls == []


def test_industry_rank_penalizes_overheated_heat_without_erasing_prospects():
    warm = IndustryProfile("I", IndustryLifecycle.HIGH_GROWTH, 80, 70, 80, HeatState.WARM)
    hot = IndustryProfile("I", IndustryLifecycle.HIGH_GROWTH, 80, 70, 80, HeatState.OVERHEATED)
    assert warm.rank_score > hot.rank_score
