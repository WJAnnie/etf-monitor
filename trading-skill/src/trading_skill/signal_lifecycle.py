from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping, Sequence

from trading_skill.domain.enums import ChanSignalType, SignalState, Timeframe
from trading_skill.multi_timeframe_structure import StructurePhase, build_structure_book
from trading_skill.strategy_policy import STANDARD_BUY_PRIORITY


class SignalLifecycleStage(StrEnum):
    FORMING = "FORMING"
    CONFIRMED = "CONFIRMED"
    ACTIVE = "ACTIVE"
    MATURE = "MATURE"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    UNRESOLVED = "UNRESOLVED"


class SignalFreshness(StrEnum):
    FRESH = "FRESH"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"


class LowerTimeframeState(StrEnum):
    ALIGNED = "ALIGNED"
    MIXED = "MIXED"
    WAITING_PULLBACK = "WAITING_PULLBACK"
    UNRESOLVED = "UNRESOLVED"


# “仍适合作为新的当前机会”的观察窗，不是4A缠论定义，也不是结构止损。
# 用完成K线根数而不是自然日/小时，避免周末、节假日、停牌让相同结构拥有不同寿命。
# 大致对应：周线8周、日线20个交易日、120m约10个交易日、30m约3个交易日、5m约1个交易日。
SIGNAL_ENTRY_WINDOW_BARS: dict[Timeframe, int] = {
    Timeframe.WEEKLY: 8,
    Timeframe.DAILY: 20,
    Timeframe.M120: 20,
    Timeframe.M30: 24,
    Timeframe.M5: 48,
}


@dataclass(frozen=True, slots=True)
class SignalLifecycleRecord:
    signal_id: str
    timeframe: Timeframe
    side: str
    standard_types: tuple[str, ...]
    extended_types: tuple[str, ...]
    level_rank: int
    structural_timestamp: datetime | None
    confirmation_timestamp: datetime | None
    stage: SignalLifecycleStage
    freshness: SignalFreshness
    age_completed_bars: int | None
    entry_window_bars: int
    structurally_valid: bool
    lifecycle_eligible_as_current: bool
    superseded_by_signal_id: str | None = None
    invalidated_by_signal_id: str | None = None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["timeframe"] = self.timeframe.value
        payload["stage"] = self.stage.value
        payload["freshness"] = self.freshness.value
        payload["structural_timestamp"] = self.structural_timestamp.isoformat() if self.structural_timestamp else None
        payload["confirmation_timestamp"] = self.confirmation_timestamp.isoformat() if self.confirmation_timestamp else None
        payload["reasons"] = list(self.reasons)
        return payload


@dataclass(frozen=True, slots=True)
class TimeframeSignalLifecycle:
    timeframe: Timeframe
    records: tuple[SignalLifecycleRecord, ...]

    def current_buy(self) -> SignalLifecycleRecord | None:
        return current_signal(self.records, side="BUY")

    def current_sell(self) -> SignalLifecycleRecord | None:
        return current_signal(self.records, side="SELL")

    def to_dict(self) -> dict[str, Any]:
        current_buy = self.current_buy()
        current_sell = self.current_sell()
        return {
            "timeframe": self.timeframe.value,
            "records": [record.to_dict() for record in self.records],
            "current_buy": current_buy.to_dict() if current_buy else None,
            "current_sell": current_sell.to_dict() if current_sell else None,
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
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value))
        except ValueError:
            return None
    if reference is not None and reference.tzinfo is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference.tzinfo)
    elif reference is not None and reference.tzinfo is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone(reference.tzinfo)
    return parsed


def _timeframe(result: Any, fallback: Timeframe | None = None) -> Timeframe:
    raw = _get(result, "timeframe", fallback)
    if isinstance(raw, Timeframe):
        return raw
    return Timeframe(str(_enum_value(raw)))


def _types(signal: Any, key: str, fallback: str | None = None) -> tuple[str, ...]:
    raw = _get(signal, key)
    if raw is None and fallback:
        raw = _get(signal, fallback, ())
    return tuple(str(_enum_value(item)) for item in (raw or ()))


