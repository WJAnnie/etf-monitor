from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Mapping

from trading_skill.domain.enums import Timeframe
from trading_skill.signal_lifecycle import SignalLifecycleRecord, TimeframeSignalLifecycle


class ExecutionChainState(StrEnum):
    ALIGNED = "ALIGNED"
    WAITING_PULLBACK = "WAITING_PULLBACK"
    MIXED = "MIXED"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True, slots=True)
class ExecutionChainContext:
    primary_timeframe: Timeframe
    state: ExecutionChainState
    child_states: tuple[tuple[Timeframe, str], ...]
    child_signal_ids: tuple[tuple[Timeframe, str], ...]
    authority_signal_id: str
    authority_anchor_timestamp: datetime | None
    final_execution_signal_id: str | None
    reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "primary_timeframe": self.primary_timeframe.value,
            "state": self.state.value,
            "child_states": {tf.value: state for tf, state in self.child_states},
            "child_signal_ids": {tf.value: signal_id for tf, signal_id in self.child_signal_ids},
            "authority_signal_id": self.authority_signal_id,
            "authority_anchor_timestamp": (
                self.authority_anchor_timestamp.isoformat() if self.authority_anchor_timestamp else None
            ),
            "final_execution_signal_id": self.final_execution_signal_id,
            "reasons": list(self.reasons),
        }


_REQUIRED_CHILDREN = (Timeframe.M120, Timeframe.M30, Timeframe.M5)


def _normalise_time(value: datetime | None, *, as_of: datetime) -> datetime | None:
    if value is None:
        return None
    if as_of.tzinfo is not None and value.tzinfo is None:
        return value.replace(tzinfo=as_of.tzinfo)
    if as_of.tzinfo is not None and value.tzinfo is not None:
        return value.astimezone(as_of.tzinfo)
    return value


def _technical_confirmation(results: Mapping[Any, Any], timeframe: Timeframe) -> str:
    result = results.get(timeframe)
    if result is None:
        result = results.get(timeframe.value)
    if result is None:
        return ""
    if isinstance(result, Mapping):
        technical = result.get("technical") or {}
        if isinstance(technical, Mapping):
            return str(technical.get("confirmation") or "")
        return ""
    technical = getattr(result, "technical", None)
    return str(getattr(technical, "confirmation", "") or "")


def _qualifying(record: SignalLifecycleRecord | None, *, anchor: datetime, as_of: datetime) -> bool:
    if record is None or record.confirmation_timestamp is None:
        return False
    when = _normalise_time(record.confirmation_timestamp, as_of=as_of)
    return bool(when is not None and anchor <= when <= as_of)


def evaluate_daily_execution_chain(
    results: Mapping[Any, Any],
    lifecycle_book: Mapping[Timeframe, TimeframeSignalLifecycle],
    *,
    authority: SignalLifecycleRecord,
    as_of: datetime,
) -> ExecutionChainContext:
    """Require a current formal BUY on 120m, 30m and 5m for the current DAILY authority.

    The chain is hierarchical, not a vote and not a chronological requirement that a
    higher timeframe must confirm before a lower one. Lower timeframes often confirm
    earlier. Membership is therefore anchored to the DAILY signal's structural timestamp
    (falling back to confirmation time): each child BUY must belong to the current daily
    reversal/second-buy leg, be lifecycle-current, and have no newer current SELL.

    Indicator confirmation may pause or caution execution, but it can never substitute
    for a formal child BUY. In particular, 5m technical=SUPPORT without a 5m formal BUY
    is not an execution trigger.
    """
    anchor = _normalise_time(authority.structural_timestamp, as_of=as_of)
    if anchor is None:
        anchor = _normalise_time(authority.confirmation_timestamp, as_of=as_of)
    if anchor is None:
        return ExecutionChainContext(
            Timeframe.DAILY,
            ExecutionChainState.UNRESOLVED,
            (),
            (),
            authority.signal_id,
            None,
            None,
            ("日线授权信号缺少结构/确认时间，无法判断低周期信号是否属于当前结构",),
        )

    child_states: list[tuple[Timeframe, str]] = []
    child_signal_ids: list[tuple[Timeframe, str]] = []
    reasons: list[str] = []
    saw_pullback = False
    saw_unresolved = False
    saw_mixed = False
    final_execution_signal_id: str | None = None

    for timeframe in _REQUIRED_CHILDREN:
        lifecycle = lifecycle_book.get(timeframe)
        if lifecycle is None:
            child_states.append((timeframe, "UNRESOLVED:NO_LIFECYCLE"))
            reasons.append(f"{timeframe.value}缺少生命周期结果")
            saw_unresolved = True
            continue

        current_buy = lifecycle.current_buy()
        current_sell = lifecycle.current_sell()
        buy_ok = _qualifying(current_buy, anchor=anchor, as_of=as_of)
        sell_ok = _qualifying(current_sell, anchor=anchor, as_of=as_of)

        buy_time = (
            _normalise_time(current_buy.confirmation_timestamp, as_of=as_of)
            if buy_ok and current_buy is not None
            else None
        )
        sell_time = (
            _normalise_time(current_sell.confirmation_timestamp, as_of=as_of)
            if sell_ok and current_sell is not None
            else None
        )

        # If both levels remain current, the newer one controls execution. Same-time
        # opposite signals are unresolved rather than silently favoring BUY.
        if buy_time is not None and sell_time is not None:
            if sell_time > buy_time:
                child_states.append((timeframe, f"PULLBACK:{'/'.join(current_sell.standard_types)}"))
                reasons.append(f"{timeframe.value}当前日线结构内最新正式信号为SELL，执行等待")
                saw_pullback = True
                continue
            if sell_time == buy_time:
                child_states.append((timeframe, "MIXED:SAME_TIME_BUY_SELL"))
                reasons.append(f"{timeframe.value}同一确认时刻存在BUY/SELL冲突")
                saw_mixed = True
                continue

        if buy_time is None:
            child_states.append((timeframe, "UNRESOLVED:NO_CURRENT_FORMAL_BUY"))
            reasons.append(
                f"{timeframe.value}在当前日线结构锚点之后没有生命周期有效的正式BUY；"
                "趋势偏多或指标SUPPORT不能替代正式买点"
            )
            saw_unresolved = True
            continue

        assert current_buy is not None
        label = "/".join(current_buy.standard_types) or "BUY"
        child_states.append((timeframe, f"ALIGNED:{label}"))
        child_signal_ids.append((timeframe, current_buy.signal_id))
        if timeframe is Timeframe.M5:
            final_execution_signal_id = current_buy.signal_id
            technical = _technical_confirmation(results, timeframe)
            if technical == "PAUSE":
                child_states[-1] = (timeframe, f"MIXED:{label}:TECH_PAUSE")
                reasons.append("5分钟已有正式BUY，但技术确认层为PAUSE；指标只能暂停，不能创造或升级买点")
                saw_mixed = True

    if saw_pullback:
        state = ExecutionChainState.WAITING_PULLBACK
    elif saw_mixed:
        state = ExecutionChainState.MIXED
    elif saw_unresolved:
        state = ExecutionChainState.UNRESOLVED
    elif len(child_signal_ids) == len(_REQUIRED_CHILDREN) and final_execution_signal_id:
        state = ExecutionChainState.ALIGNED
    else:
        state = ExecutionChainState.UNRESOLVED
        reasons.append("执行链证据数量不完整")

    return ExecutionChainContext(
        Timeframe.DAILY,
        state,
        tuple(child_states),
        tuple(child_signal_ids),
        authority.signal_id,
        anchor,
        final_execution_signal_id,
        tuple(reasons),
    )
