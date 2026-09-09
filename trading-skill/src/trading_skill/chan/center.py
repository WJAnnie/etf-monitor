from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone

from trading_skill.chan.segment import NormalizedSegment
from trading_skill.domain.enums import (
    CenterEventType,
    CenterRelationType,
    CenterState,
    Direction,
    Timeframe,
)
from trading_skill.domain.models import ValidationResult, stable_id


@dataclass(frozen=True, slots=True)
class CenterMotion:
    id: str
    source_timeframe: Timeframe
    level_rank: int
    direction: Direction
    low_ticks: int
    high_ticks: int
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    completed: bool = True
    source_object_id: str | None = None

    @property
    def structural_end_ticks(self) -> int:
        """运动结构终点。

        当前基础 motion 由已完成线段归一化而来；向上线段的结构终点取上端，向下取下端。
        后续若底层线段暴露显式 endpoint，可在这里无缝替换而不改中枢生命周期。
        """
        if self.direction is Direction.UP:
            return self.high_ticks
        if self.direction is Direction.DOWN:
            return self.low_ticks
        return self.high_ticks

    @classmethod
    def from_segment(
        cls, segment: NormalizedSegment, *, source_timeframe: Timeframe, level_rank: int = 0
    ) -> "CenterMotion":
        return cls(
            id=stable_id("cm", segment.id, source_timeframe, level_rank),
            source_timeframe=source_timeframe,
            level_rank=level_rank,
            direction=segment.direction,
            low_ticks=segment.low_ticks,
            high_ticks=segment.high_ticks,
            structural_start_timestamp=segment.structural_start_timestamp,
            structural_end_timestamp=segment.structural_end_timestamp,
            confirmation_timestamp=segment.confirmation_timestamp,
            completed=True,
            source_object_id=segment.id,
        )


@dataclass(frozen=True, slots=True)
class CompletedMotion:
    id: str
    source_timeframe: Timeframe
    level_rank: int
    direction: Direction
    low_ticks: int
    high_ticks: int
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    completed: bool = True
    source_object_type: str = "TrendTypeCompatible"

    def as_center_motion(self) -> CenterMotion:
        return CenterMotion(
            id=self.id,
            source_timeframe=self.source_timeframe,
            level_rank=self.level_rank,
            direction=self.direction,
            low_ticks=self.low_ticks,
            high_ticks=self.high_ticks,
            structural_start_timestamp=self.structural_start_timestamp,
            structural_end_timestamp=self.structural_end_timestamp,
            confirmation_timestamp=self.confirmation_timestamp,
            completed=self.completed,
            source_object_id=self.id,
        )


@dataclass(frozen=True, slots=True)
class CenterEvent:
    id: str
    center_id: str
    type: CenterEventType
    leave_motion_id: str
    return_motion_id: str
    confirmation_timestamp: datetime


@dataclass(frozen=True, slots=True)
class Center:
    id: str
    symbol: str
    source_timeframe: Timeframe
    level_rank: int
    state: CenterState
    seed_motion_ids: tuple[str, str, str]
    motion_ids: tuple[str, ...]
    zd_ticks: int
    zg_ticks: int
    dd_ticks: int
    gg_ticks: int
    d_ticks: int
    g_ticks: int
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    extension_count: int = 0
    leave_motion_id: str | None = None
    return_motion_id: str | None = None
    return_sequence_number: int = 0
    base_engine_center: bool = False
    parent_center_id: str | None = None
    revision: int = 1
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CenterRelation:
    first_center_id: str
    second_center_id: str
    type: CenterRelationType
    level_rank: int
    source_timeframe: Timeframe
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class CenterStack:
    source_timeframe: Timeframe
    centers: tuple[Center, ...]
    parent_links: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class CenterUpdate:
    center: Center
    events: tuple[CenterEvent, ...] = ()
    result: ValidationResult = ValidationResult(True)


def _validate_seed_motions(motions: tuple[CenterMotion, ...]) -> ValidationResult:
    if len(motions) != 3:
        return ValidationResult(False, ("INSUFFICIENT_MOTIONS",))
    if not all(m.completed for m in motions):
        return ValidationResult(False, ("MOTION_NOT_FINALIZED",))
    if len({m.source_timeframe for m in motions}) != 1:
        return ValidationResult(False, ("CENTER_TIMEFRAME_MISMATCH",))
    if len({m.level_rank for m in motions}) != 1:
        return ValidationResult(False, ("CENTER_LEVEL_MISMATCH",))
    if not (motions[0].direction is motions[2].direction and motions[0].direction is not motions[1].direction):
        return ValidationResult(False, ("NON_CONTIGUOUS_MOTIONS",))
    return ValidationResult(True)


