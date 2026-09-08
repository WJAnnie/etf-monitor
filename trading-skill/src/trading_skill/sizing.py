from __future__ import annotations
from dataclasses import dataclass
from enum import StrEnum
from math import floor
from trading_skill.decision import OpportunityGrade, RiskState

class StopLevel(StrEnum): L5="L5"; L30="L30"; L120="L120"; LD="LD"; LW="LW"
class StopType(StrEnum): EXECUTION_STRUCTURE="EXECUTION_STRUCTURE"; CONFIRMATION_STRUCTURE="CONFIRMATION_STRUCTURE"; CORE_STRUCTURE="CORE_STRUCTURE"; STRATEGIC_STRUCTURE="STRATEGIC_STRUCTURE"; BUY_POINT_INVALIDATION="BUY_POINT_INVALIDATION"; CENTER_INVALIDATION="CENTER_INVALIDATION"; TREND_INVALIDATION="TREND_INVALIDATION"
class TrancheRole(StrEnum): TEST="TEST"; TACTICAL="TACTICAL"; CONFIRMATION="CONFIRMATION"; CORE="CORE"; TREND_ADD="TREND_ADD"

@dataclass(frozen=True, slots=True)
class StopCandidate:
    id:str; level:StopLevel; stop_type:StopType; price_ticks:int; source_structure_id:str; source_signal_id:str|None=None
    is_structural:bool=True; noise_risk:bool=False; revision:int=1
@dataclass(frozen=True, slots=True)
class RiskBudget: effective_trade_risk:float; risk_amount:float
@dataclass(frozen=True, slots=True)
class PositionSizing: stop_distance:float; risk_based_value:float
@dataclass(frozen=True, slots=True)
class StockCapacity: allowed_max_value:float; current_value:float; remaining_value:float; over_cap_value:float
@dataclass(frozen=True, slots=True)
class PortfolioCapacity: approved_new_risk:float; industry_remaining:float; theme_remaining:float; total_remaining:float
@dataclass(frozen=True, slots=True)
class TranchePlan: role:TrancheRole; fraction:float; value:float; stop:StopCandidate
@dataclass(frozen=True, slots=True)
class PositionPlan:
    planned_entry:float; stop:StopCandidate; stop_distance:float; effective_trade_risk:float; risk_amount:float
    risk_based_value:float; final_allowed_value:float; tranche:TranchePlan; raw_quantity:float; rounded_quantity:int
    rounded_value:float; planned_risk_after_rounding:float; blocker:str|None=None

def validate_stop(stop:StopCandidate, *, planned_entry_ticks:int, current_protection_ticks:int|None=None):
    if not stop.is_structural:return False,("STOP_NOT_STRUCTURAL",)
    if stop.price_ticks>=planned_entry_ticks:return False,("INVALID_STOP_DISTANCE",)
    if current_protection_ticks is not None and stop.price_ticks<current_protection_ticks:return False,("PROTECTION_CANNOT_LOOSEN",)
    if stop.noise_risk:return False,("STOP_TOO_TIGHT_FOR_SIZING",)
    return True,()

BASE_RISK={OpportunityGrade.S:0.009,OpportunityGrade.A:0.007,OpportunityGrade.B:0.004,OpportunityGrade.C:0.0}
RISK_MULT={RiskState.L0:1.0,RiskState.L1:0.85,RiskState.L2:0.0,RiskState.L3:0.0,RiskState.L4:0.0}
def trade_risk_budget(*, equity:float, grade:OpportunityGrade, risk:RiskState, weekly_multiplier=1.0,
    market_multiplier=1.0, event_multiplier=1.0, safety_buffer=0.85, failure_multiplier=1.0):
    effective=min(0.010,BASE_RISK[grade]*RISK_MULT[risk]*weekly_multiplier*market_multiplier*event_multiplier*safety_buffer*failure_multiplier)
    return RiskBudget(effective,equity*effective)

