from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Mapping


@dataclass(frozen=True, slots=True)
class FinancialPeriod:
    report_date: str
    notice_date: str
    revenue_growth: float | None
    profit_growth: float | None
    roe: float | None
    gross_margin: float | None
    eps: float | None
    operating_cash_per_share: float | None


@dataclass(frozen=True, slots=True)
class IndustryMetricPolicy:
    name: str
    valuation_basis: str
    focus_metrics: tuple[str, ...]
    external_metrics: tuple[str, ...]
    revenue_hard_floor: float
    profit_hard_floor: float | None
    require_positive_eps: bool
    pe_soft_limit: float | None
    pb_soft_limit: float | None
    revenue_weight: int = 2
    profit_weight: int = 2
    roe_weight: int = 2
    gross_margin_weight: int = 1
    cash_weight: int = 1
    gross_margin_strong: float = 20.0
    grade_a: int = 7
    grade_b: int = 5
    grade_c: int = 3


@dataclass(frozen=True, slots=True)
class FundamentalPrefilter:
    eligible: bool
    grade: str
    annual: FinancialPeriod | None
    interim: FinancialPeriod | None
    reasons: tuple[str, ...]
    policy_name: str = "GENERIC_QUALITY"
    valuation_basis: str = "PE+PB+盈利质量"
    focus_metrics: tuple[str, ...] = ()
    external_metrics_required: tuple[str, ...] = ()


GENERIC_POLICY = IndustryMetricPolicy(
    name="GENERIC_QUALITY",
    valuation_basis="PE+PB+盈利质量",
    focus_metrics=("营收增速", "净利润增速", "ROE", "毛利率", "经营现金流/每股收益"),
    external_metrics=(),
    revenue_hard_floor=-20,
    profit_hard_floor=-25,
    require_positive_eps=True,
    pe_soft_limit=100,
    pb_soft_limit=12,
)

INNOVATION_DRUG_POLICY = IndustryMetricPolicy(
    name="INNOVATION_DRUG_RND",
    valuation_basis="管线价值+研发效率+现金跑道；亏损期PE不适用",
    focus_metrics=("营收/商业化增速", "毛利率", "经营现金流", "研发投入质量"),
    external_metrics=("研发费用率", "核心管线临床进度", "BD/授权里程碑", "现金及等价物与现金跑道", "商业化产品收入"),
    revenue_hard_floor=-35,
    profit_hard_floor=None,
    require_positive_eps=False,
    pe_soft_limit=None,
    pb_soft_limit=15,
    revenue_weight=2,
    profit_weight=1,
    roe_weight=0,
    gross_margin_weight=2,
    cash_weight=1,
    gross_margin_strong=55,
    grade_a=6,
    grade_b=5,
    grade_c=3,
)

ORDER_DRIVEN_POLICY = IndustryMetricPolicy(
    name="ORDER_DRIVEN_CAPITAL_GOODS",
    valuation_basis="订单周期+现金流+PB/PE交叉验证",
    focus_metrics=("营收增速", "净利润增速", "毛利率", "ROE", "经营现金流"),
    external_metrics=("新签订单", "在手订单/订单收入比", "合同负债", "在建工程/产能扩张", "交付周期与毛利率变化"),
    revenue_hard_floor=-25,
    profit_hard_floor=-35,
    require_positive_eps=True,
    pe_soft_limit=80,
    pb_soft_limit=10,
    gross_margin_strong=18,
)

TECH_CAPEX_POLICY = IndustryMetricPolicy(
    name="SEMICONDUCTOR_CAPEX_TECH",
    valuation_basis="成长质量+研发强度+国产替代空间；PE容忍度高于成熟制造",
    focus_metrics=("营收增速", "净利润增速", "毛利率", "ROE", "经营现金流"),
    external_metrics=("研发费用率", "核心设备/材料收入占比", "存货周转", "合同负债/订单", "资本开支", "客户验证进度"),
    revenue_hard_floor=-25,
    profit_hard_floor=-35,
    require_positive_eps=True,
    pe_soft_limit=150,
    pb_soft_limit=18,
    gross_margin_strong=35,
)

