from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from trading_skill.domain.enums import Timeframe
from trading_skill.strategy_policy import parent_timeframes


class StructureBias(StrEnum):
    BULLISH = "BULLISH"
    NEUTRAL = "NEUTRAL"
    BEARISH = "BEARISH"
    UNRESOLVED = "UNRESOLVED"


class StructurePhase(StrEnum):
    BULL_TREND = "BULL_TREND"
    BULL_PULLBACK = "BULL_PULLBACK"
    CONSOLIDATION = "CONSOLIDATION"
    BEAR_REBOUND = "BEAR_REBOUND"
    BEAR_TREND = "BEAR_TREND"
    REVERSAL_UP_FORMING = "REVERSAL_UP_FORMING"
    REVERSAL_DOWN_FORMING = "REVERSAL_DOWN_FORMING"
    BREAKOUT_UP = "BREAKOUT_UP"
    BREAKDOWN_DOWN = "BREAKDOWN_DOWN"
    UNRESOLVED = "UNRESOLVED"


class ParentContextState(StrEnum):
    SUPPORTIVE = "SUPPORTIVE"
    PERMISSIVE = "PERMISSIVE"
    CAUTION = "CAUTION"
    BLOCKED = "BLOCKED"
    UNRESOLVED = "UNRESOLVED"


class LowerTimeframeState(StrEnum):
    ALIGNED = "ALIGNED"
    MIXED = "MIXED"
    WAITING_PULLBACK = "WAITING_PULLBACK"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class SignalSnapshot:
    """4A事实快照；4B不判断fresh/stale/active/expired。"""

    side: str | None
    signal_type: str | None
    confirmation_timestamp: datetime | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "side": self.side,
            "signal_type": self.signal_type,
            "confirmation_timestamp": self.confirmation_timestamp.isoformat() if self.confirmation_timestamp else None,
        }


@dataclass(frozen=True, slots=True)
class TimeframeStructureSnapshot:
    timeframe: Timeframe
    status: str
    bias: StructureBias
    phase: StructurePhase
    trend_classification: str | None
    trend_state: str | None
    center_state: str | None
    divergence_type: str | None
    divergence_state: str | None
    latest_signal: SignalSnapshot
    latest_close: float | None
    completed_bars: int
    issues: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timeframe"] = self.timeframe.value
        payload["bias"] = self.bias.value
        payload["phase"] = self.phase.value
        payload["latest_signal"] = self.latest_signal.to_dict()
        payload["issues"] = list(self.issues)
        return payload


@dataclass(frozen=True, slots=True)
class ParentContext:
    primary_timeframe: Timeframe
    state: ParentContextState
    allows_opportunity: bool
    allows_immediate_entry: bool
    parent_states: tuple[tuple[Timeframe, ParentContextState], ...]
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_timeframe": self.primary_timeframe.value,
            "state": self.state.value,
            "allows_opportunity": self.allows_opportunity,
            "allows_immediate_entry": self.allows_immediate_entry,
            "parent_states": {tf.value: state.value for tf, state in self.parent_states},
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class LowerTimeframeContext:
    primary_timeframe: Timeframe
    state: LowerTimeframeState
    child_states: tuple[tuple[Timeframe, str], ...]
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_timeframe": self.primary_timeframe.value,
            "state": self.state.value,
            "child_states": {tf.value: state for tf, state in self.child_states},
            "reasons": list(self.reasons),
        }


@dataclass(frozen=True, slots=True)
class MultiTimeframeStructureBook:
    snapshots: tuple[TimeframeStructureSnapshot, ...]

    def by_timeframe(self) -> dict[Timeframe, TimeframeStructureSnapshot]:
        return {item.timeframe: item for item in self.snapshots}

    def to_dict(self) -> dict[str, Any]:
        return {item.timeframe.value: item.to_dict() for item in self.snapshots}


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _enum_value(value: Any) -> Any:
    return getattr(value, "value", value)


