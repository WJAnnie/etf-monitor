from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from trading_skill.decision import Action
from trading_skill.sizing import StopCandidate, StopLevel, TrancheRole
from trading_skill.domain.models import stable_id

class TradeState(StrEnum):
    PLANNED="PLANNED"; OPENING="OPENING"; ACTIVE="ACTIVE"; PYRAMIDING="PYRAMIDING"; PROFIT_PROTECTED="PROFIT_PROTECTED"; REDUCING="REDUCING"; CLOSED="CLOSED"
class ThesisState(StrEnum): ACTIVE="ACTIVE"; INVALIDATED="INVALIDATED"; PROMOTED="PROMOTED"; CLOSED="CLOSED"
class ProfitLockState(StrEnum): NONE="NONE"; INITIAL="INITIAL"; PROTECTED="PROTECTED"; LOCKED="LOCKED"; STRUCTURAL_EXIT_PENDING="STRUCTURAL_EXIT_PENDING"
class BreakState(StrEnum): WICK_BREAK="WICK_BREAK"; CLOSE_BREAK="CLOSE_BREAK"; BREAK_AND_FAILED_RECLAIM="BREAK_AND_FAILED_RECLAIM"
class ReentryState(StrEnum): INACTIVE="INACTIVE"; WATCH_NEW_STRUCTURE="WATCH_NEW_STRUCTURE"; ELIGIBLE="ELIGIBLE"; READY="READY"; EXECUTED="EXECUTED"; LOCKED="LOCKED"

@dataclass(frozen=True, slots=True)
class EntryThesis:
    id:str; trade_id:str; tranche_id:str; role:TrancheRole; management_level:StopLevel
    entry_signal_id:str; parent_structure_id:str|None; stop:StopCandidate
    state:ThesisState=ThesisState.ACTIVE; revision:int=1
@dataclass(frozen=True, slots=True)
class Tranche: id:str; value:float; entry_price:float; thesis:EntryThesis
@dataclass(frozen=True, slots=True)
class Trade: id:str; symbol:str; state:TradeState; tranches:tuple[Tranche,...]; created_at:datetime; revision:int=1
@dataclass(frozen=True, slots=True)
class Protection: tranche_id:str; level:StopLevel; price_ticks:int; source_structure_id:str; revision:int=1
@dataclass(frozen=True, slots=True)
class SellScope:
    affected_tranche_ids:tuple[str,...]
    unaffected_tranche_ids:tuple[str,...]
    action:Action
    # 每个仓位本次减仓比例；1.0=全部卖出该笔，0.5=减半。用于真正分批卖出。
    reduction_fractions:tuple[tuple[str,float],...]=()
@dataclass(frozen=True, slots=True)
class ExitRecord: tranche_id:str; expected_exit:float; actual_exit:float; slippage:float; reason:str; realized_pnl:float
@dataclass(frozen=True, slots=True)
class ReentryTracker:
    old_trade_id:str; parent_structure_id:str; failure_level:StopLevel; child_failure_count:int; state:ReentryState; risk_multiplier:float=0.80

def create_trade(symbol:str, created_at:datetime) -> Trade:
    return Trade(stable_id("trade",symbol,created_at.isoformat()),symbol,TradeState.PLANNED,(),created_at)

def add_tranche(trade:Trade, *, value:float, entry_price:float, role:TrancheRole, management_level:StopLevel,
    entry_signal_id:str, parent_structure_id:str|None, stop:StopCandidate) -> Trade:
    tid=stable_id("tranche",trade.id,len(trade.tranches),entry_signal_id)
    thesis=EntryThesis(stable_id("thesis",tid,entry_signal_id),trade.id,tid,role,management_level,entry_signal_id,parent_structure_id,stop)
    tranche=Tranche(tid,value,entry_price,thesis)
    return replace(trade,state=TradeState.ACTIVE if not trade.tranches else TradeState.PYRAMIDING,tranches=trade.tranches+(tranche,),revision=trade.revision+1)

def invalidate_thesis(tranche:Tranche) -> Tranche:
    return replace(tranche,thesis=replace(tranche.thesis,state=ThesisState.INVALIDATED,revision=tranche.thesis.revision+1))

def promote_thesis(tranche:Tranche, *, new_role:TrancheRole, new_level:StopLevel, new_stop:StopCandidate, new_structure_confirmed:bool) -> Tranche:
    if not new_structure_confirmed: raise ValueError("PROMOTION_REQUIRES_NEW_STRUCTURE")
    if new_stop.price_ticks < tranche.thesis.stop.price_ticks: raise ValueError("PROTECTION_CANNOT_LOOSEN")
    return replace(tranche,thesis=replace(tranche.thesis,role=new_role,management_level=new_level,stop=new_stop,state=ThesisState.PROMOTED,revision=tranche.thesis.revision+1))

