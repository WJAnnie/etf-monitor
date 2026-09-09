from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from scripts.run_full_a_scan_v3 import _execution_structure_ok, _major_negative_industry_events, choose_primary_v3, parent_valid_v3
from scripts.send_full_a_report_v3 import _valuation
from trading_skill.a_share_universe import IndustryCandidate
from trading_skill.chan.signals import ChanSignal
from trading_skill.chan_extensions import annotate_second_buy_variants
from trading_skill.decision import Blocker, RiskState, blockers_for
from trading_skill.domain.enums import ChanSignalType, SignalState, Timeframe
from trading_skill.industry_financial_metrics import summarize_sector_metrics
from trading_skill.industry_intelligence import industry_identity_keywords, match_industry_events, recent_report_event
from trading_skill.industry_profiles import profile_for
from trading_skill.industry_prospects import industry_rotation_state
from trading_skill.position import add_tranche, create_trade, map_sell_scope, target_exposure
from trading_skill.production_chan import ProductionChanResult
from trading_skill.sizing import StopCandidate, StopLevel, StopType, TrancheRole
from trading_skill.strategy_policy import entry_permission, primary_entry_timeframes, sell_fraction


def _signal(kind: ChanSignalType, *, side="BUY", price=1000, when=None):
    when = when or datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    return ChanSignal(
        id=f"sig-{kind.value}-{when.isoformat()}", symbol="000001", standard_types=(kind,), extended_types=(), side=side,
        level_rank=1, timeframe="30m", state=SignalState.CONFIRMED, structural_price_ticks=price,
        structural_timestamp=when, confirmation_timestamp=when, anchor_ids=(), evidence_ids=(),
    )


def _stop(level=StopLevel.LD):
    return StopCandidate("s", level, StopType.BUY_POINT_INVALIDATION, 900, "structure")


def _result(timeframe: Timeframe, signals=(), *, trend="UPTREND", divergence=None):
    return SimpleNamespace(
        timeframe=timeframe,
        signals=tuple(signals),
        status="OK",
        trend_classification=trend,
        divergence_state=divergence,
    )


def test_primary_entries_exclude_weekly_and_5m_and_daily_first_buy_waits():
    assert Timeframe.WEEKLY not in primary_entry_timeframes()
    assert Timeframe.M5 not in primary_entry_timeframes()
    assert set(primary_entry_timeframes()) == {Timeframe.DAILY, Timeframe.M120, Timeframe.M30}
    assert "等待标准二买" in entry_permission(Timeframe.DAILY, ChanSignalType.FIRST_BUY)
    assert "执行确认" in entry_permission(Timeframe.M5, ChanSignalType.SECOND_BUY)
    assert "小试仓" in entry_permission(Timeframe.M120, ChanSignalType.FIRST_BUY)
    assert "只观察" in entry_permission(Timeframe.M30, ChanSignalType.FIRST_BUY)


def test_legacy_primary_category_order_does_not_let_30m_second_buy_override_daily_third_buy():
    when = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    daily_third = _signal(ChanSignalType.THIRD_BUY, when=when)
    m30_second = _signal(ChanSignalType.SECOND_BUY, when=when + timedelta(minutes=30))
    picked = choose_primary_v3(
        {
            Timeframe.DAILY: _result(Timeframe.DAILY, (daily_third,)),
            Timeframe.M30: _result(Timeframe.M30, (m30_second,)),
        },
        as_of=when + timedelta(hours=1),
    )
    assert picked is not None and picked[0] is Timeframe.DAILY


def test_parent_context_uses_latest_formal_signal_not_any_old_sell():
    when = datetime(2026, 9, 9, 9, 0, tzinfo=timezone.utc)
    old_sell = _signal(ChanSignalType.FIRST_SELL, side="SELL", when=when)
    new_buy = _signal(ChanSignalType.SECOND_BUY, side="BUY", when=when + timedelta(hours=1))
    weekly = _result(Timeframe.WEEKLY, (old_sell, new_buy), trend="UPTREND")
    assert parent_valid_v3({Timeframe.WEEKLY: weekly}, Timeframe.DAILY, as_of=when + timedelta(hours=2)) is True
    newer_sell = _signal(ChanSignalType.SECOND_SELL, side="SELL", when=when + timedelta(hours=1, minutes=30))
    weekly2 = _result(Timeframe.WEEKLY, (old_sell, new_buy, newer_sell), trend="UPTREND")
    assert parent_valid_v3({Timeframe.WEEKLY: weekly2}, Timeframe.DAILY, as_of=when + timedelta(hours=2)) is False


