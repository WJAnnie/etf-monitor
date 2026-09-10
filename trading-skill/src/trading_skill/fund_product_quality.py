from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping

from trading_skill.industry_prospects import match_theme


class FundProductStatus(StrEnum):
    PASS = "PASS"
    WATCH = "WATCH"
    REJECT = "REJECT"


class UnderlyingAssetState(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    NEUTRAL = "NEUTRAL"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class ProductQuality(StrEnum):
    STRONG = "STRONG"
    ADEQUATE = "ADEQUATE"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class TradingQuality(StrEnum):
    STRONG = "STRONG"
    ADEQUATE = "ADEQUATE"
    WEAK = "WEAK"
    UNKNOWN = "UNKNOWN"


class FundRiskLevel(StrEnum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


class ProductEvidenceCoverage(StrEnum):
    FULL = "FULL"
    PARTIAL = "PARTIAL"
    LIMITED = "LIMITED"


@dataclass(frozen=True, slots=True)
class FundProductAssessment:
    status: FundProductStatus
    underlying_state: UnderlyingAssetState
    product_quality: ProductQuality
    trading_quality: TradingQuality
    risk_level: FundRiskLevel
    evidence_coverage: ProductEvidenceCoverage
    underlying_theme: str | None
    premium_discount_pct: float | None
    positive_evidence: tuple[str, ...]
    warnings: tuple[str, ...]
    hard_risks: tuple[str, ...]
    followups: tuple[str, ...]
    deep_analysis_eligible: bool

    def as_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["underlying_state"] = self.underlying_state.value
        data["product_quality"] = self.product_quality.value
        data["trading_quality"] = self.trading_quality.value
        data["risk_level"] = self.risk_level.value
        data["evidence_coverage"] = self.evidence_coverage.value
        return data


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


# 识别底层产业，不决定它是不是当前“优质行业”。当前支持状态仍由selected_industries决定。
FUND_THEME_ALIASES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("半导体", "芯片"), "半导体设备与材料"),
    (("创新药", "生物医药", "生物科技", "生物制品"), "创新药"),
    (("机器人", "工业母机", "自动化"), "机器人与高端自动化"),
    (("通信", "算力", "人工智能", "AI", "服务器", "光模块", "印制电路板"), "人工智能基础设施"),
    (("电网", "特高压", "储能", "综合电力设备"), "电网升级与储能"),
    (("军工", "航天", "卫星", "航空装备"), "商业航天与军工电子"),
    (("智能驾驶", "汽车电子"), "智能驾驶与汽车电子"),
    (("医疗器械", "医疗设备", "体外诊断"), "医疗器械"),
    (("核电", "氢能", "风电"), "先进能源装备"),
    (("新材料", "碳纤维", "非金属材料"), "新材料"),
    (("工业软件", "网络安全"), "工业软件与网络安全"),
    (("船舶", "海工", "航海装备"), "造船与海工"),
    (("银行", "国有大型银行", "城商行", "农商行"), "银行"),
    (("证券", "券商"), "证券"),
    (("保险",), "保险"),
    (("煤炭", "焦煤", "动力煤", "焦炭"), "煤炭"),
    (("有色", "铜", "铝", "稀土"), "有色"),
    (("农业", "种植", "粮食"), "农业"),
    (("养殖", "畜牧", "生猪"), "养殖"),
    (("白酒", "酒类"), "白酒"),
    (("食品",), "食品"),
    (("黄金股",), "黄金股"),
    (("石油", "油气"), "油气"),
    (("证券保险", "非银金融", "金融"), "金融"),
)


def _fund_theme_name(name: str) -> str | None:
    matched = match_theme(name)
    if matched:
        return matched.name
    text = str(name or "").upper()
    for tokens, theme in FUND_THEME_ALIASES:
        if any(token.upper() in text for token in tokens):
            return theme
    return None


def _selected_theme_names(selected_industries: Iterable[Mapping[str, object]]) -> set[str]:
    names: set[str] = set()
    for item in selected_industries:
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        normalized = _fund_theme_name(name)
        if normalized:
            names.add(normalized)
    return names


