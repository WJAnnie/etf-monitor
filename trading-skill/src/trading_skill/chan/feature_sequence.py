from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Iterable

from trading_skill.chan.stroke import Stroke
from trading_skill.domain.enums import Direction, FeatureFractalType, StructureState
from trading_skill.domain.models import stable_id, unique_preserve_order


@dataclass(frozen=True, slots=True)
class FeatureElement:
    id: str
    low_ticks: int
    high_ticks: int
    source_stroke_ids: tuple[str, ...]
    source_stroke_indices: tuple[int, ...]
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    is_complete: bool
    revision: int = 1


@dataclass(frozen=True, slots=True)
class FeatureFractal:
    id: str
    type: FeatureFractalType
    state: StructureState
    left_element_id: str
    middle_element_id: str
    right_element_id: str
    middle_index: int
    structural_timestamp: datetime
    confirmation_timestamp: datetime


@dataclass(frozen=True, slots=True)
class StandardFeatureSequence:
    direction_context: Direction
    raw_elements: tuple[FeatureElement, ...]
    elements: tuple[FeatureElement, ...]


def _interval(stroke: Stroke) -> tuple[int, int]:
    return min(stroke.start_ticks, stroke.end_ticks), max(stroke.start_ticks, stroke.end_ticks)


def build_raw_feature_sequence(strokes: tuple[Stroke, ...], *, segment_direction: Direction,
                               start_index: int = 0) -> tuple[FeatureElement, ...]:
    target = Direction.DOWN if segment_direction is Direction.UP else Direction.UP
    elements: list[FeatureElement] = []
    for idx in range(start_index, len(strokes)):
        stroke = strokes[idx]
        if stroke.direction is not target:
            continue
        low, high = _interval(stroke)
        elements.append(FeatureElement(
            id=stable_id("fe", stroke.id),
            low_ticks=low,
            high_ticks=high,
            source_stroke_ids=(stroke.id,),
            source_stroke_indices=(idx,),
            structural_start_timestamp=stroke.structural_start_timestamp,
            structural_end_timestamp=stroke.structural_end_timestamp,
            confirmation_timestamp=stroke.confirmation_timestamp,
            is_complete=stroke.state is StructureState.FINALIZED,
        ))
    return tuple(elements)


def has_gap(a: FeatureElement, b: FeatureElement) -> bool:
    # Touching at one tick boundary is overlap, not a gap.
    return a.high_ticks < b.low_ticks or b.high_ticks < a.low_ticks


def _inclusive(a: FeatureElement, b: FeatureElement) -> bool:
    return ((a.low_ticks <= b.low_ticks and a.high_ticks >= b.high_ticks)
            or (b.low_ticks <= a.low_ticks and b.high_ticks >= a.high_ticks))


def _move_direction(a: FeatureElement, b: FeatureElement) -> Direction:
    if b.high_ticks > a.high_ticks and b.low_ticks > a.low_ticks:
        return Direction.UP
    if b.high_ticks < a.high_ticks and b.low_ticks < a.low_ticks:
        return Direction.DOWN
    return Direction.UNRESOLVED


def _initial_direction(elements: tuple[FeatureElement, ...], fallback: Direction) -> Direction:
    for a, b in zip(elements, elements[1:]):
        if _inclusive(a, b):
            continue
        direction = _move_direction(a, b)
        if direction is not Direction.UNRESOLVED:
            return direction
    return fallback


def _merge(a: FeatureElement, b: FeatureElement, direction: Direction) -> FeatureElement:
    if direction is Direction.UP:
        low = max(a.low_ticks, b.low_ticks)
        high = max(a.high_ticks, b.high_ticks)
    elif direction is Direction.DOWN:
        low = min(a.low_ticks, b.low_ticks)
        high = min(a.high_ticks, b.high_ticks)
    else:
        raise ValueError("feature inclusion direction unresolved")
    return FeatureElement(
        id=stable_id("fe", *unique_preserve_order(a.source_stroke_ids + b.source_stroke_ids), direction),
        low_ticks=low,
        high_ticks=high,
        source_stroke_ids=unique_preserve_order(a.source_stroke_ids + b.source_stroke_ids),
        source_stroke_indices=tuple(sorted(set(a.source_stroke_indices + b.source_stroke_indices))),
        structural_start_timestamp=min(a.structural_start_timestamp, b.structural_start_timestamp),
        structural_end_timestamp=max(a.structural_end_timestamp, b.structural_end_timestamp),
        confirmation_timestamp=max(a.confirmation_timestamp, b.confirmation_timestamp),
        is_complete=a.is_complete and b.is_complete,
        revision=max(a.revision, b.revision) + 1,
    )


def standardize_feature_sequence(elements: tuple[FeatureElement, ...], *,
                                 fallback_direction: Direction) -> StandardFeatureSequence:
    if not elements:
        return StandardFeatureSequence(fallback_direction, (), ())
    direction = _initial_direction(elements, fallback_direction)
    out: list[FeatureElement] = []
    for element in elements:
        if not out:
            out.append(element)
            continue
        current = element
        while out and _inclusive(out[-1], current):
            if direction is Direction.UNRESOLVED:
                direction = fallback_direction
            current = _merge(out.pop(), current, direction)
        if out:
            new_direction = _move_direction(out[-1], current)
            if new_direction is not Direction.UNRESOLVED:
                direction = new_direction
        out.append(current)
    return StandardFeatureSequence(direction, elements, tuple(out))


def detect_feature_fractals(elements: tuple[FeatureElement, ...], *,
                            target_type: FeatureFractalType) -> tuple[FeatureFractal, ...]:
    results: list[FeatureFractal] = []
    for i in range(1, len(elements) - 1):
        left, middle, right = elements[i - 1], elements[i], elements[i + 1]
        if not right.is_complete:
            continue
        top = (middle.high_ticks > left.high_ticks and middle.high_ticks > right.high_ticks
               and middle.low_ticks > left.low_ticks and middle.low_ticks > right.low_ticks)
        bottom = (middle.high_ticks < left.high_ticks and middle.high_ticks < right.high_ticks
                  and middle.low_ticks < left.low_ticks and middle.low_ticks < right.low_ticks)
        kind = None
        if top:
            kind = FeatureFractalType.FEATURE_TOP
        elif bottom:
            kind = FeatureFractalType.FEATURE_BOTTOM
        if kind is not target_type:
            continue
        results.append(FeatureFractal(
            id=stable_id("ff", kind, left.id, middle.id, right.id),
            type=kind,
            state=StructureState.CONFIRMED,
            left_element_id=left.id,
            middle_element_id=middle.id,
            right_element_id=right.id,
            middle_index=i,
            structural_timestamp=middle.structural_start_timestamp,
            confirmation_timestamp=max(left.confirmation_timestamp, middle.confirmation_timestamp,
                                       right.confirmation_timestamp),
        ))
    return tuple(results)


def element_by_id(elements: Iterable[FeatureElement]) -> dict[str, FeatureElement]:
    return {e.id: e for e in elements}