TECH_GROWTH_POLICY = IndustryMetricPolicy(
    name="TECH_GROWTH",
    valuation_basis="增长持续性+毛利率+研发投入；高估值需由增长兑现",
    focus_metrics=("营收增速", "净利润增速", "毛利率", "经营现金流", "ROE"),
    external_metrics=("研发费用率", "订单/出货量", "客户集中度", "产品单价与渗透率", "库存变化"),
    revenue_hard_floor=-25,
    profit_hard_floor=-35,
    require_positive_eps=True,
    pe_soft_limit=140,
    pb_soft_limit=16,
    gross_margin_strong=30,
)

SOFTWARE_POLICY = IndustryMetricPolicy(
    name="SOFTWARE_RND_GROWTH",
    valuation_basis="收入增长+毛利率+现金流+研发效率；早期利润权重降低",
    focus_metrics=("营收增速", "毛利率", "经营现金流", "净利润趋势"),
    external_metrics=("研发费用率", "合同负债", "ARR/订阅收入", "续费率", "人均创收"),
    revenue_hard_floor=-30,
    profit_hard_floor=None,
    require_positive_eps=False,
    pe_soft_limit=180,
    pb_soft_limit=18,
    revenue_weight=2,
    profit_weight=1,
    roe_weight=1,
    gross_margin_weight=2,
    cash_weight=1,
    gross_margin_strong=45,
    grade_a=7,
    grade_b=5,
    grade_c=3,
)

MEDICAL_DEVICE_POLICY = IndustryMetricPolicy(
    name="MEDICAL_DEVICE_QUALITY",
    valuation_basis="盈利质量+现金流+产品结构+估值分位",
    focus_metrics=("营收增速", "净利润增速", "毛利率", "ROE", "经营现金流"),
    external_metrics=("研发费用率", "新品注册/获批", "设备更新订单", "海外收入占比", "应收账款周转"),
    revenue_hard_floor=-25,
    profit_hard_floor=-30,
    require_positive_eps=True,
    pe_soft_limit=110,
    pb_soft_limit=12,
    gross_margin_strong=40,
)

MATERIAL_CYCLE_POLICY = IndustryMetricPolicy(
    name="MATERIAL_CYCLE",
    valuation_basis="PB/ROE+现金流+产能周期；单看PE容易误判周期顶部/底部",
    focus_metrics=("ROE", "经营现金流", "毛利率", "营收与利润趋势"),
    external_metrics=("产品价格/价差", "产能利用率", "库存", "在建工程", "资本开支", "新增产能投放节奏"),
    revenue_hard_floor=-30,
    profit_hard_floor=-40,
    require_positive_eps=True,
    pe_soft_limit=90,
    pb_soft_limit=8,
    revenue_weight=1,
    profit_weight=2,
    roe_weight=2,
    gross_margin_weight=1,
    cash_weight=2,
    gross_margin_strong=20,
)


_THEME_POLICY = {
    "创新药": INNOVATION_DRUG_POLICY,
    "造船与海工": ORDER_DRIVEN_POLICY,
    "半导体设备与材料": TECH_CAPEX_POLICY,
    "人工智能基础设施": TECH_GROWTH_POLICY,
    "机器人与高端自动化": TECH_GROWTH_POLICY,
    "电网升级与储能": ORDER_DRIVEN_POLICY,
    "商业航天与军工电子": ORDER_DRIVEN_POLICY,
    "智能驾驶与汽车电子": TECH_GROWTH_POLICY,
    "医疗器械": MEDICAL_DEVICE_POLICY,
    "先进能源装备": ORDER_DRIVEN_POLICY,
    "新材料": MATERIAL_CYCLE_POLICY,
    "工业软件与网络安全": SOFTWARE_POLICY,
}


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _date(value: object) -> str:
    text = str(value or "")
    return text[:10]


