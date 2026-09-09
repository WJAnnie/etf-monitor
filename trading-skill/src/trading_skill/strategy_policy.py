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
    freshness_days: float
    parent_timeframes: tuple[Timeframe, ...]


@dataclass(frozen=True, slots=True)
class ScaleInDecision:
    allowed: bool
    role: TrancheRole | None
    remaining_capacity_fraction: float
    reason: str


TIMEFRAME_POLICY: dict[Timeframe, TimeframePolicy] = {
    Timeframe.WEEKLY: TimeframePolicy(
        Timeframe.WEEKLY, "周线", "战略环境/长期仓位边界", False, False, StopLevel.LW, 60.0, ()
    ),
    Timeframe.DAILY: TimeframePolicy(
        Timeframe.DAILY, "日线", "中期核心结构", True, True, StopLevel.LD, 30.0, (Timeframe.WEEKLY,)
    ),
    Timeframe.M120: TimeframePolicy(
        Timeframe.M120, "120分钟", "主要中短线结构/首要买点周期", True, True, StopLevel.L120, 15.0,
        (Timeframe.WEEKLY, Timeframe.DAILY),
    ),
    Timeframe.M30: TimeframePolicy(
        Timeframe.M30, "30分钟", "战术结构/主要执行买点周期", True, True, StopLevel.L30, 5.0,
        (Timeframe.DAILY, Timeframe.M120),
    ),
    Timeframe.M5: TimeframePolicy(
        Timeframe.M5, "5分钟", "精细入场与短线风险确认", False, False, StopLevel.L5, 4.0 / 24.0,
        (Timeframe.M30,),
    ),
}

PRIMARY_ENTRY_TIMEFRAMES = (Timeframe.DAILY, Timeframe.M120, Timeframe.M30)
EXECUTION_TIMEFRAME = Timeframe.M5

ENTRY_PRIORITY_MATRIX: dict[tuple[Timeframe, ChanSignalType], int] = {
    (Timeframe.DAILY, ChanSignalType.SECOND_BUY): 100,
    (Timeframe.DAILY, ChanSignalType.THIRD_BUY): 95,
    (Timeframe.M120, ChanSignalType.SECOND_BUY): 90,
    (Timeframe.M120, ChanSignalType.THIRD_BUY): 85,
    (Timeframe.M30, ChanSignalType.SECOND_BUY): 80,
    (Timeframe.M30, ChanSignalType.THIRD_BUY): 75,
    (Timeframe.M120, ChanSignalType.FIRST_BUY): 60,
    (Timeframe.DAILY, ChanSignalType.FIRST_BUY): 50,
    (Timeframe.M30, ChanSignalType.FIRST_BUY): 40,
}

STANDARD_BUY_PRIORITY = {
    ChanSignalType.SECOND_BUY: 50,
    ChanSignalType.THIRD_BUY: 40,
    ChanSignalType.FIRST_BUY: 30,
}
TIMEFRAME_ENTRY_PRIORITY = {
    Timeframe.DAILY: 30,
    Timeframe.M120: 25,
    Timeframe.M30: 20,
}

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

# 加仓不是固定金额，而是使用“风险模型算出的剩余允许仓位”的一部分。
# 这样不会因为多次结构确认突破股票/行业/主题/总组合风险上限。
SCALE_IN_REMAINING_FRACTION = {
    TrancheRole.TACTICAL: 0.30,
    TrancheRole.CONFIRMATION: 0.40,
    TrancheRole.CORE: 0.50,
    TrancheRole.TREND_ADD: 0.25,
}


def primary_entry_timeframes() -> tuple[Timeframe, ...]:
    return PRIMARY_ENTRY_TIMEFRAMES


def parent_timeframes(timeframe: Timeframe) -> tuple[Timeframe, ...]:
    return TIMEFRAME_POLICY[timeframe].parent_timeframes


def management_stop_level(timeframe: Timeframe) -> StopLevel:
    return TIMEFRAME_POLICY[timeframe].stop_level


