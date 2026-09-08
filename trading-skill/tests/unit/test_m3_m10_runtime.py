from datetime import datetime, timedelta, date
from zoneinfo import ZoneInfo
import pytest

from trading_skill.chan.center import Center
from trading_skill.chan.divergence import MacdLegEvidence, StructuralLeg, evaluate_trend_divergence
from trading_skill.chan.signals import (
    ChanSignal, LowerMove, ReversalAnchor, first_buy_or_sell, new_second_buy_tracker,
    new_third_buy_tracker, overlap_signals, second_buy_step, third_buy_step,
)
from trading_skill.chan.trend import classify_trend, complete_trend
from trading_skill.domain.enums import (
    CenterState, ChanSignalType, Direction, DivergenceState, SecondBuyTrackerState,
    SignalState, ThirdBuyTrackerState, Timeframe, TrendClassification, TrendState,
)
from trading_skill.mtf import *
from trading_skill.indicators import *
from trading_skill.decision import *
from trading_skill.sizing import *
from trading_skill.position import *
from trading_skill.persistence import *
from trading_skill.monitoring import *

TZ=ZoneInfo("Asia/Shanghai")
T0=datetime(2026,9,8,10,0,tzinfo=TZ)

def center(n,dd,gg,zd,zg):
    return Center(id=f"c{n}",symbol="X",source_timeframe=Timeframe.DAILY,level_rank=1,state=CenterState.CONFIRMED,
        seed_motion_ids=(f"a{n}",f"b{n}",f"d{n}"),motion_ids=(f"a{n}",f"b{n}",f"d{n}"),
        zd_ticks=zd,zg_ticks=zg,dd_ticks=dd,gg_ticks=gg,d_ticks=dd,g_ticks=gg,
        structural_start_timestamp=T0+timedelta(days=n*3),structural_end_timestamp=T0+timedelta(days=n*3+2),
        confirmation_timestamp=T0+timedelta(days=n*3+2),revision=1)

def leg(name,direction,low,high,day):
    return StructuralLeg(name,direction,1,low,high,T0+timedelta(days=day-2),T0+timedelta(days=day),T0+timedelta(days=day))

def move(name,direction,low,high,day,completed=True):
    return LowerMove(name,direction,0,low,high,T0+timedelta(days=day),T0+timedelta(days=day),completed)

def signal(stype=ChanSignalType.SECOND_BUY):
    return ChanSignal("s","X",(stype,),(),"BUY",1,"daily",SignalState.CONFIRMED,100,T0,T0,("a",),("e",))

# M3 Trend / Divergence / canonical signals

def test_m3_one_center_is_current_consolidation_not_completed():
    t=classify_trend((center(0,100,200,120,180),),symbol="X").trend
    assert t.current_classification is TrendClassification.CONSOLIDATION
    assert t.final_type is None and t.state is TrendState.CLASSIFIABLE

def test_m3_downtrend_completion_is_conservative():
    c1=center(0,300,400,320,380); c2=center(1,180,260,200,240)
    t=classify_trend((c1,c2),symbol="X").trend
    assert t.current_classification is TrendClassification.DOWNTREND
    t2=complete_trend(t,lower_level_opposite_turn_completed=False,direct_extension_exists=False,confirmation_timestamp=T0+timedelta(days=9))
    assert t2.final_type is None
    t3=complete_trend(t2,lower_level_opposite_turn_completed=True,direct_extension_exists=False,confirmation_timestamp=T0+timedelta(days=10))
    assert t3.final_type is TrendClassification.DOWNTREND and t3.state is TrendState.COMPLETED

def test_m3_expansion_pending_cannot_be_trend():
    out=classify_trend((center(0,100,200,120,180),center(1,170,260,190,240)),symbol="X")
    assert not out.result.valid and out.trend.current_classification is TrendClassification.UNRESOLVED

