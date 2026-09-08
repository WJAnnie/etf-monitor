from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from trading_skill.domain.enums import Timeframe
from trading_skill.domain.models import stable_id

class StructureRelationship(StrEnum):
    DECOMPOSES="DECOMPOSES"; CONFIRMS_END="CONFIRMS_END"; CONFIRMS_START="CONFIRMS_START"; EXECUTES="EXECUTES"; OSCILLATES_WITHIN="OSCILLATES_WITHIN"; DEPARTURE_COMPONENT="DEPARTURE_COMPONENT"; RETRACEMENT_COMPONENT="RETRACEMENT_COMPONENT"; DIVERGENCE_COMPONENT="DIVERGENCE_COMPONENT"; UNRELATED="UNRELATED"
class ParentComponentType(StrEnum):
    TREND_FINAL_LEG="TREND_FINAL_LEG"; FIRST_BUY_REVERSAL="FIRST_BUY_REVERSAL"; SECOND_BUY_FIRST_MOVE="SECOND_BUY_FIRST_MOVE"; SECOND_BUY_RETRACEMENT="SECOND_BUY_RETRACEMENT"; CENTER_DEPARTURE="CENTER_DEPARTURE"; THIRD_BUY_RETURN="THIRD_BUY_RETURN"; SELL_REVERSAL_COMPONENT="SELL_REVERSAL_COMPONENT"; CENTER_INTERNAL="CENTER_INTERNAL"
class ChildLocation(StrEnum): EARLY="EARLY"; MIDDLE="MIDDLE"; LATE="LATE"; END_CANDIDATE="END_CANDIDATE"; OUTSIDE="OUTSIDE"
class ParentContext(StrEnum): CORE_PARENT_CONFIRMATION="CORE_PARENT_CONFIRMATION"; TACTICAL_PARENT_CONFIRMATION="TACTICAL_PARENT_CONFIRMATION"; INTERNAL_OSCILLATION="INTERNAL_OSCILLATION"; COUNTERTREND_REBOUND="COUNTERTREND_REBOUND"; UNRELATED="UNRELATED"
class ActivationLevel(StrEnum): NONE="NONE"; M120="120M"; M30="30M"; M5="5M"
class NestingState(StrEnum): INACTIVE="INACTIVE"; ACTIVE="ACTIVE"; NARROWING="NARROWING"; EXECUTION_READY="EXECUTION_READY"; EXECUTED="EXECUTED"; FAILED="FAILED"; COMPLETED="COMPLETED"
class ExecutionMaturity(StrEnum): NOT_READY="NOT_READY"; WATCH="WATCH"; PREPARE="PREPARE"; TRIGGERED="TRIGGERED"; CONFIRMED_ADD="CONFIRMED_ADD"
class TradeNature(StrEnum): CORE_TREND="CORE_TREND"; MEDIUM_SHORT_REVERSAL="MEDIUM_SHORT_REVERSAL"; TREND_PULLBACK="TREND_PULLBACK"; TACTICAL_REBOUND="TACTICAL_REBOUND"; COUNTERTREND="COUNTERTREND"

@dataclass(frozen=True, slots=True)
class StructureNodeRef:
    object_id: str
    object_type: str
    source_timeframe: Timeframe
    level_rank: int
    structural_start: datetime
    structural_end: datetime
    confirmation_timestamp: datetime

@dataclass(frozen=True, slots=True)
class StructureLink:
    id: str
    parent: StructureNodeRef
    child: StructureNodeRef
    relationship: StructureRelationship
    parent_component: ParentComponentType
    child_location: ChildLocation
    valid: bool
    reason_codes: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class StructureLinkGraph:
    links: tuple[StructureLink, ...]

@dataclass(frozen=True, slots=True)
class NestingChain:
    id: str
    symbol: str
    parent_id: str
    state: NestingState
    activation_level: ActivationLevel
    execution_maturity: ExecutionMaturity
    parent_context: ParentContext
    trade_nature: TradeNature
    link_ids: tuple[str, ...] = ()
    failed_child_ids: tuple[str, ...] = ()
    revision: int = 1

