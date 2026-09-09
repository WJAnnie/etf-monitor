from __future__ import annotations
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from trading_skill.decision import Action
from trading_skill.sizing import StopCandidate, StopLevel, TrancheRole
from trading_skill.domain.models import stable_id
from trading_skill.strategy_policy import sell_fraction

class TradeState(StrEnum):
    PLANNED="PLANNED"; OPENING="OPENING"; ACTIVE="ACTIVE"; PYRAMIDING="PYRAMIDING"; PROFIT_PROTECTED="PROFIT_PROTECTED"; REDUCING="REDUCING"; CLOSED="CLOSED"
class ThesisState(StrEnum): ACTIVE="ACTIVE"; INVALIDATED="INVALIDATED"; PROMOTED="PROMOTED"; CLOSED="CLOSED"
class ProfitLockState(StrEnum): NONE="NONE"; INITIAL="INITIAL"; PROTECTED="PROTECTED"; LOCKED="LOCKED"; STRUCTURAL_EXIT_PENDING="STRUCTURAL_EXIT_PENDING"
class BreakState(StrEnum): LOWER_LEVEL_WARNING="LOWER_LEVEL_WARNING"; WICK_BREAK="WICK_BREAK"; CLOSE_BREAK="CLOSE_BREAK"; BREAK_AND_FAILED_RECLAIM="BREAK_AND_FAILED_RECLAIM"
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
class ProtectionEvaluation:
    state:BreakState|None
    confirmed_failure:bool
    exit_managed_tranche:bool
    reason:str
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


def evaluate_protection_break(
    protection:Protection,
    *,
    observed_level:StopLevel,
    low_ticks:int,
    close_ticks:int,
    bar_complete:bool,
    reclaim_attempt_completed:bool=False,
    reclaim_close_ticks:int|None=None,
) -> ProtectionEvaluation:
    """结构止损必须由该笔仓位自己的管理周期确认，低周期噪声只能预警。

    例如L120保护位：5分钟/30分钟跌破只能提前预警；完成的120分钟K线收在保护位下方才构成
    CLOSE_BREAK。若之后完成的同级别反抽仍无法收回保护位，则升级为BREAK_AND_FAILED_RECLAIM。
    """
    threshold=protection.price_ticks
    breached=low_ticks<threshold or close_ticks<threshold
    if not breached:
        return ProtectionEvaluation(None,False,False,"保护位未被触及")
    if observed_level is not protection.level or not bar_complete:
        return ProtectionEvaluation(BreakState.LOWER_LEVEL_WARNING,False,False,"低于管理周期的跌破或未完成K线只做预警，不直接否定主结构")
    if reclaim_attempt_completed and reclaim_close_ticks is not None and reclaim_close_ticks<threshold:
        return ProtectionEvaluation(BreakState.BREAK_AND_FAILED_RECLAIM,True,True,"同管理周期跌破后反抽仍无法收回保护位，确认该笔交易逻辑失效")
    if close_ticks<threshold:
        return ProtectionEvaluation(BreakState.CLOSE_BREAK,True,True,"同管理周期完成K线收盘跌破结构保护位，确认该笔仓位需要退出")
    return ProtectionEvaluation(BreakState.WICK_BREAK,False,False,"同管理周期仅影线跌破、收盘收回，先预警并等待后续确认")


def map_sell_scope(trade:Trade, *, timeframe:str, sell_class:int) -> SellScope:
    fractions=[]
    affected=[]
    for tr in trade.tranches:
        fraction=sell_fraction(timeframe,sell_class,tr.thesis.role)
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
