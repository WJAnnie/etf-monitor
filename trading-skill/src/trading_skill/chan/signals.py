from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from trading_skill.chan.center import Center
from trading_skill.chan.divergence import Divergence
from trading_skill.chan.trend import TrendType
from trading_skill.domain.enums import (
    ChanSignalType, Direction, DivergenceState, DivergenceType, SecondBuyTrackerState,
    SecondSellTrackerState, SignalState, ThirdBuyTrackerState, ThirdSellTrackerState,
    TrendClassification, TrendState,
)
from trading_skill.domain.models import ValidationResult, stable_id

@dataclass(frozen=True, slots=True)
class ChanSignal:
    id: str
    symbol: str
    standard_types: tuple[ChanSignalType, ...]
    extended_types: tuple[ChanSignalType, ...]
    side: str
    level_rank: int
    timeframe: str
    state: SignalState
    structural_price_ticks: int
    structural_timestamp: datetime
    confirmation_timestamp: datetime
    anchor_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    theoretical: bool = True
    tradability: str = "THEORETICAL_ONLY"
    revision: int = 1
    issues: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class ReversalAnchor:
    id: str
    symbol: str
    direction: Direction
    level_rank: int
    price_ticks: int
    confirmation_timestamp: datetime
    valid: bool = True
    timeframe: str = "recursive"

@dataclass(frozen=True, slots=True)
class LowerMove:
    id: str
    direction: Direction
    level_rank: int
    low_ticks: int
    high_ticks: int
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    completed: bool = True
    start_ticks: int | None = None
    end_ticks: int | None = None

@dataclass(frozen=True, slots=True)
class SecondBuyTracker:
    id: str
    symbol: str
    level_rank: int
    anchor: ReversalAnchor
    state: SecondBuyTrackerState = SecondBuyTrackerState.WAIT_FIRST_UP_MOVE
    first_up_move: LowerMove | None = None
    first_retracement: LowerMove | None = None
    retracement_sequence_number: int = 0
    revision: int = 1

@dataclass(frozen=True, slots=True)
class ThirdBuyTracker:
    id: str
    symbol: str
    center_id: str
    level_rank: int
    state: ThirdBuyTrackerState = ThirdBuyTrackerState.WAIT_DEPARTURE
    departure: LowerMove | None = None
    first_return: LowerMove | None = None
    return_sequence_number: int = 0
    revision: int = 1

@dataclass(frozen=True, slots=True)
class SecondSellTracker:
    id: str
    symbol: str
    level_rank: int
    anchor: ReversalAnchor
    state: SecondSellTrackerState = SecondSellTrackerState.WAIT_FIRST_DOWN_MOVE
    first_down_move: LowerMove | None = None
    first_rebound: LowerMove | None = None
    rebound_sequence_number: int = 0
    revision: int = 1

@dataclass(frozen=True, slots=True)
class ThirdSellTracker:
    id: str
    symbol: str
    center_id: str
    level_rank: int
    state: ThirdSellTrackerState = ThirdSellTrackerState.WAIT_DEPARTURE
    departure: LowerMove | None = None
    first_return: LowerMove | None = None
    return_sequence_number: int = 0
    revision: int = 1


def _move_end_ticks(move: LowerMove) -> int:
    """返回运动的结构终点，而不是整段包络极值。

    旧数据没有显式端点时，才按运动方向退化到 high/low；生产链会逐步传入 end_ticks。
    """
    if move.end_ticks is not None:
        return move.end_ticks
    return move.high_ticks if move.direction is Direction.UP else move.low_ticks


def first_buy_or_sell(*, symbol: str, trend: TrendType, divergence: Divergence,
    structural_price_ticks: int, structural_timestamp: datetime) -> tuple[ChanSignal | None, ValidationResult]:
    if trend.state is not TrendState.COMPLETED or trend.final_type is None:
        return None, ValidationResult(False, ("TREND_NOT_COMPLETED",))
    if divergence.state is not DivergenceState.CONFIRMED:
        return None, ValidationResult(False, ("DIVERGENCE_NOT_CONFIRMED",))
    if trend.final_type is TrendClassification.DOWNTREND and divergence.type is DivergenceType.TREND_BOTTOM_DIVERGENCE:
        stype, side = ChanSignalType.FIRST_BUY, "BUY"
    elif trend.final_type is TrendClassification.UPTREND and divergence.type is DivergenceType.TREND_TOP_DIVERGENCE:
        stype, side = ChanSignalType.FIRST_SELL, "SELL"
    else:
        return None, ValidationResult(False, ("SIGNAL_LEVEL_MISMATCH",))
    sig = ChanSignal(
        id=stable_id("sig", symbol, stype, trend.id, divergence.id), symbol=symbol,
        standard_types=(stype,), extended_types=(), side=side, level_rank=trend.level_rank,
        timeframe=str(trend.source_timeframe), state=SignalState.CONFIRMED,
        structural_price_ticks=structural_price_ticks, structural_timestamp=structural_timestamp,
        confirmation_timestamp=max(trend.confirmation_timestamp, divergence.confirmation_timestamp),
        anchor_ids=(trend.id,), evidence_ids=(divergence.id,))
    return sig, ValidationResult(True)


