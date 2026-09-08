from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from trading_skill.chan.center import Center
from trading_skill.chan.divergence import Divergence
from trading_skill.chan.trend import TrendType
from trading_skill.domain.enums import (
    ChanSignalType, Direction, DivergenceState, DivergenceType, SecondBuyTrackerState,
    SignalState, ThirdBuyTrackerState, TrendClassification, TrendState,
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
        updated = replace(tracker, state=SecondBuyTrackerState.SECOND_BUY_CONFIRMED,
            first_retracement=move, retracement_sequence_number=1, revision=tracker.revision + 1)
        sig = ChanSignal(
            id=stable_id("sig", tracker.symbol, ChanSignalType.SECOND_BUY, tracker.anchor.id, move.id),
            symbol=tracker.symbol, standard_types=(ChanSignalType.SECOND_BUY,), extended_types=(), side="BUY",
            level_rank=tracker.level_rank, timeframe="recursive", state=SignalState.CONFIRMED,
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
            if move.low_ticks <= center.zg_ticks:
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
