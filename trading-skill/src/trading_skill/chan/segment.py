from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from trading_skill.chan.feature_sequence import (
    FeatureElement,
    FeatureFractal,
    StandardFeatureSequence,
    build_raw_feature_sequence,
    detect_feature_fractals,
    element_by_id,
    has_gap,
    standardize_feature_sequence,
)
from trading_skill.chan.stroke import Stroke
from trading_skill.domain.enums import (
    Direction,
    FeatureFractalType,
    SegmentBreakState,
    SegmentCase,
    SegmentState,
    StructureState,
)
from trading_skill.domain.models import ValidationResult, stable_id


@dataclass(frozen=True, slots=True)
class Segment:
    id: str
    direction: Direction
    state: SegmentState
    stroke_ids: tuple[str, ...]
    structural_start_stroke_id: str
    structural_end_stroke_id: str | None
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime | None
    confirmation_timestamp: datetime | None
    structural_start_ticks: int
    structural_end_ticks: int | None
    actual_low_ticks: int
    actual_high_ticks: int
    break_state: SegmentBreakState
    confirmation_case: SegmentCase
    raw_feature_sequence: tuple[FeatureElement, ...]
    standard_feature_sequence: tuple[FeatureElement, ...]
    second_feature_sequence: tuple[FeatureElement, ...] = ()
    revision: int = 1
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NormalizedSegment:
    id: str
    source_segment_id: str
    direction: Direction
    low_ticks: int
    high_ticks: int
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    state: StructureState = StructureState.FINALIZED
    # 结构端点与整段实际包络必须分开保存。三买/三卖离开判断看 endpoint；中枢重叠看 low/high。
    structural_start_ticks: int | None = None
    structural_end_ticks: int | None = None


@dataclass(frozen=True, slots=True)
class SegmentRevision:
    segment_id: str
    from_revision: int
    to_revision: int
    reason: str
    confirmation_timestamp: datetime | None


@dataclass(frozen=True, slots=True)
class SegmentOutput:
    segments: tuple[Segment, ...]
    normalized_segments: tuple[NormalizedSegment, ...]
    revisions: tuple[SegmentRevision, ...]
    result: ValidationResult


def _interval(stroke: Stroke) -> tuple[int, int]:
    return min(stroke.start_ticks, stroke.end_ticks), max(stroke.start_ticks, stroke.end_ticks)


def _common_overlap(strokes: tuple[Stroke, ...]) -> bool:
    lows = [min(s.start_ticks, s.end_ticks) for s in strokes]
    highs = [max(s.start_ticks, s.end_ticks) for s in strokes]
    return max(lows) <= min(highs)


def validate_segment_seed(strokes: tuple[Stroke, ...]) -> ValidationResult:
    if len(strokes) < 3:
        return ValidationResult(False, ("INSUFFICIENT_STROKES",))
    seed = strokes[:3]
    if not (seed[0].direction is seed[2].direction and seed[0].direction is not seed[1].direction):
        return ValidationResult(False, ("DIRECTION_NOT_ALTERNATING",))
    if not _common_overlap(seed):
        return ValidationResult(False, ("SEED_NO_COMMON_OVERLAP",))
    return ValidationResult(True)


def _target_feature_type(direction: Direction) -> FeatureFractalType:
    return FeatureFractalType.FEATURE_TOP if direction is Direction.UP else FeatureFractalType.FEATURE_BOTTOM


def _opposite_feature_type(direction: Direction) -> FeatureFractalType:
    return FeatureFractalType.FEATURE_BOTTOM if direction is Direction.UP else FeatureFractalType.FEATURE_TOP


def _candidate_endpoint_index(middle: FeatureElement, *, segment_start: int) -> int | None:
    if not middle.source_stroke_indices:
        return None
    idx = min(middle.source_stroke_indices) - 1
    if idx < segment_start:
        return None
    return idx


def _actual_envelope(strokes: tuple[Stroke, ...], start: int, through: int) -> tuple[int, int]:
    lows: list[int] = []
    highs: list[int] = []
    for s in strokes[start: through + 1]:
        low, high = _interval(s)
        lows.append(low)
        highs.append(high)
    return min(lows), max(highs)


