from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping

from trading_skill.fundamental_quality import CYCLICAL_PROFILES, FINANCIAL_PROFILES, ORDER_DRIVEN_PROFILES, RND_TOLERANT_PROFILES


class EvidenceFamily(StrEnum):
    FINANCIAL = "FINANCIAL"
    ORDER_MANUFACTURING = "ORDER_MANUFACTURING"
    RND = "RND"
    CYCLICAL = "CYCLICAL"
    CONSUMER_CASHFLOW = "CONSUMER_CASHFLOW"
    GENERAL = "GENERAL"


class SpecializedQuality(StrEnum):
    STRONG = "STRONG"
    ADEQUATE = "ADEQUATE"
    WEAK = "WEAK"
    INSUFFICIENT = "INSUFFICIENT"


class SpecializedCoverage(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    LIMITED = "LIMITED"


@dataclass(frozen=True, slots=True)
class SpecializedEvidenceAssessment:
    profile: str
    family: EvidenceFamily
    quality: SpecializedQuality
    coverage: SpecializedCoverage
    positive_evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    hard_risks: tuple[str, ...]
    missing_evidence: tuple[str, ...]
    metrics: dict[str, float | None]
    can_upgrade_watch: bool

    def as_dict(self) -> dict:
        data = asdict(self)
        data["family"] = self.family.value
        data["quality"] = self.quality.value
        data["coverage"] = self.coverage.value
        return data


CONSUMER_PROFILES = {
    "食品饮料/白酒",
    "家电/消费电子",
    "零售/专业连锁",
    "医疗器械",
    "影视院线/传媒",
}


def evidence_family(profile: str) -> EvidenceFamily:
    if profile in FINANCIAL_PROFILES:
        return EvidenceFamily.FINANCIAL
    if profile in RND_TOLERANT_PROFILES:
        return EvidenceFamily.RND
    if profile in CYCLICAL_PROFILES:
        return EvidenceFamily.CYCLICAL
    if profile in ORDER_DRIVEN_PROFILES:
        return EvidenceFamily.ORDER_MANUFACTURING
    if profile in CONSUMER_PROFILES:
        return EvidenceFamily.CONSUMER_CASHFLOW
    return EvidenceFamily.GENERAL


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _latest_main_row(rows: Iterable[Mapping[str, object]]) -> Mapping[str, object]:
    material = [row for row in rows if isinstance(row, Mapping)]
    if not material:
        return {}
    material.sort(key=lambda row: str(row.get("REPORT_DATE") or row.get("REPORTDATE") or ""), reverse=True)
    return material[0]


def _coverage(known: int, *, full: int = 4, partial: int = 2) -> SpecializedCoverage:
    if known >= full:
        return SpecializedCoverage.FULL
    if known >= partial:
        return SpecializedCoverage.PARTIAL
    return SpecializedCoverage.LIMITED


def _quality(positives: int, warnings: int, hard: int, coverage: SpecializedCoverage) -> SpecializedQuality:
    if hard:
        return SpecializedQuality.WEAK
    if coverage is SpecializedCoverage.LIMITED:
        return SpecializedQuality.INSUFFICIENT
    if positives >= 3 and warnings == 0:
        return SpecializedQuality.STRONG
    if positives >= 1 and warnings <= 1:
        return SpecializedQuality.ADEQUATE
    return SpecializedQuality.WEAK


def _financial_evidence(profile: str, rows: Iterable[Mapping[str, object]]) -> SpecializedEvidenceAssessment:
    row = _latest_main_row(rows)
    positives: list[str] = []
    warnings: list[str] = []
    hard: list[str] = []
    missing: list[str] = []
    metrics: dict[str, float | None] = {}

    def metric(key: str) -> float | None:
        value = _num(row.get(key))
        metrics[key] = value
        return value

    if profile == "银行":
        npl = metric("BLDKBBL")
        core_tier1 = metric("HXYJBCZL")
        capital = metric("NEWCAPITALADER")
        roe = metric("ROEJQ")
        if npl is None:
            missing.append("不良贷款率")
        elif npl >= 4:
            hard.append("不良贷款率处于高风险区间")
        elif npl > 2:
            warnings.append("不良贷款率偏高")
        elif npl <= 1.5:
            positives.append("不良贷款率较低")
        if core_tier1 is None:
            missing.append("核心一级资本充足率")
        elif core_tier1 < 6:
            hard.append("核心一级资本充足率过低")
        elif core_tier1 < 8:
            warnings.append("核心一级资本充足率偏低")
        elif core_tier1 >= 9:
            positives.append("核心一级资本充足率较充足")
        if capital is not None:
            if capital < 10:
                warnings.append("资本充足率偏低")
            elif capital >= 12:
                positives.append("资本充足率较充足")
        else:
            missing.append("资本充足率")
        if roe is not None:
            if roe >= 10:
                positives.append("银行ROE较好")
            elif roe < 5:
                warnings.append("银行ROE偏低")
        else:
            missing.append("银行ROE")
        # MAINFINADATA未稳定提供净息差，不能拿其他指标冒充。
        missing.append("净息差及其趋势")

    elif profile == "保险":
        solvency = metric("SOLVENCY_AR")
        nbv = metric("NBV_LIFE")
        nbv_rate = metric("NBV_RATE")
        total_roi = metric("TOTAL_ROI")
        roe = metric("ROEJQ")
        if solvency is None:
            missing.append("偿付能力充足率")
        elif solvency < 100:
            hard.append("偿付能力充足率过低")
        elif solvency < 120:
            warnings.append("偿付能力充足率偏低")
        elif solvency >= 150:
            positives.append("偿付能力较充足")
        if nbv is None:
            missing.append("新业务价值NBV")
        elif nbv > 0:
            positives.append("新业务价值为正")
        if nbv_rate is None:
            missing.append("新业务价值率")
        elif nbv_rate > 0:
            positives.append("新业务价值率为正")
        if total_roi is None:
            missing.append("总投资收益率")
        elif total_roi >= 3:
            positives.append("投资收益率具备支撑")
        elif total_roi < 1:
            warnings.append("投资收益率偏低")
        if roe is not None and roe >= 8:
            positives.append("保险ROE具备支撑")

    else:  # 券商/资产管理
        net_capital = metric("JZB")
        net_assets = metric("JZC")
        capital_ratio = metric("JZBJZC")
        roe = metric("ROEJQ")
        if net_capital is None:
            missing.append("净资本")
        elif net_capital <= 0:
            hard.append("净资本非正")
        else:
            positives.append("净资本为正")
        if net_assets is None:
            missing.append("净资产")
        elif net_assets <= 0:
            hard.append("净资产非正")
        else:
            positives.append("净资产为正")
        if capital_ratio is None:
            missing.append("净资本/净资产")
        elif capital_ratio <= 0:
            warnings.append("净资本/净资产异常")
        else:
            positives.append("净资本/净资产为正")
        if roe is not None:
            if roe >= 8:
                positives.append("券商ROE具备支撑")
            elif roe < 3:
                warnings.append("券商ROE偏低")
        else:
            missing.append("券商ROE")
        missing.append("成交活跃度/两融/投行/资管与自营结构")

    known = sum(value is not None for value in metrics.values())
    coverage = _coverage(known)
    quality = _quality(len(positives), len(warnings), len(hard), coverage)
    can_upgrade = coverage is not SpecializedCoverage.LIMITED and quality in {SpecializedQuality.STRONG, SpecializedQuality.ADEQUATE} and not hard
    return SpecializedEvidenceAssessment(
        profile=profile,
        family=EvidenceFamily.FINANCIAL,
        quality=quality,
        coverage=coverage,
        positive_evidence=tuple(dict.fromkeys(positives)),
        warnings=tuple(dict.fromkeys(warnings)),
        hard_risks=tuple(dict.fromkeys(hard)),
        missing_evidence=tuple(dict.fromkeys(missing)),
        metrics=metrics,
        can_upgrade_watch=can_upgrade,
    )


def _statement_evidence(profile: str, detailed: Mapping[str, object] | None) -> SpecializedEvidenceAssessment:
    family = evidence_family(profile)
    current = detailed.get("current") if isinstance(detailed, Mapping) else None
    changes = detailed.get("changes") if isinstance(detailed, Mapping) else None
    current = current if isinstance(current, Mapping) else {}
    changes = changes if isinstance(changes, Mapping) else {}

    positives: list[str] = []
    warnings: list[str] = []
    hard: list[str] = []
    missing: list[str] = []
    metrics: dict[str, float | None] = {}

    def cur(key: str) -> float | None:
        value = _num(current.get(key))
        metrics[key] = value
        return value

    def chg(key: str) -> float | None:
        value = _num(changes.get(key))
        metrics[key] = value
        return value

    debt = cur("debt_asset_ratio_pct")
    ocf = cur("operating_cash_flow")
    receivables = chg("accounts_receivable_change_pct")
    inventory = chg("inventory_change_pct")
    contract = chg("contract_liabilities_change_pct")
    cash = chg("monetary_funds_change_pct")
    capex = chg("construct_long_asset_cash_change_pct")
    cip = chg("construction_in_progress_change_pct")

    if debt is None:
        missing.append("资产负债率")
    elif debt >= 90 and profile not in {"公用事业/电力", "地产/建筑重资产"}:
        hard.append("资产负债率极高")
    elif debt >= 80 and profile not in {"公用事业/电力", "地产/建筑重资产"}:
        warnings.append("资产负债率偏高")
    elif debt < 60:
        positives.append("资产负债率相对稳健")

    if ocf is None:
        missing.append("经营现金流")
    elif ocf > 0:
        positives.append("经营现金流为正")
    else:
        warnings.append("经营现金流为负")

    if family is EvidenceFamily.ORDER_MANUFACTURING:
        if contract is None:
            missing.append("合同负债变化")
        elif contract >= 20:
            positives.append("合同负债明显增长，订单景气存在辅助证据")
        elif contract <= -30:
            warnings.append("合同负债明显下降")
        if receivables is not None and receivables >= 50:
            warnings.append("应收账款快速增长")
        if inventory is not None and inventory >= 60:
            warnings.append("存货快速增长，需核对备货还是滞销")
        if capex is not None or cip is not None:
            positives.append("已取得资本开支/在建工程变化，可用于核对扩产阶段")
        else:
            missing.append("资本开支/在建工程变化")
        missing.append("真实手持订单/中标/客户验证或交付数据")

    elif family is EvidenceFamily.RND:
        cash_ratio = cur("cash_to_assets_pct")
        if cash_ratio is None:
            missing.append("现金储备占比")
        elif cash_ratio >= 15:
            positives.append("现金储备占比较高")
        elif cash_ratio < 5:
            warnings.append("现金储备占比较低")
        if cash is not None and cash <= -35:
            warnings.append("货币资金明显下降")
        missing.append("研发管线/BD授权/核心产品商业化或订阅续费等非报表证据")

    elif family is EvidenceFamily.CYCLICAL:
        if inventory is not None and inventory >= 60:
            warnings.append("周期行业库存快速增加")
        if capex is not None and capex >= 40:
            warnings.append("周期行业资本开支快速扩张，需警惕供给周期")
        if cip is not None:
            positives.append("已取得在建工程变化，可核对产能周期")
        missing.append("商品价格/运价/产量/成本曲线/开工率等周期核心数据")

    elif family is EvidenceFamily.CONSUMER_CASHFLOW:
        if inventory is None:
            missing.append("库存变化")
        elif inventory >= 50:
            warnings.append("库存快速增长")
        else:
            positives.append("库存未出现极端扩张")
        if receivables is not None and receivables >= 50:
            warnings.append("应收账款快速增长")
        if profile == "食品饮料/白酒":
            if contract is None:
                missing.append("合同负债变化")
            elif contract >= 10:
                positives.append("合同负债增长")
        missing.append("同店/动销/渠道库存/销量等经营数据")

    else:
        if receivables is not None and receivables >= 50:
            warnings.append("应收账款快速增长")
        if inventory is not None and inventory >= 60:
            warnings.append("存货快速增长")

    known = sum(value is not None for value in metrics.values())
    coverage = _coverage(known, full=5, partial=3)
    quality = _quality(len(positives), len(warnings), len(hard), coverage)

    # 研发和周期行业的关键证据天然存在于报表之外；不能仅凭会计代理指标把WATCH升级成PASS。
    externally_dependent = family in {EvidenceFamily.RND, EvidenceFamily.CYCLICAL}
    can_upgrade = (
        not externally_dependent
        and coverage is not SpecializedCoverage.LIMITED
        and quality in {SpecializedQuality.STRONG, SpecializedQuality.ADEQUATE}
        and not hard
        and len(positives) >= 2
    )
    return SpecializedEvidenceAssessment(
        profile=profile,
        family=family,
        quality=quality,
        coverage=coverage,
        positive_evidence=tuple(dict.fromkeys(positives)),
        warnings=tuple(dict.fromkeys(warnings)),
        hard_risks=tuple(dict.fromkeys(hard)),
        missing_evidence=tuple(dict.fromkeys(missing)),
        metrics=metrics,
        can_upgrade_watch=can_upgrade,
    )


def assess_specialized_evidence(
    profile: str,
    *,
    main_financial_rows: Iterable[Mapping[str, object]] = (),
    detailed_metrics: Mapping[str, object] | None = None,
) -> SpecializedEvidenceAssessment:
    if profile in FINANCIAL_PROFILES:
        return _financial_evidence(profile, main_financial_rows)
    return _statement_evidence(profile, detailed_metrics)
