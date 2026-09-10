from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_skill.decision import OpportunityGrade
from trading_skill.sizing import StopLevel, TrancheRole, tranche_fraction
from trading_skill.domain.enums import Timeframe


class AddGate(StrEnum):
    INITIAL_EXECUTION_CHAIN = "INITIAL_EXECUTION_CHAIN"
    NEW_M30_STRUCTURE = "NEW_M30_STRUCTURE"
    NEW_M120_STRUCTURE = "NEW_M120_STRUCTURE"
    DAILY_TREND_CONTINUATION = "DAILY_TREND_CONTINUATION"


class ExitIntent(StrEnum):
    NONE = "NONE"
    REDUCE_EXECUTION = "REDUCE_EXECUTION"
    REDUCE_CONFIRMATION = "REDUCE_CONFIRMATION"
    REDUCE_CORE = "REDUCE_CORE"
    EXIT_ALL = "EXIT_ALL"


class TakeProfitMode(StrEnum):
    STRUCTURAL = "STRUCTURAL"


@dataclass(frozen=True, slots=True)
class TrancheRule:
    role: TrancheRole
    target_fraction: float
    add_gate: AddGate
    management_stop_level: StopLevel
    requires_new_structure: bool
    may_average_down: bool = False


@dataclass(frozen=True, slots=True)
class StagedPositionPolicy:
    grade: OpportunityGrade
    rules: tuple[TrancheRule, ...]
    take_profit_mode: TakeProfitMode = TakeProfitMode.STRUCTURAL
    fixed_percent_take_profit_primary: bool = False

    @property
    def total_fraction(self) -> float:
        return round(sum(rule.target_fraction for rule in self.rules), 8)


@dataclass(frozen=True, slots=True)
class AddPermission:
    allowed: bool
    role: TrancheRole
    reason_codes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SellPolicy:
    timeframe: Timeframe
    sell_class: int
    intent: ExitIntent
    affected_roles: tuple[TrancheRole, ...]
    unaffected_roles: tuple[TrancheRole, ...]
    reason_codes: tuple[str, ...] = ()


# These are fractions of the *risk-approved final position*, not fractions of account equity.
# The first tranche reuses the already-frozen opportunity-grade initial fraction. Later
# tranches are structure-gated; they are never triggered merely because price fell.
_CONFIRMATION_TARGET = 0.25
_TREND_ADD_TARGET = 0.10
_CORE_BEFORE_TREND_TARGET = 0.90


def staged_position_policy(grade: OpportunityGrade) -> StagedPositionPolicy:
    initial = tranche_fraction(grade)
    if initial <= 0:
        return StagedPositionPolicy(grade, ())

    confirmation = min(_CONFIRMATION_TARGET, max(0.0, _CORE_BEFORE_TREND_TARGET - initial))
    core = max(0.0, _CORE_BEFORE_TREND_TARGET - initial - confirmation)
    trend_add = max(0.0, 1.0 - initial - confirmation - core)

    rules = (
        TrancheRule(
            TrancheRole.TEST,
            round(initial, 8),
            AddGate.INITIAL_EXECUTION_CHAIN,
            StopLevel.L5,
            requires_new_structure=False,
        ),
        TrancheRule(
            TrancheRole.CONFIRMATION,
            round(confirmation, 8),
            AddGate.NEW_M30_STRUCTURE,
            StopLevel.L30,
            requires_new_structure=True,
        ),
        TrancheRule(
            TrancheRole.CORE,
            round(core, 8),
            AddGate.NEW_M120_STRUCTURE,
            StopLevel.LD,
            requires_new_structure=True,
        ),
        TrancheRule(
            TrancheRole.TREND_ADD,
            round(trend_add, 8),
            AddGate.DAILY_TREND_CONTINUATION,
            StopLevel.L120,
            requires_new_structure=True,
        ),
    )
    return StagedPositionPolicy(grade, tuple(rule for rule in rules if rule.target_fraction > 0))