def test_m3_macd_weakening_without_trend_is_not_standard_divergence():
    t=classify_trend((center(0,100,200,120,180),),symbol="X").trend
    b=leg("b",Direction.DOWN,80,150,4); c=leg("c",Direction.DOWN,70,140,6)
    d,res=evaluate_trend_divergence(t,b,c,MacdLegEvidence("b",10,5,4,3),MacdLegEvidence("c",5,3,2,2),lower_level_turn_completed=True,as_of=T0+timedelta(days=7))
    assert not res.valid and "NO_VALID_TREND" in d.reason_codes

def test_m3_confirmed_bottom_divergence_and_first_buy():
    c1=center(0,300,400,320,380); c2=center(1,180,260,200,240)
    t=complete_trend(classify_trend((c1,c2),symbol="X").trend,lower_level_opposite_turn_completed=True,direct_extension_exists=False,confirmation_timestamp=T0+timedelta(days=10))
    b=leg("b",Direction.DOWN,150,250,5); c=leg("c",Direction.DOWN,120,210,8)
    d,res=evaluate_trend_divergence(t,b,c,MacdLegEvidence("b",20,8,7,6),MacdLegEvidence("c",10,5,4,4),lower_level_turn_completed=True,as_of=T0+timedelta(days=10))
    assert res.valid and d.state is DivergenceState.CONFIRMED
    sig,sres=first_buy_or_sell(symbol="X",trend=t,divergence=d,structural_price_ticks=120,structural_timestamp=c.structural_end_timestamp)
    assert sres.valid and sig.standard_types==(ChanSignalType.FIRST_BUY,)

def test_m3_future_confirmation_blocked():
    c1=center(0,300,400,320,380); c2=center(1,180,260,200,240)
    t=complete_trend(classify_trend((c1,c2),symbol="X").trend,lower_level_opposite_turn_completed=True,direct_extension_exists=False,confirmation_timestamp=T0+timedelta(days=10))
    b=leg("b",Direction.DOWN,150,250,5); c=leg("c",Direction.DOWN,120,210,12)
    d,res=evaluate_trend_divergence(t,b,c,MacdLegEvidence("b",20,8,7,6),MacdLegEvidence("c",10,5,4,4),lower_level_turn_completed=True,as_of=T0+timedelta(days=10))
    assert not res.valid and "FUTURE_LEAKAGE_BLOCKED" in d.reason_codes

def test_m3_second_buy_can_be_below_anchor():
    a=ReversalAnchor("ra","X",Direction.UP,1,100,T0,True); tr=new_second_buy_tracker(a)
    tr,_=second_buy_step(tr,move("up",Direction.UP,100,140,1)); tr,sig=second_buy_step(tr,move("ret",Direction.DOWN,95,130,2))
    assert tr.state is SecondBuyTrackerState.SECOND_BUY_CONFIRMED and sig.structural_price_ticks==95

def test_m3_third_buy_equal_zg_valid_one_tick_below_invalid():
    c=center(0,100,200,120,180); tr=new_third_buy_tracker(c)
    tr,_=third_buy_step(tr,c,move("dep",Direction.UP,181,240,1)); tr,sig=third_buy_step(tr,c,move("ret",Direction.DOWN,180,230,2))
    assert tr.state is ThirdBuyTrackerState.THIRD_BUY_CONFIRMED and sig is not None
    tr2=new_third_buy_tracker(c); tr2,_=third_buy_step(tr2,c,move("dep2",Direction.UP,181,240,1)); tr2,sig2=third_buy_step(tr2,c,move("ret2",Direction.DOWN,179,230,2))
    assert tr2.state is ThirdBuyTrackerState.THIRD_BUY_FAILED and sig2 is None

def test_m3_second_third_overlap():
    a=ReversalAnchor("ra","X",Direction.UP,1,100,T0,True); tr=new_second_buy_tracker(a)
    tr,_=second_buy_step(tr,move("up",Direction.UP,100,200,1)); tr,s2=second_buy_step(tr,move("ret",Direction.DOWN,150,190,2))
    c=center(0,100,200,120,150); tt=new_third_buy_tracker(c); tt,_=third_buy_step(tt,c,move("dep",Direction.UP,151,240,1)); tt,s3=third_buy_step(tt,c,move("r3",Direction.DOWN,150,220,2))
    merged=overlap_signals(s2,s3)
    assert set(merged.standard_types)=={ChanSignalType.SECOND_BUY,ChanSignalType.THIRD_BUY}

