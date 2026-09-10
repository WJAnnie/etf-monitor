from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from trading_skill.domain.enums import Timeframe
from trading_skill.sizing import StopLevel, TrancheRole


class AddGate(StrEnum):
    INITIAL_EXECUTION_CHAIN = "INITIAL_EXECUTION_CHAIN"
    NEW_M30_STRUCTURE = "NEW_M30_STRUCTURE"
    NEW_M120_STRUCTURE = "NEW_M120_STRUCTURE"
    DAILY_TREND_CONTINUATION = "DAILY_TREND_CONTINUATION"


class CapacityBasis(StrEnum):
    EXPLICIT_TEST_RISK = "EXPLICIT_TEST_RISK"
    REMAINING_RISK_CAPACITY = "REMAINING_RISK_CAPACITY"


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
    capacity_fraction: float | None
    capacity_basis: CapacityBasis
    add_gate: AddGate
    management_stop_level: StopLevel
    requires_new_structure: bool
    may_average_down: bool = False


@dataclass(frozen=True, slots=True)
class StagedPositionPolicy:
    rules: tuple[TrancheRule, ...]
    take_profit_mode: TakeProfitMode = TakeProfitMode.STRUCTURAL
    fixed_percent_take_profit_primary: bool = False
    static_full_position_fraction_plan: bool = False


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


# These fractions are deliberately NOT percentages of account equity or of a static
# "final position". They are fractions of the remaining risk-approved capacity at the
# moment a new independent structure is confirmed. The initial TEST has no static
# fraction at all: it must be sized from explicit TEST risk budget and the current 5m
# execution stop in the audited STEP5B path.
_CONFIRMATION_REMAINING_FRACTION = 0.30
_CORE_REMAINING_FRACTION = 0.50
_TREND_ADD_REMAINING_FRACTION = 0.25


def staged_position_policy() -> StagedPositionPolicy:
    return StagedPositionPolicy(
        rules=(
            TrancheRule(
                TrancheRole.TEST,
                None,
                CapacityBasis.EXPLICIT_TEST_RISK,
                AddGate.INITIAL_EXECUTION_CHAIN,
                StopLevel.L5,
                requires_new_structure=False,
            ),
            TrancheRule(
                TrancheRole.CONFIRMATION,
                _CONFIRMATION_REMAINING_FRACTION,
                CapacityBasis.REMAINING_RISK_CAPACITY,
                AddGate.NEW_M30_STRUCTURE,
                StopLevel.L30,
                requires_new_structure=True,
            ),
            TrancheRule(
                TrancheRole.CORE,
                _CORE_REMAINING_FRACTION,
                CapacityBasis.REMAINING_RISK_CAPACITY,
                AddGate.NEW_M120_STRUCTURE,
                StopLevel.LD,
                requires_new_structure=True,
            ),
            TrancheRule(
                TrancheRole.TREND_ADD,
                _TREND_ADD_REMAINING_FRACTION,
                CapacityBasis.REMAINING_RISK_CAPACITY,
                AddGate.DAILY_TREND_CONTINUATION,
                StopLevel.L120,
                requires_new_structure=True,
            ),
        )
    )


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
        # The actual initial hierarchy is resolved by the scan/new pipeline; this layer
        # never grants entry merely because a TEST rule exists.
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


def _reductions(mapping: dict[TrancheRole, float]) -> tuple[RoleReduction, ...]:
    return tuple(
        RoleReduction(role, max(0.0, min(1.0, float(fraction))))
        for role, fraction in mapping.items()
        if float(fraction) > 0
    )


def _intent_for(reductions: tuple[RoleReduction, ...]) -> ExitIntent:
    by_role = {item.role: item.fraction_of_role for item in reductions}
    all_roles = tuple(TrancheRole)
    if all(by_role.get(role, 0.0) >= 1.0 for role in all_roles):
        return ExitIntent.EXIT_ALL
    if by_role.get(TrancheRole.CORE, 0.0) > 0:
        return ExitIntent.REDUCE_CORE
    if (
        by_role.get(TrancheRole.CONFIRMATION, 0.0) > 0
        or by_role.get(TrancheRole.TREND_ADD, 0.0) > 0
    ):
        return ExitIntent.REDUCE_CONFIRMATION
    if reductions:
        return ExitIntent.REDUCE_EXECUTION
    return ExitIntent.NONE


def sell_policy(timeframe: Timeframe, sell_class: int) -> SellPolicy:
    """Canonical staged structural exit matrix.

    Sell points are not fixed-profit exits. Low levels reduce low-level/newer risk first;
    higher levels progressively touch the daily CORE. Weekly 1S/2S are strategic risk
    reductions, not automatic liquidation; only the third weekly sell fully exits all roles.
    """
    sell_class = max(1, min(3, int(sell_class)))
    table: dict[Timeframe, dict[int, dict[TrancheRole, float]]] = {
        Timeframe.M5: {
            1: {TrancheRole.TEST: 0.50},
            2: {TrancheRole.TEST: 1.00},
            3: {TrancheRole.TEST: 1.00, TrancheRole.TACTICAL: 0.50},
        },
        Timeframe.M30: {
            1: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 0.50,
                TrancheRole.CONFIRMATION: 0.50,
            },
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 0.25,
            },
            3: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 0.50,
            },
        },
        Timeframe.M120: {
            1: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 0.50,
            },
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CORE: 0.25,
            },
            3: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CORE: 0.50,
            },
        },
        Timeframe.DAILY: {
            1: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CORE: 0.25,
            },
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CORE: 0.50,
            },
            3: {role: 1.00 for role in TrancheRole},
        },
        Timeframe.WEEKLY: {
            1: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CORE: 0.50,
            },
            2: {
                TrancheRole.TEST: 1.00,
                TrancheRole.TACTICAL: 1.00,
                TrancheRole.CONFIRMATION: 1.00,
                TrancheRole.TREND_ADD: 1.00,
                TrancheRole.CORE: 0.75,
            },
            3: {role: 1.00 for role in TrancheRole},
        },
    }
    reductions = _reductions(table.get(timeframe, {}).get(sell_class, {}))
    affected = {item.role for item in reductions if item.fraction_of_role > 0}
    unaffected = tuple(role for role in TrancheRole if role not in affected)
    return SellPolicy(timeframe, sell_class, _intent_for(reductions), reductions, unaffected)


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
    return new_confirmed_structure and proposed_stop_ticks >= current_stop_ticks


def take_profit_contract() -> tuple[str, ...]:
    return (
        "不以固定盈利百分比作为主止盈",
        "5分钟一卖/二卖/三卖只逐步处理首笔TEST及旧战术风险，不单独否定日线核心逻辑",
        "30分钟卖点逐步退出确认仓并可轻度处理最新趋势加仓；120分钟二卖/三卖才开始小比例触及核心仓",
        "日线一卖/二卖分别减核心25%/50%，日线三卖退出；周线一卖/二卖分别保留核心50%/25%，周线三卖才全部退出",
        "盈利保护只随新确认结构上移或保持，保护位不得因亏损向下放宽",
    )