def new_second_buy_tracker(anchor: ReversalAnchor) -> SecondBuyTracker:
    return SecondBuyTracker(stable_id("sbtrk", anchor.symbol, anchor.id, anchor.level_rank), anchor.symbol, anchor.level_rank, anchor)


def second_buy_step(tracker: SecondBuyTracker, move: LowerMove) -> tuple[SecondBuyTracker, ChanSignal | None]:
    if not tracker.anchor.valid:
        return replace(tracker, state=SecondBuyTrackerState.SECOND_BUY_INVALIDATED, revision=tracker.revision + 1), None
    if tracker.state in (SecondBuyTrackerState.WAIT_FIRST_UP_MOVE, SecondBuyTrackerState.FIRST_UP_MOVE_FORMING):
        if move.direction is Direction.UP:
            if move.completed:
                return replace(tracker, state=SecondBuyTrackerState.WAIT_RETRACEMENT, first_up_move=move, revision=tracker.revision + 1), None
            return replace(tracker, state=SecondBuyTrackerState.FIRST_UP_MOVE_FORMING, revision=tracker.revision + 1), None
        return tracker, None
    if tracker.state in (SecondBuyTrackerState.WAIT_RETRACEMENT, SecondBuyTrackerState.RETRACEMENT_FORMING):
        if move.direction is not Direction.DOWN:
            return tracker, None
        if not move.completed:
            return replace(tracker, state=SecondBuyTrackerState.RETRACEMENT_FORMING, revision=tracker.revision + 1), None
        seq = tracker.retracement_sequence_number + 1
        if seq != 1:
            return replace(tracker, retracement_sequence_number=seq, revision=tracker.revision + 1), None
        # 标准二买的第一次完成回调不能跌破一买结构低点；等于锚点视为边界有效。
        if move.low_ticks < tracker.anchor.price_ticks:
            return replace(
                tracker,
                state=SecondBuyTrackerState.SECOND_BUY_INVALIDATED,
                first_retracement=move,
                retracement_sequence_number=1,
                revision=tracker.revision + 1,
            ), None
        updated = replace(tracker, state=SecondBuyTrackerState.SECOND_BUY_CONFIRMED,
            first_retracement=move, retracement_sequence_number=1, revision=tracker.revision + 1)
        sig = ChanSignal(
            id=stable_id("sig", tracker.symbol, ChanSignalType.SECOND_BUY, tracker.anchor.id, move.id),
            symbol=tracker.symbol, standard_types=(ChanSignalType.SECOND_BUY,), extended_types=(), side="BUY",
            level_rank=tracker.level_rank, timeframe=tracker.anchor.timeframe, state=SignalState.CONFIRMED,
            structural_price_ticks=move.low_ticks, structural_timestamp=move.structural_end_timestamp,
            confirmation_timestamp=move.confirmation_timestamp, anchor_ids=(tracker.anchor.id,),
            evidence_ids=(tracker.first_up_move.id if tracker.first_up_move else "", move.id))
        return updated, sig
    return tracker, None


def new_third_buy_tracker(center: Center) -> ThirdBuyTracker:
    return ThirdBuyTracker(stable_id("tbtrk", center.id, center.level_rank), center.symbol, center.id, center.level_rank)


