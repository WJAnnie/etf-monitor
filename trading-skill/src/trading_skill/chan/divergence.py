from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from trading_skill.chan.trend import TrendType
from trading_skill.domain.enums import DivergenceState, DivergenceType, Direction, TrendClassification, TrendState
from trading_skill.domain.models import ValidationResult, stable_id

@dataclass(frozen=True, slots=True)
class StructuralLeg:
    id: str
    direction: Direction
    level_rank: int
    low_ticks: int
    high_ticks: int
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    completed: bool = True

@dataclass(frozen=True, slots=True)
class MacdLegEvidence:
    leg_id: str
    directional_hist_area: float
    hist_peak_abs: float
    dif_extreme_abs: float
    dea_extreme_abs: float
    zero_axis_relation: str = "UNKNOWN"

@dataclass(frozen=True, slots=True)
class DivergenceEligibility:
    eligible: bool
    trend_id: str
    compare_leg_1: str
    compare_leg_2: str
    new_extreme: bool
    comparable: bool
    reason_codes: tuple[str, ...] = ()

@dataclass(frozen=True, slots=True)
class Divergence:
    id: str
    trend_id: str
    type: DivergenceType
    state: DivergenceState
    compare_leg_1: str
    compare_leg_2: str
    confirmation_timestamp: datetime
    reason_codes: tuple[str, ...] = ()
    revision: int = 1

def divergence_eligibility(trend: TrendType, b: StructuralLeg, c: StructuralLeg) -> DivergenceEligibility:
    reasons: list[str] = []
    trend_type = trend.final_type or trend.current_classification
    if trend_type not in (TrendClassification.UPTREND, TrendClassification.DOWNTREND):
        reasons.append("NO_VALID_TREND")
    comparable = b.level_rank == c.level_rank == trend.level_rank and b.direction is c.direction
    if not comparable:
        reasons.append("STRUCTURAL_COMPARABILITY_FAILED")
    if trend_type is TrendClassification.DOWNTREND:
        new_extreme = c.low_ticks < b.low_ticks
    elif trend_type is TrendClassification.UPTREND:
        new_extreme = c.high_ticks > b.high_ticks
    else:
        new_extreme = False
    if not new_extreme:
        reasons.append("C_NO_NEW_EXTREME")
    return DivergenceEligibility(not reasons, trend.id, b.id, c.id, new_extreme, comparable, tuple(reasons))

def evaluate_trend_divergence(trend: TrendType, b: StructuralLeg, c: StructuralLeg,
    b_macd: MacdLegEvidence, c_macd: MacdLegEvidence, *, lower_level_turn_completed: bool,
    as_of: datetime) -> tuple[Divergence, ValidationResult]:
    eligibility = divergence_eligibility(trend, b, c)
    if c.confirmation_timestamp > as_of:
        d = Divergence(stable_id("div", trend.id, b.id, c.id), trend.id, DivergenceType.UNRESOLVED,
            DivergenceState.INVALIDATED, b.id, c.id, as_of, ("FUTURE_LEAKAGE_BLOCKED",))
        return d, ValidationResult(False, ("FUTURE_LEAKAGE_BLOCKED",))
    if not eligibility.eligible:
        dtype = DivergenceType.MOMENTUM_WEAKENING if c_macd.directional_hist_area < b_macd.directional_hist_area else DivergenceType.NONE
        d = Divergence(stable_id("div", trend.id, b.id, c.id), trend.id, dtype,
            DivergenceState.WEAKENING if dtype is DivergenceType.MOMENTUM_WEAKENING else DivergenceState.NONE,
            b.id, c.id, c.confirmation_timestamp, eligibility.reason_codes)
        return d, ValidationResult(False, eligibility.reason_codes)
    weaker = (c_macd.directional_hist_area < b_macd.directional_hist_area
        and c_macd.hist_peak_abs <= b_macd.hist_peak_abs
        and c_macd.dif_extreme_abs <= b_macd.dif_extreme_abs)
    trend_type = trend.final_type or trend.current_classification
    dtype = DivergenceType.TREND_BOTTOM_DIVERGENCE if trend_type is TrendClassification.DOWNTREND else DivergenceType.TREND_TOP_DIVERGENCE
    if not weaker:
        d = Divergence(stable_id("div", trend.id, b.id, c.id), trend.id, dtype, DivergenceState.INVALIDATED,
            b.id, c.id, c.confirmation_timestamp, ("MOMENTUM_REACCELERATED",))
        return d, ValidationResult(False, ("MOMENTUM_REACCELERATED",))
    if trend.state is TrendState.COMPLETED and lower_level_turn_completed:
        state = DivergenceState.CONFIRMED
    elif lower_level_turn_completed:
        state = DivergenceState.FORMING
    else:
        state = DivergenceState.CANDIDATE
    d = Divergence(stable_id("div", trend.id, b.id, c.id), trend.id, dtype, state,
        b.id, c.id, c.confirmation_timestamp)
    return d, ValidationResult(True)
