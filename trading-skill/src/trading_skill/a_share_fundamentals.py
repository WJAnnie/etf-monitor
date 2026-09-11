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
    revenue: float | None = None
    total_assets: float | None = None
    total_liabilities: float | None = None
    debt_ratio: float | None = None
    monetary_funds: float | None = None
    accounts_receivable: float | None = None
    fixed_assets: float | None = None
    construction_in_progress: float | None = None
    contract_liabilities: float | None = None
    inventory: float | None = None
    rd_expense: float | None = None
    capex: float | None = None
    npl_ratio: float | None = None
    core_tier1_ratio: float | None = None
    total_deposits: float | None = None
    gross_loans: float | None = None
    solvency_ratio: float | None = None
    net_investment_return: float | None = None
    net_capital: float | None = None
    financial_detail_status: str = "BASE_ONLY"


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
    hard_fail: bool = False
    industry_evidence_status: str = "BASE_ONLY"
    industry_evidence_reasons: tuple[str, ...] = ()


GENERIC_POLICY = IndustryMetricPolicy(
    "GENERIC",
    ("PE", "PB"),
    ("营收增速", "利润增速", "ROE", "经营现金流"),
    ("资产负债率/资本开支（如可得）",),
    ("最新季报", "中报", "年报"),
)

INDUSTRY_POLICIES: tuple[tuple[tuple[str, ...], IndustryMetricPolicy], ...] = (
    (("银行",), IndustryMetricPolicy("BANK", ("PB", "股息率", "ROE"), ("净息差NIM", "不良率", "拨备覆盖率", "手续费收入"), ("核心一级资本充足率", "存贷款结构"), ("季度息差/资产质量", "年报"))),
    (("保险",), IndustryMetricPolicy("INSURANCE", ("PB", "PEV/内含价值（如可得）", "ROE"), ("新业务价值NBV", "综合成本率", "投资收益率"), ("偿付能力", "资产负债久期"), ("季度NBV/保费", "年报"))),
    (("证券", "券商"), IndustryMetricPolicy("BROKER", ("PB", "PE", "ROE"), ("经纪/投行/资管收入", "两融与成交活跃度"), ("净资本", "金融资产敞口"), ("季度业绩弹性", "年报"))),
    (("煤炭", "石油", "油气", "有色", "钢铁", "化工", "基础化工"), IndustryMetricPolicy("CYCLICAL_RESOURCE", ("PB", "EV/EBITDA（如可得）", "周期中枢PE"), ("产品价格/价差", "产销量", "单位成本", "库存"), ("固定资产", "在建工程", "资本开支", "产能利用率"), ("季度盈利弹性", "产能投放节奏", "年报"))),
    (("船舶", "海工", "电网", "电力设备", "机械", "机器人", "自动化", "机床", "军工", "航空", "航天"), IndustryMetricPolicy("ORDER_DRIVEN_MANUFACTURING", ("PE", "PB", "订单对应估值"), ("新签订单", "在手订单/订单覆盖倍数", "合同负债", "交付节奏"), ("固定资产", "在建工程", "资本开支", "产能利用率"), ("季度订单变化", "业绩兑现率", "年报"))),
    (("半导体", "电子", "光模块", "光通信", "服务器", "算力", "数据中心", "软件", "网络安全", "人工智能", "AI"), IndustryMetricPolicy("GROWTH_TECH", ("PE", "PS", "PEG/增速匹配"), ("营收增速", "毛利率", "研发强度", "客户/产品渗透率"), ("库存", "资本开支", "固定资产/在建工程（制造环节）"), ("季度增速与指引", "研发/新品进展", "年报"))),
    (("医药", "创新药", "生物", "医疗器械"), IndustryMetricPolicy("HEALTHCARE_INNOVATION", ("PE", "PS", "管线风险调整估值（如可得）"), ("研发强度", "核心产品放量", "管线里程碑", "授权BD"), ("现金储备", "研发资本化/费用化", "产能建设"), ("季度产品收入", "临床/审批里程碑", "年报"))),
    (("食品", "饮料", "白酒", "家电", "零售", "消费", "纺织", "服装"), IndustryMetricPolicy("CONSUMER", ("PE", "FCF收益率（如可得）"), ("收入增速", "毛利率", "渠道库存", "同店/销量", "经营现金流"), ("存货", "应收", "渠道投入"), ("季度动销与库存", "年报"))),
    (("电力", "公用事业", "燃气", "水务", "高速公路"), IndustryMetricPolicy("UTILITY", ("PB", "股息率", "FCF收益率（如可得）"), ("利用小时/售电量", "电价/气价", "现金流"), ("负债率", "固定资产", "在建工程", "资本开支"), ("季度现金流", "项目投产", "年报"))),
    (("房地产", "建筑", "基建"), IndustryMetricPolicy("PROPERTY_CONSTRUCTION", ("PB", "NAV折价（如可得）"), ("销售/回款", "新签合同", "合同负债", "经营现金流"), ("存货", "净负债", "在建工程", "固定资产"), ("季度销售/订单", "年报"))),
)

