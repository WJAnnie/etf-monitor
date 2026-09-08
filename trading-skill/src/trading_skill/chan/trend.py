from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from trading_skill.chan.center import Center, classify_center_relation
from trading_skill.domain.enums import CenterRelationType, TrendClassification, TrendState, Timeframe
from trading_skill.domain.models import ValidationResult, stable_id

@dataclass(frozen=True, slots=True)
class TrendType:
    id: str
    symbol: str
    source_timeframe: Timeframe
    level_rank: int
    state: TrendState
    current_classification: TrendClassification
    final_type: TrendClassification | None
    center_ids: tuple[str, ...]
    lower_motion_ids: tuple[str, ...]
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    completion_reason: str | None = None
    revision: int = 1
    issues: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class TrendUpdate:
    trend: TrendType
    result: ValidationResult = ValidationResult(True)

def classify_trend(centers: tuple[Center, ...], *, symbol: str, lower_motion_ids: tuple[str, ...] = ()) -> TrendUpdate:
    if not centers:
        raise ValueError("NO_CENTERS")
    ordered = tuple(sorted(centers, key=lambda c: (c.structural_start_timestamp, c.id)))
    tf = ordered[0].source_timeframe
    level = ordered[0].level_rank
    if any(c.source_timeframe is not tf for c in ordered):
        return _unresolved(ordered, symbol, lower_motion_ids, "CENTER_TIMEFRAME_MISMATCH")
    if any(c.level_rank != level for c in ordered):
        return _unresolved(ordered, symbol, lower_motion_ids, "CENTER_LEVEL_MISMATCH")
    if len(ordered) == 1:
        cls = TrendClassification.CONSOLIDATION
        state = TrendState.CLASSIFIABLE
    else:
        relations = tuple(classify_center_relation(a, b).type for a, b in zip(ordered, ordered[1:]))
        if any(r is CenterRelationType.EXPANSION_PENDING for r in relations):
            return _unresolved(ordered, symbol, lower_motion_ids, "CENTER_RELATION_EXPANSION_PENDING")
        up = all(r is CenterRelationType.INDEPENDENT_UP_NEW_CENTER for r in relations)
        down = all(r is CenterRelationType.INDEPENDENT_DOWN_NEW_CENTER for r in relations)
        if up:
            cls, state = TrendClassification.UPTREND, TrendState.EXTENDING
        elif down:
            cls, state = TrendClassification.DOWNTREND, TrendState.EXTENDING
        else:
            cls, state = TrendClassification.UNRESOLVED, TrendState.UNRESOLVED
    trend = TrendType(
        id=stable_id("trend", symbol, tf, level, *(c.id for c in ordered)), symbol=symbol,
        source_timeframe=tf, level_rank=level, state=state, current_classification=cls, final_type=None,
        center_ids=tuple(c.id for c in ordered), lower_motion_ids=lower_motion_ids,
        structural_start_timestamp=ordered[0].structural_start_timestamp,
        structural_end_timestamp=ordered[-1].structural_end_timestamp,
        confirmation_timestamp=ordered[-1].confirmation_timestamp,
    )
    return TrendUpdate(trend, ValidationResult(cls is not TrendClassification.UNRESOLVED, () if cls is not TrendClassification.UNRESOLVED else ("TREND_UNRESOLVED",)))

def mark_completion_candidate(trend: TrendType, *, reason: str, confirmation_timestamp: datetime) -> TrendType:
    if trend.final_type is not None:
        return trend
    if trend.current_classification is TrendClassification.UNRESOLVED:
        return replace(trend, state=TrendState.UNRESOLVED, issues=trend.issues + ("TREND_UNRESOLVED",))
    return replace(trend, state=TrendState.COMPLETION_CANDIDATE,
        confirmation_timestamp=max(trend.confirmation_timestamp, confirmation_timestamp),
        completion_reason=reason, revision=trend.revision + 1)

def complete_trend(trend: TrendType, *, lower_level_opposite_turn_completed: bool,
    direct_extension_exists: bool, confirmation_timestamp: datetime,
    reason: str = "LOWER_LEVEL_OPPOSITE_TURN") -> TrendType:
    if trend.final_type is not None:
        return trend
    if trend.current_classification is TrendClassification.UNRESOLVED:
        return replace(trend, state=TrendState.UNRESOLVED, issues=trend.issues + ("TREND_UNRESOLVED",))
    if not lower_level_opposite_turn_completed or direct_extension_exists:
        return replace(trend, state=TrendState.EXTENDING if direct_extension_exists else TrendState.COMPLETION_CANDIDATE,
            confirmation_timestamp=max(trend.confirmation_timestamp, confirmation_timestamp), revision=trend.revision + 1)
    return replace(trend, state=TrendState.COMPLETED, final_type=trend.current_classification,
        confirmation_timestamp=max(trend.confirmation_timestamp, confirmation_timestamp),
        completion_reason=reason, revision=trend.revision + 1)

def _unresolved(centers: tuple[Center, ...], symbol: str, lower_motion_ids: tuple[str, ...], issue: str) -> TrendUpdate:
    c = centers[0]
    trend = TrendType(
        id=stable_id("trend_unresolved", symbol, c.source_timeframe, c.level_rank, *(x.id for x in centers)),
        symbol=symbol, source_timeframe=c.source_timeframe, level_rank=c.level_rank, state=TrendState.UNRESOLVED,
        current_classification=TrendClassification.UNRESOLVED, final_type=None, center_ids=tuple(x.id for x in centers),
        lower_motion_ids=lower_motion_ids, structural_start_timestamp=centers[0].structural_start_timestamp,
        structural_end_timestamp=centers[-1].structural_end_timestamp,
        confirmation_timestamp=centers[-1].confirmation_timestamp, issues=(issue,))
    return TrendUpdate(trend, ValidationResult(False, (issue,)))