def _structural_end_ticks(stroke: Stroke, direction: Direction) -> int:
    return stroke.end_ticks


def _confirm_case1(
    strokes: tuple[Stroke, ...], *, start: int, direction: Direction,
    standardized: StandardFeatureSequence,
) -> tuple[int, FeatureFractal, int] | None:
    by_id = element_by_id(standardized.elements)
    fractals = detect_feature_fractals(standardized.elements, target_type=_target_feature_type(direction))
    for fractal in fractals:
        left = by_id[fractal.left_element_id]
        middle = by_id[fractal.middle_element_id]
        if has_gap(left, middle):
            continue
        endpoint = _candidate_endpoint_index(middle, segment_start=start)
        if endpoint is None or endpoint >= len(strokes):
            continue
        if (endpoint - start + 1) < 3 or (endpoint - start + 1) % 2 == 0:
            continue
        if strokes[endpoint].direction is not direction:
            continue
        confirm_through = max(by_id[fractal.right_element_id].source_stroke_indices)
        return endpoint, fractal, confirm_through
    return None


def _first_case2_candidate(
    strokes: tuple[Stroke, ...], *, start: int, direction: Direction,
    standardized: StandardFeatureSequence,
) -> tuple[int, FeatureFractal, int] | None:
    by_id = element_by_id(standardized.elements)
    fractals = detect_feature_fractals(standardized.elements, target_type=_target_feature_type(direction))
    for fractal in fractals:
        left = by_id[fractal.left_element_id]
        middle = by_id[fractal.middle_element_id]
        if not has_gap(left, middle):
            continue
        endpoint = _candidate_endpoint_index(middle, segment_start=start)
        if endpoint is None or endpoint >= len(strokes):
            continue
        if (endpoint - start + 1) < 3 or (endpoint - start + 1) % 2 == 0:
            continue
        if strokes[endpoint].direction is not direction:
            continue
        confirm_through = max(by_id[fractal.right_element_id].source_stroke_indices)
        return endpoint, fractal, confirm_through
    return None


def _case2_second_sequence(
    strokes: tuple[Stroke, ...], *, endpoint: int, direction: Direction,
) -> tuple[tuple[FeatureElement, ...], FeatureFractal | None, int | None]:
    # New opposite segment's feature sequence uses the OLD segment direction strokes.
    opposite_segment_direction = Direction.DOWN if direction is Direction.UP else Direction.UP
    raw = build_raw_feature_sequence(strokes, segment_direction=opposite_segment_direction, start_index=endpoint + 1)
    standardized = standardize_feature_sequence(raw, fallback_direction=opposite_segment_direction)
    fractals = detect_feature_fractals(standardized.elements, target_type=_opposite_feature_type(direction))
    if not fractals:
        return standardized.elements, None, None
    ff = fractals[0]
    by_id = element_by_id(standardized.elements)
    through = max(by_id[ff.right_element_id].source_stroke_indices)
    return standardized.elements, ff, through


def _candidate_failed(strokes: tuple[Stroke, ...], endpoint: int, direction: Direction) -> bool:
    pivot = _structural_end_ticks(strokes[endpoint], direction)
    for s in strokes[endpoint + 1:]:
        if s.direction is not direction:
            continue
        extreme = s.end_ticks
        if direction is Direction.UP and extreme > pivot:
            return True
        if direction is Direction.DOWN and extreme < pivot:
            return True
    return False