def _parse_time(value: Any, *, reference: datetime | None = None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        result = value
    else:
        try:
            result = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if reference is not None and reference.tzinfo is not None and result.tzinfo is None:
        result = result.replace(tzinfo=reference.tzinfo)
    elif reference is not None and reference.tzinfo is not None and result.tzinfo is not None:
        result = result.astimezone(reference.tzinfo)
    return result


def _signal_standard_types(signal: Any) -> tuple[str, ...]:
    raw = _get(signal, "standard_types")
    if raw is None:
        raw = _get(signal, "types", ())
    return tuple(str(_enum_value(item)) for item in (raw or ()))


def _signal_side(signal: Any) -> str | None:
    raw = _get(signal, "side")
    return str(_enum_value(raw)) if raw else None


def _signal_confirmation(signal: Any, *, reference: datetime | None = None) -> datetime | None:
    return _parse_time(_get(signal, "confirmation_timestamp"), reference=reference)


def _result_timeframe(result: Any, fallback: Timeframe | None = None) -> Timeframe:
    raw = _get(result, "timeframe", fallback)
    if isinstance(raw, Timeframe):
        return raw
    return Timeframe(str(_enum_value(raw)))


def _trend_fields(result: Any) -> tuple[str | None, str | None]:
    if isinstance(result, Mapping):
        trend = result.get("trend") or {}
        classification = trend.get("classification") if isinstance(trend, Mapping) else None
        state = trend.get("state") if isinstance(trend, Mapping) else None
        return classification, state
    return _get(result, "trend_classification"), _get(result, "trend_state")


def _divergence_fields(result: Any) -> tuple[str | None, str | None]:
    if isinstance(result, Mapping):
        divergence = result.get("divergence") or {}
        kind = divergence.get("type") if isinstance(divergence, Mapping) else None
        state = divergence.get("state") if isinstance(divergence, Mapping) else None
        return kind, state
    return _get(result, "divergence_type"), _get(result, "divergence_state")


def _latest_center_state(result: Any, *, as_of: datetime) -> str | None:
    centers = list(_get(result, "centers", ()) or ())
    ordered: list[tuple[datetime, str]] = []
    for center in centers:
        when = _parse_time(
            _get(center, "confirmation_timestamp", _get(center, "confirmation")),
            reference=as_of,
        )
        state = _enum_value(_get(center, "state"))
        if when is not None and when <= as_of and state:
            ordered.append((when, str(state)))
    if not ordered:
        return None
    ordered.sort(key=lambda item: item[0])
    return ordered[-1][1]


def _latest_signal(result: Any, *, as_of: datetime) -> SignalSnapshot:
    signals = list(_get(result, "signals", ()) or ())
    ordered: list[tuple[datetime, Any]] = []
    for signal in signals:
        when = _signal_confirmation(signal, reference=as_of)
        if when is not None and when <= as_of:
            ordered.append((when, signal))
    if not ordered:
        return SignalSnapshot(None, None, None)
    ordered.sort(key=lambda item: (item[0], str(_get(item[1], "id", ""))))
    when, signal = ordered[-1]
    types = _signal_standard_types(signal)
    return SignalSnapshot(_signal_side(signal), types[0] if types else None, when)


def _classify_phase(
    *,
    trend: str | None,
    center_state: str | None,
    divergence_type: str | None,
    divergence_state: str | None,
) -> tuple[StructureBias, StructurePhase]:
    """只用当前结构事实分类；买卖点新鲜度/生命周期由4C负责。"""
    trend = str(trend or "")
    center_state = str(center_state or "")
    div_type = str(divergence_type or "")
    div_state = str(divergence_state or "")
    divergence_active = div_state in {"FORMING", "CONFIRMED"}
    bottom_divergence = divergence_active and "BOTTOM" in div_type
    top_divergence = divergence_active and "TOP" in div_type

    if trend == "UPTREND":
        if top_divergence:
            return StructureBias.BULLISH, StructurePhase.REVERSAL_DOWN_FORMING
        return StructureBias.BULLISH, StructurePhase.BULL_TREND

    if trend == "DOWNTREND":
        if bottom_divergence:
            return StructureBias.BEARISH, StructurePhase.REVERSAL_UP_FORMING
        return StructureBias.BEARISH, StructurePhase.BEAR_TREND

    if trend == "CONSOLIDATION":
        if center_state == "LEAVING_UP":
            return StructureBias.NEUTRAL, StructurePhase.BREAKOUT_UP
        if center_state == "LEAVING_DOWN":
            return StructureBias.NEUTRAL, StructurePhase.BREAKDOWN_DOWN
        return StructureBias.NEUTRAL, StructurePhase.CONSOLIDATION

    # 趋势尚未达到可分类门槛时，只保留中枢生命周期给出的局部突破事实。
    if center_state == "LEAVING_UP":
        return StructureBias.UNRESOLVED, StructurePhase.BREAKOUT_UP
    if center_state == "LEAVING_DOWN":
        return StructureBias.UNRESOLVED, StructurePhase.BREAKDOWN_DOWN
    return StructureBias.UNRESOLVED, StructurePhase.UNRESOLVED


def snapshot_timeframe(result: Any, *, as_of: datetime, fallback: Timeframe | None = None) -> TimeframeStructureSnapshot:
    timeframe = _result_timeframe(result, fallback)
    trend, trend_state = _trend_fields(result)
    divergence_type, divergence_state = _divergence_fields(result)
    center_state = _latest_center_state(result, as_of=as_of)
    latest_signal = _latest_signal(result, as_of=as_of)
    bias, phase = _classify_phase(
        trend=trend,
        center_state=center_state,
        divergence_type=divergence_type,
        divergence_state=divergence_state,
    )
    issues = tuple(str(item) for item in (_get(result, "issues", ()) or ()))
    completed_bars = int(_get(result, "completed_bars", 0) or 0)
    latest_close = _get(result, "latest_close")
    return TimeframeStructureSnapshot(
        timeframe=timeframe,
        status=str(_get(result, "status", "UNAVAILABLE") or "UNAVAILABLE"),
        bias=bias,
        phase=phase,
        trend_classification=str(trend) if trend else None,
        trend_state=str(trend_state) if trend_state else None,
        center_state=center_state,
        divergence_type=str(divergence_type) if divergence_type else None,
        divergence_state=str(divergence_state) if divergence_state else None,
        latest_signal=latest_signal,
        latest_close=float(latest_close) if latest_close is not None else None,
        completed_bars=completed_bars,
        issues=issues,
    )


def build_structure_book(results: Mapping[Any, Any], *, as_of: datetime) -> MultiTimeframeStructureBook:
    snapshots: list[TimeframeStructureSnapshot] = []
    for timeframe in (Timeframe.WEEKLY, Timeframe.DAILY, Timeframe.M120, Timeframe.M30, Timeframe.M5):
        result = results.get(timeframe)
        if result is None:
            result = results.get(timeframe.value)
        if result is None:
            continue
        snapshots.append(snapshot_timeframe(result, as_of=as_of, fallback=timeframe))
    return MultiTimeframeStructureBook(tuple(snapshots))


def _parent_state(snapshot: TimeframeStructureSnapshot) -> tuple[ParentContextState, str]:
    if snapshot.status not in {"OK", "UNRESOLVED"}:
        return ParentContextState.UNRESOLVED, f"{snapshot.timeframe.value}分析不可用"

    if snapshot.phase in {StructurePhase.BEAR_TREND, StructurePhase.BREAKDOWN_DOWN}:
        return ParentContextState.BLOCKED, f"{snapshot.timeframe.value}当前仍处明确空头/向下破坏结构"

    if snapshot.phase in {StructurePhase.BULL_PULLBACK, StructurePhase.REVERSAL_DOWN_FORMING}:
        return ParentContextState.CAUTION, f"{snapshot.timeframe.value}高周期仍偏多但正在调整/顶背驰演化"

    if snapshot.phase in {
        StructurePhase.CONSOLIDATION,
        StructurePhase.BEAR_REBOUND,
        StructurePhase.REVERSAL_UP_FORMING,
        StructurePhase.BREAKOUT_UP,
    }:
        return ParentContextState.PERMISSIVE, f"{snapshot.timeframe.value}不构成硬阻断，但尚非完整顺风趋势"

    if snapshot.phase is StructurePhase.BULL_TREND:
        return ParentContextState.SUPPORTIVE, f"{snapshot.timeframe.value}高周期顺势支持"

    # UNRESOLVED不等于数据坏；趋势尚未成型时保持谨慎，但不凭空否决低一级真实买点。
    return ParentContextState.CAUTION, f"{snapshot.timeframe.value}趋势尚未完全分类"


def evaluate_parent_context(
    results: Mapping[Any, Any], *, primary_timeframe: Timeframe, as_of: datetime
) -> ParentContext:
    """4B只评估上级结构环境；上级当前买卖点冲突由4C叠加。"""
    book = build_structure_book(results, as_of=as_of).by_timeframe()
    parent_states: list[tuple[Timeframe, ParentContextState]] = []
    reasons: list[str] = []

    for timeframe in parent_timeframes(primary_timeframe):
        snapshot = book.get(timeframe)
        if snapshot is None:
            parent_states.append((timeframe, ParentContextState.UNRESOLVED))
            reasons.append(f"{timeframe.value}缺少结构结果")
            continue
        state, reason = _parent_state(snapshot)
        parent_states.append((timeframe, state))
        reasons.append(reason)

    if not parent_states:
        return ParentContext(primary_timeframe, ParentContextState.SUPPORTIVE, True, True, (), ())

    states = {state for _, state in parent_states}
    if ParentContextState.BLOCKED in states:
        overall = ParentContextState.BLOCKED
        allows_opportunity = False
        allows_immediate = False
    elif ParentContextState.UNRESOLVED in states:
        overall = ParentContextState.UNRESOLVED
        allows_opportunity = False
        allows_immediate = False
    elif ParentContextState.CAUTION in states:
        overall = ParentContextState.CAUTION
        allows_opportunity = True
        allows_immediate = False
    elif ParentContextState.PERMISSIVE in states:
        overall = ParentContextState.PERMISSIVE
        allows_opportunity = True
        allows_immediate = True
    else:
        overall = ParentContextState.SUPPORTIVE
        allows_opportunity = True
        allows_immediate = True

    return ParentContext(
        primary_timeframe=primary_timeframe,
        state=overall,
        allows_opportunity=allows_opportunity,
        allows_immediate_entry=allows_immediate,
        parent_states=tuple(parent_states),
        reasons=tuple(reasons),
    )


_CHILDREN: dict[Timeframe, tuple[Timeframe, ...]] = {
    Timeframe.DAILY: (Timeframe.M120, Timeframe.M30, Timeframe.M5),
    Timeframe.M120: (Timeframe.M30, Timeframe.M5),
    Timeframe.M30: (Timeframe.M5,),
}


def _latest_signal_after(result: Any, *, after: datetime, as_of: datetime) -> tuple[str | None, str | None]:
    ordered: list[tuple[datetime, Any]] = []
    for signal in list(_get(result, "signals", ()) or ()):
        when = _signal_confirmation(signal, reference=as_of)
        if when is not None and after < when <= as_of:
            ordered.append((when, signal))
    if not ordered:
        return None, None
    ordered.sort(key=lambda item: (item[0], str(_get(item[1], "id", ""))))
    _, signal = ordered[-1]
    types = _signal_standard_types(signal)
    return _signal_side(signal), types[0] if types else None


def evaluate_lower_context(
    results: Mapping[Any, Any], *, primary_timeframe: Timeframe, primary_confirmation: datetime, as_of: datetime
) -> LowerTimeframeContext:
    """低周期只描述主买点之后的执行关系；不会反向重写主周期买点生命周期。"""
    book = build_structure_book(results, as_of=as_of).by_timeframe()
    child_states: list[tuple[Timeframe, str]] = []
    reasons: list[str] = []
    saw_pullback = False
    saw_unresolved = False
    saw_aligned = False
    saw_neutral = False

    for timeframe in _CHILDREN.get(primary_timeframe, ()):
        result = results.get(timeframe)
        if result is None:
            result = results.get(timeframe.value)
        snapshot = book.get(timeframe)
        if result is None or snapshot is None or snapshot.status not in {"OK", "UNRESOLVED"}:
            child_states.append((timeframe, "UNRESOLVED"))
            reasons.append(f"{timeframe.value}缺少可用结构证据")
            saw_unresolved = True
            continue

        side, signal_type = _latest_signal_after(result, after=primary_confirmation, as_of=as_of)
        if side == "SELL":
            child_states.append((timeframe, f"PULLBACK:{signal_type or 'SELL'}"))
            reasons.append(f"{timeframe.value}主买点后出现新的正式卖点，只表示低周期回撤/执行等待，不否定主周期买点")
            saw_pullback = True
            continue
        if side == "BUY":
            if snapshot.phase in {StructurePhase.BULL_TREND, StructurePhase.BREAKOUT_UP}:
                child_states.append((timeframe, f"ALIGNED:{signal_type or 'BUY'}"))
                saw_aligned = True
            else:
                child_states.append((timeframe, f"MIXED:BUY_IN_{snapshot.phase.value}"))
                reasons.append(
                    f"{timeframe.value}虽出现新BUY，但当前结构仍为{snapshot.phase.value}；只能视为转折尝试，不能单独完成执行确认"
                )
                saw_neutral = True
            continue

        if snapshot.phase in {
            StructurePhase.BEAR_TREND,
            StructurePhase.BREAKDOWN_DOWN,
            StructurePhase.BULL_PULLBACK,
            StructurePhase.REVERSAL_DOWN_FORMING,
        }:
            child_states.append((timeframe, f"PULLBACK:{snapshot.phase.value}"))
            reasons.append(f"{timeframe.value}当前处调整/偏空阶段，主周期机会保留但执行需要等待")
            saw_pullback = True
        elif snapshot.phase in {StructurePhase.BULL_TREND, StructurePhase.BREAKOUT_UP}:
            child_states.append((timeframe, f"ALIGNED:{snapshot.phase.value}"))
            saw_aligned = True
        else:
            child_states.append((timeframe, f"NEUTRAL:{snapshot.phase.value}"))
            saw_neutral = True

    if saw_pullback:
        state = LowerTimeframeState.WAITING_PULLBACK
    elif saw_unresolved:
        state = LowerTimeframeState.UNRESOLVED
    elif saw_neutral:
        state = LowerTimeframeState.MIXED
    else:
        state = LowerTimeframeState.ALIGNED

    return LowerTimeframeContext(
        primary_timeframe=primary_timeframe,
        state=state,
        child_states=tuple(child_states),
        reasons=tuple(reasons),
    )
