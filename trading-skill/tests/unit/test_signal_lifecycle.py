from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_skill.domain.enums import Timeframe
from trading_skill.signal_lifecycle import (
    SignalLifecycleStage,
    build_signal_lifecycle_book,
    evaluate_signal_lifecycle,
)


NOW = datetime(2026, 9, 10, 3, 30, tzinfo=timezone.utc)


def signal(
    signal_id: str,
    side: str,
    kind: str,
    *,
    hours_ago: float = 1,
    level_rank: int = 0,
    state: str | None = None,
    extended: list[str] | None = None,
):
    row = {
        "id": signal_id,
        "side": side,
        "types": [kind],
        "extended_types": extended or [],
        "level_rank": level_rank,
        "structural_timestamp": (NOW - timedelta(hours=hours_ago + 1)).isoformat(),
        "confirmation_timestamp": (NOW - timedelta(hours=hours_ago)).isoformat(),
    }
    if state is not None:
        row["state"] = state
    return row


def result(timeframe: str, signals: list[dict], *, status: str = "OK"):
    return {
        "status": status,
        "timeframe": timeframe,
        "signals": signals,
    }


def test_signal_is_confirmed_until_a_later_completed_bar_exists():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=0)
    lifecycle = evaluate_signal_lifecycle(
        result("daily", [sig]),
        as_of=NOW,
        latest_completed_bar_timestamp=NOW,
    )
    record = lifecycle.records[0]
    assert record.stage is SignalLifecycleStage.CONFIRMED
    assert record.lifecycle_eligible_as_current is True


def test_confirmed_signal_becomes_active_after_new_completed_bar_without_magic_age_threshold():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=4)
    lifecycle = evaluate_signal_lifecycle(
        result("daily", [sig]),
        as_of=NOW,
        latest_completed_bar_timestamp=NOW,
    )
    record = lifecycle.records[0]
    assert record.stage is SignalLifecycleStage.ACTIVE
    assert record.structurally_valid is True
    assert record.lifecycle_eligible_as_current is True


def test_expired_means_too_old_for_new_entry_not_structurally_invalid():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=31 * 24)
    record = evaluate_signal_lifecycle(result("daily", [sig]), as_of=NOW).records[0]
    assert record.stage is SignalLifecycleStage.EXPIRED
    assert record.structurally_valid is True
    assert record.lifecycle_eligible_as_current is False
    assert record.invalidated_by_signal_id is None


def test_later_opposite_signal_same_timeframe_and_level_invalidates_old_signal():
    buy = signal("b1", "BUY", "SECOND_BUY", hours_ago=20)
    sell = signal("s1", "SELL", "FIRST_SELL", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(result("30m", [buy, sell]), as_of=NOW)
    records = {item.signal_id: item for item in lifecycle.records}
    assert records["b1"].stage is SignalLifecycleStage.INVALIDATED
    assert records["b1"].invalidated_by_signal_id == "s1"
    assert records["b1"].lifecycle_eligible_as_current is False


def test_opposite_signal_at_a_different_level_does_not_invalidate():
    buy = signal("b1", "BUY", "SECOND_BUY", hours_ago=6, level_rank=1)
    sell = signal("s1", "SELL", "FIRST_SELL", hours_ago=2, level_rank=2)
    lifecycle = evaluate_signal_lifecycle(result("120m", [buy, sell]), as_of=NOW)
    records = {item.signal_id: item for item in lifecycle.records}
    assert records["b1"].stage is SignalLifecycleStage.ACTIVE
    assert records["b1"].invalidated_by_signal_id is None


def test_newer_same_side_signal_matures_old_signal_and_becomes_current():
    older = signal("b1", "BUY", "SECOND_BUY", hours_ago=8)
    newer = signal("b2", "BUY", "THIRD_BUY", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(result("daily", [older, newer]), as_of=NOW)
    records = {item.signal_id: item for item in lifecycle.records}
    assert records["b1"].stage is SignalLifecycleStage.MATURE
    assert records["b1"].superseded_by_signal_id == "b2"
    assert records["b2"].stage is SignalLifecycleStage.ACTIVE
    assert lifecycle.current_buy().signal_id == "b2"


def test_same_time_second_and_third_buy_use_type_priority_only_as_tie_break():
    second = signal("b2", "BUY", "SECOND_BUY", hours_ago=2)
    third = signal("b3", "BUY", "THIRD_BUY", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(result("120m", [third, second]), as_of=NOW)
    assert all(item.stage is SignalLifecycleStage.ACTIVE for item in lifecycle.records)
    assert lifecycle.current_buy().signal_id == "b2"


def test_class2_is_metadata_on_standard_second_buy_not_an_independent_lifecycle_signal():
    second = signal(
        "b2",
        "BUY",
        "SECOND_BUY",
        hours_ago=2,
        extended=["STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY"],
    )
    lifecycle = evaluate_signal_lifecycle(result("120m", [second]), as_of=NOW)
    assert len(lifecycle.records) == 1
    assert lifecycle.records[0].standard_types == ("SECOND_BUY",)
    assert lifecycle.records[0].extended_types == ("STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY")


def test_forming_or_candidate_signal_is_never_current():
    forming = signal("b1", "BUY", "SECOND_BUY", hours_ago=1, state="FORMING")
    record = evaluate_signal_lifecycle(result("30m", [forming]), as_of=NOW).records[0]
    assert record.stage is SignalLifecycleStage.FORMING
    assert record.lifecycle_eligible_as_current is False


def test_unavailable_timeframe_never_leaks_old_signal_as_current():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=1)
    record = evaluate_signal_lifecycle(result("30m", [sig], status="DATA_INCOMPLETE"), as_of=NOW).records[0]
    assert record.stage is SignalLifecycleStage.UNRESOLVED
    assert record.lifecycle_eligible_as_current is False


def test_simultaneous_opposite_signals_are_unresolved_instead_of_arbitrarily_picking_one():
    buy = signal("b1", "BUY", "SECOND_BUY", hours_ago=2)
    sell = signal("s1", "SELL", "SECOND_SELL", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(result("30m", [buy, sell]), as_of=NOW)
    assert {item.stage for item in lifecycle.records} == {SignalLifecycleStage.UNRESOLVED}
    assert lifecycle.current_buy() is None
    assert lifecycle.current_sell() is None


def test_book_builds_all_five_timeframes_but_does_not_decide_primary_trade_cycle():
    results = {
        tf.value: result(tf.value, [signal(f"{tf.value}-b", "BUY", "THIRD_BUY", hours_ago=1)])
        for tf in Timeframe
    }
    book = build_signal_lifecycle_book(results, as_of=NOW)
    assert set(book) == set(Timeframe)
    assert book[Timeframe.M5].current_buy() is not None
    # 5m拥有自己的信号生命周期，但是否允许成为主交易周期由strategy_policy决定，不由4C篡改4A信号。
