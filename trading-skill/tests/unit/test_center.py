from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_skill.chan.center import (
    CenterMotion,
    CompletedMotion,
    build_center_stack,
    build_recursive_center,
    classify_center_relation,
    extend_center,
    motion_leaves_core,
    motion_overlaps_core,
    register_first_return,
    register_leave,
    seed_center,
)
from trading_skill.domain.enums import (
    CenterEventType,
    CenterRelationType,
    CenterState,
    Direction,
    Timeframe,
)

BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def motion(i, direction, low, high, *, level=0, complete=True, tf=Timeframe.DAILY):
    return CenterMotion(
        id=f"m{i}", source_timeframe=tf, level_rank=level, direction=direction,
        low_ticks=low, high_ticks=high,
        structural_start_timestamp=BASE+timedelta(days=i),
        structural_end_timestamp=BASE+timedelta(days=i+1),
        confirmation_timestamp=BASE+timedelta(days=i+1, minutes=1),
        completed=complete,
    )


def seed_triplet(offset=0, *, shift=0, level=0, tf=Timeframe.DAILY):
    return (
        motion(offset, Direction.UP, 5+shift, 12+shift, level=level, tf=tf),
        motion(offset+1, Direction.DOWN, 7+shift, 11+shift, level=level, tf=tf),
        motion(offset+2, Direction.UP, 6+shift, 13+shift, level=level, tf=tf),
    )


def center_from(offset=0, shift=0, *, level=0):
    return seed_center(seed_triplet(offset, shift=shift, level=level), symbol="TEST").center


def test_center_seed_formula():
    c = center_from()
    assert c.state is CenterState.CONFIRMED
    assert c.zd_ticks == 7
    assert c.zg_ticks == 11
    assert c.dd_ticks == 5
    assert c.gg_ticks == 13


def test_center_seed_touching_overlap_is_valid():
    ms = (
        motion(0, Direction.UP, 0, 10),
        motion(1, Direction.DOWN, 10, 15),
        motion(2, Direction.UP, 5, 10),
    )
    c = seed_center(ms, symbol="TEST")
    assert c.result.valid
    assert c.center.zd_ticks == c.center.zg_ticks == 10


def test_center_seed_no_common_overlap_rejected():
    ms = (
        motion(0, Direction.UP, 0, 4),
        motion(1, Direction.DOWN, 6, 9),
        motion(2, Direction.UP, 10, 14),
    )
    assert "NO_COMMON_OVERLAP" in seed_center(ms, symbol="TEST").result.reason_codes


def test_forming_third_motion_blocks_confirmation():
    ms = list(seed_triplet())
    ms[2] = motion(2, Direction.UP, 6, 13, complete=False)
    result = seed_center(tuple(ms), symbol="TEST")
    assert not result.result.valid
    assert result.center.state is CenterState.FORMING


def test_extension_keeps_fixed_core_when_motion_ends_inside_core():
    c = center_from()
    candidate = motion(3, Direction.DOWN, 8, 12)
    assert motion_overlaps_core(c, candidate)
    assert not motion_leaves_core(c, candidate)
    updated = extend_center(c, candidate).center
    assert updated.state is CenterState.EXTENDING
    assert (updated.zd_ticks, updated.zg_ticks) == (c.zd_ticks, c.zg_ticks)
    assert updated.dd_ticks == 5
    assert updated.extension_count == 1
    assert updated.id == c.id


def test_motion_can_overlap_geometry_but_structurally_leave():
    c = center_from()
    leaving_up = motion(3, Direction.UP, 8, 15)
    leaving_down = motion(4, Direction.DOWN, 4, 10)
    assert motion_overlaps_core(c, leaving_up)
    assert motion_overlaps_core(c, leaving_down)
    assert motion_leaves_core(c, leaving_up)
    assert motion_leaves_core(c, leaving_down)
    assert not extend_center(c, leaving_up).result.valid
    assert not extend_center(c, leaving_down).result.valid
    assert register_leave(c, leaving_up).center.state is CenterState.LEAVING_UP
    assert register_leave(c, leaving_down).center.state is CenterState.LEAVING_DOWN


def test_repeated_extension_never_upgrades_level():
    c = center_from()
    c2 = extend_center(c, motion(3, Direction.DOWN, 8, 12)).center
    c3 = extend_center(c2, motion(4, Direction.UP, 8, 11)).center
    assert c3.level_rank == c.level_rank
    assert (c3.zd_ticks, c3.zg_ticks) == (7, 11)


def test_leave_up_is_not_immediate_destruction():
    c = center_from()
    left = register_leave(c, motion(3, Direction.UP, 8, 18)).center
    assert left.state is CenterState.LEAVING_UP
    assert left.state is not CenterState.DESTROYED


def test_leave_down_is_not_immediate_destruction():
    c = center_from()
    left = register_leave(c, motion(3, Direction.DOWN, 0, 10)).center
    assert left.state is CenterState.LEAVING_DOWN


def test_up_leave_first_return_outside_emits_foundation_event():
    c = register_leave(center_from(), motion(3, Direction.UP, 8, 18)).center
    result = register_first_return(c, motion(4, Direction.DOWN, 12, 16))
    assert result.center.state is CenterState.DESTROYED
    assert result.center.return_sequence_number == 1
    assert result.events[0].type is CenterEventType.CENTER_UP_BREAK_RETURN_OUTSIDE