def _underlying_state(
    item: Mapping[str, object],
    selected_industries: Iterable[Mapping[str, object]],
) -> tuple[UnderlyingAssetState, str | None, list[str], list[str]]:
    category = str(item.get("fund_category") or "OTHER")
    name = str(item.get("name") or "")
    positives: list[str] = []
    warnings: list[str] = []
    theme = _fund_theme_name(name)

    if category == "EQUITY_BROAD":
        positives.append("宽基底层资产具备分散化基础，不以单一行业景气作为准入门槛")
        return UnderlyingAssetState.SUPPORTIVE, theme, positives, warnings
    if category == "EQUITY_STRATEGY":
        positives.append("策略指数按自身规则评估，不与行业ETF直接混排")
        return UnderlyingAssetState.SUPPORTIVE, theme, positives, warnings
    if category == "EQUITY_SECTOR":
        selected_themes = _selected_theme_names(selected_industries)
        if theme and theme in selected_themes:
            positives.append("底层主题与当前动态重点行业池一致")
            return UnderlyingAssetState.SUPPORTIVE, theme, positives, warnings
        if theme:
            warnings.append("底层主题已识别，但当前未进入动态重点行业池；产品可观察，不把行业质量伪装成PASS")
            return UnderlyingAssetState.NEUTRAL, theme, positives, warnings
        warnings.append("行业/主题ETF尚未可靠解析到底层产业主题")
        return UnderlyingAssetState.UNKNOWN, None, positives, warnings
    if category in {"CROSS_BORDER", "COMMODITY", "BOND"}:
        warnings.append("该资产类别需要宏观/底层资产专属上下文，不使用A股行业质量替代")
        return UnderlyingAssetState.NEUTRAL, theme, positives, warnings
    if category == "CASH":
        positives.append("现金类产品主要承担流动性管理功能")
        return UnderlyingAssetState.NEUTRAL, theme, positives, warnings
    warnings.append("底层资产类别信息有限")
    return UnderlyingAssetState.UNKNOWN, theme, positives, warnings


def _trading_quality(item: Mapping[str, object]) -> tuple[TradingQuality, list[str], list[str]]:
    positives: list[str] = []
    warnings: list[str] = []
    percentile = _num(item.get("fund_liquidity_percentile"))
    amount = _num(item.get("amount"))

    if percentile is not None:
        if percentile >= 70:
            positives.append("同类成交活跃度处于前30%，交易承载能力较好")
            return TradingQuality.STRONG, positives, warnings
        if percentile >= 35:
            positives.append("同类成交活跃度处于可接受区间")
            return TradingQuality.ADEQUATE, positives, warnings
        warnings.append("同类成交活跃度偏低，滑点和冲击成本风险较高")
        return TradingQuality.WEAK, positives, warnings
    if amount is None:
        warnings.append("缺少成交额和同类流动性分位，无法确认场内交易质量")
        return TradingQuality.UNKNOWN, positives, warnings
    if amount >= 100_000_000:
        positives.append("成交额较高，交易承载能力较好")
        return TradingQuality.STRONG, positives, warnings
    if amount >= 10_000_000:
        positives.append("成交额达到可接受水平")
        return TradingQuality.ADEQUATE, positives, warnings
    warnings.append("成交额偏低，滑点和冲击成本风险较高")
    return TradingQuality.WEAK, positives, warnings