# M4 MTF

def test_m4_parent_child_failure_semantics():
    p=StructureNodeRef("p","SECOND_BUY_RETRACEMENT",Timeframe.DAILY,1,T0,T0+timedelta(hours=4),T0)
    c=StructureNodeRef("c","FIRST_BUY",Timeframe.M30,0,T0+timedelta(hours=1),T0+timedelta(hours=2),T0+timedelta(hours=2))
    l=link_structures(p,c,StructureRelationship.RETRACEMENT_COMPONENT,ParentComponentType.SECOND_BUY_RETRACEMENT,child_location=ChildLocation.END_CANDIDATE,as_of=T0+timedelta(hours=3))
    assert l.valid
    ch=new_chain("X","p",ParentContext.CORE_PARENT_CONFIRMATION,TradeNature.TREND_PULLBACK)
    ch=advance_chain(ch,activation=ActivationLevel.M30,maturity=ExecutionMaturity.TRIGGERED,link_id=l.id)
    assert ch.state is NestingState.EXECUTION_READY
    assert child_failed(ch,"c",parent_failed=False).state is NestingState.ACTIVE
    assert child_failed(ch,"c",parent_failed=True).state is NestingState.FAILED

def test_m4_future_child_blocked():
    p=StructureNodeRef("p","parent",Timeframe.DAILY,1,T0,T0+timedelta(hours=4),T0)
    c=StructureNodeRef("c","child",Timeframe.M30,0,T0,T0+timedelta(hours=1),T0+timedelta(hours=5))
    l=link_structures(p,c,StructureRelationship.EXECUTES,ParentComponentType.SECOND_BUY_RETRACEMENT,as_of=T0+timedelta(hours=4))
    assert not l.valid and "FUTURE_LEAKAGE_BLOCKED" in l.reason_codes

def test_m4_top_down_activation():
    assert activation_for(daily_setup_active=False,m120_tail_active=True,m30_execution_candidate=True) is ActivationLevel.NONE
    assert activation_for(daily_setup_active=True,m120_tail_active=False,m30_execution_candidate=False) is ActivationLevel.M120
    assert activation_for(daily_setup_active=True,m120_tail_active=True,m30_execution_candidate=False) is ActivationLevel.M30
    assert activation_for(daily_setup_active=True,m120_tail_active=True,m30_execution_candidate=True) is ActivationLevel.M5

# M5 indicators

def test_m5_indicator_defaults_and_volume_thresholds():
    assert volume_state(79,100) is VolumeState.SHRINK
    assert volume_state(120,100) is VolumeState.MILD_EXPAND
    assert volume_state(200,100) is VolumeState.EXTREME
    assert macd([1,1,1,2,3,4,5,6])

def test_m5_boll_and_kdj_are_confirmation_only():
    closes=list(range(1,25)); p=boll(closes)
    assert p and p.upper>p.mid>p.lower
    k=kdj(closes,closes,closes)
    assert kdj_state(k) in tuple(KdjState)

def test_m5_provisional_bundle():
    closes=[10]*20+[9,8,7]; highs=[x+1 for x in closes]; lows=[x-1 for x in closes]; vols=[100]*22+[300]
    b=build_bundle(volumes=vols,closes=closes,highs=highs,lows=lows,bar_complete=False)
    assert b.provisional and b.confirmation in (TechnicalConfirmation.PAUSE,TechnicalConfirmation.CAUTION)

# M6 decision

def test_m6_opportunity_and_no_chan_cap():
    e=OpportunityEvidence(35,20,15,10,10,10)
    assert score_opportunity(e,chan_buy_eligible=True,stop_defined=True).grade is OpportunityGrade.S
    assert score_opportunity(e,chan_buy_eligible=False,stop_defined=True).grade is OpportunityGrade.C

