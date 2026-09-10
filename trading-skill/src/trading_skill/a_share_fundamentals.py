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
    fixed_assets: float | None = None
    construction_in_progress: float | None = None
    contract_liabilities: float | None = None
    inventory: float | None = None
    rd_expense: float | None = None


@dataclass(frozen=True, slots=True)
class IndustryMetricPolicy:
    key: str
    valuation_focus: tuple[str, ...]
    operating_focus: tuple[str, ...]
    asset_focus: tuple[str, ...]
    report_focus: tuple[str, ...]

    @property
    def metric_focus(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(self.valuation_focus + self.operating_focus + self.asset_focus))


@dataclass(frozen=True, slots=True)
class FundamentalPrefilter:
    eligible: bool
    grade: str
    annual: FinancialPeriod | None
    interim: FinancialPeriod | None
    latest: FinancialPeriod | None
    reasons: tuple[str, ...]
    industry_policy: str
    valuation_focus: tuple[str, ...]
    metric_focus: tuple[str, ...]
    report_focus: tuple[str, ...]


GENERIC_POLICY = IndustryMetricPolicy(
    "GENERIC",
    ("PE", "PB"),
    ("营收增速", "利润增速", "ROE", "经营现金流"),
    ("资产负债率/资本开支（如可得）",),
    ("最新季报", "中报", "年报"),
)

INDUSTRY_POLICIES: tuple[tuple[tuple[str, ...], IndustryMetricPolicy], ...] = (
    (("银行",), IndustryMetricPolicy(
        "BANK",
        ("PB", "股息率", "ROE"),
        ("净息差NIM", "不良率", "拨备覆盖率", "手续费收入"),
        ("核心一级资本充足率", "存贷款结构"),
        ("季度息差/资产质量", "年报"),
    )),
    (("保险",), IndustryMetricPolicy(
        "INSURANCE",
        ("PB", "PEV/内含价值（如可得）", "ROE"),
        ("新业务价值NBV", "综合成本率", "投资收益率"),
        ("偿付能力", "资产负债久期"),
        ("季度NBV/保费", "年报"),
    )),
    (("证券", "券商"), IndustryMetricPolicy(
        "BROKER",
        ("PB", "PE", "ROE"),
        ("经纪/投行/资管收入", "两融与成交活跃度"),
        ("净资本", "金融资产敞口"),
        ("季度业绩弹性", "年报"),
    )),
    (("煤炭", "石油", "油气", "有色", "钢铁", "化工", "基础化工"), IndustryMetricPolicy(
        "CYCLICAL_RESOURCE",
        ("PB", "EV/EBITDA（如可得）", "周期中枢PE"),
        ("产品价格/价差", "产销量", "单位成本", "库存"),
        ("固定资产", "在建工程", "资本开支", "产能利用率"),
        ("季度盈利弹性", "产能投放节奏", "年报"),
    )),
    (("船舶", "海工", "电网", "电力设备", "机械", "机器人", "自动化", "机床", "军工", "航空", "航天"), IndustryMetricPolicy(
        "ORDER_DRIVEN_MANUFACTURING",
        ("PE", "PB", "订单对应估值"),
        ("新签订单", "在手订单/订单覆盖倍数", "合同负债", "交付节奏"),
        ("固定资产", "在建工程", "资本开支", "产能利用率"),
        ("季度订单变化", "业绩兑现率", "年报"),
    )),
    (("半导体", "电子", "光模块", "光通信", "服务器", "算力", "数据中心", "软件", "网络安全", "人工智能", "AI"), IndustryMetricPolicy(
        "GROWTH_TECH",
        ("PE", "PS", "PEG/增速匹配"),
        ("营收增速", "毛利率", "研发强度", "客户/产品渗透率"),
        ("库存", "资本开支", "固定资产/在建工程（制造环节）"),
        ("季度增速与指引", "研发/新品进展", "年报"),
    )),
    (("医药", "创新药", "生物", "医疗器械"), IndustryMetricPolicy(
        "HEALTHCARE_INNOVATION",
        ("PE", "PS", "管线风险调整估值（如可得）"),
        ("研发强度", "核心产品放量", "管线里程碑", "授权BD"),
        ("现金储备", "研发资本化/费用化", "产能建设"),
        ("季度产品收入", "临床/审批里程碑", "年报"),
    )),
    (("食品", "饮料", "白酒", "家电", "零售", "消费", "纺织", "服装"), IndustryMetricPolicy(
        "CONSUMER",
        ("PE", "FCF收益率（如可得）"),
        ("收入增速", "毛利率", "渠道库存", "同店/销量", "经营现金流"),
        ("存货", "应收", "渠道投入"),
        ("季度动销与库存", "年报"),
    )),
    (("电力", "公用事业", "燃气", "水务", "高速公路"), IndustryMetricPolicy(
        "UTILITY",
        ("PB", "股息率", "FCF收益率（如可得）"),
        ("利用小时/售电量", "电价/气价", "现金流"),
        ("负债率", "固定资产", "在建工程", "资本开支"),
        ("季度现金流", "项目投产", "年报"),
    )),
    (("房地产", "建筑", "基建"), IndustryMetricPolicy(
        "PROPERTY_CONSTRUCTION",
        ("PB", "NAV折价（如可得）"),
        ("销售/回款", "新签合同", "合同负债", "经营现金流"),
        ("存货", "净负债", "在建工程", "固定资产"),
        ("季度销售/订单", "年报"),
    )),
)