def link_structures(parent: StructureNodeRef, child: StructureNodeRef, relationship: StructureRelationship,
    parent_component: ParentComponentType, *, child_location: ChildLocation = ChildLocation.MIDDLE,
    as_of: datetime) -> StructureLink:
    reasons = []
    if child.confirmation_timestamp > as_of:
        reasons.append("FUTURE_LEAKAGE_BLOCKED")
    if child.structural_start < parent.structural_start or child.structural_end > max(parent.structural_end, as_of):
        reasons.append("TEMPORAL_CONTAINMENT_FAILED")
    if parent.object_id == child.object_id:
        reasons.append("SELF_LINK")
    valid = not reasons and relationship is not StructureRelationship.UNRELATED
    return StructureLink(stable_id("slink", parent.object_id, child.object_id, relationship), parent, child,
        relationship, parent_component, child_location, valid, tuple(reasons))

def build_graph(links: tuple[StructureLink, ...]) -> StructureLinkGraph:
    graph = {}
    for link in links:
        if link.valid:
            graph.setdefault(link.parent.object_id, []).append(link.child.object_id)
    visiting, visited = set(), set()
    def visit(node):
        if node in visiting:
            raise ValueError("STRUCTURE_LINK_CYCLE")
        if node in visited:
            return
        visiting.add(node)
        for child in graph.get(node, []):
            visit(child)
        visiting.remove(node); visited.add(node)
    for node in list(graph):
        visit(node)
    return StructureLinkGraph(links)

def resolve_parent_context(link: StructureLink, *, child_direction_matches_parent_goal: bool,
    parent_is_core: bool, parent_active: bool) -> ParentContext:
    if not link.valid or not parent_active:
        return ParentContext.UNRELATED
    if link.relationship is StructureRelationship.OSCILLATES_WITHIN:
        return ParentContext.INTERNAL_OSCILLATION
    if not child_direction_matches_parent_goal:
        return ParentContext.COUNTERTREND_REBOUND
    return ParentContext.CORE_PARENT_CONFIRMATION if parent_is_core else ParentContext.TACTICAL_PARENT_CONFIRMATION

def activation_for(*, daily_setup_active: bool, m120_tail_active: bool, m30_execution_candidate: bool) -> ActivationLevel:
    if not daily_setup_active:
        return ActivationLevel.NONE
    if m30_execution_candidate:
        return ActivationLevel.M5
    if m120_tail_active:
        return ActivationLevel.M30
    return ActivationLevel.M120

def new_chain(symbol: str, parent_id: str, context: ParentContext, nature: TradeNature) -> NestingChain:
    return NestingChain(stable_id("nest", symbol, parent_id), symbol, parent_id, NestingState.ACTIVE,
        ActivationLevel.M120, ExecutionMaturity.WATCH, context, nature)

def advance_chain(chain: NestingChain, *, activation: ActivationLevel | None = None,
    maturity: ExecutionMaturity | None = None, link_id: str | None = None, executed: bool = False) -> NestingChain:
    m = maturity or chain.execution_maturity
    if executed:
        state = NestingState.EXECUTED
    elif m in (ExecutionMaturity.TRIGGERED, ExecutionMaturity.CONFIRMED_ADD):
        state = NestingState.EXECUTION_READY
    elif m is ExecutionMaturity.PREPARE:
        state = NestingState.NARROWING
    else:
        state = NestingState.ACTIVE
    return replace(chain, state=state, activation_level=activation or chain.activation_level,
        execution_maturity=m, link_ids=chain.link_ids + ((link_id,) if link_id else ()), revision=chain.revision + 1)

def child_failed(chain: NestingChain, child_id: str, *, parent_failed: bool = False) -> NestingChain:
    return replace(chain, state=NestingState.FAILED if parent_failed else NestingState.ACTIVE,
        execution_maturity=ExecutionMaturity.NOT_READY if parent_failed else ExecutionMaturity.WATCH,
        failed_child_ids=chain.failed_child_ids + (child_id,), revision=chain.revision + 1)
