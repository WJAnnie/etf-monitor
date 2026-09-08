from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum


class IndustryLifecycle(StrEnum):
    EMERGING = "EMERGING"
    HIGH_GROWTH = "HIGH_GROWTH"
    MATURING = "MATURING"
    OVERCOMPETITIVE = "OVERCOMPETITIVE"
    DECLINING = "DECLINING"


class HeatState(StrEnum):
    COLD = "COLD"
    WARMING = "WARMING"
    WARM = "WARM"
    HOT = "HOT"
    OVERHEATED = "OVERHEATED"


class LeaderType(StrEnum):
    INDUSTRIAL_LEADER = "INDUSTRIAL_LEADER"
    MARKET_LEADER = "MARKET_LEADER"
    BOTH = "BOTH"
    EMERGING_LEADER = "EMERGING_LEADER"
    NON_LEADER = "NON_LEADER"


class CompanyArchetype(StrEnum):
    QUALITY_GROWTH = "QUALITY_GROWTH"
    TURNAROUND = "TURNAROUND"


class FundamentalGrade(StrEnum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"


class ValuationGrade(StrEnum):
    A_UNDERVALUED = "A_UNDERVALUED"
    B_REASONABLY_LOW = "B_REASONABLY_LOW"
    C_FAIR = "C_FAIR"
    D_EXPENSIVE = "D_EXPENSIVE"
    E_STRETCHED = "E_STRETCHED"


class MarginalState(StrEnum):
    IMPROVING_FAST = "IMPROVING_FAST"
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    WEAKENING = "WEAKENING"
    DETERIORATING = "DETERIORATING"


class FundamentalVeto(StrEnum):
    ABNORMAL_AUDIT = "ABNORMAL_AUDIT"
    MAJOR_FRAUD_RISK = "MAJOR_FRAUD_RISK"
    CORE_BUSINESS_BREAK = "CORE_BUSINESS_BREAK"
    MAJOR_CUSTOMER_LOSS = "MAJOR_CUSTOMER_LOSS"
    MARKET_SHARE_COLLAPSE = "MARKET_SHARE_COLLAPSE"
    CHRONIC_PROFIT_CASH_DIVERGENCE = "CHRONIC_PROFIT_CASH_DIVERGENCE"
    SEVERE_GOODWILL_RISK = "SEVERE_GOODWILL_RISK"
    MAJOR_GOVERNANCE_RISK = "MAJOR_GOVERNANCE_RISK"
    MAJOR_REGULATORY_RISK = "MAJOR_REGULATORY_RISK"
    BALANCE_SHEET_CRISIS = "BALANCE_SHEET_CRISIS"


class FundamentalAnomaly(StrEnum):
    AR_GROWTH_TOO_FAST = "AR_GROWTH_TOO_FAST"
    INVENTORY_GROWTH_TOO_FAST = "INVENTORY_GROWTH_TOO_FAST"
    OCF_PROFIT_DIVERGENCE = "OCF_PROFIT_DIVERGENCE"
    GROSS_MARGIN_ABNORMAL = "GROSS_MARGIN_ABNORMAL"
    CAPEX_SURGE = "CAPEX_SURGE"
    DEBT_SURGE = "DEBT_SURGE"
    GOODWILL_SURGE = "GOODWILL_SURGE"
    RELATED_PARTY_SURGE = "RELATED_PARTY_SURGE"
    NON_RECURRING_PROFIT_HIGH = "NON_RECURRING_PROFIT_HIGH"


@dataclass(frozen=True, slots=True)
class IndustryProfile:
    industry_id: str
    lifecycle: IndustryLifecycle
    prospects_score: float
    low_position_score: float
    heat_score: float
    heat_state: HeatState

    @property
    def rank_score(self) -> float:
        heat = self.heat_score * (0.65 if self.heat_state is HeatState.OVERHEATED else 1.0)
        return round(0.40 * self.prospects_score + 0.35 * self.low_position_score + 0.25 * heat, 8)


@dataclass(frozen=True, slots=True)
class ReportMetrics:
    revenue_growth: float
    profit_growth: float
    gross_margin: float
    roe: float
    roic: float
    ocf_to_profit: float
    free_cash_flow_positive: bool
    ar_growth: float
    inventory_growth: float
    debt_ratio: float
    goodwill_ratio: float
    capex_growth: float = 0.0
    related_party_growth: float = 0.0
    non_recurring_profit_ratio: float = 0.0


@dataclass(frozen=True, slots=True)
class FinancialReport:
    report_id: str
    report_type: str  # ANNUAL or INTERIM
    period_end: datetime
    published_at: datetime
    metrics: ReportMetrics


@dataclass(frozen=True, slots=True)
class FundamentalInput:
    symbol: str
    industry: IndustryProfile
    leader_type: LeaderType
    archetype: CompanyArchetype
    annual_report: FinancialReport | None
    interim_report: FinancialReport | None
    explicit_vetoes: tuple[FundamentalVeto, ...] = ()
    abnormal_audit: bool = False
    major_fraud_risk: bool = False
    governance_risk: bool = False
    regulatory_risk: bool = False
    core_business_break: bool = False
    market_share_collapse: bool = False
    balance_sheet_crisis: bool = False
    severe_goodwill_risk: bool = False
    major_customer_loss: bool = False


@dataclass(frozen=True, slots=True)
class FundamentalProfile:
    symbol: str
    industry: IndustryProfile
    leader_type: LeaderType
    archetype: CompanyArchetype
    fundamental_grade: FundamentalGrade
    fundamental_eligible: bool
    valuation_grade: ValuationGrade
    marginal_state: MarginalState
    vetoes: tuple[FundamentalVeto, ...]
    anomalies: tuple[FundamentalAnomaly, ...]
    annual_report_id: str | None
    interim_report_id: str | None
    data_complete: bool
    reason_codes: tuple[str, ...]


def valuation_grade(percentile: float) -> ValuationGrade:
    p = max(0.0, min(100.0, float(percentile)))
    if p <= 20:
        return ValuationGrade.A_UNDERVALUED
    if p <= 40:
        return ValuationGrade.B_REASONABLY_LOW
    if p <= 65:
        return ValuationGrade.C_FAIR
    if p <= 85:
        return ValuationGrade.D_EXPENSIVE
    return ValuationGrade.E_STRETCHED


def _visible(report: FinancialReport | None, as_of: datetime) -> bool:
    return report is not None and report.published_at <= as_of


def _derive_anomalies(m: ReportMetrics) -> tuple[FundamentalAnomaly, ...]:
    out: list[FundamentalAnomaly] = []
    revenue_base = max(abs(m.revenue_growth), 5.0)
    if m.ar_growth > revenue_base + 20:
        out.append(FundamentalAnomaly.AR_GROWTH_TOO_FAST)
    if m.inventory_growth > revenue_base + 25:
        out.append(FundamentalAnomaly.INVENTORY_GROWTH_TOO_FAST)
    if m.ocf_to_profit < 0.60:
        out.append(FundamentalAnomaly.OCF_PROFIT_DIVERGENCE)
    if m.gross_margin <= 0 or m.gross_margin > 90:
        out.append(FundamentalAnomaly.GROSS_MARGIN_ABNORMAL)
    if m.capex_growth > 80:
        out.append(FundamentalAnomaly.CAPEX_SURGE)
    if m.debt_ratio > 70:
        out.append(FundamentalAnomaly.DEBT_SURGE)
    if m.goodwill_ratio > 30:
        out.append(FundamentalAnomaly.GOODWILL_SURGE)
    if m.related_party_growth > 80:
        out.append(FundamentalAnomaly.RELATED_PARTY_SURGE)
    if m.non_recurring_profit_ratio > 30:
        out.append(FundamentalAnomaly.NON_RECURRING_PROFIT_HIGH)
    return tuple(out)


def _derive_vetoes(inp: FundamentalInput, annual: FinancialReport | None, interim: FinancialReport | None) -> tuple[FundamentalVeto, ...]:
    vetoes = list(inp.explicit_vetoes)
    mapping = (
        (inp.abnormal_audit, FundamentalVeto.ABNORMAL_AUDIT),
        (inp.major_fraud_risk, FundamentalVeto.MAJOR_FRAUD_RISK),
        (inp.core_business_break, FundamentalVeto.CORE_BUSINESS_BREAK),
        (inp.major_customer_loss, FundamentalVeto.MAJOR_CUSTOMER_LOSS),
        (inp.market_share_collapse, FundamentalVeto.MARKET_SHARE_COLLAPSE),
        (inp.severe_goodwill_risk, FundamentalVeto.SEVERE_GOODWILL_RISK),
        (inp.governance_risk, FundamentalVeto.MAJOR_GOVERNANCE_RISK),
        (inp.regulatory_risk, FundamentalVeto.MAJOR_REGULATORY_RISK),
        (inp.balance_sheet_crisis, FundamentalVeto.BALANCE_SHEET_CRISIS),
    )
    for condition, veto in mapping:
        if condition and veto not in vetoes:
            vetoes.append(veto)
    reports = tuple(r for r in (annual, interim) if r is not None)
    if reports and all(r.metrics.ocf_to_profit < 0.45 for r in reports):
        if FundamentalVeto.CHRONIC_PROFIT_CASH_DIVERGENCE not in vetoes:
            vetoes.append(FundamentalVeto.CHRONIC_PROFIT_CASH_DIVERGENCE)
    return tuple(vetoes)


def _marginal_state(annual: ReportMetrics, interim: ReportMetrics) -> MarginalState:
    revenue_delta = interim.revenue_growth - annual.revenue_growth
    profit_delta = interim.profit_growth - annual.profit_growth
    cash_delta = interim.ocf_to_profit - annual.ocf_to_profit
    composite = revenue_delta * 0.35 + profit_delta * 0.45 + cash_delta * 20 * 0.20
    if composite >= 12:
        return MarginalState.IMPROVING_FAST
    if composite >= 4:
        return MarginalState.IMPROVING
    if composite > -4:
        return MarginalState.STABLE
    if composite > -12:
        return MarginalState.WEAKENING
    return MarginalState.DETERIORATING


def _quality_grade(m: ReportMetrics, anomaly_count: int) -> FundamentalGrade:
    score = 0
    score += 2 if m.revenue_growth >= 15 else 1 if m.revenue_growth >= 5 else 0
    score += 2 if m.profit_growth >= 15 else 1 if m.profit_growth >= 5 else 0
    score += 2 if m.roe >= 15 else 1 if m.roe >= 8 else 0
    score += 1 if m.roic >= 10 else 0
    score += 2 if m.ocf_to_profit >= 1.0 else 1 if m.ocf_to_profit >= 0.7 else 0
    score += 1 if m.free_cash_flow_positive else 0
    score -= min(3, anomaly_count)
    if score >= 8:
        return FundamentalGrade.A
    if score >= 6:
        return FundamentalGrade.B
    if score >= 4:
        return FundamentalGrade.C
    if score >= 2:
        return FundamentalGrade.D
    return FundamentalGrade.E


def evaluate_fundamentals(inp: FundamentalInput, *, as_of: datetime, valuation_percentile: float) -> FundamentalProfile:
    annual = inp.annual_report if _visible(inp.annual_report, as_of) else None
    interim = inp.interim_report if _visible(inp.interim_report, as_of) else None
    reasons: list[str] = []
    if annual is None:
        reasons.append("ANNUAL_REPORT_UNAVAILABLE")
    if interim is None:
        reasons.append("INTERIM_REPORT_UNAVAILABLE")
    data_complete = annual is not None and interim is not None

    reports = tuple(r for r in (annual, interim) if r is not None)
    anomalies: list[FundamentalAnomaly] = []
    for report in reports:
        for anomaly in _derive_anomalies(report.metrics):
            if anomaly not in anomalies:
                anomalies.append(anomaly)
    vetoes = _derive_vetoes(inp, annual, interim)

    if data_complete:
        marginal = _marginal_state(annual.metrics, interim.metrics)
        grade = _quality_grade(interim.metrics, len(anomalies))
    else:
        marginal = MarginalState.STABLE
        grade = FundamentalGrade.E
    eligible = data_complete and not vetoes
    if vetoes:
        reasons.append("FUNDAMENTAL_HARD_VETO")
    if inp.leader_type is LeaderType.NON_LEADER:
        reasons.append("NON_LEADER")
    if inp.industry.lifecycle is IndustryLifecycle.DECLINING:
        reasons.append("DECLINING_INDUSTRY")

    return FundamentalProfile(
        symbol=inp.symbol,
        industry=inp.industry,
        leader_type=inp.leader_type,
        archetype=inp.archetype,
        fundamental_grade=grade,
        fundamental_eligible=eligible,
        valuation_grade=valuation_grade(valuation_percentile),
        marginal_state=marginal,
        vetoes=vetoes,
        anomalies=tuple(anomalies),
        annual_report_id=annual.report_id if annual else None,
        interim_report_id=interim.report_id if interim else None,
        data_complete=data_complete,
        reason_codes=tuple(reasons),
    )