def seed_center(
    motions: tuple[CenterMotion, ...], *, symbol: str, target_level_rank: int | None = None,
    base_engine_center: bool = False,
) -> CenterUpdate:
    validation = _validate_seed_motions(motions)
    if not validation.valid:
        return CenterUpdate(
            center=_unresolved_center(motions, symbol=symbol, target_level_rank=target_level_rank or 0),
            result=validation,
        )
    lows = [m.low_ticks for m in motions]
    highs = [m.high_ticks for m in motions]
    zd = max(lows)
    zg = min(highs)
    if zd > zg:
        return CenterUpdate(
            center=_unresolved_center(motions, symbol=symbol, target_level_rank=target_level_rank or motions[0].level_rank + 1),
            result=ValidationResult(False, ("NO_COMMON_OVERLAP",)),
        )
    level = target_level_rank if target_level_rank is not None else motions[0].level_rank + 1
    dd = min(lows)
    gg = max(highs)
    cid = stable_id("ctr", symbol, motions[0].source_timeframe, level, *(m.id for m in motions))
    center = Center(
        id=cid,
        symbol=symbol,
        source_timeframe=motions[0].source_timeframe,
        level_rank=level,
        state=CenterState.CONFIRMED,
        seed_motion_ids=(motions[0].id, motions[1].id, motions[2].id),
        motion_ids=tuple(m.id for m in motions),
        zd_ticks=zd,
        zg_ticks=zg,
        dd_ticks=dd,
        gg_ticks=gg,
        d_ticks=dd,
        g_ticks=gg,
        structural_start_timestamp=motions[0].structural_start_timestamp,
        structural_end_timestamp=motions[-1].structural_end_timestamp,
        confirmation_timestamp=motions[-1].confirmation_timestamp,
        base_engine_center=base_engine_center,
    )
    return CenterUpdate(center)


def _unresolved_center(motions: tuple[CenterMotion, ...], *, symbol: str, target_level_rank: int) -> Center:
    tf = motions[0].source_timeframe if motions else Timeframe.DAILY
    now = motions[-1].confirmation_timestamp if motions else datetime.min.replace(tzinfo=timezone.utc)
    ids = tuple(m.id for m in motions[:3])
    padded = (ids + ("", "", ""))[:3]
    return Center(
        id=stable_id("ctr_unresolved", symbol, tf, target_level_rank, *ids),
        symbol=symbol,
        source_timeframe=tf,
        level_rank=target_level_rank,
        state=CenterState.FORMING,
        seed_motion_ids=(padded[0], padded[1], padded[2]),
        motion_ids=tuple(m.id for m in motions),
        zd_ticks=0,
        zg_ticks=0,
        dd_ticks=0,
        gg_ticks=0,
        d_ticks=0,
        g_ticks=0,
        structural_start_timestamp=motions[0].structural_start_timestamp if motions else now,
        structural_end_timestamp=motions[-1].structural_end_timestamp if motions else now,
        confirmation_timestamp=now,
    )


def motion_leaves_core(center: Center, motion: CenterMotion) -> bool:
    """结构事实：完成运动的终点是否有效离开中枢核心区。"""
    if not motion.completed:
        return False
    if motion.direction is Direction.UP:
        return motion.structural_end_ticks > center.zg_ticks
    if motion.direction is Direction.DOWN:
        return motion.structural_end_ticks < center.zd_ticks
    return False


def motion_overlaps_core(center: Center, motion: CenterMotion) -> bool:
    """几何事实：运动包络是否与中枢核心区有交集。

    overlap 与 leave 并不互斥：一段走势可以从中枢内部出发、穿过核心区并最终离开。
    生命周期调用者必须先判断 leave，再决定是否按 extension 处理。
    """
    return motion.low_ticks <= center.zg_ticks and motion.high_ticks >= center.zd_ticks


