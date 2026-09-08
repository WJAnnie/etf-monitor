from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from trading_skill.chan.feature_sequence import (
    FeatureElement,
    build_raw_feature_sequence,
    detect_feature_fractals,
    has_gap,
    standardize_feature_sequence,
)
from trading_skill.chan.segment import build_segments, to_normalized_segment, validate_segment_seed
from trading_skill.chan.stroke import Stroke
from trading_skill.domain.enums import (
    Direction,
    FeatureFractalType,
    SegmentBreakState,
    SegmentCase,
    SegmentState,
    StrokeMode,
    StrokePhase,
    StructureState,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def stroke(i: int, direction: Direction, start: int, end: int, *, finalized: bool = True) -> Stroke:
    return Stroke(
        id=f"s{i}",
        direction=direction,
        state=StructureState.FINALIZED if finalized else StructureState.ACTIVE,
        start_fractal_id=f"f{i}",
        end_fractal_id=f"f{i+1}",
        start_ticks=start,
        end_ticks=end,
        structural_start_timestamp=BASE + timedelta(minutes=i),
        structural_end_timestamp=BASE + timedelta(minutes=i + 1),
        confirmation_timestamp=BASE + timedelta(minutes=i + 2),
        phase=StrokePhase.EXTENDING,
        mode=StrokeMode.RELAXED_LATE,
    )


def case1_strokes(*, last_finalized: bool = True) -> tuple[Stroke, ...]:
    return (
        stroke(0, Direction.UP, 0, 10),
        stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 12),
        stroke(3, Direction.DOWN, 12, 8),
        stroke(4, Direction.UP, 8, 11),
        stroke(5, Direction.DOWN, 11, 6, finalized=last_finalized),
    )


def case2_strokes() -> tuple[Stroke, ...]:
    return (
        stroke(0, Direction.UP, 0, 10),
        stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 16),
        stroke(3, Direction.DOWN, 16, 12),
        stroke(4, Direction.UP, 12, 15),
        stroke(5, Direction.DOWN, 15, 8),
        stroke(6, Direction.UP, 8, 11),
        stroke(7, Direction.DOWN, 11, 9),
        stroke(8, Direction.UP, 9, 14),
    )


def feature(low: int, high: int, idx: int) -> FeatureElement:
    return FeatureElement(
        id=f"e{idx}", low_ticks=low, high_ticks=high,
        source_stroke_ids=(f"s{idx}",), source_stroke_indices=(idx,),
        structural_start_timestamp=BASE + timedelta(minutes=idx),
        structural_end_timestamp=BASE + timedelta(minutes=idx + 1),
        confirmation_timestamp=BASE + timedelta(minutes=idx + 2),
        is_complete=True,
    )


def test_seed_requires_three_strokes():
    result = validate_segment_seed(case1_strokes()[:2])
    assert not result.valid
    assert "INSUFFICIENT_STROKES" in result.reason_codes


def test_seed_requires_alternating_first_third_same_direction():
    bad = (
        stroke(0, Direction.UP, 0, 10),
        stroke(1, Direction.UP, 10, 12),
        stroke(2, Direction.DOWN, 12, 5),
    )
    assert not validate_segment_seed(bad).valid


def test_seed_requires_common_overlap_and_touch_is_valid():
    good = (
        stroke(0, Direction.UP, 0, 10),
        stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 12),
    )
    assert validate_segment_seed(good).valid
    bad = (
        stroke(0, Direction.UP, 0, 4),
        stroke(1, Direction.DOWN, 9, 6),
        stroke(2, Direction.UP, 10, 14),
    )
    assert not validate_segment_seed(bad).valid


def test_up_segment_feature_sequence_uses_down_strokes():
    raw = build_raw_feature_sequence(case1_strokes(), segment_direction=Direction.UP)
    assert [e.source_stroke_ids for e in raw] == [("s1",), ("s3",), ("s5",)]


def test_down_segment_feature_sequence_uses_up_strokes():
    raw = build_raw_feature_sequence(case1_strokes(), segment_direction=Direction.DOWN)
    assert [e.source_stroke_ids for e in raw] == [("s0",), ("s2",), ("s4",)]


def test_touching_feature_intervals_have_no_gap():
    assert has_gap(feature(5, 10, 0), feature(10, 12, 1)) is False
    assert has_gap(feature(5, 9, 0), feature(10, 12, 1)) is True