def test_m6_s_plus_l2_is_pause_add():
    r=risk_state(RiskEvidence(structure=2),prior=RiskState.L0)
    o=score_opportunity(OpportunityEvidence(35,20,15,10,10,10),chan_buy_eligible=True,stop_defined=True,risk=r)
    b=blockers_for(signal=signal(),fundamental_eligible=True,stop_defined=True,risk=r,technical=None,parent_valid=True,data_complete=True,portfolio_permission=True)
    d=decide(opportunity=o,risk=r,signal=signal(),blockers=b,has_position=True,execution_maturity="TRIGGERED")
    assert d.action is Action.PAUSE_ADD

def test_m6_daily_first_buy_waits_2b():
    s=signal(ChanSignalType.FIRST_BUY); o=score_opportunity(OpportunityEvidence(35,20,15,10,10,10),chan_buy_eligible=True,stop_defined=True)
    b=blockers_for(signal=s,fundamental_eligible=True,stop_defined=True,risk=RiskState.L0,technical=None,parent_valid=True,data_complete=True,portfolio_permission=True,allow_daily_first_buy=False)
    assert decide(opportunity=o,risk=RiskState.L0,signal=s,blockers=b,has_position=False,execution_maturity="TRIGGERED").action is Action.WAIT_2B

def test_m6_risk_recovery_slow():
    assert risk_state(RiskEvidence(),prior=RiskState.L3,recovery_new_structure=False) is RiskState.L3
    assert risk_state(RiskEvidence(),prior=RiskState.L3,recovery_new_structure=True) is RiskState.L2

# M7 sizing

def test_m7_risk_sizing_caps_and_lot_rounding():
    st=StopCandidate("st",StopLevel.L30,StopType.EXECUTION_STRUCTURE,1900,"struct")
    p=build_position_plan(equity=1_000_000,grade=OpportunityGrade.A,risk=RiskState.L0,entry=20,stop=st,tick_size=.01,archetype="quality_name")
    assert round(p.stop_distance,4)==.05 and p.final_allowed_value<=100_000 and p.rounded_quantity%100==0 and p.planned_risk_after_rounding<=p.risk_amount

def test_m7_turnaround_cap_even_s():
    st=StopCandidate("st",StopLevel.L30,StopType.EXECUTION_STRUCTURE,1900,"struct")
    p=build_position_plan(equity=1_000_000,grade=OpportunityGrade.S,risk=RiskState.L0,entry=20,stop=st,tick_size=.01,archetype="turnaround")
    assert p.final_allowed_value<=60_000

def test_m7_l2_has_zero_new_risk():
    assert trade_risk_budget(equity=1_000_000,grade=OpportunityGrade.S,risk=RiskState.L2).risk_amount==0

def test_m7_stop_cannot_loosen():
    st=StopCandidate("st",StopLevel.L30,StopType.EXECUTION_STRUCTURE,1800,"struct")
    ok,reasons=validate_stop(st,planned_entry_ticks=2000,current_protection_ticks=1900)
    assert not ok and "PROTECTION_CANNOT_LOOSEN" in reasons

# M8 position

def test_m8_failed_test_cannot_silently_become_core():
    tr=create_trade("X",T0); st=StopCandidate("st",StopLevel.L30,StopType.EXECUTION_STRUCTURE,900,"s")
    tr=add_tranche(tr,value=20000,entry_price=10,role=TrancheRole.TEST,management_level=StopLevel.L30,entry_signal_id="sig",parent_structure_id="p",stop=st)
    st2=StopCandidate("st2",StopLevel.LD,StopType.CORE_STRUCTURE,800,"d")
    with pytest.raises(ValueError): promote_thesis(tr.tranches[0],new_role=TrancheRole.CORE,new_level=StopLevel.LD,new_stop=st2,new_structure_confirmed=True)

