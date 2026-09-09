from __future__ import annotations

from datetime import datetime, timezone

from trading_skill.chan.signals import ChanSignal
from trading_skill.chan_extensions import annotate_second_buy_variants
from trading_skill.decision import Blocker, Opportunity, OpportunityGrade, RiskState, blockers_for
from trading_skill.domain.enums import ChanSignalType, SignalState, Timeframe
from trading_skill.industry_intelligence import match_industry_events, recent_report_event
from trading_skill.industry_profiles import profile_for
from trading_skill.industry_prospects import industry_rotation_state
from trading_skill.a_share_universe import IndustryCandidate
from trading_skill.position import add_tranche, create_trade, map_sell_scope, target_exposure
from trading_skill.production_chan import ProductionChanResult
from trading_skill.sizing import StopCandidate, StopLevel, StopType, TrancheRole
from trading_skill.strategy_policy import primary_entry_timeframes, entry_permission


def _signal(kind: ChanSignalType, *, side="BUY", price=1000, when=None):
    when = when or datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    return ChanSignal(
        id=f"sig-{kind.value}", symbol="000001", standard_types=(kind,), extended_types=(), side=side,
        level_rank=1, timeframe="30m", state=SignalState.CONFIRMED, structural_price_ticks=price,
        structural_timestamp=when, confirmation_timestamp=when, anchor_ids=(), evidence_ids=(),
    )


def _stop(level=StopLevel.LD):
    return StopCandidate("s", level, StopType.BUY_POINT_INVALIDATION, 900, "structure")


def test_primary_entries_exclude_weekly_and_5m_and_daily_first_buy_waits():
    assert Timeframe.WEEKLY not in primary_entry_timeframes()
    assert Timeframe.M5 not in primary_entry_timeframes()
    assert set(primary_entry_timeframes()) == {Timeframe.DAILY, Timeframe.M120, Timeframe.M30}
    assert "等待二买" in entry_permission(Timeframe.DAILY, ChanSignalType.FIRST_BUY)
    assert "执行确认" in entry_permission(Timeframe.M5, ChanSignalType.SECOND_BUY)


def test_execution_structure_conflict_is_a_real_blocker():
    blockers = blockers_for(
        signal=_signal(ChanSignalType.SECOND_BUY), fundamental_eligible=True, stop_defined=True,
        risk=RiskState.L0, technical=None, parent_valid=True, data_complete=True,
        portfolio_permission=True, execution_structure_ok=False, allow_daily_first_buy=True,
    )
    assert Blocker.EXECUTION_STRUCTURE_CONFLICT in blockers


def test_strong_class2_is_only_annotation_on_standard_second_buy():
    when = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    second = _signal(ChanSignalType.SECOND_BUY, when=when)
    third = _signal(ChanSignalType.THIRD_BUY, when=when)
    result = ProductionChanResult(
        "OK", Timeframe.M30, 100, 100, 100, 10, 6, 4, 4, (), None, None, None, None,
        (second, third), None, 10.0, (),
    )
    annotated = annotate_second_buy_variants(result)
    updated_second = next(s for s in annotated.signals if ChanSignalType.SECOND_BUY in s.standard_types)
    assert ChanSignalType.SECOND_BUY in updated_second.standard_types
    assert ChanSignalType.STRONG_CLASS2_BUY in updated_second.extended_types
    assert ChanSignalType.STRONG_CLASS2_BUY not in updated_second.standard_types


def test_sell_scope_is_hierarchical_and_daily_second_sell_is_not_full_exit():
    trade = create_trade("000001", datetime(2026, 9, 9, tzinfo=timezone.utc))
    trade = add_tranche(trade, value=10000, entry_price=10, role=TrancheRole.TACTICAL, management_level=StopLevel.L30,
                        entry_signal_id="a", parent_structure_id=None, stop=_stop(StopLevel.L30))
    trade = add_tranche(trade, value=20000, entry_price=10, role=TrancheRole.CORE, management_level=StopLevel.LD,
                        entry_signal_id="b", parent_structure_id=None, stop=_stop(StopLevel.LD))
    five = map_sell_scope(trade, timeframe="5m", sell_class=3)
    assert trade.tranches[1].id not in five.affected_tranche_ids
    daily2 = map_sell_scope(trade, timeframe="daily", sell_class=2)
    fractions = dict(daily2.reduction_fractions)
    assert fractions[trade.tranches[0].id] == 1.0
    assert fractions[trade.tranches[1].id] == 0.5
    assert target_exposure(trade, daily2) == 10000
    daily3 = map_sell_scope(trade, timeframe="daily", sell_class=3)
    assert target_exposure(trade, daily3) == 0


def test_weekly_third_sell_can_exit_all():
    trade = create_trade("000001", datetime(2026, 9, 9, tzinfo=timezone.utc))
    trade = add_tranche(trade, value=20000, entry_price=10, role=TrancheRole.CORE, management_level=StopLevel.LW,
                        entry_signal_id="w", parent_structure_id=None, stop=_stop(StopLevel.LW))
    scope = map_sell_scope(trade, timeframe="weekly", sell_class=3)
    assert target_exposure(trade, scope) == 0


def test_industry_rotation_pauses_high_position_and_profiles_differ():
    high = IndustryCandidate("x", "船舶制造", 90, 5, 95, 80, "过热", 4, 52, 80, 1, .8, prospect_theme="造船与海工")
    assert industry_rotation_state(high) == "高位暂退"
    ship = profile_for("船舶制造", "造船与海工")
    bank = profile_for("银行", None)
    assert "手持订单" in ship.operating_focus
    assert any("PB" in item for item in bank.valuation_focus)
    assert ship.name != bank.name


def test_industry_news_and_recent_report_are_structured_without_network():
    as_of = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    industries = [{"name": "船舶制造", "prospect_theme": "造船与海工"}]
    news = [{"time": "2026-09-09 10:00:00", "content": "船舶制造企业获得重大订单，新接订单增长"}]
    matched = match_industry_events(industries, news, as_of=as_of)
    assert matched["船舶制造"][0]["impact"] == "利好"
    report = recent_report_event(
        {"NOTICE_DATE": "2026-09-08", "REPORTDATE": "2026-06-30", "YSTZ": 20, "SJLTZ": 30, "WEIGHTAVG_ROE": 8},
        as_of=as_of,
    )
    assert report and report["report_type"] == "中报"
