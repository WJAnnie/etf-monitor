from __future__ import annotations
from dataclasses import dataclass
from enum import IntEnum, StrEnum
from trading_skill.chan.signals import ChanSignal
from trading_skill.indicators import TechnicalBundle, TechnicalConfirmation
from trading_skill.domain.enums import ChanSignalType

class OpportunityGrade(StrEnum): S="S"; A="A"; B="B"; C="C"
class RiskState(IntEnum): L0=0; L1=1; L2=2; L3=3; L4=4
class Action(StrEnum):
    OBSERVE="OBSERVE"; WAIT_2B="WAIT_2B"; PREPARE_BUY="PREPARE_BUY"; BUY_TRANCHE_1="BUY_TRANCHE_1"; ADD_TRANCHE_2="ADD_TRANCHE_2"; ADD_TREND="ADD_TREND"; HOLD="HOLD"; PAUSE_ADD="PAUSE_ADD"; REDUCE_TACTICAL="REDUCE_TACTICAL"; REDUCE_CORE="REDUCE_CORE"; EXIT="EXIT"
class Blocker(StrEnum):
    FUNDAMENTAL_VETO="FUNDAMENTAL_VETO"; NO_CHAN_BUY="NO_CHAN_BUY"; DAILY_FIRST_BUY_WAIT_2B="DAILY_FIRST_BUY_WAIT_2B"; SIGNAL_NOT_MATURE="SIGNAL_NOT_MATURE"; STOP_UNDEFINED="STOP_UNDEFINED"; RISK_TOO_HIGH="RISK_TOO_HIGH"; TECHNICAL_EXECUTION_PAUSED="TECHNICAL_EXECUTION_PAUSED"; EXECUTION_STRUCTURE_CONFLICT="EXECUTION_STRUCTURE_CONFLICT"; EXECUTION_CONFIRMATION_PENDING="EXECUTION_CONFIRMATION_PENDING"; PARENT_CONTEXT_INVALID="PARENT_CONTEXT_INVALID"; DATA_INCOMPLETE="DATA_INCOMPLETE"; PORTFOLIO_RISK_FULL="PORTFOLIO_RISK_FULL"; NO_NEW_STRUCTURE_FOR_ADD="NO_NEW_STRUCTURE_FOR_ADD"; REENTRY_LOCKED="REENTRY_LOCKED"

@dataclass(frozen=True, slots=True)
class OpportunityEvidence:
    chan:int; volume:int; macd:int; boll:int; kdj:int; mtf:int
    def total(self):
        return max(0,min(35,self.chan))+max(0,min(20,self.volume))+max(0,min(15,self.macd))+max(0,min(10,self.boll))+max(0,min(10,self.kdj))+max(0,min(10,self.mtf))
@dataclass(frozen=True, slots=True)
class Opportunity: score:int; grade:OpportunityGrade; raw_grade:OpportunityGrade
@dataclass(frozen=True, slots=True)
class RiskEvidence:
    structure:int=0; volume:int=0; momentum:int=0; position:int=0; rhythm:int=0; event:int=0; portfolio:int=0
@dataclass(frozen=True, slots=True)
class Decision:
    opportunity:Opportunity; risk:RiskState; action:Action; blockers:tuple[Blocker,...]; suppressed_actions:tuple[Action,...]=()

def _grade(score):
    if score>=85:return OpportunityGrade.S
    if score>=75:return OpportunityGrade.A
    if score>=60:return OpportunityGrade.B
    return OpportunityGrade.C

def score_opportunity(e:OpportunityEvidence, *, chan_buy_eligible:bool, stop_defined:bool,
    risk:RiskState=RiskState.L0, prior_grade:OpportunityGrade|None=None, structural_event=False):
    score=e.total(); raw=_grade(score); grade=raw
    if not chan_buy_eligible or not stop_defined:
        grade=OpportunityGrade.C
    elif raw is OpportunityGrade.S and (e.chan<30 or e.volume<15 or risk>=RiskState.L2):
        grade=OpportunityGrade.A
    if prior_grade and not structural_event:
        if prior_grade is OpportunityGrade.A and grade is OpportunityGrade.B and score>72: grade=OpportunityGrade.A
        if prior_grade is OpportunityGrade.B and grade is OpportunityGrade.A and score<78: grade=OpportunityGrade.B
    return Opportunity(score,grade,raw)

