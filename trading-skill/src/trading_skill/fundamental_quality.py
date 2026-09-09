from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping, Sequence

from trading_skill.a_share_fundamentals import FinancialPeriod, parse_period
from trading_skill.industry_profiles import profile_for


class FundamentalStatus(StrEnum):
    PASS = "PASS"
    WATCH = "WATCH"
    REJECT = "REJECT"


class CompanyQuality(StrEnum):
    EXCELLENT = "EXCELLENT"
    GOOD = "GOOD"
    AVERAGE = "AVERAGE"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class GrowthState(StrEnum):
    ACCELERATING = "ACCELERATING"
    IMPROVING = "IMPROVING"
    STABLE = "STABLE"
    SLOWING = "SLOWING"
    DETERIORATING = "DETERIORATING"
    UNKNOWN = "UNKNOWN"


class ValuationState(StrEnum):
    LOW = "LOW"
    REASONABLE = "REASONABLE"
    HIGH = "HIGH"
    VERY_HIGH = "VERY_HIGH"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNKNOWN = "UNKNOWN"


class RiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class EvidenceCoverage(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    LIMITED = "LIMITED"


@dataclass(frozen=True, slots=True)
class StockFundamentalAssessment:
    status: FundamentalStatus
    profile: str
    company_quality: CompanyQuality
    growth_state: GrowthState
    valuation_state: ValuationState
    risk_level: RiskLevel
    evidence_coverage: EvidenceCoverage
    latest_period: FinancialPeriod | None
    previous_period: FinancialPeriod | None
    visible_period_count: int
    positive_evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    hard_risks: tuple[str, ...]
    followups: tuple[str, ...]
    rationale: tuple[str, ...]

    @property
    def deep_analysis_eligible(self) -> bool:
        return self.status is not FundamentalStatus.REJECT

    def as_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["company_quality"] = self.company_quality.value
        data["growth_state"] = self.growth_state.value
        data["valuation_state"] = self.valuation_state.value
        data["risk_level"] = self.risk_level.value
        data["evidence_coverage"] = self.evidence_coverage.value
        data["deep_analysis_eligible"] = self.deep_analysis_eligible
        return data


FINANCIAL_PROFILES = {"银行", "券商/资产管理", "保险"}
RND_TOLERANT_PROFILES = {"创新药/生物医药", "工业软件/网络安全"}
CYCLICAL_PROFILES = {
    "造船与海工",
    "光伏与新能源制造",
    "新材料/周期制造",
    "化工/橡胶",
    "影视院线/传媒",
    "港口/航运",
    "农业/养殖",
    "煤炭/油气/资源品",
    "地产/建筑重资产",
}
ORDER_DRIVEN_PROFILES = {
    "造船与海工",
    "半导体设备与材料",
    "AI基础设施/通信硬件",
    "机器人与高端自动化",
    "电网设备与储能",
    "商业航天与军工电子",
    "智能驾驶与汽车电子",
    "先进能源装备",
    "机械/工程机械",
}
MATURE_PE_PROFILES = {
    "食品饮料/白酒",
    "家电/消费电子",
    "零售/专业连锁",
    "医疗器械",
    "公用事业/电力",
}


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _published(period: FinancialPeriod, as_of: datetime) -> bool:
    if not period.notice_date:
        return True
    try:
        published = datetime.fromisoformat(period.notice_date)
    except ValueError:
        return True
    if as_of.tzinfo is not None:
        published = published.replace(tzinfo=as_of.tzinfo)
    return published <= as_of


def visible_periods(rows: Iterable[Mapping[str, object]], *, as_of: datetime) -> tuple[FinancialPeriod, ...]:
    periods = [parse_period(row) for row in rows]
    periods = [period for period in periods if period.report_date and _published(period, as_of)]
    periods.sort(key=lambda item: item.report_date, reverse=True)
    deduped: list[FinancialPeriod] = []
    seen: set[str] = set()
    for period in periods:
        if period.report_date in seen:
            continue
        seen.add(period.report_date)
        deduped.append(period)
    return tuple(deduped)


def _growth_state(periods: Sequence[FinancialPeriod]) -> GrowthState:
    if not periods:
        return GrowthState.UNKNOWN
    current = periods[0]
    revenue = current.revenue_growth
    profit = current.profit_growth
    if revenue is None and profit is None:
        return GrowthState.UNKNOWN
    if (revenue is not None and revenue <= -20) or (profit is not None and profit <= -30):
        return GrowthState.DETERIORATING

    previous = periods[1] if len(periods) > 1 else None
    if previous is not None:
        rev_delta = None if revenue is None or previous.revenue_growth is None else revenue - previous.revenue_growth
        profit_delta = None if profit is None or previous.profit_growth is None else profit - previous.profit_growth
        deltas = [value for value in (rev_delta, profit_delta) if value is not None]
        if deltas and min(deltas) >= 8:
            return GrowthState.ACCELERATING
        if deltas and sum(deltas) / len(deltas) >= 4:
            return GrowthState.IMPROVING
        if deltas and max(deltas) <= -8:
            return GrowthState.SLOWING

    positive = [value for value in (revenue, profit) if value is not None]
    if positive and min(positive) >= 12:
        return GrowthState.IMPROVING
    if positive and min(positive) >= 0:
        return GrowthState.STABLE
    return GrowthState.SLOWING


def _valuation_state(profile: str, *, pe: float | None, pb: float | None) -> ValuationState:
    pe = _num(pe)
    pb = _num(pb)
    if profile in FINANCIAL_PROFILES:
        if pb is None or pb <= 0:
            return ValuationState.UNKNOWN
        if pb < 0.8:
            return ValuationState.LOW
        if pb <= 1.6:
            return ValuationState.REASONABLE
        if pb <= 3.0:
            return ValuationState.HIGH
        return ValuationState.VERY_HIGH

    if profile in RND_TOLERANT_PROFILES and (pe is None or pe <= 0):
        return ValuationState.NOT_APPLICABLE

    if profile in CYCLICAL_PROFILES:
        if pb is not None and pb > 0:
            if pb < 1.0:
                return ValuationState.LOW
            if pb <= 2.2:
                return ValuationState.REASONABLE
            if pb <= 4.0:
                return ValuationState.HIGH
            return ValuationState.VERY_HIGH
        if pe is None or pe <= 0:
            return ValuationState.NOT_APPLICABLE

    if pe is None or pe <= 0:
        return ValuationState.NOT_APPLICABLE
    if profile in MATURE_PE_PROFILES:
        if pe < 15:
            return ValuationState.LOW
        if pe <= 32:
            return ValuationState.REASONABLE
        if pe <= 55:
            return ValuationState.HIGH
        return ValuationState.VERY_HIGH
    if pe < 20:
        return ValuationState.LOW
    if pe <= 45:
        return ValuationState.REASONABLE
    if pe <= 80:
        return ValuationState.HIGH
    return ValuationState.VERY_HIGH


def _quality_from_common_metrics(profile: str, latest: FinancialPeriod | None) -> CompanyQuality:
    if latest is None or profile in FINANCIAL_PROFILES:
        return CompanyQuality.UNKNOWN

    evidence: list[int] = []
    if latest.revenue_growth is not None:
        evidence.append(2 if latest.revenue_growth >= 15 else 1 if latest.revenue_growth >= 5 else -1 if latest.revenue_growth < 0 else 0)
    if latest.profit_growth is not None and profile not in RND_TOLERANT_PROFILES:
        evidence.append(2 if latest.profit_growth >= 15 else 1 if latest.profit_growth >= 5 else -1 if latest.profit_growth < 0 else 0)
    if latest.roe is not None and profile not in RND_TOLERANT_PROFILES:
        evidence.append(2 if latest.roe >= 15 else 1 if latest.roe >= 8 else -1 if latest.roe < 3 else 0)
    if latest.gross_margin is not None:
        evidence.append(1 if latest.gross_margin >= 20 else -2 if latest.gross_margin <= 0 else 0)
    if latest.eps is not None and profile not in RND_TOLERANT_PROFILES:
        evidence.append(1 if latest.eps > 0 else -2)
    if latest.eps is not None and latest.eps > 0 and latest.operating_cash_per_share is not None:
        conversion = latest.operating_cash_per_share / latest.eps
        evidence.append(1 if conversion >= 0.7 else -1 if conversion < 0.3 else 0)

    if not evidence:
        return CompanyQuality.UNKNOWN
    average = sum(evidence) / len(evidence)
    if average >= 1.35:
        return CompanyQuality.EXCELLENT
    if average >= 0.65:
        return CompanyQuality.GOOD
    if average >= -0.15:
        return CompanyQuality.AVERAGE
    return CompanyQuality.WEAK


def _detailed_evidence(
    profile: str,
    detailed_metrics: Mapping[str, object] | None,
    latest: FinancialPeriod | None,
) -> tuple[list[str], list[str], list[str]]:
    positives: list[str] = []
    warnings: list[str] = []
    hard: list[str] = []
    if not detailed_metrics:
        return positives, warnings, hard

    current = detailed_metrics.get("current") if isinstance(detailed_metrics, Mapping) else None
    changes = detailed_metrics.get("changes") if isinstance(detailed_metrics, Mapping) else None
    current = current if isinstance(current, Mapping) else {}
    changes = changes if isinstance(changes, Mapping) else {}

    debt_ratio = _num(current.get("debt_asset_ratio_pct"))
    if debt_ratio is not None and profile not in FINANCIAL_PROFILES | {"公用事业/电力", "地产/建筑重资产"}:
        if debt_ratio >= 90:
            hard.append("资产负债率极高")
        elif debt_ratio >= 80:
            warnings.append("资产负债率偏高")

    operating_cash = _num(current.get("operating_cash_flow"))
    if operating_cash is not None:
        if operating_cash > 0:
            positives.append("经营现金流为正")
        elif latest and latest.eps is not None and latest.eps > 0:
            warnings.append("盈利为正但经营现金流为负")

    receivable_growth = _num(changes.get("accounts_receivable_change_pct"))
    revenue_growth = latest.revenue_growth if latest else None
    if receivable_growth is not None and revenue_growth is not None and receivable_growth - revenue_growth >= 35:
        warnings.append("应收账款增速明显快于营收")

    inventory_growth = _num(changes.get("inventory_change_pct"))
    if inventory_growth is not None and revenue_growth is not None and inventory_growth >= 50 and revenue_growth < 10:
        warnings.append("存货增速明显快于营收")

    contract_growth = _num(changes.get("contract_liabilities_change_pct"))
    if profile in ORDER_DRIVEN_PROFILES and contract_growth is not None:
        if contract_growth >= 20:
            positives.append("合同负债增长，订单景气存在正向辅助证据")
        elif contract_growth <= -30:
            warnings.append("合同负债明显下降，需核对订单与交付节奏")

    cash_growth = _num(changes.get("monetary_funds_change_pct"))
    if profile in RND_TOLERANT_PROFILES and cash_growth is not None and cash_growth <= -35:
        warnings.append("研发型公司货币资金明显下降，需核对现金储备与融资需求")

    return positives, warnings, hard


def _common_evidence(profile: str, latest: FinancialPeriod | None) -> tuple[list[str], list[str], list[str]]:
    positives: list[str] = []
    warnings: list[str] = []
    hard: list[str] = []
    if latest is None:
        return positives, ["缺少可见定期报告"], hard

    if latest.revenue_growth is not None:
        if latest.revenue_growth >= 15:
            positives.append("营收保持较快增长")
        elif latest.revenue_growth < -20:
            warnings.append("营收同比明显下滑")
    if latest.profit_growth is not None:
        if latest.profit_growth >= 15:
            positives.append("利润保持较快增长")
        elif latest.profit_growth < -30:
            warnings.append("利润同比明显下滑")
    if latest.roe is not None and profile not in FINANCIAL_PROFILES | RND_TOLERANT_PROFILES:
        if latest.roe >= 15:
            positives.append("ROE较强")
        elif latest.roe < 3:
            warnings.append("ROE偏低")
    if latest.gross_margin is not None and profile not in FINANCIAL_PROFILES:
        if latest.gross_margin <= 0 and profile not in RND_TOLERANT_PROFILES:
            hard.append("毛利率非正")
        elif latest.gross_margin >= 20:
            positives.append("毛利率具备一定缓冲")
    if latest.eps is not None:
        if latest.eps > 0:
            positives.append("每股收益为正")
        elif profile not in RND_TOLERANT_PROFILES | CYCLICAL_PROFILES:
            warnings.append("每股收益非正")

    if latest.eps is not None and latest.eps > 0 and latest.operating_cash_per_share is not None:
        conversion = latest.operating_cash_per_share / latest.eps
        if conversion >= 0.7:
            positives.append("经营现金流与盈利匹配度较好")
        elif conversion < 0.3:
            warnings.append("经营现金流与盈利匹配度偏弱")

    severe_revenue = latest.revenue_growth is not None and latest.revenue_growth <= -35
    severe_profit = latest.profit_growth is not None and latest.profit_growth <= -50
    if severe_revenue and severe_profit and profile not in RND_TOLERANT_PROFILES | CYCLICAL_PROFILES:
        hard.append("营收与利润同时大幅恶化")
    return positives, warnings, hard


def _coverage(profile: str, periods: Sequence[FinancialPeriod], detailed_metrics: Mapping[str, object] | None) -> EvidenceCoverage:
    if profile in FINANCIAL_PROFILES:
        return EvidenceCoverage.LIMITED
    if not periods:
        return EvidenceCoverage.LIMITED
    if len(periods) >= 4 and detailed_metrics:
        return EvidenceCoverage.FULL
    if len(periods) >= 2:
        return EvidenceCoverage.PARTIAL
    return EvidenceCoverage.LIMITED


def assess_stock_fundamentals(
    rows: Iterable[Mapping[str, object]],
    *,
    as_of: datetime,
    industry_name: str,
    prospect_theme: str | None = None,
    pe: float | None = None,
    pb: float | None = None,
    detailed_metrics: Mapping[str, object] | None = None,
    industry_state: str | None = None,
) -> StockFundamentalAssessment:
    profile_obj = profile_for(industry_name, prospect_theme)
    profile = profile_obj.name
    periods = visible_periods(rows, as_of=as_of)
    latest = periods[0] if periods else None
    previous = periods[1] if len(periods) > 1 else None
    growth = _growth_state(periods)
    valuation = _valuation_state(profile, pe=pe, pb=pb)
    quality = _quality_from_common_metrics(profile, latest)

    positives, warnings, hard = _common_evidence(profile, latest)
    detail_pos, detail_warn, detail_hard = _detailed_evidence(profile, detailed_metrics, latest)
    positives.extend(detail_pos)
    warnings.extend(detail_warn)
    hard.extend(detail_hard)

    followups: list[str] = []
    for item in profile_obj.operating_focus[:4]:
        followups.append(item)
    for item in profile_obj.balance_sheet_focus[:3]:
        if item not in followups:
            followups.append(item)

    coverage = _coverage(profile, periods, detailed_metrics)
    if profile in FINANCIAL_PROFILES:
        warnings.append("金融行业不能由通用制造业财务指标完成最终判断")
    if profile in RND_TOLERANT_PROFILES and latest and latest.eps is not None and latest.eps <= 0 and not detailed_metrics:
        warnings.append("研发期亏损不能用EPS否决，但缺少现金储备/现金流等专属证据")
    if profile in CYCLICAL_PROFILES and latest and latest.profit_growth is not None and latest.profit_growth < 0:
        if not detailed_metrics and industry_state not in {"EARLY", "TRENDING"}:
            warnings.append("周期行业利润走弱，尚缺价格/订单/产能等拐点证据")

    # 去重同时保持解释顺序。
    positives = list(dict.fromkeys(positives))
    warnings = list(dict.fromkeys(warnings))
    hard = list(dict.fromkeys(hard))
    followups = list(dict.fromkeys(followups))

    if hard:
        risk = RiskLevel.HIGH
    elif len(warnings) >= 3:
        risk = RiskLevel.HIGH
    elif warnings:
        risk = RiskLevel.MEDIUM
    else:
        risk = RiskLevel.LOW

    if hard:
        status = FundamentalStatus.REJECT
    elif profile in FINANCIAL_PROFILES:
        status = FundamentalStatus.WATCH
    elif latest is None:
        status = FundamentalStatus.WATCH
    elif growth is GrowthState.DETERIORATING and profile not in CYCLICAL_PROFILES | RND_TOLERANT_PROFILES:
        status = FundamentalStatus.WATCH
    elif risk is RiskLevel.HIGH or quality in {CompanyQuality.WEAK, CompanyQuality.UNKNOWN}:
        status = FundamentalStatus.WATCH
    elif profile in RND_TOLERANT_PROFILES and latest.eps is not None and latest.eps <= 0 and not detailed_metrics:
        status = FundamentalStatus.WATCH
    else:
        status = FundamentalStatus.PASS

    rationale: list[str] = [
        f"行业模型:{profile}",
        f"公司质量:{quality.value}",
        f"成长状态:{growth.value}",
        f"估值状态:{valuation.value}",
        f"风险:{risk.value}",
        f"证据覆盖:{coverage.value}",
    ]
    if valuation is ValuationState.VERY_HIGH:
        rationale.append("估值很高仅作为风险标签，不单独否决")
    if profile in CYCLICAL_PROFILES:
        rationale.append("周期行业不使用低PE=便宜的机械结论")
    if profile in RND_TOLERANT_PROFILES:
        rationale.append("研发型公司不使用负EPS/高PE机械否决")

    return StockFundamentalAssessment(
        status=status,
        profile=profile,
        company_quality=quality,
        growth_state=growth,
        valuation_state=valuation,
        risk_level=risk,
        evidence_coverage=coverage,
        latest_period=latest,
        previous_period=previous,
        visible_period_count=len(periods),
        positive_evidence=tuple(positives),
        warnings=tuple(warnings),
        hard_risks=tuple(hard),
        followups=tuple(followups),
        rationale=tuple(rationale),
    )