def entry_priority(timeframe: Timeframe, signal_type: ChanSignalType, extended_types=()) -> int:
    """统一决定哪个正式买点成为本轮主交易逻辑；类二买只作为同级二买的加分标签。"""
    score = ENTRY_PRIORITY_MATRIX.get((timeframe, signal_type), 0)
    extended = set(extended_types or ())
    if signal_type is ChanSignalType.SECOND_BUY:
        if ChanSignalType.STRONG_CLASS2_BUY in extended:
            score += 3
        if ChanSignalType.CENTER_CLASS2_BUY in extended:
            score += 2
    return score


def signal_label(signal) -> str:
    standard = [SIGNAL_CN.get(item, item.value) for item in signal.standard_types]
    extended = [SIGNAL_CN.get(item, item.value) for item in signal.extended_types]
    if extended:
        return f"{'/'.join(standard)}（{'/'.join(extended)}）"
    return "/".join(standard) if standard else "未形成正式买点"


def entry_permission(timeframe: Timeframe, signal_type: ChanSignalType) -> str:
    """统一交易含义；它不重新定义缠论信号，只决定信号怎么用于交易。"""
    if timeframe is Timeframe.WEEKLY:
        return "战略环境，不单独下单"
    if timeframe is Timeframe.M5:
        return "仅执行确认，不单独开仓"
    if timeframe is Timeframe.DAILY and signal_type is ChanSignalType.FIRST_BUY:
        return "日线一买成立，但默认等待标准二买"
    if timeframe is Timeframe.DAILY:
        return "核心机会，可在低级别执行条件满足后分批建立核心仓"
    if timeframe is Timeframe.M120:
        if signal_type is ChanSignalType.FIRST_BUY:
            return "120分钟一买只作为准备/小试仓信号，优先等待30分钟或5分钟执行条件进一步确认"
        return "主要结构买点，可在5分钟执行条件满足后分批执行"
    if timeframe is Timeframe.M30:
        if signal_type is ChanSignalType.FIRST_BUY:
            return "30分钟一买属于反转初期，只观察，不直接新开仓；优先等待标准二买/三买"
        return "战术买点，必须有日线/120分钟上级结构支持并满足5分钟执行条件"
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
    """统一决定已有仓位后是否允许增加下一笔。

    关键约束：
    - 一买与5分钟信号都不能作为已有仓位的加仓触发；只接受新的标准二买/三买。
    - 类二买只是标准二买的扩展标签，不独立增加一笔。
    - 必须有新结构、机会至少B、风险低于L2、上下文完整、保护位不下移。
    - “价格低于成本”本身不是禁加仓条件；真正禁止的是没有新结构支撑、仅为了摊低成本的机械补仓。
    - 每类确认仓只建立一次；之后最多允许一层TREND_ADD，避免无限金字塔。
    """
    if signal_type not in {ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY}:
        return ScaleInDecision(False, None, 0.0, "只有新的标准二买/三买才能触发已有仓位加仓；一买和类二买标签本身不触发加仓")
    if timeframe not in {Timeframe.DAILY, Timeframe.M120, Timeframe.M30}:
        return ScaleInDecision(False, None, 0.0, "周线只做战略环境，5分钟只做执行确认，均不能独立触发加仓")
    if not new_structure_confirmed:
        return ScaleInDecision(False, None, 0.0, "没有新的确认结构，不加仓；价格更低也不能替代结构条件")
    if str(opportunity_grade) not in {"S", "A", "B"}:
        return ScaleInDecision(False, None, 0.0, "机会等级为C，不加仓")
    if int(risk_level) >= 2:
        return ScaleInDecision(False, None, 0.0, "风险达到L2及以上，暂停新增仓位")
    if not context_valid:
        return ScaleInDecision(False, None, 0.0, "行业/基本面/历史/上级结构上下文不完整，不加仓")
    if not protection_not_loosened:
        return ScaleInDecision(False, None, 0.0, "新增仓位需要下移保护位，违反保护位只能上移或保持的规则")
    if mechanical_average_down_requested:
        return ScaleInDecision(False, None, 0.0, "本次动作的依据只是摊低持仓成本而不是新的确认结构，属于机械补仓，禁止加仓")

    roles = set(existing_roles)
    if timeframe is Timeframe.M30 and TrancheRole.TACTICAL not in roles:
        role = TrancheRole.TACTICAL
        reason = "新的30分钟标准二买/三买确认，可增加一层战术仓"
    elif timeframe is Timeframe.M120 and TrancheRole.CONFIRMATION not in roles:
        role = TrancheRole.CONFIRMATION
        reason = "新的120分钟标准二买/三买确认，可增加一层确认仓"
    elif timeframe is Timeframe.DAILY and TrancheRole.CORE not in roles:
        role = TrancheRole.CORE
        reason = "新的日线标准二买/三买确认，可增加一层核心仓"
    elif TrancheRole.TREND_ADD not in roles:
        role = TrancheRole.TREND_ADD
        reason = "对应层级仓位已建立，新结构再次确认且保护位未放宽，最多增加一层趋势仓"
    else:
        return ScaleInDecision(False, None, 0.0, "对应确认仓和趋势加仓都已存在，不继续无限金字塔加仓")

    if current_price_below_cost:
        reason += "；当前价虽低于持仓成本，但本次由新的同级/更高级结构触发，不按机械摊低成本处理"
    return ScaleInDecision(True, role, SCALE_IN_REMAINING_FRACTION[role], reason)


