from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Iterable, Sequence

from trading_skill.chan.center import Center
from trading_skill.chan.signals import ChanSignal
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.domain.models import stable_id


class TimeframeRole(StrEnum):
    STRATEGIC_CONTEXT = "STRATEGIC_CONTEXT"
    ENTRY_AUTHORITY = "ENTRY_AUTHORITY"
    STRUCTURAL_CONFIRMATION = "STRUCTURAL_CONFIRMATION"
    EXECUTION_SETUP = "EXECUTION_SETUP"
    EXECUTION_TRIGGER = "EXECUTION_TRIGGER"


TIMEFRAME_ROLES = {
    Timeframe.WEEKLY: TimeframeRole.STRATEGIC_CONTEXT,
    Timeframe.DAILY: TimeframeRole.ENTRY_AUTHORITY,
    Timeframe.M120: TimeframeRole.STRUCTURAL_CONFIRMATION,
    Timeframe.M30: TimeframeRole.EXECUTION_SETUP,
    Timeframe.M5: TimeframeRole.EXECUTION_TRIGGER,
}

# 新开核心仓的结构准入：日线二买 / 类二买。日线一买只观察；
# 120m/30m/5m 只能确认和执行，不能越级创造核心买入资格。
CORE_ENTRY_STANDARD_TYPES = {ChanSignalType.SECOND_BUY}
WATCH_ONLY_DAILY_TYPES = {ChanSignalType.FIRST_BUY}
INTRADAY_CONFIRM_TYPES = {
    ChanSignalType.FIRST_BUY,
    ChanSignalType.SECOND_BUY,
    ChanSignalType.THIRD_BUY,
}