def risk_based_size(*, entry:float, stop:float, risk_amount:float):
    if entry<=0 or stop>=entry:return None
    dist=(entry-stop)/entry
    if dist<=0:return None
    return PositionSizing(dist,risk_amount/dist)

def stock_capacity(*, equity:float, archetype:str, risk_based_value:float, current_value:float=0.0, context_multiplier:float=1.0):
    caps={"core_leader":0.15,"quality_name":0.10,"turnaround":0.06}
    cap=equity*caps[archetype]*context_multiplier
    allowed=min(risk_based_value,cap); rem=max(0.0,allowed-current_value); over=max(0.0,current_value-allowed)
    return StockCapacity(allowed,current_value,rem,over)

def portfolio_capacity(*, requested_new_risk:float, industry_used:float, theme_used:float, total_used:float, equity:float):
    industry=max(0.0,equity*0.018-industry_used); theme=max(0.0,equity*0.025-theme_used); total=max(0.0,equity*0.045-total_used)
    approved=max(0.0,min(requested_new_risk,equity*0.010,industry,theme,total))
    return PortfolioCapacity(approved,industry,theme,total)

def tranche_fraction(grade:OpportunityGrade):
    return {OpportunityGrade.S:0.35,OpportunityGrade.A:0.30,OpportunityGrade.B:0.20,OpportunityGrade.C:0.0}[grade]

def build_position_plan(*, equity:float, grade:OpportunityGrade, risk:RiskState, entry:float, stop:StopCandidate,
    tick_size:float, archetype:str, current_value:float=0.0, industry_risk_used:float=0.0,
    theme_risk_used:float=0.0, total_risk_used:float=0.0, role:TrancheRole=TrancheRole.TEST, lot_size:int=100):
    entry_ticks=round(entry/tick_size)
    valid,reasons=validate_stop(stop,planned_entry_ticks=entry_ticks)
    if not valid:
        dummy=TranchePlan(role,0.0,0.0,stop)
        return PositionPlan(entry,stop,0.0,0.0,0.0,0.0,0.0,dummy,0.0,0,0.0,0.0,reasons[0])
    rb=trade_risk_budget(equity=equity,grade=grade,risk=risk)
    stop_price=stop.price_ticks*tick_size
    sz=risk_based_size(entry=entry,stop=stop_price,risk_amount=rb.risk_amount)
    if sz is None:
        dummy=TranchePlan(role,0.0,0.0,stop)
        return PositionPlan(entry,stop,0,rb.effective_trade_risk,rb.risk_amount,0,0,dummy,0,0,0,0,"INVALID_STOP_DISTANCE")
    sc=stock_capacity(equity=equity,archetype=archetype,risk_based_value=sz.risk_based_value,current_value=current_value)
    pc=portfolio_capacity(requested_new_risk=rb.risk_amount,industry_used=industry_risk_used,theme_used=theme_risk_used,total_used=total_risk_used,equity=equity)
    portfolio_value=pc.approved_new_risk/sz.stop_distance if sz.stop_distance else 0
    allowed=min(sc.allowed_max_value,portfolio_value)
    frac=tranche_fraction(grade)
    value=max(0.0,min(sc.remaining_value,allowed)*frac)
    raw=value/entry if entry else 0.0; qty=floor(raw/lot_size)*lot_size
    if qty<lot_size:
        tranche=TranchePlan(role,frac,value,stop)
        return PositionPlan(entry,stop,sz.stop_distance,rb.effective_trade_risk,rb.risk_amount,sz.risk_based_value,allowed,tranche,raw,0,0.0,0.0,"NO_EXECUTION_MINIMUM_LOT")
    rounded=qty*entry; risk_after=rounded*sz.stop_distance
    tranche=TranchePlan(role,frac,rounded,stop)
    return PositionPlan(entry,stop,sz.stop_distance,rb.effective_trade_risk,rb.risk_amount,sz.risk_based_value,allowed,tranche,raw,qty,rounded,risk_after,None)
