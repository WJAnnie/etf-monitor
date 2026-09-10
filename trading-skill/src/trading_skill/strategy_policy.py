from __future__ import annotations

from dataclasses import dataclass

from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.sizing import StopLevel, TrancheRole


@dataclass(frozen=True, slots=True)
class TimeframePolicy:
    timeframe: Timeframe
    chinese_name: str
    role: str
    may_be_primary_entry: bool
    may_standalone_open: bool
    stop_level: StopLevel
    parent_timeframes: tuple[Timeframe, ...]


@dataclass(frozen=True, slots=True)
class ScaleInDecision:
    allowed: bool
    role: TrancheRole | None
    remaining_capacity_fraction: float
    reason: str


# Canonical hierarchy. A timeframe may contribute structure without being allowed to
# create new-entry authority. In particular, DAILY itself is not "standalone": a valid
# daily authority still needs the 120m/30m/5m execution stack before an order may exist.
TIMEFRAME_POLICY: dict[Timeframe, TimeframePolicy] = {
    Timeframe.WEEKLY: TimeframePolicy(
        Timeframe.WEEKLY,
        "周线",
        "战略环境/长期风险边界",
        False,
        False,
        StopLevel.LW,
        (),
    ),
    Timeframe.DAILY: TimeframePolicy(
        Timeframe.DAILY,
        "日线",
        "核心交易方向与新开仓授权",
        True,
        False,
        StopLevel.LD,
        (Timeframe.WEEKLY,),
    ),
    Timeframe.M120: TimeframePolicy(
        Timeframe.M120,
        "120分钟",
        "日线尾部结构确认/核心仓升级条件",
        False,
        False,
        StopLevel.L120,
        (Timeframe.WEEKLY, Timeframe.DAILY),
    ),
    Timeframe.M30: TimeframePolicy(
        Timeframe.M30,
        "30分钟",
        "执行准备/回踩结构细化/确认仓升级条件",
        False,
        False,
        StopLevel.L30,
        (Timeframe.DAILY, Timeframe.M120),
    ),
    Timeframe.M5: TimeframePolicy(
        Timeframe.M5,
        "5分钟",
        "最终执行触发与首笔试仓保护",
        False,
        False,
        StopLevel.L5,
        (Timeframe.M30,),
    ),
}

# New-position authority is intentionally narrow. THIRD_BUY is a continuation/add
# structure, not a substitute for a fresh-core SECOND_BUY permission.
PRIMARY_ENTRY_TIMEFRAMES = (Timeframe.DAILY,)
NEW_ENTRY_AUTHORITY_SIGNALS = frozenset({ChanSignalType.SECOND_BUY})
SCALE_IN_SIGNALS = frozenset({ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY})
EXECUTION_TIMEFRAME = Timeframe.M5

SIGNAL_CN = {
    ChanSignalType.FIRST_BUY: "一买",
    ChanSignalType.SECOND_BUY: "标准二买",
    ChanSignalType.THIRD_BUY: "三买",
    ChanSignalType.FIRST_SELL: "一卖",
    ChanSignalType.SECOND_SELL: "二卖",
    ChanSignalType.THIRD_SELL: "三卖",
    ChanSignalType.STRONG_CLASS2_BUY: "强势类二买",
    ChanSignalType.CENTER_CLASS2_BUY: "中枢类二买",
    ChanSignalType.HIGH_LEVEL_CLASS2_SELL: "高一级类二卖",
}

# Additions consume a fraction of the *remaining risk-approved capacity*, never a
# percentage of account equity. This avoids exceeding symbol/industry/theme/portfolio
# limits after several confirmations. TEST sizing is handled by the entry sizing layer.
SCALE_IN_REMAINING_FRACTION = {
    TrancheRole.TACTICAL: 0.30,      # legacy compatibility only
    TrancheRole.CONFIRMATION: 0.30,  # new independent 30m structure
    TrancheRole.CORE: 0.50,          # new independent 120m structure, promoted to daily-core management
    TrancheRole.TREND_ADD: 0.25,     # new daily continuation structure
}

ROLE_STOP_LEVEL = {
    TrancheRole.TEST: StopLevel.L5,
    TrancheRole.TACTICAL: StopLevel.L30,
    TrancheRole.CONFIRMATION: StopLevel.L30,
    TrancheRole.CORE: StopLevel.LD,
    TrancheRole.TREND_ADD: StopLevel.L120,
}