def _make_active_segment(strokes: tuple[Stroke, ...], *, start: int, state: SegmentState,
                         break_state: SegmentBreakState, confirmation_case: SegmentCase,
                         raw: tuple[FeatureElement, ...], standardized: tuple[FeatureElement, ...],
                         second: tuple[FeatureElement, ...] = (), issues: tuple[str, ...] = ()) -> Segment:
    direction = strokes[start].direction
    low, high = _actual_envelope(strokes, start, len(strokes) - 1)
    sid = stable_id("seg", strokes[start].id, direction)
    return Segment(
        id=sid,
        direction=direction,
        state=state,
        stroke_ids=tuple(s.id for s in strokes[start:]),
        structural_start_stroke_id=strokes[start].id,
        structural_end_stroke_id=None,
        structural_start_timestamp=strokes[start].structural_start_timestamp,
        structural_end_timestamp=None,
        confirmation_timestamp=None,
        structural_start_ticks=strokes[start].start_ticks,
        structural_end_ticks=None,
        actual_low_ticks=low,
        actual_high_ticks=high,
        break_state=break_state,
        confirmation_case=confirmation_case,
        raw_feature_sequence=raw,
        standard_feature_sequence=standardized,
        second_feature_sequence=second,
        issues=issues,
    )


def _make_finalized_segment(strokes: tuple[Stroke, ...], *, start: int, endpoint: int,
                            confirm_through: int, confirmation_timestamp: datetime,
                            confirmation_case: SegmentCase,
                            raw: tuple[FeatureElement, ...], standardized: tuple[FeatureElement, ...],
                            second: tuple[FeatureElement, ...] = ()) -> Segment:
    direction = strokes[start].direction
    low, high = _actual_envelope(strokes, start, confirm_through)
    sid = stable_id("seg", strokes[start].id, direction)
    end = strokes[endpoint]
    return Segment(
        id=sid,
        direction=direction,
        state=SegmentState.FINALIZED,
        stroke_ids=tuple(s.id for s in strokes[start: endpoint + 1]),
        structural_start_stroke_id=strokes[start].id,
        structural_end_stroke_id=end.id,
        structural_start_timestamp=strokes[start].structural_start_timestamp,
        structural_end_timestamp=end.structural_end_timestamp,
        confirmation_timestamp=confirmation_timestamp,
        structural_start_ticks=strokes[start].start_ticks,
        structural_end_ticks=_structural_end_ticks(end, direction),
        actual_low_ticks=low,
        actual_high_ticks=high,
        break_state=SegmentBreakState.BREAK_CONFIRMED,
        confirmation_case=confirmation_case,
        raw_feature_sequence=raw,
        standard_feature_sequence=standardized,
        second_feature_sequence=second,
    )


def to_normalized_segment(segment: Segment) -> NormalizedSegment:
    if (
        segment.state is not SegmentState.FINALIZED
        or segment.structural_end_timestamp is None
        or segment.confirmation_timestamp is None
        or segment.structural_end_ticks is None
    ):
        raise ValueError("only finalized segments with structural endpoint can be normalized")
    return NormalizedSegment(
        id=stable_id("nseg", segment.id),
        source_segment_id=segment.id,
        direction=segment.direction,
        low_ticks=segment.actual_low_ticks,
        high_ticks=segment.actual_high_ticks,
        structural_start_timestamp=segment.structural_start_timestamp,
        structural_end_timestamp=segment.structural_end_timestamp,
        confirmation_timestamp=segment.confirmation_timestamp,
        structural_start_ticks=segment.structural_start_ticks,
        structural_end_ticks=segment.structural_end_ticks,
    )


def _apply_previous_revision(segment: Segment, previous: dict[str, Segment]) -> tuple[Segment, SegmentRevision | None]:
    old = previous.get(segment.id)
    if old is None:
        return segment, None
    if old.state is SegmentState.FINALIZED:
        # Frozen history is immutable under ordinary forward updates.
        return old, None
    structural_changed = (
        old.state != segment.state
        or old.stroke_ids != segment.stroke_ids
        or old.structural_end_stroke_id != segment.structural_end_stroke_id
        or old.break_state != segment.break_state
        or old.confirmation_case != segment.confirmation_case
        or old.actual_low_ticks != segment.actual_low_ticks
        or old.actual_high_ticks != segment.actual_high_ticks
    )
    if not structural_changed:
        return replace(segment, revision=old.revision), None
    updated = replace(segment, revision=old.revision + 1)
    return updated, SegmentRevision(
        segment_id=segment.id,
        from_revision=old.revision,
        to_revision=updated.revision,
        reason="SEGMENT_STATE_UPDATED",
        confirmation_timestamp=updated.confirmation_timestamp,
    )


