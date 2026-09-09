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

# 正式选股主信号：5分钟不能独立创造选股理由；周线只做战略环境。
PRIMARY_ENTRY_TIMEFRAMES = (Timeframe.M120, Timeframe.DAILY, Timeframe.M30)
EXECUTION_TIMEFRAME = Timeframe.M5

# 买点优先级只用于“多个正式信号同时存在时选主逻辑”，不改变缠论定义。
STANDARD_BUY_PRIORITY = {
    ChanSignalType.SECOND_BUY: 50,
    ChanSignalType.THIRD_BUY: 40,
    ChanSignalType.FIRST_BUY: 30,
}
TIMEFRAME_ENTRY_PRIORITY = {
    Timeframe.M120: 30,
    Timeframe.DAILY: 25,
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


def primary_entry_timeframes() -> tuple[Timeframe, ...]:
    return PRIMARY_ENTRY_TIMEFRAMES


def parent_timeframes(timeframe: Timeframe) -> tuple[Timeframe, ...]:
    return TIMEFRAME_POLICY[timeframe].parent_timeframes


def management_stop_level(timeframe: Timeframe) -> StopLevel:
    return TIMEFRAME_POLICY[timeframe].stop_level


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
        return "日线一买成立，但默认等待二买"
    if timeframe is Timeframe.DAILY:
        return "核心机会，可在低级别确认后分批建立核心仓"
    if timeframe is Timeframe.M120:
        if signal_type is ChanSignalType.FIRST_BUY:
            return "120分钟一买可作为小试仓/准备信号，优先等待30分钟或5分钟进一步确认"
        return "主要结构买点，可在5分钟确认后分批执行"
    if timeframe is Timeframe.M30:
        if signal_type is ChanSignalType.FIRST_BUY:
            return "30分钟一买属于反转初期，只允许观察/小试仓，优先等待二买或5分钟确认"
        return "战术买点，必须有日线/120分钟上级结构支持并由5分钟确认"
    return "观察"


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
    return "只有新的同级或更高级确认结构才能加仓；下跌本身不是加仓理由。"


def take_profit_policy() -> str:
    return "不设固定百分比止盈；按5分钟、30分钟、120分钟、日线、周线卖点分层减仓，并随新结构抬高保护位。"