def primary_entry_timeframes() -> tuple[Timeframe, ...]:
    return PRIMARY_ENTRY_TIMEFRAMES


def parent_timeframes(timeframe: Timeframe) -> tuple[Timeframe, ...]:
    return TIMEFRAME_POLICY[timeframe].parent_timeframes


def management_stop_level(timeframe: Timeframe) -> StopLevel:
    return TIMEFRAME_POLICY[timeframe].stop_level


def tranche_management_stop_level(role: TrancheRole) -> StopLevel:
    return ROLE_STOP_LEVEL[role]


def signal_label(signal) -> str:
    standard = [SIGNAL_CN.get(item, item.value) for item in signal.standard_types]
    extended = [SIGNAL_CN.get(item, item.value) for item in signal.extended_types]
    if extended:
        return f"{'/'.join(standard)}（{'/'.join(extended)}）"
    return "/".join(standard) if standard else "未形成正式买点"


def entry_permission(timeframe: Timeframe, signal_type: ChanSignalType) -> str:
    """Trading meaning only; canonical Chan definitions remain in chan/signals.py."""
    if timeframe is Timeframe.WEEKLY:
        return "周线只定义战略环境和长期风险边界，不单独下单"
    if timeframe is Timeframe.M5:
        return "5分钟只负责最终执行触发和首笔试仓保护，不能独立创造新开仓资格"
    if timeframe is Timeframe.M30:
        return "30分钟只负责执行准备/回踩确认；已有仓位后新的标准二买/三买可申请确认仓，不得独立新开核心仓"
    if timeframe is Timeframe.M120:
        return "120分钟只负责日线结构确认；已有仓位后新的标准二买/三买可申请核心仓升级，不得独立新开仓"
    if timeframe is Timeframe.DAILY and signal_type is ChanSignalType.FIRST_BUY:
        return "日线一买成立但只观察，默认等待标准二买/类二买"
    if timeframe is Timeframe.DAILY and signal_type is ChanSignalType.SECOND_BUY:
        return "日线标准二买是新开仓结构授权；类二买仅作为二买扩展标签，仍需120分钟、30分钟、5分钟执行链"
    if timeframe is Timeframe.DAILY and signal_type is ChanSignalType.THIRD_BUY:
        return "日线三买属于趋势延续/已有仓位加仓结构，不替代新开仓所需的日线二买授权"
    return "观察"


def scale_in_decision(
    *,
    timeframe: Timeframe,
    signal_type: ChanSignalType,
    existing_roles: tuple[TrancheRole, ...] = (),
    new_structure_confirmed: bool,
    opportunity_grade: str,
    risk_level: int,
    context_valid: bool,
    protection_not_loosened: bool,
    current_price_below_cost: bool,
    mechanical_average_down_requested: bool,
) -> ScaleInDecision:
    """Decide whether an existing position may add one new structure-gated tranche.

    Canonical ladder:
    - initial TEST: only after DAILY 2B authority + 120m + 30m + 5m execution stack;
    - new 30m 2B/3B: one CONFIRMATION tranche;
    - new 120m 2B/3B: one CORE tranche, then managed by the DAILY thesis;
    - new DAILY 2B/3B continuation: at most one TREND_ADD tranche.

    A lower price never creates permission. Class-2 labels never create an extra tranche.
    """
    if signal_type not in SCALE_IN_SIGNALS:
        return ScaleInDecision(False, None, 0.0, "只有新的标准二买/三买才能触发已有仓位加仓；一买和类二买标签本身不触发加仓")
    if timeframe not in {Timeframe.DAILY, Timeframe.M120, Timeframe.M30}:
        return ScaleInDecision(False, None, 0.0, "周线只做战略环境，5分钟只做执行确认，均不能独立触发加仓")
    if not new_structure_confirmed:
        return ScaleInDecision(False, None, 0.0, "没有新的确认结构，不加仓；价格更低不能替代结构条件")
    if str(opportunity_grade) not in {"S", "A", "B"}:
        return ScaleInDecision(False, None, 0.0, "机会等级为C，不加仓")
    if int(risk_level) >= 2:
        return ScaleInDecision(False, None, 0.0, "风险达到L2及以上，暂停新增仓位")
    if not context_valid:
        return ScaleInDecision(False, None, 0.0, "行业/基本面/历史/上级结构上下文不完整，不加仓")
    if not protection_not_loosened:
        return ScaleInDecision(False, None, 0.0, "新增仓位需要下移保护位，违反保护位只能上移或保持的规则")
    if mechanical_average_down_requested:
        return ScaleInDecision(False, None, 0.0, "本次依据只是摊低持仓成本而不是新的确认结构，属于机械补仓，禁止加仓")

    roles = set(existing_roles)
    if timeframe is Timeframe.M30 and TrancheRole.CONFIRMATION not in roles:
        role = TrancheRole.CONFIRMATION
        reason = "新的30分钟标准二买/三买确认，可使用剩余风险容量增加一层确认仓"
    elif timeframe is Timeframe.M120 and TrancheRole.CORE not in roles:
        role = TrancheRole.CORE
        reason = "新的120分钟标准二买/三买确认，可将一部分剩余风险容量升级为日线核心仓"
    elif timeframe is Timeframe.DAILY and TrancheRole.TREND_ADD not in roles:
        role = TrancheRole.TREND_ADD
        reason = "新的日线标准二买/三买趋势延续确认，可增加最后一层趋势仓"
    else:
        return ScaleInDecision(False, None, 0.0, "该层确认仓已存在或没有对应的下一层仓位，不继续无限金字塔加仓")

    if current_price_below_cost:
        reason += "；当前价虽低于持仓成本，但本次由新结构触发，不按机械摊低成本处理"
    return ScaleInDecision(True, role, SCALE_IN_REMAINING_FRACTION[role], reason)