def _signal_state(signal: Any) -> str:
    raw = _get(signal, "state")
    return str(_enum_value(raw or SignalState.CONFIRMED))


def _signal_side(signal: Any) -> str:
    return str(_enum_value(_get(signal, "side", "")))


def _signal_level(signal: Any) -> int:
    try:
        return int(_get(signal, "level_rank", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _signal_id(signal: Any) -> str:
    return str(_get(signal, "id", ""))


def _is_formal_confirmed(signal: Any, *, as_of: datetime) -> bool:
    state = _signal_state(signal)
    when = _parse_time(_get(signal, "confirmation_timestamp"), reference=as_of)
    return state == SignalState.CONFIRMED.value and when is not None and when <= as_of


def _same_level(a: Any, b: Any) -> bool:
    return _signal_level(a) == _signal_level(b)


def _completed_bar_age(
    confirmation: datetime | None,
    completed_bar_timestamps: Sequence[datetime] | None,
    *,
    as_of: datetime,
) -> int | None:
    if confirmation is None or completed_bar_timestamps is None:
        return None
    normalized: set[datetime] = set()
    for value in completed_bar_timestamps:
        parsed = _parse_time(value, reference=as_of)
        if parsed is not None and parsed <= as_of:
            normalized.add(parsed)
    return sum(1 for timestamp in normalized if timestamp > confirmation)


def _freshness(timeframe: Timeframe, age_completed_bars: int | None) -> SignalFreshness:
    if age_completed_bars is None:
        return SignalFreshness.UNKNOWN
    return (
        SignalFreshness.FRESH
        if age_completed_bars <= SIGNAL_ENTRY_WINDOW_BARS[timeframe]
        else SignalFreshness.STALE
    )


def evaluate_signal_lifecycle(
    result: Any,
    *,
    as_of: datetime,
    completed_bar_timestamps: Sequence[datetime] | None = None,
    latest_completed_bar_timestamp: datetime | None = None,
    fallback_timeframe: Timeframe | None = None,
) -> TimeframeSignalLifecycle:
    """管理4A正式买卖点的生命周期，不重写4A canonical定义。

    EXPIRED：完成K根数超过当前机会观察窗，但结构没有被反向信号证伪。
    INVALIDATED：之后出现同周期、同级别反向正式信号，或4A显式标记失效。
    MATURE：之后出现同周期、同级别更新的同向正式信号，旧入口让位于新结构。
    """
    timeframe = _timeframe(result, fallback_timeframe)
    status = str(_get(result, "status", "UNAVAILABLE") or "UNAVAILABLE")
    signals = list(_get(result, "signals", ()) or ())
    floor = datetime.min.replace(tzinfo=as_of.tzinfo)
    ordered = sorted(
        signals,
        key=lambda signal: (
            _parse_time(_get(signal, "confirmation_timestamp"), reference=as_of) or floor,
            _signal_id(signal),
        ),
    )
    records: list[SignalLifecycleRecord] = []

    for signal in ordered:
        signal_id = _signal_id(signal)
        side = _signal_side(signal)
        standard_types = _types(signal, "standard_types", "types")
        extended_types = _types(signal, "extended_types")
        level_rank = _signal_level(signal)
        structural_timestamp = _parse_time(_get(signal, "structural_timestamp"), reference=as_of)
        confirmation = _parse_time(_get(signal, "confirmation_timestamp"), reference=as_of)
        age_bars = _completed_bar_age(confirmation, completed_bar_timestamps, as_of=as_of)
        freshness = _freshness(timeframe, age_bars)
        source_state = _signal_state(signal)
        reasons: list[str] = []
        superseded_by: str | None = None
        invalidated_by: str | None = None

        if status not in {"OK", "UNRESOLVED"}:
            stage = SignalLifecycleStage.UNRESOLVED
            reasons.append(f"{timeframe.value}结构结果状态为{status}，生命周期不可验证")
        elif confirmation is None or confirmation > as_of:
            stage = SignalLifecycleStage.UNRESOLVED
            reasons.append("买卖点确认时间缺失或晚于分析时点")
        elif source_state in {SignalState.FORMING.value, SignalState.CANDIDATE.value}:
            stage = SignalLifecycleStage.FORMING
            reasons.append("结构仍在形成/候选阶段，尚未成为正式买卖点")
        elif source_state == SignalState.INVALIDATED.value:
            stage = SignalLifecycleStage.INVALIDATED
            reasons.append("4A结构引擎已明确标记该信号失效")
        else:
            simultaneous_opposites = [
                other for other in ordered
                if other is not signal
                and _same_level(signal, other)
                and _signal_side(other)
                and _signal_side(other) != side
                and _parse_time(_get(other, "confirmation_timestamp"), reference=as_of) == confirmation
                and _is_formal_confirmed(other, as_of=as_of)
            ]
            if simultaneous_opposites:
                stage = SignalLifecycleStage.UNRESOLVED
                invalidated_by = _signal_id(simultaneous_opposites[0])
                reasons.append("同级别同一确认时刻同时出现反向正式信号，结构冲突，禁止作为当前信号")
            else:
                later_opposites = [
                    other for other in ordered
                    if _same_level(signal, other)
                    and _signal_side(other)
                    and _signal_side(other) != side
                    and _is_formal_confirmed(other, as_of=as_of)
                    and (_parse_time(_get(other, "confirmation_timestamp"), reference=as_of) or confirmation) > confirmation
                ]
                if later_opposites:
                    invalidator = min(
                        later_opposites,
                        key=lambda other: (
                            _parse_time(_get(other, "confirmation_timestamp"), reference=as_of),
                            _signal_id(other),
                        ),
                    )
                    stage = SignalLifecycleStage.INVALIDATED
                    invalidated_by = _signal_id(invalidator)
                    reasons.append("之后出现同周期同级别反向正式信号，旧信号结构失效")
                else:
                    later_same_side = [
                        other for other in ordered
                        if _same_level(signal, other)
                        and _signal_side(other) == side
                        and _is_formal_confirmed(other, as_of=as_of)
                        and (_parse_time(_get(other, "confirmation_timestamp"), reference=as_of) or confirmation) > confirmation
                    ]
                    if later_same_side:
                        successor = min(
                            later_same_side,
                            key=lambda other: (
                                _parse_time(_get(other, "confirmation_timestamp"), reference=as_of),
                                _signal_id(other),
                            ),
                        )
                        stage = SignalLifecycleStage.MATURE
                        superseded_by = _signal_id(successor)
                        reasons.append("之后出现同周期同级别同向正式信号，旧入口已让位于更新结构；这不等于允许加仓")
                    elif freshness is SignalFreshness.STALE:
                        stage = SignalLifecycleStage.EXPIRED
                        reasons.append(
                            f"确认后已过去{age_bars}根完成{timeframe.value}K线，超过新机会观察窗"
                            f"{SIGNAL_ENTRY_WINDOW_BARS[timeframe]}根；仅表示过期，不表示结构曾被否定"
                        )
                    elif age_bars == 0:
                        stage = SignalLifecycleStage.CONFIRMED
                        reasons.append("正式信号刚确认，之后尚无新的完成K线")
                    elif age_bars is not None:
                        stage = SignalLifecycleStage.ACTIVE
                        reasons.append(
                            f"正式信号已确认，之后经过{age_bars}根完成K线，仍在{SIGNAL_ENTRY_WINDOW_BARS[timeframe]}根观察窗内"
                        )
                    elif latest_completed_bar_timestamp is not None and latest_completed_bar_timestamp <= confirmation:
                        stage = SignalLifecycleStage.CONFIRMED
                        reasons.append("仅有最新完成K时间证据：尚未看到确认后的新完成K线；精确K线年龄未知")
                    else:
                        stage = SignalLifecycleStage.ACTIVE
                        reasons.append("缺少完整完成K时间序列；结构仍保留，但无法验证新机会年龄")

        structurally_valid = stage in {
            SignalLifecycleStage.CONFIRMED,
            SignalLifecycleStage.ACTIVE,
            SignalLifecycleStage.MATURE,
            SignalLifecycleStage.EXPIRED,
        }
        eligible = (
            stage in {SignalLifecycleStage.CONFIRMED, SignalLifecycleStage.ACTIVE}
            and freshness is SignalFreshness.FRESH
        )
        records.append(
            SignalLifecycleRecord(
                signal_id=signal_id,
                timeframe=timeframe,
                side=side,
                standard_types=standard_types,
                extended_types=extended_types,
                level_rank=level_rank,
                structural_timestamp=structural_timestamp,
                confirmation_timestamp=confirmation,
                stage=stage,
                freshness=freshness,
                age_completed_bars=age_bars,
                entry_window_bars=SIGNAL_ENTRY_WINDOW_BARS[timeframe],
                structurally_valid=structurally_valid,
                lifecycle_eligible_as_current=eligible,
                superseded_by_signal_id=superseded_by,
                invalidated_by_signal_id=invalidated_by,
                reasons=tuple(reasons),
            )
        )

    return TimeframeSignalLifecycle(timeframe=timeframe, records=tuple(records))


def _buy_type_priority(record: SignalLifecycleRecord) -> int:
    priorities: list[int] = []
    for raw in record.standard_types:
        try:
            kind = ChanSignalType(raw)
        except ValueError:
            continue
        priorities.append(STANDARD_BUY_PRIORITY.get(kind, 0))
    return max(priorities, default=0)


def current_signal(records: Sequence[SignalLifecycleRecord], *, side: str) -> SignalLifecycleRecord | None:
    """先取最新结构；买点类别优先级只处理同一确认时刻的并列。"""
    eligible = [
        record for record in records
        if record.side == side
        and record.lifecycle_eligible_as_current
        and record.confirmation_timestamp is not None
    ]
    if not eligible:
        return None
    if side == "BUY":
        return max(
            eligible,
            key=lambda record: (record.confirmation_timestamp, _buy_type_priority(record), record.signal_id),
        )
    return max(eligible, key=lambda record: (record.confirmation_timestamp, record.signal_id))


def build_signal_lifecycle_book(
    results: Mapping[Any, Any],
    *,
    as_of: datetime,
    completed_bar_times_by_timeframe: Mapping[Any, Sequence[datetime]] | None = None,
    latest_completed_bar_by_timeframe: Mapping[Any, datetime | None] | None = None,
) -> dict[Timeframe, TimeframeSignalLifecycle]:
    completed_bar_times_by_timeframe = completed_bar_times_by_timeframe or {}
    latest_completed_bar_by_timeframe = latest_completed_bar_by_timeframe or {}
    out: dict[Timeframe, TimeframeSignalLifecycle] = {}
    for timeframe in (Timeframe.WEEKLY, Timeframe.DAILY, Timeframe.M120, Timeframe.M30, Timeframe.M5):
        result = results.get(timeframe)
        if result is None:
            result = results.get(timeframe.value)
        if result is None:
            continue
        completed_times = completed_bar_times_by_timeframe.get(timeframe)
        if completed_times is None:
            completed_times = completed_bar_times_by_timeframe.get(timeframe.value)
        latest = latest_completed_bar_by_timeframe.get(timeframe)
        if latest is None:
            latest = latest_completed_bar_by_timeframe.get(timeframe.value)
        out[timeframe] = evaluate_signal_lifecycle(
            result,
            as_of=as_of,
            completed_bar_timestamps=completed_times,
            latest_completed_bar_timestamp=latest,
            fallback_timeframe=timeframe,
        )
    return out


_CHILDREN: dict[Timeframe, tuple[Timeframe, ...]] = {
    Timeframe.DAILY: (Timeframe.M120, Timeframe.M30, Timeframe.M5),
    Timeframe.M120: (Timeframe.M30, Timeframe.M5),
    Timeframe.M30: (Timeframe.M5,),
}


def evaluate_lower_context(
    results: Mapping[Any, Any],
    lifecycle_book: Mapping[Timeframe, TimeframeSignalLifecycle],
    *,
    primary_timeframe: Timeframe,
    primary_confirmation: datetime,
    as_of: datetime,
) -> LowerTimeframeContext:
    """用4C“当前生命周期信号”评估低周期执行关系，不让已过期/已失效的历史卖点永久阻断。"""
    structure_book = build_structure_book(results, as_of=as_of).by_timeframe()
    child_states: list[tuple[Timeframe, str]] = []
    reasons: list[str] = []
    saw_pullback = False
    saw_unresolved = False
    saw_aligned = False
    saw_mixed = False

    for timeframe in _CHILDREN.get(primary_timeframe, ()):
        snapshot = structure_book.get(timeframe)
        lifecycle = lifecycle_book.get(timeframe)
        if snapshot is None or lifecycle is None or snapshot.status not in {"OK", "UNRESOLVED"}:
            child_states.append((timeframe, "UNRESOLVED"))
            reasons.append(f"{timeframe.value}缺少可用结构/生命周期证据")
            saw_unresolved = True
            continue

        current_buy = lifecycle.current_buy()
        current_sell = lifecycle.current_sell()
        buy_after = bool(
            current_buy
            and current_buy.confirmation_timestamp
            and current_buy.confirmation_timestamp > primary_confirmation
        )
        sell_after = bool(
            current_sell
            and current_sell.confirmation_timestamp
            and current_sell.confirmation_timestamp > primary_confirmation
        )

        if buy_after and sell_after:
            child_states.append((timeframe, "MIXED:CURRENT_BUY_AND_SELL"))
            reasons.append(f"{timeframe.value}不同级别仍同时存在当前BUY和SELL，执行关系冲突，不做单向确认")
            saw_mixed = True
            continue
        if sell_after:
            kind = "/".join(current_sell.standard_types) if current_sell else "SELL"
            child_states.append((timeframe, f"PULLBACK:{kind}"))
            reasons.append(f"{timeframe.value}主买点后仍存在当前有效SELL；只暂停执行，不反向失效主周期买点")
            saw_pullback = True
            continue
        if buy_after:
            kind = "/".join(current_buy.standard_types) if current_buy else "BUY"
            if snapshot.phase in {StructurePhase.BULL_TREND, StructurePhase.BREAKOUT_UP}:
                child_states.append((timeframe, f"ALIGNED:{kind}"))
                saw_aligned = True
            else:
                child_states.append((timeframe, f"MIXED:BUY_IN_{snapshot.phase.value}"))
                reasons.append(
                    f"{timeframe.value}虽有主买点后的当前BUY，但当前结构仍为{snapshot.phase.value}；只能视为转折尝试"
                )
                saw_mixed = True
            continue

        if snapshot.phase in {StructurePhase.BEAR_TREND, StructurePhase.BREAKDOWN_DOWN, StructurePhase.REVERSAL_DOWN_FORMING}:
            child_states.append((timeframe, f"PULLBACK:{snapshot.phase.value}"))
            reasons.append(f"{timeframe.value}当前结构仍偏空/向下，执行继续等待")
            saw_pullback = True
        elif snapshot.phase in {StructurePhase.BULL_TREND, StructurePhase.BREAKOUT_UP}:
            child_states.append((timeframe, f"ALIGNED:{snapshot.phase.value}"))
            saw_aligned = True
        elif snapshot.phase is StructurePhase.UNRESOLVED:
            child_states.append((timeframe, "UNRESOLVED"))
            saw_unresolved = True
        else:
            child_states.append((timeframe, f"MIXED:{snapshot.phase.value}"))
            saw_mixed = True

    if saw_pullback:
        state = LowerTimeframeState.WAITING_PULLBACK
    elif saw_unresolved:
        state = LowerTimeframeState.UNRESOLVED
    elif saw_mixed:
        state = LowerTimeframeState.MIXED
    elif saw_aligned:
        state = LowerTimeframeState.ALIGNED
    else:
        state = LowerTimeframeState.UNRESOLVED

    return LowerTimeframeContext(
        primary_timeframe=primary_timeframe,
        state=state,
        child_states=tuple(child_states),
        reasons=tuple(reasons),
    )
