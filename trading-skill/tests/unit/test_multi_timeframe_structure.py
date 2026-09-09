from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_skill.domain.enums import Timeframe
from trading_skill.multi_timeframe_structure import (
    ParentContextState,
    StructurePhase,
    build_structure_book,
    evaluate_parent_context,
)


NOW = datetime(2026, 9, 9, 14, 0, tzinfo=timezone.utc)


def result(
    timeframe: str,
    *,
    trend: str | None,
    trend_state: str | None = "EXTENDING",
    divergence_type: str | None = None,
    divergence_state: str | None = None,
    center_state: str | None = "EXTENDING",
    signals: list[dict] | None = None,
    status: str = "OK",
):
    centers = []
    if center_state:
        centers.append(
            {
                "state": center_state,
                "confirmation": (NOW - timedelta(hours=1)).isoformat(),
            }
        )
    return {
        "status": status,
        "timeframe": timeframe,
        "completed_bars": 300,
        "centers": centers,
        "trend": {"classification": trend, "state": trend_state},
        "divergence": {"type": divergence_type, "state": divergence_state},
        "signals": signals or [],
        "latest_close": 10.0,
        "issues": [],
    }


def signal(side: str, kind: str, *, hours_ago: float = 1.0):
    return {
        "side": side,
        "types": [kind],
        "extended_types": [],
        "confirmation_timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }


def test_each_timeframe_gets_independent_structure_snapshot_without_signal_freshness_logic():
    results = {
        "weekly": result("weekly", trend="UPTREND"),
        "daily": result("daily", trend="CONSOLIDATION", center_state="LEAVING_UP"),
        "120m": result(
            "120m",
            trend="DOWNTREND",
            divergence_type="TREND_BOTTOM_DIVERGENCE",
            divergence_state="FORMING",
        ),
        "30m": result("30m", trend="UPTREND", signals=[signal("SELL", "FIRST_SELL")]),
        "5m": result("5m", trend="CONSOLIDATION"),
    }
    book = build_structure_book(results, as_of=NOW).by_timeframe()
    assert book[Timeframe.WEEKLY].phase is StructurePhase.BULL_TREND
    assert book[Timeframe.DAILY].phase is StructurePhase.BREAKOUT_UP
    assert book[Timeframe.M120].phase is StructurePhase.REVERSAL_UP_FORMING
    # 4B记录最近SELL这个4A事实，但不判断其fresh/active，因此不能改写当前UPTREND结构分类。
    assert book[Timeframe.M30].phase is StructurePhase.BULL_TREND
    assert book[Timeframe.M30].latest_signal.side == "SELL"
    assert book[Timeframe.M5].phase is StructurePhase.CONSOLIDATION


def test_parent_context_is_hierarchical_not_a_vote():
    results = {
        Timeframe.WEEKLY: result("weekly", trend="UPTREND"),
        Timeframe.DAILY: result("daily", trend="DOWNTREND"),
        Timeframe.M120: result("120m", trend="UPTREND"),
        Timeframe.M30: result("30m", trend="UPTREND"),
        Timeframe.M5: result("5m", trend="UPTREND"),
    }
    context = evaluate_parent_context(results, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert context.state is ParentContextState.BLOCKED
    # 即使120m/30m/5m都偏多，日线明确下跌仍不能靠低周期“投票”覆盖。


def test_downtrend_parent_with_bottom_divergence_is_permissive_not_supportive():
    results = {
        Timeframe.WEEKLY: result("weekly", trend="UPTREND"),
        Timeframe.DAILY: result(
            "daily",
            trend="DOWNTREND",
            divergence_type="TREND_BOTTOM_DIVERGENCE",
            divergence_state="CONFIRMED",
        ),
        Timeframe.M120: result("120m", trend="UPTREND"),
    }
    context = evaluate_parent_context(results, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert context.state is ParentContextState.PERMISSIVE


def test_parent_context_does_not_use_signal_age_or_freshness_because_that_belongs_to_step4c():
    recent_sell = {
        Timeframe.WEEKLY: result("weekly", trend="UPTREND"),
        Timeframe.DAILY: result("daily", trend="UPTREND", signals=[signal("SELL", "FIRST_SELL", hours_ago=2)]),
    }
    old_sell = {
        Timeframe.WEEKLY: result("weekly", trend="UPTREND"),
        Timeframe.DAILY: result(
            "daily",
            trend="UPTREND",
            signals=[
                {
                    "side": "SELL",
                    "types": ["FIRST_SELL"],
                    "extended_types": [],
                    "confirmation_timestamp": (NOW - timedelta(days=40)).isoformat(),
                }
            ],
        ),
    }
    recent = evaluate_parent_context(recent_sell, primary_timeframe=Timeframe.M120, as_of=NOW)
    old = evaluate_parent_context(old_sell, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert recent.state is ParentContextState.SUPPORTIVE
    assert old.state is ParentContextState.SUPPORTIVE
    # 是否为当前有效SELL、是否过期、是否阻断新交易，由4C生命周期叠加。


def test_parent_caution_is_only_a_structural_fact_not_an_order_permission():
    results = {
        Timeframe.WEEKLY: result(
            "weekly",
            trend="UPTREND",
            divergence_type="TREND_TOP_DIVERGENCE",
            divergence_state="FORMING",
        ),
        Timeframe.DAILY: result("daily", trend="UPTREND"),
    }
    context = evaluate_parent_context(results, primary_timeframe=Timeframe.DAILY, as_of=NOW)
    assert context.state is ParentContextState.CAUTION
    assert not hasattr(context, "allows_opportunity")
    assert not hasattr(context, "allows_immediate_entry")


def test_missing_parent_is_unresolved_and_cannot_be_silently_treated_as_support():
    results = {
        Timeframe.M120: result("120m", trend="UPTREND"),
    }
    context = evaluate_parent_context(results, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert context.state is ParentContextState.UNRESOLVED