DETAIL_POLICIES = {
    "BANK", "INSURANCE", "BROKER", "CYCLICAL_RESOURCE", "ORDER_DRIVEN_MANUFACTURING",
    "GROWTH_TECH", "HEALTHCARE_INNOVATION", "CONSUMER", "UTILITY", "PROPERTY_CONSTRUCTION",
}


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
        if row.get(key) not in (None, "", "-"):
            return row.get(key)
    return None


def _date(value: object) -> str:
    return str(value or "")[:10]


def parse_period(row: Mapping[str, object]) -> FinancialPeriod:
    return FinancialPeriod(
        report_date=_date(_first(row, "REPORTDATE", "REPORT_DATE")),
        notice_date=_date(_first(row, "NOTICE_DATE", "UPDATE_DATE", "NOTICE_DATE_NAME")),
        revenue_growth=_num(_first(row, "YSTZ", "TOTALOPERATEREVETZ")),
        profit_growth=_num(_first(row, "SJLTZ", "PARENTNETPROFITTZ")),
        roe=_num(_first(row, "WEIGHTAVG_ROE", "ROEJQ")),
        gross_margin=_num(_first(row, "XSMLL")),
        eps=_num(_first(row, "BASIC_EPS", "EPSJB")),
        operating_cash_per_share=_num(_first(row, "MGJYXJJE")),
        revenue=_num(_first(row, "TOTALOPERATEREVE", "TOTAL_OPERATE_INCOME")),
        total_assets=_num(_first(row, "TOTAL_ASSETS")),
        total_liabilities=_num(_first(row, "TOTAL_LIABILITIES")),
        debt_ratio=_num(_first(row, "ZCFZL")),
        monetary_funds=_num(_first(row, "MONETARYFUNDS")),
        accounts_receivable=_num(_first(row, "ACCOUNTS_RECE", "NOTE_ACCOUNTS_RECE")),
        fixed_assets=_num(_first(row, "FIXED_ASSET", "FIXED_ASSETS", "FIXED_ASSET_BALANCE")),
        construction_in_progress=_num(_first(row, "CIP", "CONSTRUCTION_IN_PROGRESS", "CONSTRUCT_PROCESS")),
        contract_liabilities=_num(_first(row, "CONTRACT_LIAB", "CONTRACT_LIABILITY", "CONTRACT_LIABILITIES")),
        inventory=_num(_first(row, "INVENTORY", "INVENTORY_BALANCE")),
        rd_expense=_num(_first(row, "RESEARCH_EXPENSE", "R_AND_D_EXPENSE", "RD_EXPENSE")),
        capex=_num(_first(row, "CONSTRUCT_LONG_ASSET")),
        npl_ratio=_num(_first(row, "BLDKBBL")),
        core_tier1_ratio=_num(_first(row, "HXYJBCZL")),
        total_deposits=_num(_first(row, "TOTALDEPOSITS")),
        gross_loans=_num(_first(row, "GROSSLOANS")),
        solvency_ratio=_num(_first(row, "SOLVENCY_AR")),
        net_investment_return=_num(_first(row, "NET_ROI")),
        net_capital=_num(_first(row, "JZB")),
        financial_detail_status=str(row.get("FINANCIAL_DETAIL_STATUS") or "BASE_ONLY"),
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


def _periods(rows: Iterable[Mapping[str, object]], *, as_of: datetime) -> list[FinancialPeriod]:
    periods = [parse_period(row) for row in rows]
    periods = [p for p in periods if p.report_date and _visible(p, as_of)]
    periods.sort(key=lambda p: p.report_date, reverse=True)
    return periods


def select_reports(rows: Iterable[Mapping[str, object]], *, as_of: datetime) -> tuple[FinancialPeriod | None, FinancialPeriod | None, FinancialPeriod | None]:
    periods = _periods(rows, as_of=as_of)
    annual = next((p for p in periods if p.report_date.endswith("12-31")), None)
    interim = next((p for p in periods if p.report_date.endswith("06-30")), None)
    return annual, interim, periods[0] if periods else None


def select_annual_and_interim(rows: Iterable[Mapping[str, object]], *, as_of: datetime) -> tuple[FinancialPeriod | None, FinancialPeriod | None]:
    annual, interim, _ = select_reports(rows, as_of=as_of)
    return annual, interim


def _pct_change(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return (current / previous - 1.0) * 100.0


def _valuation_penalty(policy: IndustryMetricPolicy, *, pe: float | None, pb: float | None, growth: float | None) -> int:
    if policy.key in {"BANK", "INSURANCE", "BROKER"}:
        return 1 if pb is not None and pb > 4 else 0
    if policy.key in {"CYCLICAL_RESOURCE", "UTILITY", "PROPERTY_CONSTRUCTION", "ORDER_DRIVEN_MANUFACTURING"}:
        return 1 if pb is not None and pb > 6 else 0
    if policy.key in {"GROWTH_TECH", "HEALTHCARE_INNOVATION"}:
        return 1 if pe is not None and pe > 150 and (growth or 0) < 20 else 0
    if policy.key == "CONSUMER":
        return 1 if pe is not None and pe > 80 else 0
    return min(2, (1 if pe is not None and pe > 100 else 0) + (1 if pb is not None and pb > 12 else 0))


def _base_score(m: FinancialPeriod, policy: IndustryMetricPolicy) -> int:
    if policy.key in {"BANK", "INSURANCE", "BROKER"}:
        return (2 if (m.roe or 0) >= 12 else 1 if (m.roe or 0) >= 7 else 0) + (1 if (m.profit_growth or 0) >= 5 else 0)
    score = 2 if (m.revenue_growth or 0) >= 15 else 1 if (m.revenue_growth or 0) >= 5 else 0
    if policy.key == "CYCLICAL_RESOURCE":
        score += 1 if (m.profit_growth or 0) >= 10 else 0
    else:
        score += 2 if (m.profit_growth or 0) >= 15 else 1 if (m.profit_growth or 0) >= 5 else 0
    score += 2 if (m.roe or 0) >= 15 else 1 if (m.roe or 0) >= 8 else 0
    score += 1 if (m.gross_margin or 0) >= 20 else 0
    if m.eps and m.operating_cash_per_share is not None and m.eps > 0:
        score += 1 if m.operating_cash_per_share / m.eps >= 0.7 else 0
    return score


def _industry_adjustment(policy: IndustryMetricPolicy, latest: FinancialPeriod, previous: FinancialPeriod | None) -> tuple[int, list[str], bool, bool]:
    delta = 0
    notes: list[str] = []
    hard = False
    used = False

    if policy.key == "BANK":
        if latest.npl_ratio is not None:
            used = True
            if latest.npl_ratio > 5:
                hard = True; notes.append("银行不良贷款率处于高风险区间")
            elif latest.npl_ratio > 3:
                delta -= 2; notes.append("银行不良贷款率偏高")
            elif latest.npl_ratio <= 1.5:
                delta += 1; notes.append("银行不良贷款率处于较低水平")
        if latest.core_tier1_ratio is not None:
            used = True
            if latest.core_tier1_ratio < 7.5:
                hard = True; notes.append("核心一级资本充足率偏低")
            elif latest.core_tier1_ratio >= 9:
                delta += 1; notes.append("核心一级资本充足率较稳健")
    elif policy.key == "INSURANCE":
        if latest.solvency_ratio is not None:
            used = True
            if latest.solvency_ratio < 100:
                hard = True; notes.append("保险偿付能力充足率低于安全门槛")
            elif latest.solvency_ratio < 120:
                delta -= 2; notes.append("保险偿付能力偏紧")
            elif latest.solvency_ratio >= 150:
                delta += 1; notes.append("保险偿付能力较充足")
        if latest.net_investment_return is not None:
            used = True
            if latest.net_investment_return >= 3:
                delta += 1; notes.append("净投资收益率提供正向支持")
    elif policy.key == "BROKER":
        if latest.net_capital is not None:
            used = True
            if latest.net_capital <= 0:
                hard = True; notes.append("券商净资本异常")
            else:
                delta += 1; notes.append("券商净资本为正")
    elif policy.key == "ORDER_DRIVEN_MANUFACTURING":
        change = _pct_change(latest.contract_liabilities, previous.contract_liabilities if previous else None)
        if change is not None:
            used = True
            if change >= 15:
                delta += 2; notes.append("合同负债较上一报告期明显上升，订单/预收端提供支持")
            elif change <= -20:
                delta -= 2; notes.append("合同负债较上一报告期明显下降，订单兑现需谨慎")
        if latest.construction_in_progress is not None or latest.capex is not None:
            used = True
            if (latest.profit_growth or 0) < 0 and latest.fixed_assets and (latest.construction_in_progress or 0) > latest.fixed_assets:
                delta -= 1; notes.append("盈利承压同时在建工程偏重，扩产兑现风险上升")
    elif policy.key in {"GROWTH_TECH", "HEALTHCARE_INNOVATION"}:
        if latest.rd_expense is not None and latest.revenue not in (None, 0):
            used = True
            intensity = latest.rd_expense / latest.revenue
            if intensity >= 0.15:
                delta += 2; notes.append("研发强度较高")
            elif intensity >= 0.08:
                delta += 1; notes.append("研发投入强度提供支持")
            elif policy.key == "GROWTH_TECH" and intensity < 0.02:
                delta -= 1; notes.append("研发强度偏低")
        change = _pct_change(latest.inventory, previous.inventory if previous else None)
        if change is not None and policy.key == "GROWTH_TECH":
            used = True
            if change > 35 and (latest.revenue_growth or 0) < 10:
                delta -= 1; notes.append("库存增速明显快于收入，关注去库存压力")
    elif policy.key == "CONSUMER":
        change = _pct_change(latest.inventory, previous.inventory if previous else None)
        if change is not None:
            used = True
            if change > 25 and (latest.revenue_growth or 0) < 5:
                delta -= 2; notes.append("库存上升而收入偏弱，渠道库存风险增加")
            elif change < 10 and (latest.revenue_growth or 0) >= 5:
                delta += 1; notes.append("库存与收入匹配较健康")
    elif policy.key == "CYCLICAL_RESOURCE":
        change = _pct_change(latest.inventory, previous.inventory if previous else None)
        if change is not None:
            used = True
            if change > 30 and (latest.revenue_growth or 0) < 5:
                delta -= 1; notes.append("周期品库存累积而收入偏弱")
        if latest.capex is not None or latest.construction_in_progress is not None:
            used = True
            if (latest.profit_growth or 0) < 0 and (latest.capex or 0) > 0:
                delta -= 1; notes.append("盈利下行阶段仍有明显资本开支，供给扩张风险需跟踪")
    elif policy.key == "UTILITY":
        if latest.debt_ratio is not None:
            used = True
            if latest.debt_ratio > 80:
                delta -= 2; notes.append("公用事业资产负债率偏高")
            elif latest.debt_ratio < 65:
                delta += 1; notes.append("资产负债率相对稳健")
        used = used or latest.capex is not None or latest.construction_in_progress is not None
    elif policy.key == "PROPERTY_CONSTRUCTION":
        if latest.debt_ratio is not None:
            used = True
            if latest.debt_ratio > 85:
                delta -= 2; notes.append("地产/建筑负债率处于高位")
        change = _pct_change(latest.contract_liabilities, previous.contract_liabilities if previous else None)
        if change is not None:
            used = True
            if change >= 10:
                delta += 1; notes.append("合同负债上升，预收/订单端改善")
            elif change <= -20:
                delta -= 1; notes.append("合同负债下降，订单/回款端需谨慎")
    return max(-3, min(3, delta)), notes, hard, used


def evaluate_prefilter(rows: Iterable[Mapping[str, object]], *, as_of: datetime, pe: float | None, pb: float | None, industry_name: str | None = None) -> FundamentalPrefilter:
    periods = _periods(list(rows), as_of=as_of)
    annual = next((p for p in periods if p.report_date.endswith("12-31")), None)
    interim = next((p for p in periods if p.report_date.endswith("06-30")), None)
    latest = periods[0] if periods else None
    previous = periods[1] if len(periods) > 1 else None
    policy = industry_metric_policy(industry_name)
    reasons: list[str] = []
    evidence: list[str] = []

    if annual is None:
        reasons.append("缺少已披露年报")
    if latest is None:
        reasons.append("缺少已披露财报")
    if annual is None or latest is None:
        return FundamentalPrefilter(False, "E", annual, interim, latest, tuple(reasons), policy.key, policy.valuation_focus, policy.metric_focus, policy.report_focus, True, "MISSING", ())

    financial = policy.key in {"BANK", "INSURANCE", "BROKER"}
    cyclical = policy.key == "CYCLICAL_RESOURCE"
    if not financial and not cyclical and latest.revenue_growth is not None and latest.revenue_growth < -20:
        reasons.append("营收同比明显下滑")
    elif cyclical and latest.revenue_growth is not None and latest.revenue_growth < -20:
        evidence.append("周期行业营收明显下滑，作为周期风险降级而非单项硬否决")
    if not financial and not cyclical and latest.profit_growth is not None and latest.profit_growth < -25:
        reasons.append("净利润同比明显下滑")
    elif cyclical and latest.profit_growth is not None and latest.profit_growth < -25:
        evidence.append("周期行业利润明显下滑，结合库存/资本开支判断而非单项硬否决")
    elif financial and latest.profit_growth is not None and latest.profit_growth < -50:
        reasons.append("金融行业利润同比极端下滑")
    if latest.roe is not None and latest.roe < 3 and not cyclical:
        reasons.append("净资产收益率偏低")
    if latest.eps is not None and latest.eps <= 0 and not financial:
        reasons.append("每股收益非正")
    if latest.gross_margin is not None and latest.gross_margin <= 0 and not financial:
        reasons.append("毛利率异常")

    score = _base_score(latest, policy) - _valuation_penalty(policy, pe=pe, pb=pb, growth=latest.revenue_growth)
    delta, notes, industry_hard, used = _industry_adjustment(policy, latest, previous)
    score += delta
    evidence.extend(notes)
    if policy.key in DETAIL_POLICIES and not used:
        score -= 1
        evidence.append("行业专属三表/主要指标证据不足，按基础财务质量降一级置信度")
    if as_of.month >= 5 and latest.report_date.endswith("12-31") and latest.report_date[:4] < str(as_of.year):
        score -= 1
        reasons.append("最新季度财报暂未取得，按已披露年报降一级置信度")

    hard_names = {"营收同比明显下滑", "净利润同比明显下滑", "每股收益非正", "毛利率异常", "金融行业利润同比极端下滑"}
    hard_fail = industry_hard or any(reason in hard_names for reason in reasons)
    grade = "A" if score >= 7 else "B" if score >= 5 else "C" if score >= 3 else "D" if score >= 1 else "E"
    evidence_required = policy.key in DETAIL_POLICIES
    eligible = not hard_fail and grade in {"A", "B", "C"} and (not evidence_required or used)
    if evidence_required and not used:
        reasons.append("行业专属财务证据缺失，禁止直接新开仓，仅保留结构观察")
    if not eligible and not reasons and not evidence:
        reasons.append("财务质量暂未达到候选阈值")

    if not evidence_required:
        evidence_status = "NOT_REQUIRED"
    elif used and latest.financial_detail_status == "COMPLETE":
        evidence_status = "COMPLETE"
    elif used:
        evidence_status = "PARTIAL"
    else:
        evidence_status = "BASE_ONLY"

    return FundamentalPrefilter(
        eligible=eligible,
        grade=grade,
        annual=annual,
        interim=interim,
        latest=latest,
        reasons=tuple(dict.fromkeys(reasons)),
        industry_policy=policy.key,
        valuation_focus=policy.valuation_focus,
        metric_focus=policy.metric_focus,
        report_focus=policy.report_focus,
        hard_fail=hard_fail,
        industry_evidence_status=evidence_status,
        industry_evidence_reasons=tuple(dict.fromkeys(evidence)),
    )
