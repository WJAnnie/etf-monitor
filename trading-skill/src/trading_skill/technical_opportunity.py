from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence

from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.strategy_policy import entry_permission, primary_entry_timeframes


class TechnicalOpportunityState(StrEnum):
    READY = "READY"
    READY_WITH_CAUTION = "READY_WITH_CAUTION"
    WAIT_LOWER_CONFIRMATION = "WAIT_LOWER_CONFIRMATION"
    WAIT_PULLBACK = "WAIT_PULLBACK"
    HISTORY_LIMITED = "HISTORY_LIMITED"
    PARENT_STRUCTURE_BLOCKED = "PARENT_STRUCTURE_BLOCKED"
    PARENT_STRUCTURE_UNRESOLVED = "PARENT_STRUCTURE_UNRESOLVED"
    PARENT_SIGNAL_CONFLICT = "PARENT_SIGNAL_CONFLICT"
    WAIT_STANDARD_SECOND_BUY = "WAIT_STANDARD_SECOND_BUY"
    PREPARE_FIRST_BUY = "PREPARE_FIRST_BUY"
    OBSERVE_FIRST_BUY = "OBSERVE_FIRST_BUY"
    NOT_PRIMARY_TIMEFRAME = "NOT_PRIMARY_TIMEFRAME"
    UNSUPPORTED_SIGNAL = "UNSUPPORTED_SIGNAL"


EXECUTABLE_STATES = {
    TechnicalOpportunityState.READY,
    TechnicalOpportunityState.READY_WITH_CAUTION,
}

# 只表达结构级别，不是综合分。日线结构高于120m，120m高于30m；5m永不进入。
STRUCTURAL_DOMINANCE_ORDER = (
    Timeframe.DAILY,
    Timeframe.M120,
    Timeframe.M30,
)


@dataclass(frozen=True, slots=True)
class TechnicalOpportunity:
    timeframe: Timeframe
    signal_id: str
    signal_type: ChanSignalType | None
    class2_types: tuple[str, ...]
    state: TechnicalOpportunityState
    executable_candidate: bool
    history_eligible: bool
    parent_structure_state: str
    parent_current_sell_conflict: bool
    lower_state: str
    age_completed_bars: int | None
    entry_permission_text: str
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timeframe"] = self.timeframe.value
        payload["signal_type"] = self.signal_type.value if self.signal_type else None
        payload["state"] = self.state.value
        payload["class2_types"] = list(self.class2_types)
        payload["reasons"] = list(self.reasons)
        return payload


def _enum_signal_type(raw_types: Sequence[str]) -> ChanSignalType | None:
    for raw in raw_types:
        try:
            kind = ChanSignalType(str(raw))
        except ValueError:
            continue
        if kind in {
            ChanSignalType.FIRST_BUY,
            ChanSignalType.SECOND_BUY,
            ChanSignalType.THIRD_BUY,
        }:
            return kind
    return None