def risk_state(e:RiskEvidence, *, prior:RiskState=RiskState.L0, recovery_new_structure=False):
    if e.event>=4 or e.structure>=4 or e.position>=4:return RiskState.L4
    if e.structure>=3 or e.position>=3:return RiskState.L3
    if e.structure>=2 or e.volume>=2 or (e.momentum>=1 and e.volume>=1): target=RiskState.L2
    elif max(e.structure,e.volume,e.momentum,e.position,e.rhythm,e.event,e.portfolio)>=1: target=RiskState.L1
    else: target=RiskState.L0
    if target>prior:return target
    if target<prior:
        if not recovery_new_structure:return prior
        return RiskState(max(int(target),int(prior)-1))
    return target

def blockers_for(*, signal:ChanSignal|None, fundamental_eligible:bool, stop_defined:bool, risk:RiskState,
    technical:TechnicalBundle|None, parent_valid:bool, data_complete:bool, portfolio_permission:bool,
    execution_structure_ok:bool=True, execution_confirmation_ready:bool|None=None,
    add_requested=False, new_structure_for_add=False, reentry_locked=False, allow_daily_first_buy=False):
    b=[]
    if not fundamental_eligible:b.append(Blocker.FUNDAMENTAL_VETO)
    if signal is None:b.append(Blocker.NO_CHAN_BUY)
    elif not allow_daily_first_buy and ChanSignalType.FIRST_BUY in signal.standard_types:b.append(Blocker.DAILY_FIRST_BUY_WAIT_2B)
    if not stop_defined:b.append(Blocker.STOP_UNDEFINED)
    if risk>=RiskState.L2:b.append(Blocker.RISK_TOO_HIGH)
    if technical and technical.confirmation is TechnicalConfirmation.PAUSE:b.append(Blocker.TECHNICAL_EXECUTION_PAUSED)
    if not execution_structure_ok:b.append(Blocker.EXECUTION_STRUCTURE_CONFLICT)
    elif execution_confirmation_ready is False:b.append(Blocker.EXECUTION_CONFIRMATION_PENDING)
    if not parent_valid:b.append(Blocker.PARENT_CONTEXT_INVALID)
    if not data_complete:b.append(Blocker.DATA_INCOMPLETE)
    if not portfolio_permission:b.append(Blocker.PORTFOLIO_RISK_FULL)
    if add_requested and not new_structure_for_add:b.append(Blocker.NO_NEW_STRUCTURE_FOR_ADD)
    if reentry_locked:b.append(Blocker.REENTRY_LOCKED)
    return tuple(b)

def decide(*, opportunity:Opportunity, risk:RiskState, signal:ChanSignal|None, blockers:tuple[Blocker,...],
    has_position:bool, execution_maturity:str="NOT_READY", add_requested=False, trend_add=False):
    if risk is RiskState.L4 or Blocker.FUNDAMENTAL_VETO in blockers:
        action=Action.EXIT if has_position else Action.OBSERVE
    elif risk is RiskState.L3:
        action=Action.REDUCE_TACTICAL if has_position else Action.OBSERVE
    elif risk is RiskState.L2:
        action=Action.PAUSE_ADD if has_position else Action.OBSERVE
    elif Blocker.DAILY_FIRST_BUY_WAIT_2B in blockers:
        action=Action.WAIT_2B
    elif blockers:
        action=Action.HOLD if has_position else Action.OBSERVE
    elif trend_add and has_position:
        action=Action.ADD_TREND
    elif add_requested and has_position:
        action=Action.ADD_TRANCHE_2
    elif not has_position and opportunity.grade in (OpportunityGrade.S,OpportunityGrade.A,OpportunityGrade.B):
        if execution_maturity=="TRIGGERED": action=Action.BUY_TRANCHE_1
        elif execution_maturity=="PREPARE": action=Action.PREPARE_BUY
        else: action=Action.OBSERVE
    else:
        action=Action.HOLD if has_position else Action.OBSERVE
    return Decision(opportunity,risk,action,blockers,())
