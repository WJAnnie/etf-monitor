from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_skill.chan.segment import build_segments
from trading_skill.chan.stroke import Stroke
from trading_skill.domain.enums import Direction, SegmentCase, SegmentState, StrokeMode, StrokePhase, StructureState

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def stroke(i, direction, start, end, *, finalized=True):
    return Stroke(
        id=f"s{i}", direction=direction,
        state=StructureState.FINALIZED if finalized else StructureState.ACTIVE,
        start_fractal_id=f"f{i}", end_fractal_id=f"f{i+1}",
        start_ticks=start, end_ticks=end,
        structural_start_timestamp=BASE + timedelta(minutes=i),
        structural_end_timestamp=BASE + timedelta(minutes=i + 1),
        confirmation_timestamp=BASE + timedelta(minutes=i + 2),
        phase=StrokePhase.EXTENDING, mode=StrokeMode.RELAXED_LATE,
    )


def case1():
    return (
        stroke(0, Direction.UP, 0, 10), stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 12), stroke(3, Direction.DOWN, 12, 8),
        stroke(4, Direction.UP, 8, 11), stroke(5, Direction.DOWN, 11, 6),
    )


def case2():
    return (
        stroke(0, Direction.UP, 0, 10), stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 16), stroke(3, Direction.DOWN, 16, 12),
        stroke(4, Direction.UP, 12, 15), stroke(5, Direction.DOWN, 15, 8),
        stroke(6, Direction.UP, 8, 11), stroke(7, Direction.DOWN, 11, 9),
        stroke(8, Direction.UP, 9, 14),
    )


def test_gold_gs004_case1():
    seg = build_segments(case1()).segments[0]
    assert (seg.state, seg.confirmation_case, seg.structural_end_stroke_id) == (
        SegmentState.FINALIZED, SegmentCase.CASE1, "s2"
    )
    assert seg.structural_end_timestamp < seg.confirmation_timestamp


def test_gold_gs005_case2():
    seg = build_segments(case2()).segments[0]
    assert (seg.state, seg.confirmation_case) == (SegmentState.FINALIZED, SegmentCase.CASE2)
    assert len(seg.second_feature_sequence) >= 3


def test_gold_gs006_stroke_break_without_segment_break():
    seq = (
        stroke(0, Direction.UP, 0, 10), stroke(1, Direction.DOWN, 10, 5),
        stroke(2, Direction.UP, 5, 12), stroke(3, Direction.DOWN, 12, 8, finalized=False),
    )
    seg = build_segments(seq).segments[0]
    assert seg.state is SegmentState.STROKE_BREAK_PENDING
    assert seg.structural_end_stroke_id is None