def test_down_leave_first_return_outside_emits_foundation_event():
    c = register_leave(center_from(), motion(3, Direction.DOWN, 0, 10)).center
    result = register_first_return(c, motion(4, Direction.UP, 1, 6))
    assert result.center.state is CenterState.DESTROYED
    assert result.events[0].type is CenterEventType.CENTER_DOWN_BREAK_RETURN_OUTSIDE


def test_return_reentering_center_is_returning_not_third_buy():
    c = register_leave(center_from(), motion(3, Direction.UP, 8, 18)).center
    result = register_first_return(c, motion(4, Direction.DOWN, 9, 15))
    assert result.center.state is CenterState.RETURNING
    assert result.events == ()


def test_returning_motion_can_cross_core_and_still_extend_original_center():
    c = register_leave(center_from(), motion(3, Direction.UP, 8, 18)).center
    returned = register_first_return(c, motion(4, Direction.DOWN, 4, 15)).center
    assert returned.state is CenterState.RETURNING
    extended = extend_center(returned, motion(4, Direction.DOWN, 4, 15))
    assert extended.result.valid
    assert extended.center.state is CenterState.EXTENDING
    assert extended.center.dd_ticks == 4


def test_independent_up_center_relation():
    a = center_from(0, 0)
    b = center_from(10, 20)
    assert classify_center_relation(a, b).type is CenterRelationType.INDEPENDENT_UP_NEW_CENTER


def test_independent_down_center_relation():
    a = center_from(0, 20)
    b = center_from(10, 0)
    assert classify_center_relation(a, b).type is CenterRelationType.INDEPENDENT_DOWN_NEW_CENTER


def test_expansion_pending_when_cores_separate_but_envelopes_overlap():
    a = center_from()
    b_ms = (
        motion(10, Direction.UP, 10, 17),
        motion(11, Direction.DOWN, 12, 16),
        motion(12, Direction.UP, 11, 18),
    )
    b = seed_center(b_ms, symbol="TEST").center
    rel = classify_center_relation(a, b)
    assert rel.type is CenterRelationType.EXPANSION_PENDING


def test_overlapping_core_relation_is_same_center_extension():
    a = center_from()
    b = center_from(10, 2)
    assert classify_center_relation(a, b).type is CenterRelationType.SAME_CENTER_EXTENSION


def test_level_mismatch_relation_unresolved():
    a = center_from(level=0)
    b = center_from(10, 0, level=1)
    rel = classify_center_relation(a, b)
    assert rel.type is CenterRelationType.UNRESOLVED
    assert "CENTER_LEVEL_MISMATCH" in rel.reason_codes


def completed(i, direction, low, high, *, level=1, complete=True):
    return CompletedMotion(
        id=f"t{i}", source_timeframe=Timeframe.DAILY, level_rank=level, direction=direction,
        low_ticks=low, high_ticks=high,
        structural_start_timestamp=BASE+timedelta(days=i),
        structural_end_timestamp=BASE+timedelta(days=i+1),
        confirmation_timestamp=BASE+timedelta(days=i+1, minutes=2), completed=complete,
    )


def test_recursive_center_uses_completed_trendtype_compatible_motions():
    motions = (
        completed(0, Direction.UP, 20, 30),
        completed(1, Direction.DOWN, 22, 28),
        completed(2, Direction.UP, 21, 31),
    )
    c = build_recursive_center(motions, symbol="TEST")
    assert c.result.valid
    assert c.center.level_rank == 2
    assert c.center.base_engine_center is False
    assert c.center.source_timeframe is Timeframe.DAILY


def test_recursive_center_forming_third_blocks_confirmation():
    motions = (
        completed(0, Direction.UP, 20, 30),
        completed(1, Direction.DOWN, 22, 28),
        completed(2, Direction.UP, 21, 31, complete=False),
    )
    result = build_recursive_center(motions, symbol="TEST")
    assert not result.result.valid


def test_three_center_objects_are_not_recursive_input():
    centers = (center_from(0), center_from(10, 2), center_from(20, 4))
    assert all(hasattr(c, "zd_ticks") for c in centers)
    assert not all(isinstance(c, CompletedMotion) for c in centers)


def test_center_stack_keeps_multiple_levels_and_timeframe():
    low = center_from()
    higher = build_recursive_center((
        completed(0, Direction.UP, 20, 30),
        completed(1, Direction.DOWN, 22, 28),
        completed(2, Direction.UP, 21, 31),
    ), symbol="TEST").center
    stack = build_center_stack(Timeframe.DAILY, (low, higher), ((low.id, higher.id),))
    assert len(stack.centers) == 2
    assert all(c.source_timeframe is Timeframe.DAILY for c in stack.centers)
    assert {c.level_rank for c in stack.centers} == {1, 2}


def test_return_direction_must_be_opposite_leave():
    c = register_leave(center_from(), motion(3, Direction.UP, 8, 18)).center
    result = register_first_return(c, motion(4, Direction.UP, 12, 20))
    assert not result.result.valid
    assert "RETURN_DIRECTION_MISMATCH" in result.result.reason_codes


def test_center_stack_rejects_indirect_cycle():
    a = center_from(0)
    b = center_from(10, 20)
    c = center_from(20, 40)
    try:
        build_center_stack(Timeframe.DAILY, (a, b, c), ((a.id, b.id), (b.id, c.id), (c.id, a.id)))
    except ValueError as exc:
        assert str(exc) == "CENTER_STACK_CYCLE"
    else:
        raise AssertionError("cycle must be rejected")