def raise_protection(current:Protection|None, *, tranche_id:str, level:StopLevel, price_ticks:int, source_structure_id:str, new_structure_confirmed:bool) -> Protection:
    if not new_structure_confirmed: raise ValueError("PROTECTION_REQUIRES_NEW_STRUCTURE")
    if current and price_ticks < current.price_ticks: return current
    return Protection(tranche_id,level,price_ticks,source_structure_id,1 if current is None else current.revision+1)


def _sell_fraction(timeframe:str, sell_class:int, role:TrancheRole) -> float:
    """级别越高/卖点越强，影响越深；低级别不能直接否定高级别核心仓。"""
    sell_class=max(1,min(3,int(sell_class)))
    if timeframe=="5m":
        table={
            1:{TrancheRole.TEST:0.50},
            2:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:0.50},
            3:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00},
        }
    elif timeframe=="30m":
        table={
            1:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:0.50},
            2:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:0.50},
            3:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:0.50},
        }
    elif timeframe=="120m":
        table={
            1:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:0.50},
            2:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:0.50},
            3:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:1.00},
        }
    elif timeframe=="daily":
        table={
            1:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:0.50},
            2:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:1.00,TrancheRole.CORE:0.50},
            3:{r:1.00 for r in TrancheRole},
        }
    elif timeframe=="weekly":
        table={
            1:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:1.00,TrancheRole.CORE:0.50},
            2:{TrancheRole.TEST:1.00,TrancheRole.TACTICAL:1.00,TrancheRole.TREND_ADD:1.00,TrancheRole.CONFIRMATION:1.00,TrancheRole.CORE:0.75},
            3:{r:1.00 for r in TrancheRole},
        }
    else:
        return 0.0
    return float(table[sell_class].get(role,0.0))


def map_sell_scope(trade:Trade, *, timeframe:str, sell_class:int) -> SellScope:
    fractions=[]
    affected=[]
    for tr in trade.tranches:
        fraction=_sell_fraction(timeframe,sell_class,tr.thesis.role)
        if fraction>0:
            affected.append(tr.id)
            fractions.append((tr.id,fraction))
    if (timeframe in ("daily","weekly")) and sell_class>=3:
        action=Action.EXIT
    elif timeframe in ("daily","weekly") and sell_class>=2:
        action=Action.REDUCE_CORE
    else:
        action=Action.REDUCE_TACTICAL
    unaffected=tuple(t.id for t in trade.tranches if t.id not in affected)
    return SellScope(tuple(affected),unaffected,action,tuple(fractions))

def target_exposure(trade:Trade, scope:SellScope) -> float:
    # 向后兼容旧SellScope：没有显式比例时，affected_tranche_ids仍表示整笔退出。
    fractions=dict(scope.reduction_fractions)
    if not fractions and scope.affected_tranche_ids:
        fractions={tranche_id:1.0 for tranche_id in scope.affected_tranche_ids}
    return sum(t.value*(1.0-fractions.get(t.id,0.0)) for t in trade.tranches)

def execute_exit(tranche:Tranche, *, expected_exit:float, actual_exit:float, reason:str, fraction:float=1.0) -> ExitRecord:
    fraction=max(0.0,min(1.0,float(fraction)))
    pnl=(actual_exit-tranche.entry_price)*(tranche.value*fraction/tranche.entry_price if tranche.entry_price else 0)
    return ExitRecord(tranche.id,expected_exit,actual_exit,actual_exit-expected_exit,reason,pnl)

def close_trade(trade:Trade) -> Trade:
    return replace(trade,state=TradeState.CLOSED,tranches=tuple(replace(t,thesis=replace(t.thesis,state=ThesisState.CLOSED,revision=t.thesis.revision+1)) for t in trade.tranches),revision=trade.revision+1)

def new_reentry_tracker(trade:Trade, parent_structure_id:str, failure_level:StopLevel, failures:int=1) -> ReentryTracker:
    state=ReentryState.LOCKED if failures>=3 else ReentryState.WATCH_NEW_STRUCTURE
    return ReentryTracker(trade.id,parent_structure_id,failure_level,failures,state)

def record_child_failure(tracker:ReentryTracker) -> ReentryTracker:
    n=tracker.child_failure_count+1
    return replace(tracker,child_failure_count=n,state=ReentryState.LOCKED if n>=3 else ReentryState.WATCH_NEW_STRUCTURE)

def allow_reentry(tracker:ReentryTracker, *, new_structure_id:str|None, fundamental_eligible:bool, same_parent:bool=True):
    if not fundamental_eligible:return replace(tracker,state=ReentryState.LOCKED)
    if tracker.state is ReentryState.LOCKED and same_parent:return tracker
    if not new_structure_id:return replace(tracker,state=ReentryState.WATCH_NEW_STRUCTURE)
    return replace(tracker,state=ReentryState.ELIGIBLE)