def industry_metric_policy(industry_name: str | None) -> IndustryMetricPolicy:
    text = str(industry_name or "")
    for keywords, policy in INDUSTRY_POLICIES:
        if any(keyword.lower() in text.lower() for keyword in keywords):
            return policy
    return GENERIC_POLICY


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _first(row: Mapping[str, object], *keys: str) -> object:
    for key in keys:
        if key in row and row.get(key) not in (None, "", "-"):
            return row.get(key)
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
        fixed_assets=_num(_first(row, "FIXED_ASSET", "FIXED_ASSETS", "FIXED_ASSET_BALANCE")),
        construction_in_progress=_num(_first(row, "CONSTRUCTION_IN_PROGRESS", "CIP", "CONSTRUCT_PROCESS")),
        contract_liabilities=_num(_first(row, "CONTRACT_LIAB", "CONTRACT_LIABILITY", "CONTRACT_LIABILITIES")),
        inventory=_num(_first(row, "INVENTORY", "INVENTORY_BALANCE")),
        rd_expense=_num(_first(row, "RESEARCH_EXPENSE", "R_AND_D_EXPENSE", "RD_EXPENSE")),
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


def select_reports(
    rows: Iterable[Mapping[str, object]], *, as_of: datetime
) -> tuple[FinancialPeriod | None, FinancialPeriod | None, FinancialPeriod | None]:
    periods = [parse_period(row) for row in rows]
    periods = [period for period in periods if period.report_date and _visible(period, as_of)]
    periods.sort(key=lambda p: p.report_date, reverse=True)
    annual = next((p for p in periods if p.report_date.endswith("12-31")), None)
    interim = next((p for p in periods if p.report_date.endswith("06-30")), None)
    latest = periods[0] if periods else None
    return annual, interim, latest


def select_annual_and_interim(
    rows: Iterable[Mapping[str, object]], *, as_of: datetime
) -> tuple[FinancialPeriod | None, FinancialPeriod | None]:
    annual, interim, _ = select_reports(rows, as_of=as_of)
    return annual, interim


