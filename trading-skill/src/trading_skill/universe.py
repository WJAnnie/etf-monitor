from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Iterable

from trading_skill.fundamentals import FundamentalProfile, IndustryLifecycle, LeaderType
from trading_skill.monitoring import MonitoringLevel


@dataclass(frozen=True, slots=True)
class UniverseInput:
    symbol: str
    data_complete: bool = True
    hard_veto: bool = False
    liquidity_ok: bool = True
    listing_age_ok: bool = True
    fundamental_eligible: bool = True
    leader_eligible: bool = True
    industry_eligible: bool = True
    prospects_score: float = 0.0
    low_position_score: float = 0.0
    heat_score: float = 0.0
    overheated: bool = False
    value_trap_risk: bool = False
    open_position: bool = False


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    symbol: str
    universe_eligible: bool
    rank_score: float
    monitoring_level: MonitoringLevel
    deep_scanned: bool
    deep_result: object | None
    reason_codes: tuple[str, ...]


def universe_input_from_profile(
    profile: FundamentalProfile,
    *,
    low_position_score: float,
    liquidity_ok: bool = True,
    listing_age_ok: bool = True,
    open_position: bool = False,
) -> UniverseInput:
    return UniverseInput(
        symbol=profile.symbol,
        data_complete=profile.data_complete,
        hard_veto=bool(profile.vetoes),
        liquidity_ok=liquidity_ok,
        listing_age_ok=listing_age_ok,
        fundamental_eligible=profile.fundamental_eligible,
        leader_eligible=profile.leader_type is not LeaderType.NON_LEADER,
        industry_eligible=profile.industry.lifecycle is not IndustryLifecycle.DECLINING,
        prospects_score=profile.industry.prospects_score,
        low_position_score=low_position_score,
        heat_score=profile.industry.heat_score,
        overheated=profile.industry.heat_state.value == "OVERHEATED",
        value_trap_risk=(profile.fundamental_grade.value in ("D", "E")),
        open_position=open_position,
    )


def candidate_rank(item: UniverseInput) -> float:
    heat = item.heat_score * (0.65 if item.overheated else 1.0)
    score = 0.40 * item.prospects_score + 0.35 * item.low_position_score + 0.25 * heat
    if item.value_trap_risk:
        score -= 25.0
    return round(score, 8)


def cheap_eligibility(item: UniverseInput) -> tuple[bool, tuple[str, ...]]:
    reasons: list[str] = []
    if not item.data_complete:
        reasons.append("DATA_INCOMPLETE")
    if item.hard_veto:
        reasons.append("FUNDAMENTAL_HARD_VETO")
    if not item.liquidity_ok:
        reasons.append("LIQUIDITY_FILTER")
    if not item.listing_age_ok:
        reasons.append("LISTING_AGE_FILTER")
    if not item.fundamental_eligible:
        reasons.append("FUNDAMENTAL_INELIGIBLE")
    if not item.leader_eligible:
        reasons.append("NON_LEADER")
    if not item.industry_eligible:
        reasons.append("INDUSTRY_INELIGIBLE")
    if item.value_trap_risk:
        reasons.append("VALUE_TRAP_RISK")
    blocking = {
        "DATA_INCOMPLETE",
        "FUNDAMENTAL_HARD_VETO",
        "LIQUIDITY_FILTER",
        "LISTING_AGE_FILTER",
        "FUNDAMENTAL_INELIGIBLE",
        "NON_LEADER",
        "INDUSTRY_INELIGIBLE",
    }
    return not any(reason in blocking for reason in reasons), tuple(reasons)


class UniverseScanner:
    """Stage cheap filters before expensive structural analysis."""

    def __init__(self, *, max_deep_scan_symbols: int, deep_analyzer: Callable[[UniverseInput], object]):
        if max_deep_scan_symbols < 0:
            raise ValueError("INVALID_DEEP_SCAN_BUDGET")
        self.max_deep_scan_symbols = max_deep_scan_symbols
        self.deep_analyzer = deep_analyzer

    def scan(self, items: Iterable[UniverseInput]) -> tuple[CandidateRecord, ...]:
        staged: list[tuple[UniverseInput, bool, tuple[str, ...], float]] = []
        for item in items:
            eligible, reasons = cheap_eligibility(item)
            staged.append((item, eligible, reasons, candidate_rank(item)))

        # Open positions are always monitored/deep-scanned even if no longer eligible for new risk.
        open_items = sorted((row for row in staged if row[0].open_position), key=lambda row: row[0].symbol)
        eligible_non_positions = sorted(
            (row for row in staged if row[1] and not row[0].open_position),
            key=lambda row: (-row[3], row[0].symbol),
        )
        deep_symbols = {row[0].symbol for row in open_items}
        deep_symbols.update(row[0].symbol for row in eligible_non_positions[: self.max_deep_scan_symbols])

        records: list[CandidateRecord] = []
        for item, eligible, reasons, score in staged:
            deep = item.symbol in deep_symbols
            deep_result = self.deep_analyzer(item) if deep else None
            level = MonitoringLevel.M3 if item.open_position else (MonitoringLevel.M1 if eligible else MonitoringLevel.M0)
            records.append(
                CandidateRecord(
                    symbol=item.symbol,
                    universe_eligible=eligible,
                    rank_score=score,
                    monitoring_level=level,
                    deep_scanned=deep,
                    deep_result=deep_result,
                    reason_codes=reasons,
                )
            )
        return tuple(sorted(records, key=lambda record: (-record.rank_score, record.symbol)))
