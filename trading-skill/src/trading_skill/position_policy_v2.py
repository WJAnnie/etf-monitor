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
class RoleReduction:
    role: TrancheRole
    fraction_of_role: float


@dataclass(frozen=True, slots=True)
class SellPolicy:
    timeframe: Timeframe
    sell_class: int
    intent: ExitIntent
    reductions: tuple[RoleReduction, ...]
    unaffected_roles: tuple[TrancheRole, ...]
    reason_codes: tuple[str, ...] = ()

    @property
    def affected_roles(self) -> tuple[TrancheRole, ...]:
        return tuple(item.role for item in self.reductions if item.fraction_of_role > 0)


# Fractions below are percentages of the *risk-approved final position*, not account equity.
# The first tranche reuses the frozen grade-dependent initial fraction. Later tranches are
# structure-gated and may never be created just because price fell.
_CONFIRMATION_TARGET = 0.25
_TREND_ADD_TARGET = 0.10
_CORE_BEFORE_TREND_TARGET = 0.90

# A daily 2S is deliberately not identical to a daily 3S. It removes every non-core
# tranche and half of the remaining CORE tranche; a daily 3S (or weekly thesis failure)
# closes the rest. This makes “reduce core” semantically different from “exit all”.
_DAILY_SECOND_SELL_CORE_REDUCTION = 0.50


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
        # Initial hierarchy eligibility is resolved by run_full_a_scan_v2; here we only
        # keep thesis/risk/failure guards aligned with later additions.
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


def _full(*roles: TrancheRole) -> tuple[RoleReduction, ...]:
    return tuple(RoleReduction(role, 1.0) for role in roles)


def sell_policy(timeframe: Timeframe, sell_class: int) -> SellPolicy:
    """Staged exits: low-level sells remove tactical risk before touching the daily core.

    - 5m / 30m: execution risk only.
    - 120m: confirmation/trend risk, still not the daily CORE thesis.
    - daily 1S: remove non-core continuation exposure.
    - daily 2S: remove all non-core exposure + 50% of CORE.
    - daily 3S / weekly strategic failure: exit everything.

    This policy intentionally supersedes the old all-or-nothing interpretation of
    `REDUCE_CORE`; otherwise a daily 2S and 3S had effectively the same exposure result.
    """
    all_roles = (
        TrancheRole.TEST,
        TrancheRole.TACTICAL,
        TrancheRole.CONFIRMATION,
        TrancheRole.CORE,
        TrancheRole.TREND_ADD,
    )
    if timeframe is Timeframe.M5:
        reductions = _full(TrancheRole.TEST, TrancheRole.TACTICAL)
        intent = ExitIntent.REDUCE_EXECUTION
    elif timeframe is Timeframe.M30:
        reductions = _full(TrancheRole.TEST, TrancheRole.TACTICAL, TrancheRole.TREND_ADD)
        intent = ExitIntent.REDUCE_EXECUTION
    elif timeframe is Timeframe.M120:
        reductions = _full(
            TrancheRole.TEST,
            TrancheRole.TACTICAL,
            TrancheRole.CONFIRMATION,
            TrancheRole.TREND_ADD,
        )
        intent = ExitIntent.REDUCE_CONFIRMATION
    elif timeframe is Timeframe.DAILY and sell_class <= 1:
        reductions = _full(TrancheRole.TEST, TrancheRole.TACTICAL, TrancheRole.TREND_ADD)
        intent = ExitIntent.REDUCE_EXECUTION
    elif timeframe is Timeframe.DAILY and sell_class == 2:
        reductions = _full(
            TrancheRole.TEST,
            TrancheRole.TACTICAL,
            TrancheRole.CONFIRMATION,
            TrancheRole.TREND_ADD,
        ) + (RoleReduction(TrancheRole.CORE, _DAILY_SECOND_SELL_CORE_REDUCTION),)
        intent = ExitIntent.REDUCE_CORE
    elif timeframe is Timeframe.DAILY and sell_class >= 3:
        reductions = _full(*all_roles)
        intent = ExitIntent.EXIT_ALL
    elif timeframe is Timeframe.WEEKLY:
        reductions = _full(*all_roles)
        intent = ExitIntent.EXIT_ALL
    else:
        reductions = ()
        intent = ExitIntent.NONE

    affected = {item.role for item in reductions if item.fraction_of_role > 0}
    unaffected = tuple(role for role in all_roles if role not in affected)
    return SellPolicy(timeframe, sell_class, intent, reductions, unaffected)


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
    # Profit protection is a one-way ratchet. Only a newly confirmed structure may
    # raise protection; a lower proposed stop loosens risk and is always forbidden.
    return new_confirmed_structure and proposed_stop_ticks >= current_stop_ticks


def take_profit_contract() -> tuple[str, ...]:
    return (
        "不以固定盈利百分比作为主止盈",
        "5分钟/30分钟卖点先处理试仓、战术仓和新近趋势加仓，不能单独击穿日线核心仓",
        "120分钟卖点处理确认仓和趋势加仓，仍不直接清空日线核心仓",
        "日线二卖清除非核心仓并减半核心仓，日线三卖或周线战略结构失效退出剩余仓位",
        "盈利保护只随新确认结构上移，保护位不得下移",
    )