def test_execution_structure_conflict_is_a_real_blocker():
    blockers = blockers_for(
        signal=_signal(ChanSignalType.SECOND_BUY), fundamental_eligible=True, stop_defined=True,
        risk=RiskState.L0, technical=None, parent_valid=True, data_complete=True,
        portfolio_permission=True, execution_structure_ok=False, allow_daily_first_buy=True,
    )
    assert Blocker.EXECUTION_STRUCTURE_CONFLICT in blockers


def test_execution_confirmation_pending_has_separate_blocker_taxonomy():
    blockers = blockers_for(
        signal=_signal(ChanSignalType.SECOND_BUY), fundamental_eligible=True, stop_defined=True,
        risk=RiskState.L0, technical=None, parent_valid=True, data_complete=True,
        portfolio_permission=True, execution_structure_ok=True, execution_confirmation_ready=False,
        allow_daily_first_buy=True,
    )
    assert Blocker.EXECUTION_CONFIRMATION_PENDING in blockers
    assert Blocker.EXECUTION_STRUCTURE_CONFLICT not in blockers


def test_120m_child_buy_plus_5m_support_confirms_execution_after_older_sell():
    base_time = datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc)
    candidate = {
        "timeframe": "120m",
        "signal_confirmation_time": base_time.isoformat(),
        "execution_maturity": "TRIGGERED",
    }
    analysis = {
        "timeframes": {
            "30m": {
                "status": "OK",
                "signals": [
                    {"side": "SELL", "types": ["FIRST_SELL"], "confirmation_timestamp": (base_time + timedelta(minutes=30)).isoformat()},
                    {"side": "BUY", "types": ["SECOND_BUY"], "confirmation_timestamp": (base_time + timedelta(minutes=60)).isoformat()},
                ],
            },
            "5m": {
                "status": "OK",
                "signals": [],
                "technical": {"confirmation": "SUPPORT"},
            },
        }
    }
    ok, conflicts, states = _execution_structure_ok(analysis, candidate)
    assert ok is True
    assert conflicts == []
    assert states["30m"].startswith("BUY:")
    assert states["5m_execution"] == "CONFIRMED:TECH_SUPPORT"


def test_no_child_sell_is_not_enough_when_5m_has_no_positive_confirmation():
    base_time = datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc)
    candidate = {
        "timeframe": "30m",
        "signal_confirmation_time": base_time.isoformat(),
        "execution_maturity": "TRIGGERED",
    }
    analysis = {
        "timeframes": {
            "5m": {
                "status": "OK",
                "signals": [],
                "technical": {"confirmation": "NEUTRAL"},
            }
        }
    }
    ok, conflicts, states = _execution_structure_ok(analysis, candidate)
    assert ok is False
    assert states["5m_execution"] == "WAITING_5M_CONFIRMATION"
    assert any("未达到SUPPORT" in item for item in conflicts)


def test_5m_formal_buy_can_confirm_execution_even_when_technical_is_neutral():
    base_time = datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc)
    candidate = {
        "timeframe": "30m",
        "signal_confirmation_time": base_time.isoformat(),
        "execution_maturity": "PREPARE",
    }
    analysis = {
        "timeframes": {
            "5m": {
                "status": "OK",
                "signals": [
                    {"side": "BUY", "types": ["SECOND_BUY"], "confirmation_timestamp": (base_time + timedelta(minutes=10)).isoformat()},
                ],
                "technical": {"confirmation": "NEUTRAL"},
            }
        }
    }
    ok, conflicts, states = _execution_structure_ok(analysis, candidate)
    assert ok is True
    assert conflicts == []
    assert states["5m_execution"] == "CONFIRMED:FORMAL_BUY"


def test_latest_child_sell_blocks_current_execution_but_not_parent_thesis():
    base_time = datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc)
    candidate = {
        "timeframe": "30m",
        "signal_confirmation_time": base_time.isoformat(),
        "execution_maturity": "TRIGGERED",
    }
    analysis = {
        "timeframes": {
            "5m": {
                "status": "OK",
                "signals": [
                    {"side": "BUY", "types": ["SECOND_BUY"], "confirmation_timestamp": (base_time + timedelta(minutes=10)).isoformat()},
                    {"side": "SELL", "types": ["FIRST_SELL"], "confirmation_timestamp": (base_time + timedelta(minutes=20)).isoformat()},
                ],
                "technical": {"confirmation": "SUPPORT"},
            }
        }
    }
    ok, conflicts, states = _execution_structure_ok(analysis, candidate)
    assert ok is False
    assert "5分钟最新正式结构仍为卖点" in conflicts
    assert states["5m"].startswith("SELL:")
    assert states["5m_execution"] == "CONFLICT"