def test_feature_inclusion_is_recursive_and_preserves_sources():
    elems = (feature(5, 10, 0), feature(6, 9, 1), feature(7, 8, 2))
    std = standardize_feature_sequence(elems, fallback_direction=Direction.UP)
    assert len(std.elements) == 1
    assert set(std.elements[0].source_stroke_ids) == {"s0", "s1", "s2"}


def test_strict_feature_top_geometry():
    elems = (feature(5, 10, 0), feature(8, 12, 1), feature(6, 11, 2))
    ff = detect_feature_fractals(elems, target_type=FeatureFractalType.FEATURE_TOP)
    assert len(ff) == 1
    equal = (feature(5, 10, 0), feature(8, 12, 1), feature(8, 11, 2))
    assert detect_feature_fractals(equal, target_type=FeatureFractalType.FEATURE_TOP) == ()


def test_case1_finalizes_only_after_feature_confirmation():
    out = build_segments(case1_strokes())
    assert len(out.segments) >= 1
    seg = out.segments[0]
    assert seg.state is SegmentState.FINALIZED
    assert seg.confirmation_case is SegmentCase.CASE1
    assert seg.stroke_ids == ("s0", "s1", "s2")
    assert seg.structural_end_stroke_id == "s2"
    assert seg.structural_end_timestamp < seg.confirmation_timestamp


def test_case1_incomplete_right_feature_does_not_finalize():
    out = build_segments(case1_strokes(last_finalized=False))
    assert out.segments[0].state is not SegmentState.FINALIZED


def test_structural_endpoint_and_actual_envelope_are_distinct():
    out = build_segments(case1_strokes())
    seg = out.segments[0]
    assert seg.structural_end_ticks == 12
    assert seg.actual_low_ticks == 0
    assert seg.actual_high_ticks == 12
    assert seg.confirmation_timestamp == case1_strokes()[5].confirmation_timestamp


def test_case2_requires_second_feature_sequence_confirmation():
    out = build_segments(case2_strokes())
    seg = out.segments[0]
    assert seg.state is SegmentState.FINALIZED
    assert seg.confirmation_case is SegmentCase.CASE2
    assert seg.stroke_ids == ("s0", "s1", "s2")
    assert len(seg.second_feature_sequence) >= 3


def test_case2_does_not_require_filling_original_gap():
    seq = case2_strokes()
    raw = build_raw_feature_sequence(seq, segment_direction=Direction.UP)
    assert has_gap(raw[0], raw[1])
    seg = build_segments(seq).segments[0]
    assert seg.state is SegmentState.FINALIZED
    assert seg.confirmation_case is SegmentCase.CASE2


def test_case2_candidate_can_fail_and_original_segment_resume_active():
    seq = (
        stroke(0, Direction.UP, 0, 10),
        stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 16),
        stroke(3, Direction.DOWN, 16, 12),
        stroke(4, Direction.UP, 12, 15),
        stroke(5, Direction.DOWN, 15, 8),
        stroke(6, Direction.UP, 8, 17),
    )
    seg = build_segments(seq).segments[0]
    assert seg.state is SegmentState.ACTIVE
    assert seg.break_state is SegmentBreakState.BREAK_FAILED
    assert "CASE2_CONFIRMATION_FAILED" in seg.issues


def test_one_reverse_stroke_does_not_finalize_segment():
    seq = (
        stroke(0, Direction.UP, 0, 10),
        stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 12),
        stroke(3, Direction.DOWN, 12, 8, finalized=False),
    )
    seg = build_segments(seq).segments[0]
    assert seg.state is SegmentState.STROKE_BREAK_PENDING
    assert seg.state is not SegmentState.FINALIZED


def test_normalized_segment_only_from_finalized():
    seg = build_segments(case1_strokes()).segments[0]
    norm = to_normalized_segment(seg)
    assert norm.source_segment_id == seg.id
    seed = build_segments(case1_strokes()[:3]).segments[0]
    with pytest.raises(ValueError):
        to_normalized_segment(seed)


def test_active_segment_keeps_id_and_increments_revision_on_update():
    first = build_segments(case1_strokes()[:4]).segments[0]
    second_out = build_segments(case1_strokes()[:5], previous_segments=(first,))
    second = second_out.segments[0]
    assert second.id == first.id
    assert second.revision == first.revision + 1
    assert len(second_out.revisions) == 1


def test_finalized_segment_is_immutable_under_future_updates():
    final = build_segments(case1_strokes()).segments[0]
    later = case1_strokes() + (
        stroke(6, Direction.UP, 6, 20),
        stroke(7, Direction.DOWN, 20, 7),
    )
    out = build_segments(later, previous_segments=(final,))
    assert out.segments[0] == final
