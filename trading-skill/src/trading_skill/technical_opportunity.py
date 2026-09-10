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
    OBSERVE_FIRST_BUY = "OBSERVE_FIRST_BUY"
    CONTINUATION_ONLY = "CONTINUATION_ONLY"
    NOT_PRIMARY_TIMEFRAME = "NOT_PRIMARY_TIMEFRAME"
    UNSUPPORTED_SIGNAL = "UNSUPPORTED_SIGNAL"


EXECUTABLE_STATES = {
    TechnicalOpportunityState.READY,
    TechnicalOpportunityState.READY_WITH_CAUTION,
}

# Structural reporting order only. M120/M30 are still shown so the audit/report can
# explain what they are doing, but only DAILY may become a new-entry authority.
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
    if signal_type is None:
        state = TechnicalOpportunityState.UNSUPPORTED_SIGNAL
        reasons.append("当前生命周期信号不是一买/标准二买/三买，不能进入技术机会判定")
    elif timeframe not in primary_entry_timeframes():
        state = TechnicalOpportunityState.NOT_PRIMARY_TIMEFRAME
        reasons.append(
            f"{timeframe.value}只承担确认/执行或加仓结构职责，不能独立创造新开仓主买点"
        )
    elif not history_eligible:
        state = TechnicalOpportunityState.HISTORY_LIMITED
        reasons.append(str(context.get("history_reason") or "日线授权周期历史证据不足"))
    elif parent_sell_conflict:
        state = TechnicalOpportunityState.PARENT_SIGNAL_CONFLICT
        reasons.append("周线仍存在当前有效SELL；保留日线结构观察，但不能视为可执行新开仓机会")
    elif parent_state == "BLOCKED":
        state = TechnicalOpportunityState.PARENT_STRUCTURE_BLOCKED
        reasons.append("周线战略环境明确处于空头/向下破坏状态")
    elif parent_state == "UNRESOLVED":
        state = TechnicalOpportunityState.PARENT_STRUCTURE_UNRESOLVED
        reasons.append("周线战略环境证据缺失或不可验证，不能静默当成顺风环境")
    elif signal_type is ChanSignalType.FIRST_BUY:
        state = TechnicalOpportunityState.WAIT_STANDARD_SECOND_BUY
        reasons.append("日线一买仅记录反转事实，默认等待标准二买；不得借120m/30m/5m信号提前开仓")
    elif signal_type is ChanSignalType.THIRD_BUY:
        state = TechnicalOpportunityState.CONTINUATION_ONLY
        reasons.append("日线三买用于已有仓位的趋势延续/加仓管理，不替代新开仓所需的日线二买授权")
    elif signal_type is ChanSignalType.SECOND_BUY:
        if lower_state == "WAITING_PULLBACK":
            state = TechnicalOpportunityState.WAIT_PULLBACK
            reasons.append("日线二买仍有效，但低周期当前存在有效SELL/向下结构，等待执行回撤结束")
        elif lower_state in {"MIXED", "UNRESOLVED"}:
            state = TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION
            reasons.append("日线二买有效，但120分钟→30分钟→5分钟执行链尚未形成一致确认")
        elif lower_state == "ALIGNED":
            if parent_state == "CAUTION":
                state = TechnicalOpportunityState.READY_WITH_CAUTION
                reasons.append("日线二买授权与低周期执行链均成立；周线为CAUTION，因此保留高周期谨慎标签")
            else:
                state = TechnicalOpportunityState.READY
                reasons.append("日线标准二买/类二买授权、周线环境和120m→30m→5m执行链均满足")
        else:
            state = TechnicalOpportunityState.WAIT_LOWER_CONFIRMATION
            reasons.append(f"未知低周期关系{lower_state}，按未确认处理")
    else:
        state = TechnicalOpportunityState.UNSUPPORTED_SIGNAL
        reasons.append("当前买点类型不在STEP4D新开仓合同内")

    executable = (
        timeframe is Timeframe.DAILY
        and signal_type is ChanSignalType.SECOND_BUY
        and state in EXECUTABLE_STATES
    )
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
    """Highest structural current buy for reporting; not equivalent to entry permission."""
    by_timeframe = {item.timeframe: item for item in opportunities}
    for timeframe in STRUCTURAL_DOMINANCE_ORDER:
        if timeframe in by_timeframe:
            return by_timeframe[timeframe]
    return None


def best_executable_candidate(opportunities: Sequence[TechnicalOpportunity]) -> TechnicalOpportunity | None:
    """Return the DAILY second-buy authority only after the nested execution chain is READY."""
    for item in opportunities:
        if item.timeframe is Timeframe.DAILY and item.executable_candidate:
            return item
    return None
