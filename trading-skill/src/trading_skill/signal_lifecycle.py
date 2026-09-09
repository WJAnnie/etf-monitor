from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any, Mapping, Sequence

from trading_skill.domain.enums import ChanSignalType, SignalState, Timeframe
from trading_skill.strategy_policy import STANDARD_BUY_PRIORITY, TIMEFRAME_POLICY


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


def _freshness(timeframe: Timeframe, confirmation: datetime | None, *, as_of: datetime) -> SignalFreshness:
    if confirmation is None:
        return SignalFreshness.UNKNOWN
    cutoff = as_of - timedelta(days=TIMEFRAME_POLICY[timeframe].freshness_days)
    return SignalFreshness.FRESH if confirmation >= cutoff else SignalFreshness.STALE


def evaluate_signal_lifecycle(
    result: Any,
    *,
    as_of: datetime,
    latest_completed_bar_timestamp: datetime | None = None,
    fallback_timeframe: Timeframe | None = None,
) -> TimeframeSignalLifecycle:
    """Evaluate confirmed Chan signals without rewriting the canonical 4A signal definition.

    Time expiry and structural invalidation are deliberately separate:
    - EXPIRED means the signal is too old to act as a new current opportunity, but its structure was not disproved.
    - INVALIDATED requires a later formal opposite signal at the same timeframe and level, or an explicit 4A invalidation.
    - MATURE means a newer same-side formal signal at the same timeframe and level has advanced the structure.
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
        freshness = _freshness(timeframe, confirmation, as_of=as_of)
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
                other
                for other in ordered
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
                    other
                    for other in ordered
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
                        other
                        for other in ordered
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
                        reasons.append("之后出现同周期同级别同向正式信号，机会已推进到更新结构")
                    elif freshness is SignalFreshness.STALE:
                        stage = SignalLifecycleStage.EXPIRED
                        reasons.append("超过该周期新开仓观察窗口；仅表示过期，不表示结构曾被否定")
                    elif latest_completed_bar_timestamp is not None and latest_completed_bar_timestamp <= confirmation:
                        stage = SignalLifecycleStage.CONFIRMED
                        reasons.append("正式信号刚确认，尚无更新的完成K线")
                    else:
                        stage = SignalLifecycleStage.ACTIVE
                        reasons.append("正式信号已确认、仍在观察窗口内，且没有后续同级反向信号或更新同向结构")

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
    """Newest structurally-current signal wins; buy-type priority only breaks same-time ties.

    This prevents an older SECOND_BUY from outranking a genuinely newer THIRD_BUY merely because
    SECOND_BUY has a larger strategy score. If SECOND_BUY and THIRD_BUY confirm at the same instant,
    the normal buy-type priority still selects SECOND_BUY, matching the strong class-2 combined case.
    """
    eligible = [
        record
        for record in records
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
    latest_completed_bar_by_timeframe: Mapping[Any, datetime | None] | None = None,
) -> dict[Timeframe, TimeframeSignalLifecycle]:
    latest_completed_bar_by_timeframe = latest_completed_bar_by_timeframe or {}
    out: dict[Timeframe, TimeframeSignalLifecycle] = {}
    for timeframe in (Timeframe.WEEKLY, Timeframe.DAILY, Timeframe.M120, Timeframe.M30, Timeframe.M5):
        result = results.get(timeframe)
        if result is None:
            result = results.get(timeframe.value)
        if result is None:
            continue
        latest = latest_completed_bar_by_timeframe.get(timeframe)
        if latest is None:
            latest = latest_completed_bar_by_timeframe.get(timeframe.value)
        out[timeframe] = evaluate_signal_lifecycle(
            result,
            as_of=as_of,
            latest_completed_bar_timestamp=latest,
            fallback_timeframe=timeframe,
        )
    return out