def sell_fraction(timeframe: str, sell_class: int, role: TrancheRole) -> float:
    """唯一分级卖出表。低级别卖点不得无条件推翻高一级别核心逻辑。"""
    sell_class = max(1, min(3, int(sell_class)))
    if timeframe == "5m":
        table = {
            1: {TrancheRole.TEST: 0.50},
            2: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 0.50},
            3: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00},
        }
    elif timeframe == "30m":
        table = {
            1: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 0.50},
            2: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.TREND_ADD: 0.50},
            3: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 0.50,
            },
        }
    elif timeframe == "120m":
        table = {
            1: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 1.00, TrancheRole.TREND_ADD: 0.50},
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 0.50,
            },
            3: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
            },
        }
    elif timeframe == "daily":
        table = {
            1: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 0.50,
            },
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.CORE: 0.50,
            },
            3: {item: 1.00 for item in TrancheRole},
        }
    elif timeframe == "weekly":
        table = {
            1: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.CORE: 0.50,
            },
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.CORE: 0.75,
            },
            3: {item: 1.00 for item in TrancheRole},
        }
    else:
        return 0.0
    return float(table[sell_class].get(role, 0.0))


def stop_break_policy() -> dict[str, str]:
    return {
        "wick_break": "只预警；单根影线跌破不直接清掉更高周期仓位",
        "close_break": "按该笔仓位的管理周期执行减仓/退出",
        "failed_reclaim": "跌破后反抽无法站回，确认结构失效，退出受影响仓位",
        "protection": "保护位只能上移或保持，不能因为亏损向下放宽",
    }


def add_position_policy() -> str:
    return (
        "已有仓位后只接受新的标准二买/三买或同级以上结构升级；30分钟对应战术仓、120分钟对应确认仓、"
        "日线对应核心仓，之后最多再加一层趋势仓。加仓金额按剩余允许仓位计算；L2及以上、机会C、"
        "上下文不完整、保护位需下移或仅为了摊低成本的机械补仓一律不加。价格低于持仓成本不是单独否决项；"
        "若新的同级/更高级结构重新确认且全部风险门通过，仍可按结构加仓。类二买只作为标准二买加分标签。"
    )


def take_profit_policy() -> str:
    return "不设固定百分比止盈；按5分钟、30分钟、120分钟、日线、周线卖点分层减仓，并随新结构抬高保护位。"
