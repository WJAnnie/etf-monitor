from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_skill.domain.enums import Timeframe
from trading_skill.multi_timeframe_structure import (
    LowerTimeframeState,
    ParentContextState,
    StructurePhase,
    build_structure_book,
    evaluate_lower_context,
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


def test_each_timeframe_gets_independent_structure_snapshot():
    results = {
        "weekly": result("weekly", trend="UPTREND"),
        "daily": result("daily", trend="CONSOLIDATION", center_state="LEAVING_UP"),
        "120m": result("120m", trend="DOWNTREND", divergence_type="TREND_BOTTOM_DIVERGENCE", divergence_state="FORMING"),
        "30m": result("30m", trend="UPTREND", signals=[signal("SELL", "FIRST_SELL")]),
        "5m": result("5m", trend="CONSOLIDATION"),
    }
    book = build_structure_book(results, as_of=NOW).by_timeframe()
    assert book[Timeframe.WEEKLY].phase is StructurePhase.BULL_TREND
    assert book[Timeframe.DAILY].phase is StructurePhase.BREAKOUT_UP
    assert book[Timeframe.M120].phase is StructurePhase.REVERSAL_UP_FORMING
    assert book[Timeframe.M30].phase is StructurePhase.BULL_PULLBACK
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
    assert context.allows_opportunity is False
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
    assert context.allows_opportunity is True
    assert context.allows_immediate_entry is True


def test_fresh_parent_sell_blocks_but_stale_sell_does_not_permanently_block():
    fresh_results = {
        Timeframe.WEEKLY: result("weekly", trend="UPTREND"),
        Timeframe.DAILY: result("daily", trend="UPTREND", signals=[signal("SELL", "FIRST_SELL", hours_ago=2)]),
        Timeframe.M120: result("120m", trend="UPTREND"),
    }
    fresh = evaluate_parent_context(fresh_results, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert fresh.state is ParentContextState.BLOCKED

    stale_results = {
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
        Timeframe.M120: result("120m", trend="UPTREND"),
    }
    stale = evaluate_parent_context(stale_results, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert stale.state is ParentContextState.SUPPORTIVE
    assert stale.allows_opportunity is True


def test_parent_caution_keeps_opportunity_but_blocks_immediate_entry():
    results = {
        Timeframe.WEEKLY: result("weekly", trend="UPTREND", divergence_type="TREND_TOP_DIVERGENCE", divergence_state="FORMING"),
        Timeframe.DAILY: result("daily", trend="UPTREND"),
    }
    context = evaluate_parent_context(results, primary_timeframe=Timeframe.DAILY, as_of=NOW)
    assert context.state is ParentContextState.CAUTION
    assert context.allows_opportunity is True
    assert context.allows_immediate_entry is False


def test_lower_timeframe_sell_means_waiting_not_primary_invalidation():
    primary_confirmation = NOW - timedelta(hours=6)
    results = {
        Timeframe.DAILY: result("daily", trend="UPTREND"),
        Timeframe.M120: result("120m", trend="UPTREND"),
        Timeframe.M30: result("30m", trend="UPTREND", signals=[signal("SELL", "FIRST_SELL", hours_ago=2)]),
        Timeframe.M5: result("5m", trend="UPTREND"),
    }
    lower = evaluate_lower_context(
        results,
        primary_timeframe=Timeframe.DAILY,
        primary_confirmation=primary_confirmation,
        as_of=NOW,
    )
    assert lower.state is LowerTimeframeState.WAITING_PULLBACK
    states = dict(lower.child_states)
    assert states[Timeframe.M30].startswith("PULLBACK:")
    assert any("不否定主周期买点" in reason for reason in lower.reasons)


def test_lower_timeframe_new_buy_can_align_execution_without_becoming_primary_signal():
    primary_confirmation = NOW - timedelta(hours=6)
    results = {
        Timeframe.M120: result("120m", trend="UPTREND"),
        Timeframe.M30: result("30m", trend="UPTREND", signals=[signal("BUY", "SECOND_BUY", hours_ago=2)]),
        Timeframe.M5: result("5m", trend="UPTREND", signals=[signal("BUY", "THIRD_BUY", hours_ago=1)]),
    }
    lower = evaluate_lower_context(
        results,
        primary_timeframe=Timeframe.M120,
        primary_confirmation=primary_confirmation,
        as_of=NOW,
    )
    assert lower.state is LowerTimeframeState.ALIGNED
    states = dict(lower.child_states)
    assert states[Timeframe.M30] == "ALIGNED:SECOND_BUY"
    assert states[Timeframe.M5] == "ALIGNED:THIRD_BUY"
    # 这里仅表示执行层与主结构同向，不改变primary_timeframe仍为120m。


def test_missing_parent_is_unresolved_and_cannot_be_silently_treated_as_support():
    results = {
        Timeframe.M120: result("120m", trend="UPTREND"),
    }
    context = evaluate_parent_context(results, primary_timeframe=Timeframe.M120, as_of=NOW)
    assert context.state is ParentContextState.UNRESOLVED
    assert context.allows_opportunity is False