def parse_period(row: Mapping[str, object]) -> FinancialPeriod:
    return FinancialPeriod(
        report_date=_date(row.get("REPORTDATE")),
        notice_date=_date(row.get("NOTICE_DATE") or row.get("UPDATE_DATE")),
        revenue_growth=_num(row.get("YSTZ")),
        profit_growth=_num(row.get("SJLTZ")),
        roe=_num(row.get("WEIGHTAVG_ROE")),
        gross_margin=_num(row.get("XSMLL")),
        eps=_num(row.get("BASIC_EPS")),
        operating_cash_per_share=_num(row.get("MGJYXJJE")),
    )


def _visible(period: FinancialPeriod, as_of: datetime) -> bool:
    if not period.notice_date:
        return True
    try:
        published = datetime.fromisoformat(period.notice_date)
    except ValueError:
        return True
    if as_of.tzinfo is not None:
        published = published.replace(tzinfo=as_of.tzinfo)
    return published <= as_of


def select_annual_and_interim(
    rows: Iterable[Mapping[str, object]], *, as_of: datetime
) -> tuple[FinancialPeriod | None, FinancialPeriod | None]:
    periods = [parse_period(row) for row in rows]
    periods = [period for period in periods if period.report_date and _visible(period, as_of)]
    periods.sort(key=lambda p: p.report_date, reverse=True)
    annual = next((p for p in periods if p.report_date.endswith("12-31")), None)
    interim = next((p for p in periods if p.report_date.endswith("06-30")), None)
    return annual, interim


def industry_metric_policy(*, industry_name: str | None = None, prospect_theme: str | None = None) -> IndustryMetricPolicy:
    if prospect_theme and prospect_theme in _THEME_POLICY:
        return _THEME_POLICY[prospect_theme]
    text = str(industry_name or "")
    keyword_policies = (
        (("创新药", "生物药", "生物制品", "医药研发"), INNOVATION_DRUG_POLICY),
        (("船舶", "海工", "电网", "特高压", "变压器", "航天", "军工", "核电", "风电设备"), ORDER_DRIVEN_POLICY),
        (("半导体设备", "半导体材料", "电子特气", "光刻"), TECH_CAPEX_POLICY),
        (("服务器", "光模块", "算力", "机器人", "汽车电子", "智能驾驶"), TECH_GROWTH_POLICY),
        (("软件", "网络安全", "信息安全", "数据库"), SOFTWARE_POLICY),
        (("医疗器械", "体外诊断", "医学影像"), MEDICAL_DEVICE_POLICY),
        (("材料", "碳纤维", "高温合金"), MATERIAL_CYCLE_POLICY),
    )
    for keywords, policy in keyword_policies:
        if any(keyword in text for keyword in keywords):
            return policy
    return GENERIC_POLICY