def _product_quality(
    item: Mapping[str, object],
    reference: Mapping[str, object],
    trading: TradingQuality,
) -> tuple[ProductQuality, list[str], list[str], list[str]]:
    """Explicit product rules; no synthetic weighted product score."""
    positives: list[str] = []
    warnings: list[str] = []
    hard: list[str] = []
    fund_family = str(item.get("fund_family") or "").strip()
    size = _num(reference.get("fund_size_cny"))
    management_fee = _num(reference.get("management_fee_pct"))
    custody_fee = _num(reference.get("custody_fee_pct"))
    tracking_error = _num(reference.get("tracking_error_pct"))

    if fund_family:
        positives.append("第二步已按同指数/同主题家族去重，并优先保留更高流动性的代表产品")

    adverse = False
    favorable_reference_count = 0
    reference_seen = 0

    if trading is TradingQuality.WEAK:
        adverse = True
    if size is not None:
        reference_seen += 1
        if size < 10_000_000:
            hard.append("基金规模极小，清盘/流动性风险不可忽略")
        elif size < 50_000_000:
            warnings.append("基金规模偏小，需关注持续规模与清盘风险")
            adverse = True
        elif size >= 1_000_000_000:
            positives.append("基金规模较大")
            favorable_reference_count += 1

    if management_fee is not None or custody_fee is not None:
        reference_seen += 1
        total_fee = (management_fee or 0.0) + (custody_fee or 0.0)
        if total_fee <= 0.35:
            positives.append("管理费+托管费较低")
            favorable_reference_count += 1
        elif total_fee >= 1.2:
            warnings.append("管理费+托管费偏高，长期持有成本需要比较")
            adverse = True

    if tracking_error is not None:
        reference_seen += 1
        if tracking_error <= 1.0:
            positives.append("跟踪误差较低")
            favorable_reference_count += 1
        elif tracking_error >= 3.0:
            warnings.append("跟踪误差偏高")
            adverse = True

    if hard:
        return ProductQuality.WEAK, positives, warnings, hard
    if adverse:
        return ProductQuality.WEAK, positives, warnings, hard
    if trading is TradingQuality.STRONG and favorable_reference_count >= 2:
        return ProductQuality.STRONG, positives, warnings, hard
    if fund_family and trading in {TradingQuality.STRONG, TradingQuality.ADEQUATE}:
        return ProductQuality.ADEQUATE, positives, warnings, hard
    if reference_seen >= 2 and trading in {TradingQuality.STRONG, TradingQuality.ADEQUATE}:
        return ProductQuality.ADEQUATE, positives, warnings, hard
    return ProductQuality.UNKNOWN, positives, warnings, hard


