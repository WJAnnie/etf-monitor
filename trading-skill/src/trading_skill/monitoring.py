from __future__ import annotations
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum, StrEnum
from typing import Iterable, Callable
from trading_skill.decision import Action, RiskState
from trading_skill.domain.models import stable_id

class MonitoringLevel(IntEnum): M0=0; M1=1; M2=2; M3=3
class AlertPriority(IntEnum): P1_INFORMATION=1; P2_ATTENTION=2; P3_NEW_BUY=3; P3_POSITION_ACTION=4; P4_POSITION_RISK=5
class DeliveryStatus(StrEnum): PENDING="PENDING"; SUCCESS="SUCCESS"; FAILED_RETRYABLE="FAILED_RETRYABLE"; FAILED_FINAL="FAILED_FINAL"; SKIPPED_DISABLED="SKIPPED_DISABLED"
class HealthStatus(StrEnum): HEALTHY="HEALTHY"; DEGRADED="DEGRADED"; FAILED="FAILED"; UNKNOWN="UNKNOWN"

@dataclass(frozen=True, slots=True)
class MonitoringState:
    symbol:str; current_level:MonitoringLevel; previous_level:MonitoringLevel; reasons:tuple[str,...]; revision:int=1
@dataclass(frozen=True, slots=True)
class ScanRequest:
    scan_id:str; trigger_type:str; scheduled_slot:str|None; symbols:tuple[str,...]; requested_depth:MonitoringLevel; priority:int; as_of:datetime
@dataclass(frozen=True, slots=True)
class Alert:
    alert_id:str; symbol:str; priority:AlertPriority; alert_type:str; action:Action|None; old_state:str|None; new_state:str|None
    reason_codes:tuple[str,...]; analysis_id:str; snapshot_id:str; event_time:datetime; dedup_key:str
@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    delivery_id:str; alert_id:str; channel:str; status:DeliveryStatus; attempt:int; error_code:str|None=None
@dataclass(frozen=True, slots=True)
class SystemHealthSnapshot:
    overall_status:HealthStatus; components:dict[str,HealthStatus]; new_trade_permission:bool; position_risk_monitor_permission:bool; reasons:tuple[str,...]

SCHEDULE=("PREMARKET","10:30","11:30","13:30","14:30","14:50","AFTER_CLOSE")
def is_trading_day(d:date, holidays:set[date]|None=None):
    return d.weekday()<5 and d not in (holidays or set())
def schedule_for_day(d:date, holidays:set[date]|None=None):
    return SCHEDULE if is_trading_day(d,holidays) else ()

def update_monitoring(state:MonitoringState|None, *, symbol:str, open_position:bool=False,
    triggered:bool=False, prepare:bool=False, risk:RiskState=RiskState.L0, watch:bool=False,
    ordinary_demote_confirmed:bool=True):
    old=state.current_level if state else MonitoringLevel.M0
    if open_position or triggered or risk>=RiskState.L2: target=MonitoringLevel.M3
    elif prepare: target=MonitoringLevel.M2
    elif watch: target=MonitoringLevel.M1
    else: target=MonitoringLevel.M0
    if target<old and not ordinary_demote_confirmed: target=old
    return MonitoringState(symbol,target,old,("OPEN_POSITION",) if open_position else (),1 if state is None else state.revision+1)

def build_alert(*, symbol:str, analysis_id:str, snapshot_id:str, action:Action|None, risk:RiskState,
    old_state:str|None, new_state:str|None, event_time:datetime, reason_codes:tuple[str,...]=()):
    if risk>=RiskState.L4: p=AlertPriority.P4_POSITION_RISK; typ="POSITION_RISK"
    elif action in (Action.EXIT,Action.REDUCE_CORE,Action.REDUCE_TACTICAL): p=AlertPriority.P3_POSITION_ACTION; typ="POSITION_ACTION"
    elif action in (Action.BUY_TRANCHE_1,Action.ADD_TRANCHE_2,Action.ADD_TREND): p=AlertPriority.P3_NEW_BUY; typ="NEW_BUY"
    elif action in (Action.PREPARE_BUY,Action.WAIT_2B,Action.PAUSE_ADD) or risk>=RiskState.L1: p=AlertPriority.P2_ATTENTION; typ="ATTENTION"
    else: p=AlertPriority.P1_INFORMATION; typ="INFORMATION"
    dedup=stable_id("dedup",symbol,typ,new_state,action,risk)
    aid=stable_id("alert",analysis_id,symbol,typ,new_state,action,risk)
    return Alert(aid,symbol,p,typ,action,old_state,new_state,reason_codes,analysis_id,snapshot_id,event_time,dedup)