def _points(value: float | None, *, strong: float, acceptable: float, weight: int) -> int:
    if weight <= 0 or value is None:
        return 0
    if value >= strong:
        return weight
    if value >= acceptable:
        return max(1, weight // 2)
    return 0


def _score_metrics(metrics: FinancialPeriod, policy: IndustryMetricPolicy) -> int:
    score = 0
    score += _points(metrics.revenue_growth, strong=15, acceptable=5, weight=policy.revenue_weight)
    score += _points(metrics.profit_growth, strong=15, acceptable=5, weight=policy.profit_weight)
    score += _points(metrics.roe, strong=15, acceptable=8, weight=policy.roe_weight)
    score += _points(metrics.gross_margin, strong=policy.gross_margin_strong, acceptable=max(10, policy.gross_margin_strong * 0.6), weight=policy.gross_margin_weight)
    if policy.cash_weight > 0:
        cash_quality = 0.0
        if metrics.eps is not None and metrics.eps > 0 and metrics.operating_cash_per_share is not None:
            cash_quality = metrics.operating_cash_per_share / metrics.eps
        elif metrics.operating_cash_per_share is not None and metrics.operating_cash_per_share > 0:
            cash_quality = 0.7
        if cash_quality >= 1.0:
            score += policy.cash_weight
        elif cash_quality >= 0.7:
            score += max(1, policy.cash_weight // 2)
    return score


def _grade(score: int, policy: IndustryMetricPolicy) -> str:
    if score >= policy.grade_a:
        return "A"
    if score >= policy.grade_b:
        return "B"
    if score >= policy.grade_c:
        return "C"
    if score >= 1:
        return "D"
    return "E"


def evaluate_prefilter(
    rows: Iterable[Mapping[str, object]],
    *,
    as_of: datetime,
    pe: float | None,
    pb: float | None,
    industry_name: str | None = None,
    prospect_theme: str | None = None,
) -> FundamentalPrefilter:
    policy = industry_metric_policy(industry_name=industry_name, prospect_theme=prospect_theme)
    annual, interim = select_annual_and_interim(rows, as_of=as_of)
    reasons: list[str] = [f"行业指标策略:{policy.name}"]
    if annual is None:
        reasons.append("缺少已披露年报")
    if interim is None:
        reasons.append("缺少已披露中报")
    if annual is None or interim is None:
        return FundamentalPrefilter(
            False,
            "E",
            annual,
            interim,
            tuple(reasons),
            policy.name,
            policy.valuation_basis,
            policy.focus_metrics,
            policy.external_metrics,
        )

    metrics = interim
    hard_fail = False
    if metrics.revenue_growth is not None and metrics.revenue_growth < policy.revenue_hard_floor:
        reasons.append("营收同比明显下滑")
        hard_fail = True
    if policy.profit_hard_floor is not None and metrics.profit_growth is not None and metrics.profit_growth < policy.profit_hard_floor:
        reasons.append("净利润同比明显下滑")
        hard_fail = True
    elif policy.profit_hard_floor is None and metrics.profit_growth is not None and metrics.profit_growth < -25:
        reasons.append("净利润承压（研发/成长型行业不作单项硬否决）")
    if metrics.roe is not None and metrics.roe < 3 and policy.roe_weight >= 2:
        reasons.append("净资产收益率偏低")
    if policy.require_positive_eps and metrics.eps is not None and metrics.eps <= 0:
        reasons.append("每股收益非正")
        hard_fail = True
    elif not policy.require_positive_eps and metrics.eps is not None and metrics.eps <= 0:
        reasons.append("每股收益非正（本行业允许研发/扩张期亏损，需结合现金跑道等指标）")
    if metrics.gross_margin is not None and metrics.gross_margin <= 0:
        reasons.append("毛利率异常")
        hard_fail = True

    score = _score_metrics(metrics, policy)
    if policy.pe_soft_limit is not None and pe is not None and pe > policy.pe_soft_limit:
        score -= 1
        reasons.append("PE高于本行业软阈值")
    if policy.pb_soft_limit is not None and pb is not None and pb > policy.pb_soft_limit:
        score -= 1
        reasons.append("PB高于本行业软阈值")

    grade = _grade(max(0, score), policy)
    eligible = not hard_fail and grade in {"A", "B", "C"}
    if policy.external_metrics:
        reasons.append("行业关键外部指标待补采:" + "、".join(policy.external_metrics))
    if not eligible and not hard_fail:
        reasons.append("按本行业指标策略，当前财务质量暂未达到严格候选阈值")

    return FundamentalPrefilter(
        eligible,
        grade,
        annual,
        interim,
        tuple(reasons),
        policy.name,
        policy.valuation_basis,
        policy.focus_metrics,
        policy.external_metrics,
    )
