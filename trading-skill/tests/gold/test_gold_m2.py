from __future__ import annotations
from datetime import datetime, timedelta, timezone
from trading_skill.chan.center import CenterMotion, classify_center_relation, extend_center, motion_leaves_core, seed_center
from trading_skill.domain.enums import CenterRelationType, Direction, Timeframe

BASE=datetime(2026,1,1,tzinfo=timezone.utc)

def m(i,d,lo,hi):
    return CenterMotion(f"m{i}",Timeframe.DAILY,0,d,lo,hi,BASE+timedelta(days=i),BASE+timedelta(days=i+1),BASE+timedelta(days=i+1,minutes=1),True)

def seed(offset=0, shift=0):
    return seed_center((m(offset,Direction.UP,5+shift,12+shift),m(offset+1,Direction.DOWN,7+shift,11+shift),m(offset+2,Direction.UP,6+shift,13+shift)),symbol="TEST").center

def test_gold_gs007_standard_center():
    c=seed()
    assert (c.zd_ticks,c.zg_ticks,c.dd_ticks,c.gg_ticks)==(7,11,5,13)

def test_gold_gs008_center_extension_core_does_not_drift():
    c=seed(); e=extend_center(c,m(3,Direction.DOWN,8,12)).center
    assert e.id==c.id
    assert (e.zd_ticks,e.zg_ticks)==(7,11)
    assert e.dd_ticks==5

def test_gold_gs008b_endpoint_leave_is_not_silently_recorded_as_extension():
    c=seed(); departure=m(3,Direction.DOWN,4,12)
    assert motion_leaves_core(c,departure)
    update=extend_center(c,departure)
    assert not update.result.valid
    assert "CENTER_STILL_LEAVING" in update.result.reason_codes
    assert update.center==c

def test_gold_gs009_center_expansion_not_independent_trend_center():
    a=seed()
    b=seed_center((m(10,Direction.UP,10,17),m(11,Direction.DOWN,12,16),m(12,Direction.UP,11,18)),symbol="TEST").center
    assert classify_center_relation(a,b).type is CenterRelationType.EXPANSION_PENDING