def test_m8_protection_only_rises():
    p=raise_protection(None,tranche_id="t",level=StopLevel.L30,price_ticks=100,source_structure_id="a",new_structure_confirmed=True)
    assert raise_protection(p,tranche_id="t",level=StopLevel.L30,price_ticks=90,source_structure_id="b",new_structure_confirmed=True).price_ticks==100

def test_m8_low_sell_does_not_exit_core():
    tr=create_trade("X",T0); st=StopCandidate("s",StopLevel.L30,StopType.EXECUTION_STRUCTURE,900,"x")
    tr=add_tranche(tr,value=20000,entry_price=10,role=TrancheRole.TEST,management_level=StopLevel.L30,entry_signal_id="a",parent_structure_id="p",stop=st)
    sd=StopCandidate("d",StopLevel.LD,StopType.CORE_STRUCTURE,800,"d")
    tr=add_tranche(tr,value=50000,entry_price=10,role=TrancheRole.CORE,management_level=StopLevel.LD,entry_signal_id="b",parent_structure_id="p",stop=sd)
    scope=map_sell_scope(tr,timeframe="5m",sell_class=3)
    assert len(scope.affected_tranche_ids)==1 and tr.tranches[1].id in scope.unaffected_tranche_ids

def test_m8_daily_third_sell_targets_zero():
    tr=create_trade("X",T0); st=StopCandidate("d",StopLevel.LD,StopType.CORE_STRUCTURE,800,"d")
    tr=add_tranche(tr,value=50000,entry_price=10,role=TrancheRole.CORE,management_level=StopLevel.LD,entry_signal_id="b",parent_structure_id="p",stop=st)
    scope=map_sell_scope(tr,timeframe="daily",sell_class=3)
    assert scope.action is Action.EXIT and target_exposure(tr,scope)==0

def test_m8_reentry_lock_and_gap_actual_fill():
    tr=create_trade("X",T0); r=record_child_failure(new_reentry_tracker(tr,"parent",StopLevel.L30,failures=2))
    assert r.state is ReentryState.LOCKED
    st=StopCandidate("d",StopLevel.LD,StopType.CORE_STRUCTURE,800,"d")
    tr=add_tranche(tr,value=10000,entry_price=10,role=TrancheRole.CORE,management_level=StopLevel.LD,entry_signal_id="b",parent_structure_id="p",stop=st)
    ex=execute_exit(tr.tranches[0],expected_exit=9,actual_exit=8,reason="GAP")
    assert ex.actual_exit==8 and ex.slippage==-1

# M9 persistence / replay

def test_m9_repository_transaction_tracker_event_snapshot(tmp_path):
    repo=StateRepository(tmp_path/"s.db"); payload={"x":1}; h=state_hash(payload)
    snap=Snapshot("sn","a","X",T0.isoformat(),payload,"v1","1",{"chan":"1"},h)
    ev=StateEvent("e1","X","tracker","t","CHANGE","A","B",(),(),T0.isoformat(),T0.isoformat(),"a")
    repo.transaction_commit(symbol="X",current=payload,trackers=[("t",{"state":"B"},2)],events=[ev],snapshot=snap)
    assert repo.load_current("X")==payload and repo.load_tracker("t")[1]==2 and repo.list_events("X")[0]["new_state"]=="B" and repo.load_snapshot("sn")["state_hash"]==h
    repo.close()

def test_m9_event_idempotency(tmp_path):
    repo=StateRepository(tmp_path/"s.db"); e=StateEvent("e","X","o","1","C",None,"A",(),(),T0.isoformat(),T0.isoformat(),"a")
    repo.append_event(e); repo.append_event(e); repo.db.commit(); assert len(repo.list_events("X"))==1; repo.close()

def test_m9_replay_future_blocked_and_atomic_checksum(tmp_path):
    c=ReplayClock(T0)
    with pytest.raises(ValueError): c.assert_visible(T0+timedelta(seconds=1))
    path=tmp_path/"out.json"; chk=atomic_publish(path,{"a":1}); assert verify_publish(path,chk)
    path.write_text('{"a":2}'); assert not verify_publish(path,chk)