def _valuation_penalty(policy: IndustryMetricPolicy, *, pe: float | None, pb: float | None, growth: float | None) -> int:
    if policy.key in {"BANK", "INSURANCE", "BROKER"}:
        return 1 if pb is not None and pb > 4 else 0
    if policy.key in {"CYCLICAL_RESOURCE", "UTILITY", "PROPERTY_CONSTRUCTION", "ORDER_DRIVEN_MANUFACTURING"}:
        return 1 if pb is not None and pb > 6 else 0
    if policy.key in {"GROWTH_TECH", "HEALTHCARE_INNOVATION"}:
        return 1 if pe is not None and pe > 150 and (growth or 0) < 20 else 0
    if policy.key == "CONSUMER":
        return 1 if pe is not None and pe > 80 else 0
    penalty = 0
    if pe is not None and pe > 100:
        penalty += 1
    if pb is not None and pb > 12:
        penalty += 1
    return min(2, penalty)


def evaluate_prefilter(
    rows: Iterable[Mapping[str, object]], *, as_of: datetime, pe: float | None, pb: float | None,
    industry_name: str | None = None,
) -> FundamentalPrefilter:
    annual, interim, latest = select_reports(rows, as_of=as_of)
    policy = industry_metric_policy(industry_name)
    reasons: list[str] = []
    if annual is None:
        reasons.append("缺少已披露年报")
    if latest is None:
        reasons.append("缺少已披露财报")
    if annual is None or latest is None:
        return FundamentalPrefilter(
            False, "E", annual, interim, latest, tuple(reasons), policy.key,
            policy.valuation_focus, policy.metric_focus, policy.report_focus,
        )

    metrics = latest
    if metrics.revenue_growth is not None and metrics.revenue_growth < -20:
        reasons.append("营收同比明显下滑")
    if metrics.profit_growth is not None and metrics.profit_growth < -25:
        reasons.append("净利润同比明显下滑")
    if metrics.roe is not None and metrics.roe < 3 and policy.key not in {"CYCLICAL_RESOURCE"}:
        reasons.append("净资产收益率偏低")
    if metrics.eps is not None and metrics.eps <= 0:
        reasons.append("每股收益非正")
    if metrics.gross_margin is not None and metrics.gross_margin <= 0 and policy.key not in {"BANK", "INSURANCE", "BROKER"}:
        reasons.append("毛利率异常")

    score = 0
    score += 2 if (metrics.revenue_growth or 0) >= 15 else 1 if (metrics.revenue_growth or 0) >= 5 else 0
    score += 2 if (metrics.profit_growth or 0) >= 15 else 1 if (metrics.profit_growth or 0) >= 5 else 0
    score += 2 if (metrics.roe or 0) >= 15 else 1 if (metrics.roe or 0) >= 8 else 0
    if policy.key not in {"BANK", "INSURANCE", "BROKER"}:
        score += 1 if (metrics.gross_margin or 0) >= 20 else 0
    if metrics.eps and metrics.operating_cash_per_share is not None and metrics.eps > 0:
        score += 1 if metrics.operating_cash_per_share / metrics.eps >= 0.7 else 0
    score -= _valuation_penalty(policy, pe=pe, pb=pb, growth=metrics.revenue_growth)

    # 已进入应披露当年季报的时段却只拿到上年年报时，不硬否决，但降低置信度。
    if as_of.month >= 5 and latest.report_date.endswith("12-31") and latest.report_date[:4] < str(as_of.year):
        score -= 1
        reasons.append("最新季度财报暂未取得，按已披露年报降一级置信度")

    hard_fail = any(
        reason in reasons
        for reason in ("营收同比明显下滑", "净利润同比明显下滑", "每股收益非正", "毛利率异常")
    )
    if score >= 7:
        grade = "A"
    elif score >= 5:
        grade = "B"
    elif score >= 3:
        grade = "C"
    elif score >= 1:
        grade = "D"
    else:
        grade = "E"
    eligible = not hard_fail and grade in {"A", "B", "C"}
    if not eligible and not reasons:
        reasons.append("财务质量暂未达到候选阈值")

    return FundamentalPrefilter(
        eligible=eligible,
        grade=grade,
        annual=annual,
        interim=interim,
        latest=latest,
        reasons=tuple(reasons),
        industry_policy=policy.key,
        valuation_focus=policy.valuation_focus,
        metric_focus=policy.metric_focus,
        report_focus=policy.report_focus,
    )