def assess_fund_product(
    item: Mapping[str, object],
    *,
    selected_industries: Iterable[Mapping[str, object]] = (),
    reference: Mapping[str, object] | None = None,
) -> FundProductAssessment:
    reference = reference or {}
    category = str(item.get("fund_category") or "OTHER")
    security_type = str(item.get("security_type") or "")
    position_stage = str(item.get("position_stage") or "UNKNOWN")
    risk_tags = {str(tag) for tag in (item.get("fund_risk_tags") or ())}
    premium = _num(reference.get("premium_discount_pct"))
    premium_fresh = bool(reference.get("premium_is_fresh"))
    asset_context_complete = bool(reference.get("asset_context_complete"))
    fund_size_known = _num(reference.get("fund_size_cny")) is not None

    underlying, theme, positives, warnings = _underlying_state(item, selected_industries)
    trading, trade_pos, trade_warn = _trading_quality(item)
    positives.extend(trade_pos)
    warnings.extend(trade_warn)
    product, product_pos, product_warn, hard = _product_quality(item, reference, trading)
    positives.extend(product_pos)
    warnings.extend(product_warn)

    if not fund_size_known:
        warnings.append("缺少基金当前规模证据，无法排除小规模/清盘风险，只能WATCH")
    if position_stage == "OVERHEATED":
        warnings.append("底层资产/产品近期位置过热，属于时点风险而非产品质量永久否决")

    special_premium_check = (
        category == "CROSS_BORDER"
        or security_type == "LOF"
        or "CROSS_BORDER_QDII" in risk_tags
        or "LOF_PREMIUM" in risk_tags
    )
    if premium is not None and premium_fresh:
        if abs(premium) >= 10:
            warnings.append("新鲜折溢价证据显示偏离净值超过10%，暂不进入直接交易候选")
        elif abs(premium) >= 5:
            warnings.append("折溢价偏高，交易前必须再次确认")
        else:
            positives.append("折溢价处于可接受范围")
    elif special_premium_check:
        warnings.append("跨境/QDII或LOF缺少足够新鲜的折溢价证据，只能WATCH")

    external_asset_context_required = category in {"CROSS_BORDER", "COMMODITY", "BOND"} or "CROSS_BORDER_QDII" in risk_tags
    if external_asset_context_required and not asset_context_complete:
        warnings.append("底层资产专属上下文尚未完成，只能WATCH但可保留技术观察")

    sector_context_not_supportive = category == "EQUITY_SECTOR" and underlying is not UnderlyingAssetState.SUPPORTIVE
    if sector_context_not_supportive:
        warnings.append("行业ETF当前没有得到动态重点行业池确认，不能仅凭产品质量直接PASS")

    if category == "CASH":
        warnings.append("现金类产品不需要进入多周期缠论深扫，除非任务目标是现金管理")

    reference_fields = sum(
        reference.get(key) not in (None, "", "-")
        for key in ("fund_size_cny", "management_fee_pct", "custody_fee_pct", "tracking_error_pct")
    )
    if reference_fields >= 3 and (not special_premium_check or (premium is not None and premium_fresh)):
        coverage = ProductEvidenceCoverage.FULL
    elif fund_size_known and str(item.get("fund_family") or "").strip():
        coverage = ProductEvidenceCoverage.PARTIAL
    else:
        coverage = ProductEvidenceCoverage.LIMITED

    high_premium = premium is not None and premium_fresh and abs(premium) >= 10
    missing_premium = special_premium_check and not (premium is not None and premium_fresh)
    missing_asset_context = external_asset_context_required and not asset_context_complete

    if hard:
        risk = FundRiskLevel.HIGH
        status = FundProductStatus.REJECT
    else:
        medium_risk = (
            not fund_size_known
            or high_premium
            or missing_premium
            or missing_asset_context
            or sector_context_not_supportive
            or trading in {TradingQuality.WEAK, TradingQuality.UNKNOWN}
            or product in {ProductQuality.WEAK, ProductQuality.UNKNOWN}
            or underlying in {UnderlyingAssetState.WEAK, UnderlyingAssetState.UNKNOWN}
        )
        risk = FundRiskLevel.HIGH if high_premium else FundRiskLevel.MEDIUM if medium_risk or warnings else FundRiskLevel.LOW
        if (
            not fund_size_known
            or high_premium
            or missing_premium
            or missing_asset_context
            or sector_context_not_supportive
            or trading in {TradingQuality.WEAK, TradingQuality.UNKNOWN}
            or product in {ProductQuality.WEAK, ProductQuality.UNKNOWN}
            or underlying is UnderlyingAssetState.UNKNOWN
        ):
            status = FundProductStatus.WATCH
        else:
            status = FundProductStatus.PASS

    followups: list[str] = []
    if not fund_size_known:
        followups.append("补充基金规模用于清盘/承载能力复核")
    if reference.get("management_fee_pct") is None or reference.get("custody_fee_pct") is None:
        followups.append("补充管理费和托管费用于同类长期成本比较")
    if reference.get("tracking_error_pct") is None and category in {"EQUITY_BROAD", "EQUITY_SECTOR", "EQUITY_STRATEGY", "BOND"}:
        followups.append("补充跟踪误差/跟踪偏离度用于指数产品质量比较")
    if special_premium_check and not (premium is not None and premium_fresh):
        followups.append("交易前获取新鲜IOPV/估算净值或可靠NAV口径重新计算折溢价")
    if missing_asset_context:
        followups.append("第四步前补充对应宏观/底层资产专属上下文")
    if sector_context_not_supportive:
        followups.append("等待该行业进入动态重点行业池或补充更强的行业景气证据")

    positives = list(dict.fromkeys(positives))
    warnings = list(dict.fromkeys(warnings))
    hard = list(dict.fromkeys(hard))
    followups = list(dict.fromkeys(followups))
    deep_eligible = status is not FundProductStatus.REJECT and category != "CASH"

    return FundProductAssessment(
        status=status,
        underlying_state=underlying,
        product_quality=product,
        trading_quality=trading,
        risk_level=risk,
        evidence_coverage=coverage,
        underlying_theme=theme,
        premium_discount_pct=premium if premium_fresh else None,
        positive_evidence=tuple(positives),
        warnings=tuple(warnings),
        hard_risks=tuple(hard),
        followups=tuple(followups),
        deep_analysis_eligible=deep_eligible,
    )