def test_execution_waits_when_price_is_outside_trigger_or_prepare_zone():
    base_time = datetime(2026, 9, 9, 9, 30, tzinfo=timezone.utc)
    candidate = {
        "timeframe": "30m",
        "signal_confirmation_time": base_time.isoformat(),
        "execution_maturity": "WATCH",
    }
    analysis = {
        "timeframes": {
            "5m": {
                "status": "OK",
                "signals": [
                    {"side": "BUY", "types": ["SECOND_BUY"], "confirmation_timestamp": (base_time + timedelta(minutes=10)).isoformat()},
                ],
                "technical": {"confirmation": "SUPPORT"},
            }
        }
    }
    ok, conflicts, states = _execution_structure_ok(analysis, candidate)
    assert ok is False
    assert states["5m_execution"] == "WAITING_PRICE"
    assert any("WATCH" in item for item in conflicts)


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
    assert sell_fraction("daily", 2, TrancheRole.CORE) == 0.5


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


def test_dynamic_rotation_industries_use_specific_profiles_not_generic_template():
    assert profile_for("院线", None).name == "影视院线/传媒"
    assert profile_for("专业连锁Ⅱ", None).name == "零售/专业连锁"
    assert profile_for("橡胶助剂", None).name == "化工/橡胶"
    assert profile_for("航空装备Ⅱ", None).name == "商业航天与军工电子"
    assert profile_for("光伏设备", None).name == "光伏与新能源制造"


def test_industry_news_identity_keywords_exclude_generic_order_word():
    keywords = industry_identity_keywords({"name": "机器人", "prospect_theme": "机器人与高端自动化"})
    assert "订单" not in keywords
    as_of = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    industries = [{"name": "机器人", "prospect_theme": "机器人与高端自动化"}]
    unrelated = [{"time": "2026-09-09 10:00:00", "content": "某造船企业获得重大订单", "source": "测试"}]
    assert match_industry_events(industries, unrelated, as_of=as_of) == {}


def test_industry_news_and_recent_report_are_structured_without_network():
    as_of = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
    industries = [{"name": "船舶制造", "prospect_theme": "造船与海工"}]
    news = [{"time": "2026-09-09 10:00:00", "content": "船舶制造企业获得重大订单，新接订单增长", "source": "测试财经"}]
    matched = match_industry_events(industries, news, as_of=as_of)
    assert matched["船舶制造"][0]["impact"] == "利好"
    assert matched["船舶制造"][0]["source"] == "测试财经"
    report = recent_report_event(
        {"NOTICE_DATE": "2026-09-08", "REPORTDATE": "2026-06-30", "YSTZ": 20, "SJLTZ": 30, "WEIGHTAVG_ROE": 8},
        as_of=as_of,
    )
    assert report and report["report_type"] == "中报"


def test_major_negative_industry_event_is_explicit_new_entry_pause():
    events = [
        {"importance": "重大", "impact": "利空", "content": "行业重大限制政策"},
        {"importance": "重要", "impact": "利好", "content": "普通利好"},
    ]
    assert _major_negative_industry_events({"events": events}) == [events[0]]


def test_sector_specific_report_metrics_prioritize_shipbuilding_and_dynamic_cycle_sector():
    metrics = {
        "changes": {
            "contract_liabilities_change_pct": 18.5,
            "construction_in_progress_change_pct": 12.0,
            "fixed_asset_change_pct": 5.0,
            "inventory_change_pct": -8.0,
            "operating_cash_flow_change_pct": -3.0,
        }
    }
    ship = summarize_sector_metrics("造船与海工", metrics)
    assert ship[0].startswith("合同负债同比")
    assert any(item.startswith("在建工程同比") for item in ship)
    chemical = summarize_sector_metrics("化工/橡胶", metrics)
    assert chemical[0].startswith("存货同比")
    bank = summarize_sector_metrics("银行", metrics)
    assert len(bank) == 1 and "净息差" in bank[0] and "不良率" in bank[0]


def test_valuation_display_keeps_loss_making_pe_context():
    assert "亏损期" in _valuation(-12.3, kind="PE")
    assert _valuation(25.2, kind="PE") == "25.20"
    assert "不适用" in _valuation(-0.5, kind="PB")