def extend_center(center: Center, motion: CenterMotion) -> CenterUpdate:
    if not motion.completed:
        return CenterUpdate(center, result=ValidationResult(False, ("MOTION_NOT_FINALIZED",)))
    if motion.source_timeframe is not center.source_timeframe:
        return CenterUpdate(center, result=ValidationResult(False, ("CENTER_TIMEFRAME_MISMATCH",)))
    if motion.level_rank + 1 != center.level_rank:
        return CenterUpdate(center, result=ValidationResult(False, ("CENTER_LEVEL_MISMATCH",)))
    if not motion_overlaps_core(center, motion):
        return CenterUpdate(center, result=ValidationResult(False, ("CENTER_STILL_LEAVING",)))
    # 正常确认/延伸阶段，终点已经离开就不能再把它记作中枢延伸。
    # RETURNING 是例外：回试已经被确认重新进入中枢，即使该完成运动进一步穿越到另一侧，仍属于原中枢的回归/延伸处理。
    if center.state is not CenterState.RETURNING and motion_leaves_core(center, motion):
        return CenterUpdate(center, result=ValidationResult(False, ("CENTER_STILL_LEAVING",)))
    updated = replace(
        center,
        state=CenterState.EXTENDING,
        motion_ids=center.motion_ids + (motion.id,),
        leave_motion_id=None if center.state is CenterState.RETURNING else center.leave_motion_id,
        return_motion_id=None if center.state is CenterState.RETURNING else center.return_motion_id,
        return_sequence_number=0 if center.state is CenterState.RETURNING else center.return_sequence_number,
        dd_ticks=min(center.dd_ticks, motion.low_ticks),
        gg_ticks=max(center.gg_ticks, motion.high_ticks),
        d_ticks=min(center.d_ticks, motion.low_ticks),
        g_ticks=max(center.g_ticks, motion.high_ticks),
        structural_end_timestamp=motion.structural_end_timestamp,
        confirmation_timestamp=motion.confirmation_timestamp,
        extension_count=center.extension_count + 1,
        revision=center.revision + 1,
    )
    return CenterUpdate(updated)


def register_leave(center: Center, motion: CenterMotion) -> CenterUpdate:
    if not motion.completed:
        return CenterUpdate(center, result=ValidationResult(False, ("LEAVE_NOT_CONFIRMED",)))
    if motion.source_timeframe is not center.source_timeframe:
        return CenterUpdate(center, result=ValidationResult(False, ("CENTER_TIMEFRAME_MISMATCH",)))
    if motion.level_rank + 1 != center.level_rank:
        return CenterUpdate(center, result=ValidationResult(False, ("CENTER_LEVEL_MISMATCH",)))
    if not motion_leaves_core(center, motion):
        return CenterUpdate(center, result=ValidationResult(False, ("MOTION_NOT_OUTSIDE_CENTER",)))
    state = CenterState.LEAVING_UP if motion.direction is Direction.UP else CenterState.LEAVING_DOWN
    return CenterUpdate(replace(
        center,
        state=state,
        leave_motion_id=motion.id,
        return_motion_id=None,
        return_sequence_number=0,
        structural_end_timestamp=motion.structural_end_timestamp,
        confirmation_timestamp=motion.confirmation_timestamp,
        revision=center.revision + 1,
    ))


def register_first_return(center: Center, motion: CenterMotion) -> CenterUpdate:
    if center.state not in (CenterState.LEAVING_UP, CenterState.LEAVING_DOWN):
        return CenterUpdate(center, result=ValidationResult(False, ("LEAVE_NOT_CONFIRMED",)))
    if not motion.completed:
        return CenterUpdate(center, result=ValidationResult(False, ("RETURN_NOT_CONFIRMED",)))
    events: list[CenterEvent] = []
    if center.state is CenterState.LEAVING_UP:
        if motion.direction is not Direction.DOWN:
            return CenterUpdate(center, result=ValidationResult(False, ("RETURN_DIRECTION_MISMATCH",)))
        if motion.low_ticks > center.zg_ticks:
            event_type = CenterEventType.CENTER_UP_BREAK_RETURN_OUTSIDE
            new_state = CenterState.DESTROYED
        else:
            event_type = None
            new_state = CenterState.RETURNING
    else:
        if motion.direction is not Direction.UP:
            return CenterUpdate(center, result=ValidationResult(False, ("RETURN_DIRECTION_MISMATCH",)))
        if motion.high_ticks < center.zd_ticks:
            event_type = CenterEventType.CENTER_DOWN_BREAK_RETURN_OUTSIDE
            new_state = CenterState.DESTROYED
        else:
            event_type = None
            new_state = CenterState.RETURNING
    if event_type is not None:
        events.append(CenterEvent(
            id=stable_id("ctrevt", center.id, event_type, center.leave_motion_id, motion.id),
            center_id=center.id,
            type=event_type,
            leave_motion_id=center.leave_motion_id or "",
            return_motion_id=motion.id,
            confirmation_timestamp=motion.confirmation_timestamp,
        ))
    updated = replace(
        center,
        state=new_state,
        return_motion_id=motion.id,
        return_sequence_number=1,
        structural_end_timestamp=motion.structural_end_timestamp,
        confirmation_timestamp=motion.confirmation_timestamp,
        revision=center.revision + 1,
    )
    return CenterUpdate(updated, tuple(events))