def evaluate_technical_opportunity(timeframe: Timeframe, context: Mapping[str, Any]) -> TechnicalOpportunity:
    signal = dict(context.get("signal") or {})
    signal_id = str(signal.get("signal_id") or "")
    signal_type = _enum_signal_type(tuple(signal.get("standard_types") or ()))
    class2_types = tuple(str(item) for item in (context.get("class2_types") or ()))
    history_eligible = bool(context.get("history_eligible"))
    parent_context = dict(context.get("structural_parent_context") or {})
    parent_state = str(parent_context.get("state") or "UNRESOLVED")
    parent_sell_conflict = bool(context.get("has_parent_current_sell_conflict"))
    lower_context = dict(context.get("lower_context") or {})
    lower_state = str(lower_context.get("state") or "UNRESOLVED")
    age_bars = signal.get("age_completed_bars")
    try:
        age_bars = int(age_bars) if age_bars is not None else None
    except (TypeError, ValueError):
        age_bars = None

    reasons: list[str] = []
    if timeframe not in primary_entry_timeframes():
        state = TechnicalOpportunityState.NOT_PRIMARY_TIMEFRAME
        reasons.append(f"{timeframe.value}不是主交易买点周期；5分钟只能做执行确认，周线只做战略环境")
    elif signal_type is None:
        state = TechnicalOpportunityState.UNSUPPORTED_SIGNAL
        reasons.append("当前生命周期信号不是一买/标准二买/三买，不能进入技术机会判定")
    elif not history_eligible:
        state = TechnicalOpportunityState.HISTORY_LIMITED
        reasons.append(str(context.get("history_reason") or "当前买点周期历史证据不足"))
    elif parent_sell_conflict:
        state = TechnicalOpportunityState.PARENT_SIGNAL_CONFLICT
        reasons.append("上级周期仍存在当前有效SELL；保留本周期结构观察，但不能视为可执行技术机会")
    elif parent_state == "BLOCKED":
        state = TechnicalOpportunityState.PARENT_STRUCTURE_BLOCKED
        reasons.append("上级结构明确处于空头/向下破坏状态")
    elif parent_state == "UNRESOLVED":
        state = TechnicalOpportunityState.PARENT_STRUCTURE_UNRESOLVED
        reasons.append("上级结构证据缺失或不可验证，不能静默当成顺风环境")
    elif signal_type is ChanSignalType.FIRST_BUY:
        if timeframe is Timeframe.DAILY:
            state = TechnicalOpportunityState.WAIT_STANDARD_SECOND_BUY
            reasons.append("日线一买保留为核心反转事实，但策略默认等待标准二买，不直接形成新开仓技术许可")
        elif timeframe is Timeframe.M120:
            state = TechnicalOpportunityState.PREPARE_FIRST_BUY
            reasons.append("120分钟一买只进入准备/小试仓候选；是否允许实际试仓由后续风险与仓位层决定")
        else:
            state = TechnicalOpportunityState.OBSERVE_FIRST_BUY
            reasons.append("30分钟一买处于反转初期，只观察并等待标准二买/三买")
    elif signal_type in {ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY}:
        if lower_state == "WAITING_PULLBACK":
            state = TechnicalOpportunityState.WAIT_PULLBACK
            reasons.append("主周期标准买点仍有效，但低周期当前有有效SELL/向下结构，等待回撤结束")
        elif lower_state in {"MIXED", "UNRESOLVED"}:
            state = TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION
            reasons.append("主周期标准买点有效，但低周期尚未形成一致的执行确认")
        elif lower_state == "ALIGNED":
            if parent_state == "CAUTION":
                state = TechnicalOpportunityState.READY_WITH_CAUTION
                reasons.append("低周期已同向确认；上级结构为CAUTION而非硬阻断，技术机会成立但必须保留高周期谨慎标签")
            else:
                state = TechnicalOpportunityState.READY
                reasons.append("标准买点、历史证据、上级结构与低周期执行关系均满足技术机会条件")
        else:
            state = TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION
            reasons.append(f"未知低周期关系{lower_state}，按未确认处理")
    else:
        state = TechnicalOpportunityState.UNSUPPORTED_SIGNAL
        reasons.append("当前买点类型不在STEP4D技术机会合同内")

    executable = state in EXECUTABLE_STATES and signal_type in {
        ChanSignalType.SECOND_BUY,
        ChanSignalType.THIRD_BUY,
    }
    permission = entry_permission(timeframe, signal_type) if signal_type else "观察"
    return TechnicalOpportunity(
        timeframe=timeframe,
        signal_id=signal_id,
        signal_type=signal_type,
        class2_types=class2_types,
        state=state,
        executable_candidate=executable,
        history_eligible=history_eligible,
        parent_structure_state=parent_state,
        parent_current_sell_conflict=parent_sell_conflict,
        lower_state=lower_state,
        age_completed_bars=age_bars,
        entry_permission_text=permission,
        reasons=tuple(reasons),
    )


def build_technical_opportunities(current_buy_contexts: Mapping[str, Mapping[str, Any]]) -> tuple[TechnicalOpportunity, ...]:
    opportunities: list[TechnicalOpportunity] = []
    for timeframe in STRUCTURAL_DOMINANCE_ORDER:
        context = current_buy_contexts.get(timeframe.value)
        if context is None:
            continue
        opportunities.append(evaluate_technical_opportunity(timeframe, context))
    return tuple(opportunities)


def dominant_current_buy(opportunities: Sequence[TechnicalOpportunity]) -> TechnicalOpportunity | None:
    """最高结构级别的当前买点；不代表它已经可以执行。"""
    by_timeframe = {item.timeframe: item for item in opportunities}
    for timeframe in STRUCTURAL_DOMINANCE_ORDER:
        if timeframe in by_timeframe:
            return by_timeframe[timeframe]
    return None


def best_executable_candidate(opportunities: Sequence[TechnicalOpportunity]) -> TechnicalOpportunity | None:
    """在已经技术READY的机会中按结构级别选一个；不涉及资金、仓位或最终下单。"""
    executable = {item.timeframe: item for item in opportunities if item.executable_candidate}
    for timeframe in STRUCTURAL_DOMINANCE_ORDER:
        if timeframe in executable:
            return executable[timeframe]
    return None