def sell_fraction(timeframe: str, sell_class: int, role: TrancheRole) -> float:
    """Single staged-exit matrix. Lower-level sells reduce lower-level risk first."""
    sell_class = max(1, min(3, int(sell_class)))
    if timeframe == "5m":
        table = {
            1: {TrancheRole.TEST: 0.50},
            2: {TrancheRole.TEST: 1.00},
            3: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 0.50},
        }
    elif timeframe == "30m":
        table = {
            1: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 0.50, TrancheRole.CONFIRMATION: 0.50},
            2: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 0.25},
            3: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 0.50},
        }
    elif timeframe == "120m":
        table = {
            1: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 0.50},
            2: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 1.00, TrancheRole.CORE: 0.25},
            3: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 1.00, TrancheRole.CORE: 0.50},
        }
    elif timeframe == "daily":
        table = {
            1: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 1.00, TrancheRole.CORE: 0.25},
            2: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 1.00, TrancheRole.CORE: 0.50},
            3: {item: 1.00 for item in TrancheRole},
        }
    elif timeframe == "weekly":
        table = {
            1: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 1.00, TrancheRole.CORE: 0.50},
            2: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.CONFIRMATION: 1.00, TrancheRole.TREND_ADD: 1.00, TrancheRole.CORE: 0.75},
            3: {item: 1.00 for item in TrancheRole},
        }
    else:
        return 0.0
    return float(table[sell_class].get(role, 0.0))


def stop_break_policy() -> dict[str, str]:
    return {
        "wick_break": "只预警；单根影线跌破不直接清掉更高周期仓位",
        "close_break": "按该笔仓位自己的管理周期执行减仓/退出",
        "failed_reclaim": "跌破后同级别反抽无法站回，确认结构失效，退出受影响仓位",
        "protection": "保护位只能上移或保持，不能因为亏损向下放宽",
    }


def add_position_policy() -> str:
    return (
        "首笔试仓必须先有日线标准二买/类二买授权，再完成120分钟确认、30分钟执行准备和5分钟触发；"
        "已有仓位后，新的30分钟标准二买/三买对应确认仓，新的120分钟标准二买/三买对应核心仓升级，"
        "新的日线二买/三买趋势延续最多再增加一层趋势仓。所有加仓都使用剩余风险容量；L2及以上、机会C、"
        "上下文不完整、保护位需下移或仅为摊低成本的机械补仓一律禁止。"
    )


def take_profit_policy() -> str:
    return (
        "不设固定百分比止盈；按5分钟、30分钟、120分钟、日线、周线的一卖/二卖/三卖逐层减仓，"
        "并只允许在新确认结构出现后上移保护位。"
    )