class AlertDeduper:
    def __init__(self): self.sent:set[str]=set(); self.p2_daily:dict[tuple[date,str],int]={}
    def should_send(self, alert:Alert):
        if alert.dedup_key in self.sent:return False
        # Risk/action state changes bypass the ordinary P2 daily cap.
        if alert.old_state is not None and alert.new_state is not None and alert.old_state != alert.new_state:
            return True
        key=(alert.event_time.date(),alert.symbol)
        if alert.priority is AlertPriority.P2_ATTENTION and self.p2_daily.get(key,0)>=1:return False
        return True
    def record(self,alert:Alert):
        self.sent.add(alert.dedup_key)
        if alert.priority is AlertPriority.P2_ATTENTION:
            key=(alert.event_time.date(),alert.symbol); self.p2_daily[key]=self.p2_daily.get(key,0)+1

class ChannelAdapter:
    name="BASE"
    def __init__(self, *, enabled:bool, sender:Callable[[Alert],None]|None=None, secret_loader:Callable[[],str]|None=None):
        self.enabled=enabled; self.sender=sender; self.secret_loader=secret_loader
    def deliver(self,alert:Alert,attempt=1):
        did=stable_id("delivery",alert.alert_id,self.name)
        if not self.enabled:return DeliveryRecord(did,alert.alert_id,self.name,DeliveryStatus.SKIPPED_DISABLED,attempt)
        try:
            if self.secret_loader:self.secret_loader()
            if self.sender:self.sender(alert)
            return DeliveryRecord(did,alert.alert_id,self.name,DeliveryStatus.SUCCESS,attempt)
        except PermissionError:
            return DeliveryRecord(did,alert.alert_id,self.name,DeliveryStatus.FAILED_FINAL,attempt,"AUTH")
        except Exception:
            return DeliveryRecord(did,alert.alert_id,self.name,DeliveryStatus.FAILED_RETRYABLE,attempt,"TRANSIENT")

class FeishuAdapter(ChannelAdapter): name="FEISHU"
class GitHubAdapter(ChannelAdapter): name="GITHUB"
class ServerChanAdapter(ChannelAdapter): name="SERVERCHAN"

def dispatch(alert:Alert, adapters:Iterable[ChannelAdapter]):
    return tuple(a.deliver(alert) for a in adapters)

def system_health(components:dict[str,HealthStatus], *, open_position_data_available:bool=True):
    reasons=[]; critical=("PERSISTENCE","CHAN_ENGINE")
    trade_permission=all(components.get(k,HealthStatus.HEALTHY) is HealthStatus.HEALTHY for k in critical) and components.get("MARKET_DATA",HealthStatus.HEALTHY) is not HealthStatus.FAILED
    position_permission=open_position_data_available and components.get("PERSISTENCE",HealthStatus.HEALTHY) is not HealthStatus.FAILED
    if not open_position_data_available: reasons.append("POSITION_DATA_MISSING")
    vals=list(components.values())
    if any(v is HealthStatus.FAILED for v in vals): overall=HealthStatus.DEGRADED if position_permission or trade_permission else HealthStatus.FAILED
    elif any(v is HealthStatus.DEGRADED for v in vals): overall=HealthStatus.DEGRADED
    else: overall=HealthStatus.HEALTHY
    return SystemHealthSnapshot(overall,components,trade_permission,position_permission,tuple(reasons))