def third_buy_step(tracker: ThirdBuyTracker, center: Center, move: LowerMove) -> tuple[ThirdBuyTracker, ChanSignal | None]:
    if tracker.state in (ThirdBuyTrackerState.WAIT_DEPARTURE, ThirdBuyTrackerState.DEPARTURE_FORMING):
        if move.direction is Direction.UP:
            if not move.completed:
                return replace(tracker, state=ThirdBuyTrackerState.DEPARTURE_FORMING, revision=tracker.revision + 1), None
            # 离开段可以从中枢内部开始；关键是结构终点已经有效站上 ZG。
            if _move_end_ticks(move) <= center.zg_ticks:
                return tracker, None
            return replace(tracker, state=ThirdBuyTrackerState.WAIT_FIRST_RETURN, departure=move, revision=tracker.revision + 1), None
        return tracker, None
    if tracker.state in (ThirdBuyTrackerState.WAIT_FIRST_RETURN, ThirdBuyTrackerState.FIRST_RETURN_FORMING):
        if move.direction is not Direction.DOWN:
            return tracker, None
        if not move.completed:
            return replace(tracker, state=ThirdBuyTrackerState.FIRST_RETURN_FORMING, revision=tracker.revision + 1), None
        seq = tracker.return_sequence_number + 1
        if seq != 1:
            return replace(tracker, return_sequence_number=seq, revision=tracker.revision + 1), None
        if move.low_ticks < center.zg_ticks:
            return replace(tracker, state=ThirdBuyTrackerState.THIRD_BUY_FAILED,
                first_return=move, return_sequence_number=1, revision=tracker.revision + 1), None
        updated = replace(tracker, state=ThirdBuyTrackerState.THIRD_BUY_CONFIRMED,
            first_return=move, return_sequence_number=1, revision=tracker.revision + 1)
        sig = ChanSignal(
            id=stable_id("sig", tracker.symbol, ChanSignalType.THIRD_BUY, center.id, move.id),
            symbol=tracker.symbol, standard_types=(ChanSignalType.THIRD_BUY,), extended_types=(), side="BUY",
            level_rank=tracker.level_rank, timeframe=str(center.source_timeframe), state=SignalState.CONFIRMED,
            structural_price_ticks=move.low_ticks, structural_timestamp=move.structural_end_timestamp,
            confirmation_timestamp=move.confirmation_timestamp, anchor_ids=(center.id,),
            evidence_ids=(tracker.departure.id if tracker.departure else "", move.id))
        return updated, sig
    return tracker, None


def new_second_sell_tracker(anchor: ReversalAnchor) -> SecondSellTracker:
    return SecondSellTracker(stable_id("sstrk", anchor.symbol, anchor.id, anchor.level_rank), anchor.symbol, anchor.level_rank, anchor)


def second_sell_step(tracker: SecondSellTracker, move: LowerMove) -> tuple[SecondSellTracker, ChanSignal | None]:
    if not tracker.anchor.valid:
        return replace(tracker, state=SecondSellTrackerState.SECOND_SELL_INVALIDATED, revision=tracker.revision + 1), None
    if tracker.state in (SecondSellTrackerState.WAIT_FIRST_DOWN_MOVE, SecondSellTrackerState.FIRST_DOWN_MOVE_FORMING):
        if move.direction is Direction.DOWN:
            if move.completed:
                return replace(tracker, state=SecondSellTrackerState.WAIT_REBOUND, first_down_move=move, revision=tracker.revision + 1), None
            return replace(tracker, state=SecondSellTrackerState.FIRST_DOWN_MOVE_FORMING, revision=tracker.revision + 1), None
        return tracker, None
    if tracker.state in (SecondSellTrackerState.WAIT_REBOUND, SecondSellTrackerState.REBOUND_FORMING):
        if move.direction is not Direction.UP:
            return tracker, None
        if not move.completed:
            return replace(tracker, state=SecondSellTrackerState.REBOUND_FORMING, revision=tracker.revision + 1), None
        seq = tracker.rebound_sequence_number + 1
        if seq != 1:
            return replace(tracker, rebound_sequence_number=seq, revision=tracker.revision + 1), None
        # 二卖镜像：第一次完成反弹不能突破一卖结构高点；等于锚点视为边界有效。
        if move.high_ticks > tracker.anchor.price_ticks:
            return replace(
                tracker,
                state=SecondSellTrackerState.SECOND_SELL_INVALIDATED,
                first_rebound=move,
                rebound_sequence_number=1,
                revision=tracker.revision + 1,
            ), None
        updated = replace(tracker, state=SecondSellTrackerState.SECOND_SELL_CONFIRMED,
            first_rebound=move, rebound_sequence_number=1, revision=tracker.revision + 1)
        sig = ChanSignal(
            id=stable_id("sig", tracker.symbol, ChanSignalType.SECOND_SELL, tracker.anchor.id, move.id),
            symbol=tracker.symbol, standard_types=(ChanSignalType.SECOND_SELL,), extended_types=(), side="SELL",
            level_rank=tracker.level_rank, timeframe=tracker.anchor.timeframe, state=SignalState.CONFIRMED,
            structural_price_ticks=move.high_ticks, structural_timestamp=move.structural_end_timestamp,
            confirmation_timestamp=move.confirmation_timestamp, anchor_ids=(tracker.anchor.id,),
            evidence_ids=(tracker.first_down_move.id if tracker.first_down_move else "", move.id))
        return updated, sig
    return tracker, None