def test_m9_migration_explicit():
    r=MigrationRegistry(); r.register("1","2",lambda p:{**p,"v":2}); assert r.migrate({"a":1},"1","2")["v"]==2
    with pytest.raises(ValueError): r.migrate({},"2","3")

# M10 production monitoring / alerts / delivery

def test_m10_open_position_forces_m3_and_weekend_skips():
    assert update_monitoring(None,symbol="X",open_position=True).current_level is MonitoringLevel.M3
    assert schedule_for_day(date(2026,9,12))==()

def test_m10_position_exit_alert_outranks_buy():
    a=build_alert(symbol="A",analysis_id="x",snapshot_id="s",action=Action.EXIT,risk=RiskState.L3,old_state=None,new_state="EXIT",event_time=T0)
    b=build_alert(symbol="B",analysis_id="x",snapshot_id="s",action=Action.BUY_TRANCHE_1,risk=RiskState.L0,old_state=None,new_state="BUY",event_time=T0)
    assert a.priority>b.priority

def test_m10_dedup_but_risk_change_bypasses_p2_cap():
    d=AlertDeduper(); a=build_alert(symbol="X",analysis_id="x",snapshot_id="s",action=Action.PREPARE_BUY,risk=RiskState.L0,old_state=None,new_state="PREPARE",event_time=T0)
    assert d.should_send(a); d.record(a); assert not d.should_send(a)
    b=build_alert(symbol="X",analysis_id="y",snapshot_id="s2",action=Action.PAUSE_ADD,risk=RiskState.L2,old_state="L1",new_state="L2",event_time=T0)
    assert d.should_send(b)

def test_m10_serverchan_disabled_never_reads_secret_and_feishu_succeeds():
    touched={"server":0,"feishu":0}
    server=ServerChanAdapter(enabled=False,secret_loader=lambda:touched.__setitem__("server",1))
    feishu=FeishuAdapter(enabled=True,sender=lambda a:touched.__setitem__("feishu",1))
    a=build_alert(symbol="X",analysis_id="x",snapshot_id="s",action=Action.HOLD,risk=RiskState.L0,old_state=None,new_state="HOLD",event_time=T0)
    recs=dispatch(a,[server,feishu])
    assert touched=={"server":0,"feishu":1} and recs[0].status is DeliveryStatus.SKIPPED_DISABLED and recs[1].status is DeliveryStatus.SUCCESS

def test_m10_channel_failure_isolated():
    bad=FeishuAdapter(enabled=True,sender=lambda a:(_ for _ in ()).throw(RuntimeError("x"))); good=GitHubAdapter(enabled=True,sender=lambda a:None)
    a=build_alert(symbol="X",analysis_id="x",snapshot_id="s",action=Action.HOLD,risk=RiskState.L0,old_state=None,new_state="HOLD",event_time=T0)
    recs=dispatch(a,[bad,good]); assert recs[0].status is DeliveryStatus.FAILED_RETRYABLE and recs[1].status is DeliveryStatus.SUCCESS

def test_m10_notification_failure_does_not_block_analysis_health():
    h=system_health({"MARKET_DATA":HealthStatus.HEALTHY,"PERSISTENCE":HealthStatus.HEALTHY,"CHAN_ENGINE":HealthStatus.HEALTHY,"FEISHU":HealthStatus.FAILED,"GITHUB":HealthStatus.HEALTHY})
    assert h.new_trade_permission and h.overall_status is HealthStatus.DEGRADED

def test_m10_position_data_missing_disables_position_monitor_permission():
    h=system_health({"MARKET_DATA":HealthStatus.HEALTHY,"PERSISTENCE":HealthStatus.HEALTHY,"CHAN_ENGINE":HealthStatus.HEALTHY},open_position_data_available=False)
    assert not h.position_risk_monitor_permission and "POSITION_DATA_MISSING" in h.reasons