def classify_center_relation(first: Center, second: Center) -> CenterRelation:
    if first.source_timeframe is not second.source_timeframe:
        return CenterRelation(first.id, second.id, CenterRelationType.UNRESOLVED, first.level_rank,
                              first.source_timeframe, ("CENTER_TIMEFRAME_MISMATCH",))
    if first.level_rank != second.level_rank:
        return CenterRelation(first.id, second.id, CenterRelationType.UNRESOLVED, first.level_rank,
                              first.source_timeframe, ("CENTER_LEVEL_MISMATCH",))
    if second.dd_ticks > first.gg_ticks:
        relation = CenterRelationType.INDEPENDENT_UP_NEW_CENTER
    elif second.gg_ticks < first.dd_ticks:
        relation = CenterRelationType.INDEPENDENT_DOWN_NEW_CENTER
    else:
        core_up_separated = second.zd_ticks > first.zg_ticks
        core_down_separated = second.zg_ticks < first.zd_ticks
        if (core_up_separated and second.dd_ticks <= first.gg_ticks) or (
            core_down_separated and second.gg_ticks >= first.dd_ticks
        ):
            relation = CenterRelationType.EXPANSION_PENDING
        elif not (second.zd_ticks > first.zg_ticks or second.zg_ticks < first.zd_ticks):
            relation = CenterRelationType.SAME_CENTER_EXTENSION
        else:
            relation = CenterRelationType.UNRESOLVED
    return CenterRelation(first.id, second.id, relation, first.level_rank, first.source_timeframe)


def build_base_center_from_segments(
    segments: tuple[NormalizedSegment, ...], *, symbol: str, source_timeframe: Timeframe,
) -> CenterUpdate:
    motions = tuple(CenterMotion.from_segment(s, source_timeframe=source_timeframe, level_rank=0) for s in segments[:3])
    return seed_center(motions, symbol=symbol, target_level_rank=1, base_engine_center=True)


def build_recursive_center(
    motions: tuple[CompletedMotion, ...], *, symbol: str,
) -> CenterUpdate:
    if len(motions) < 3:
        cm = tuple(m.as_center_motion() for m in motions)
        return CenterUpdate(_unresolved_center(cm, symbol=symbol, target_level_rank=(motions[0].level_rank + 1 if motions else 1)),
                            result=ValidationResult(False, ("INSUFFICIENT_MOTIONS",)))
    cm = tuple(m.as_center_motion() for m in motions[:3])
    return seed_center(cm, symbol=symbol, target_level_rank=motions[0].level_rank + 1, base_engine_center=False)


def build_center_stack(source_timeframe: Timeframe, centers: tuple[Center, ...],
                       parent_links: tuple[tuple[str, str], ...] = ()) -> CenterStack:
    known = {c.id for c in centers}
    graph: dict[str, list[str]] = {c.id: [] for c in centers}
    for child, parent in parent_links:
        if child not in known or parent not in known:
            raise ValueError("CENTER_STACK_UNKNOWN_NODE")
        graph[child].append(parent)
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str) -> None:
        if node in visiting:
            raise ValueError("CENTER_STACK_CYCLE")
        if node in visited:
            return
        visiting.add(node)
        for nxt in graph[node]:
            visit(nxt)
        visiting.remove(node)
        visited.add(node)
    for node in graph:
        visit(node)
    return CenterStack(source_timeframe=source_timeframe, centers=centers, parent_links=parent_links)