def new_third_sell_tracker(center: Center) -> ThirdSellTracker:
    return ThirdSellTracker(stable_id("tstrk", center.id, center.level_rank), center.symbol, center.id, center.level_rank)


def third_sell_step(tracker: ThirdSellTracker, center: Center, move: LowerMove) -> tuple[ThirdSellTracker, ChanSignal | None]:
    if tracker.state in (ThirdSellTrackerState.WAIT_DEPARTURE, ThirdSellTrackerState.DEPARTURE_FORMING):
        if move.direction is Direction.DOWN:
            if not move.completed:
                return replace(tracker, state=ThirdSellTrackerState.DEPARTURE_FORMING, revision=tracker.revision + 1), None
            # 镜像：离开段结构终点有效跌破 ZD 即可，不要求整段包络都在中枢下方。
            if _move_end_ticks(move) >= center.zd_ticks:
                return tracker, None
            return replace(tracker, state=ThirdSellTrackerState.WAIT_FIRST_RETURN, departure=move, revision=tracker.revision + 1), None
        return tracker, None
    if tracker.state in (ThirdSellTrackerState.WAIT_FIRST_RETURN, ThirdSellTrackerState.FIRST_RETURN_FORMING):
        if move.direction is not Direction.UP:
            return tracker, None
        if not move.completed:
            return replace(tracker, state=ThirdSellTrackerState.FIRST_RETURN_FORMING, revision=tracker.revision + 1), None
        seq = tracker.return_sequence_number + 1
        if seq != 1:
            return replace(tracker, return_sequence_number=seq, revision=tracker.revision + 1), None
        if move.high_ticks > center.zd_ticks:
            return replace(tracker, state=ThirdSellTrackerState.THIRD_SELL_FAILED,
                first_return=move, return_sequence_number=1, revision=tracker.revision + 1), None
        updated = replace(tracker, state=ThirdSellTrackerState.THIRD_SELL_CONFIRMED,
            first_return=move, return_sequence_number=1, revision=tracker.revision + 1)
        sig = ChanSignal(
            id=stable_id("sig", tracker.symbol, ChanSignalType.THIRD_SELL, center.id, move.id),
            symbol=tracker.symbol, standard_types=(ChanSignalType.THIRD_SELL,), extended_types=(), side="SELL",
            level_rank=tracker.level_rank, timeframe=str(center.source_timeframe), state=SignalState.CONFIRMED,
            structural_price_ticks=move.high_ticks, structural_timestamp=move.structural_end_timestamp,
            confirmation_timestamp=move.confirmation_timestamp, anchor_ids=(center.id,),
            evidence_ids=(tracker.departure.id if tracker.departure else "", move.id))
        return updated, sig
    return tracker, None


def overlap_signals(*signals: ChanSignal) -> ChanSignal:
    if not signals:
        raise ValueError("NO_SIGNALS")
    first = signals[0]
    std: list[ChanSignalType] = []
    for signal in signals:
        if signal.symbol != first.symbol or signal.side != first.side or signal.level_rank != first.level_rank:
            raise ValueError("SIGNAL_LEVEL_MISMATCH")
        for t in signal.standard_types:
            if t not in std:
                std.append(t)
    if ChanSignalType.FIRST_BUY in std and len(std) > 1:
        raise ValueError("FIRST_BUY_CANNOT_OVERLAP")
    if ChanSignalType.FIRST_SELL in std and len(std) > 1:
        raise ValueError("FIRST_SELL_CANNOT_OVERLAP")
    return replace(first, id=stable_id("sig_overlap", *(s.id for s in signals)), standard_types=tuple(std),
        confirmation_timestamp=max(s.confirmation_timestamp for s in signals),
        evidence_ids=tuple(x for s in signals for x in s.evidence_ids), revision=max(s.revision for s in signals))