def add_permission(
    rule: TrancheRule,
    *,
    daily_thesis_valid: bool,
    new_m30_structure: bool = False,
    new_m120_structure: bool = False,
    daily_trend_continuation: bool = False,
    current_structure_failed: bool = False,
    risk_allows_add: bool = True,
) -> AddPermission:
    reasons: list[str] = []
    if not daily_thesis_valid:
        reasons.append("DAILY_THESIS_INVALID")
    if current_structure_failed:
        reasons.append("FAILED_STRUCTURE_CANNOT_BE_AVERAGED_DOWN")
    if not risk_allows_add:
        reasons.append("RISK_BLOCKS_ADD")

    gate_ok = False
    if rule.add_gate is AddGate.INITIAL_EXECUTION_CHAIN:
        # Initial execution-chain eligibility is resolved by run_full_a_scan_v2; this
        # function only enforces thesis/risk/failure guards for the resulting TEST rule.
        gate_ok = True
    elif rule.add_gate is AddGate.NEW_M30_STRUCTURE:
        gate_ok = new_m30_structure
        if not gate_ok:
            reasons.append("NEW_M30_STRUCTURE_REQUIRED")
    elif rule.add_gate is AddGate.NEW_M120_STRUCTURE:
        gate_ok = new_m120_structure
        if not gate_ok:
            reasons.append("NEW_M120_STRUCTURE_REQUIRED")
    elif rule.add_gate is AddGate.DAILY_TREND_CONTINUATION:
        gate_ok = daily_trend_continuation
        if not gate_ok:
            reasons.append("DAILY_TREND_CONTINUATION_REQUIRED")

    allowed = gate_ok and not reasons
    return AddPermission(allowed, rule.role, tuple(reasons))


def sell_policy(timeframe: Timeframe, sell_class: int) -> SellPolicy:
    """Map sell structures to tranche scope without letting low-level noise kill core.

    This mirrors the existing frozen `position.map_sell_scope` semantics but exposes the
    role contract directly for planning/reporting. Lower-level sells first remove the
    most tactical risk. Core thesis exits only on daily-level structural deterioration.
    """
    all_roles = (
        TrancheRole.TEST,
        TrancheRole.TACTICAL,
        TrancheRole.CONFIRMATION,
        TrancheRole.CORE,
        TrancheRole.TREND_ADD,
    )
    if timeframe is Timeframe.M5:
        affected = (TrancheRole.TEST, TrancheRole.TACTICAL)
        intent = ExitIntent.REDUCE_EXECUTION
    elif timeframe is Timeframe.M30:
        affected = (TrancheRole.TEST, TrancheRole.TACTICAL, TrancheRole.TREND_ADD)
        intent = ExitIntent.REDUCE_EXECUTION
    elif timeframe is Timeframe.M120:
        affected = (TrancheRole.TEST, TrancheRole.TACTICAL, TrancheRole.CONFIRMATION, TrancheRole.TREND_ADD)
        intent = ExitIntent.REDUCE_CONFIRMATION
    elif timeframe is Timeframe.DAILY and sell_class <= 1:
        affected = (TrancheRole.TEST, TrancheRole.TACTICAL, TrancheRole.TREND_ADD)
        intent = ExitIntent.REDUCE_EXECUTION
    elif timeframe is Timeframe.DAILY and sell_class == 2:
        affected = all_roles
        intent = ExitIntent.REDUCE_CORE
    elif timeframe is Timeframe.DAILY and sell_class >= 3:
        affected = all_roles
        intent = ExitIntent.EXIT_ALL
    elif timeframe is Timeframe.WEEKLY:
        affected = all_roles
        intent = ExitIntent.EXIT_ALL
    else:
        affected = ()
        intent = ExitIntent.NONE

    unaffected = tuple(role for role in all_roles if role not in affected)
    return SellPolicy(timeframe, sell_class, intent, affected, unaffected)


def stop_level_for_role(role: TrancheRole) -> StopLevel:
    return {
        TrancheRole.TEST: StopLevel.L5,
        TrancheRole.TACTICAL: StopLevel.L30,
        TrancheRole.CONFIRMATION: StopLevel.L30,
        TrancheRole.CORE: StopLevel.LD,
        TrancheRole.TREND_ADD: StopLevel.L120,
    }[role]


def protection_can_tighten(
    *,
    current_stop_ticks: int,
    proposed_stop_ticks: int,
    new_confirmed_structure: bool,
) -> bool:
    # Profit protection is a one-way ratchet: only a newly confirmed structure may
    # raise protection. A lower proposed stop would loosen risk and is forbidden.
    return new_confirmed_structure and proposed_stop_ticks >= current_stop_ticks


def take_profit_contract() -> tuple[str, ...]:
    return (
        "不以固定盈利百分比作为主止盈",
        "5分钟/30分钟卖点先处理试仓与战术仓，不能单独击穿日线核心仓",
        "120分钟卖点处理确认仓与趋势加仓，日线二卖开始处理核心仓",
        "日线三卖或周线战略结构失效退出剩余仓位",
        "盈利保护只随新确认结构上移，保护位不得下移",
    )