def build_segments(strokes: tuple[Stroke, ...], *, previous_segments: tuple[Segment, ...] = ()) -> SegmentOutput:
    if len(strokes) < 3:
        return SegmentOutput((), (), (), ValidationResult(True))

    previous = {s.id: s for s in previous_segments}
    segments: list[Segment] = []
    revisions: list[SegmentRevision] = []
    start = 0

    while start + 2 < len(strokes):
        seed = strokes[start:start + 3]
        seed_result = validate_segment_seed(seed)
        if not seed_result.valid:
            start += 1
            continue
        direction = strokes[start].direction
        raw = build_raw_feature_sequence(strokes, segment_direction=direction, start_index=start)
        std = standardize_feature_sequence(raw, fallback_direction=direction)

        case1 = _confirm_case1(strokes, start=start, direction=direction, standardized=std)
        if case1 is not None:
            endpoint, ff, through = case1
            segment = _make_finalized_segment(
                strokes, start=start, endpoint=endpoint, confirm_through=through,
                confirmation_timestamp=ff.confirmation_timestamp,
                confirmation_case=SegmentCase.CASE1,
                raw=raw, standardized=std.elements,
            )
            segment, revision = _apply_previous_revision(segment, previous)
            segments.append(segment)
            if revision:
                revisions.append(revision)
            start = endpoint + 1
            continue

        case2 = _first_case2_candidate(strokes, start=start, direction=direction, standardized=std)
        if case2 is not None:
            endpoint, ff, _ = case2
            second, second_ff, second_through = _case2_second_sequence(strokes, endpoint=endpoint, direction=direction)
            if second_ff is not None and second_through is not None:
                segment = _make_finalized_segment(
                    strokes, start=start, endpoint=endpoint, confirm_through=second_through,
                    confirmation_timestamp=second_ff.confirmation_timestamp,
                    confirmation_case=SegmentCase.CASE2,
                    raw=raw, standardized=std.elements, second=second,
                )
                segment, revision = _apply_previous_revision(segment, previous)
                segments.append(segment)
                if revision:
                    revisions.append(revision)
                start = endpoint + 1
                continue
            if _candidate_failed(strokes, endpoint, direction):
                segment = _make_active_segment(
                    strokes, start=start, state=SegmentState.ACTIVE,
                    break_state=SegmentBreakState.BREAK_FAILED,
                    confirmation_case=SegmentCase.CASE2,
                    raw=raw, standardized=std.elements, second=second,
                    issues=("CASE2_CONFIRMATION_FAILED",),
                )
            else:
                segment = _make_active_segment(
                    strokes, start=start, state=SegmentState.CASE2_PENDING,
                    break_state=SegmentBreakState.STROKE_BREAK_PENDING,
                    confirmation_case=SegmentCase.CASE2,
                    raw=raw, standardized=std.elements, second=second,
                )
            segment, revision = _apply_previous_revision(segment, previous)
            segments.append(segment)
            if revision:
                revisions.append(revision)
            break

        # Seed exists but no feature-fractal confirmation yet.
        any_nonfinal = any(s.state is not StructureState.FINALIZED for s in strokes[start:])
        state = SegmentState.SEED if len(strokes[start:]) == 3 else SegmentState.ACTIVE
        break_state = SegmentBreakState.NONE
        if len(strokes[start:]) >= 4:
            state = SegmentState.STROKE_BREAK_PENDING if any_nonfinal else SegmentState.ACTIVE
            break_state = SegmentBreakState.STROKE_BREAK_PENDING if any_nonfinal else SegmentBreakState.NONE
        segment = _make_active_segment(
            strokes, start=start, state=state, break_state=break_state,
            confirmation_case=SegmentCase.NONE, raw=raw, standardized=std.elements,
        )
        segment, revision = _apply_previous_revision(segment, previous)
        segments.append(segment)
        if revision:
            revisions.append(revision)
        break

    normalized = tuple(to_normalized_segment(s) for s in segments if s.state is SegmentState.FINALIZED)
    return SegmentOutput(tuple(segments), normalized, tuple(revisions), ValidationResult(True))