@dataclass(frozen=True, slots=True)
class SecondBuyValidation:
    valid: bool
    first_buy: ChanSignal | None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Class2Annotation:
    standard_type: ChanSignalType
    extended_types: tuple[ChanSignalType, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ExecutionHierarchy:
    daily_permission: bool
    m120_confirmed: bool
    m30_confirmed: bool
    m5_confirmed: bool
    maturity: str
    reason_codes: tuple[str, ...] = ()


def signal_kind(signal: ChanSignal | None) -> ChanSignalType | None:
    if signal is None or not signal.standard_types:
        return None
    return signal.standard_types[0]


def timeframe_role(timeframe: Timeframe) -> TimeframeRole:
    return TIMEFRAME_ROLES[timeframe]


def _matching_first_buy(second_buy: ChanSignal, signals: Iterable[ChanSignal]) -> ChanSignal | None:
    anchors = set(second_buy.anchor_ids)
    candidates = []
    for signal in signals:
        if signal.side != "BUY" or ChanSignalType.FIRST_BUY not in signal.standard_types:
            continue
        expected_anchor = stable_id("anchor", signal.id)
        if expected_anchor not in anchors:
            continue
        if signal.confirmation_timestamp > second_buy.confirmation_timestamp:
            continue
        candidates.append(signal)
    if not candidates:
        return None
    return max(candidates, key=lambda item: item.confirmation_timestamp)


def validate_standard_second_buy(second_buy: ChanSignal, signals: Iterable[ChanSignal]) -> SecondBuyValidation:
    """Validate the agreed standard 2B boundary.

    Standard 2B = first buy -> completed first rise -> first completed pullback,
    and the pullback low must be >= the first-buy low. Equality is valid; below invalidates.
    The tracker already guarantees the rise/pullback sequence. This post-policy closes the
    price-boundary gap without changing the frozen M3 core state machine.
    """
    if signal_kind(second_buy) is not ChanSignalType.SECOND_BUY:
        return SecondBuyValidation(False, None, ("NOT_STANDARD_SECOND_BUY",))
    first_buy = _matching_first_buy(second_buy, signals)
    if first_buy is None:
        return SecondBuyValidation(False, None, ("SECOND_BUY_ANCHOR_NOT_FOUND",))
    if second_buy.structural_price_ticks < first_buy.structural_price_ticks:
        return SecondBuyValidation(False, first_buy, ("SECOND_BUY_BROKE_FIRST_BUY_LOW",))
    return SecondBuyValidation(True, first_buy, ())


def validate_standard_second_sell(second_sell: ChanSignal, signals: Iterable[ChanSignal]) -> bool:
    """Symmetric boundary for 2S: rebound high may equal, but may not exceed, first-sell high."""
    if signal_kind(second_sell) is not ChanSignalType.SECOND_SELL:
        return False
    anchors = set(second_sell.anchor_ids)
    for signal in signals:
        if signal.side != "SELL" or ChanSignalType.FIRST_SELL not in signal.standard_types:
            continue
        if stable_id("anchor", signal.id) not in anchors:
            continue
        if signal.confirmation_timestamp > second_sell.confirmation_timestamp:
            continue
        return second_sell.structural_price_ticks <= signal.structural_price_ticks
    return False


def classify_second_buy_extensions(
    second_buy: ChanSignal,
    *,
    all_signals: Sequence[ChanSignal],
    centers: Sequence[Center] = (),
) -> Class2Annotation:
    """Annotate class-2 buys without fabricating a standard buy type.

    Strong class-2 = a valid standard 2B overlapping a 3B at the same level and the
    same structural location. We require a shared structural evidence id.
    Center class-2 = a valid standard 2B whose first pullback low is >= latest same-level ZG.
    Extended labels stay in extended_types only.
    """
    validation = validate_standard_second_buy(second_buy, all_signals)
    if not validation.valid:
        return Class2Annotation(ChanSignalType.SECOND_BUY, (), validation.reason_codes)

    extended: list[ChanSignalType] = []
    second_evidence = {item for item in second_buy.evidence_ids if item}
    for signal in all_signals:
        if signal.id == second_buy.id or signal.side != "BUY":
            continue
        if ChanSignalType.THIRD_BUY not in signal.standard_types:
            continue
        if signal.level_rank != second_buy.level_rank:
            continue
        third_evidence = {item for item in signal.evidence_ids if item}
        if second_evidence & third_evidence:
            extended.append(ChanSignalType.STRONG_CLASS2_BUY)
            break

    eligible_centers = [
        center
        for center in centers
        if center.level_rank == second_buy.level_rank
        and center.confirmation_timestamp <= second_buy.confirmation_timestamp
    ]
    if eligible_centers:
        latest_center = max(eligible_centers, key=lambda item: item.confirmation_timestamp)
        if second_buy.structural_price_ticks >= latest_center.zg_ticks:
            extended.append(ChanSignalType.CENTER_CLASS2_BUY)

    return Class2Annotation(ChanSignalType.SECOND_BUY, tuple(dict.fromkeys(extended)), ())


def daily_entry_permission(signal: ChanSignal | None, *, all_daily_signals: Sequence[ChanSignal]) -> bool:
    if signal is None or signal_kind(signal) not in CORE_ENTRY_STANDARD_TYPES:
        return False
    return validate_standard_second_buy(signal, all_daily_signals).valid


def build_execution_hierarchy(
    *,
    daily_signal: ChanSignal | None,
    all_daily_signals: Sequence[ChanSignal],
    m120_has_buy: bool,
    m30_has_buy: bool,
    m5_has_buy: bool,
) -> ExecutionHierarchy:
    permission = daily_entry_permission(daily_signal, all_daily_signals=all_daily_signals)
    reasons: list[str] = []
    if not permission:
        reasons.append("DAILY_2B_OR_CLASS2_PERMISSION_MISSING")
        return ExecutionHierarchy(False, False, False, False, "WATCH", tuple(reasons))
    if not m120_has_buy:
        reasons.append("M120_CONFIRMATION_MISSING")
        return ExecutionHierarchy(True, False, False, False, "WATCH", tuple(reasons))
    if not m30_has_buy:
        reasons.append("M30_EXECUTION_SETUP_MISSING")
        return ExecutionHierarchy(True, True, False, False, "WATCH", tuple(reasons))
    if not m5_has_buy:
        reasons.append("M5_EXECUTION_TRIGGER_MISSING")
        return ExecutionHierarchy(True, True, True, False, "PREPARE", tuple(reasons))
    return ExecutionHierarchy(True, True, True, True, "TRIGGERED", ())
