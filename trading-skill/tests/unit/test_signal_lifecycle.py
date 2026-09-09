from __future__ import annotations

from datetime import datetime, timedelta, timezone

from trading_skill.domain.enums import Timeframe
from trading_skill.signal_lifecycle import (
    SIGNAL_ENTRY_WINDOW_BARS,
    SignalFreshness,
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


def completed_after(signal_row: dict, count: int) -> list[datetime]:
    confirmation = datetime.fromisoformat(signal_row["confirmation_timestamp"])
    if count <= 0:
        return [confirmation]
    span = NOW - confirmation
    step = span / (count + 1)
    return [confirmation + step * (index + 1) for index in range(count)]


def test_signal_is_confirmed_until_a_later_completed_bar_exists():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=0)
    lifecycle = evaluate_signal_lifecycle(
        result("daily", [sig]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, 0),
    )
    record = lifecycle.records[0]
    assert record.stage is SignalLifecycleStage.CONFIRMED
    assert record.age_completed_bars == 0
    assert record.lifecycle_eligible_as_current is True


def test_confirmed_signal_becomes_active_after_new_completed_bars():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=4)
    lifecycle = evaluate_signal_lifecycle(
        result("daily", [sig]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, 2),
    )
    record = lifecycle.records[0]
    assert record.stage is SignalLifecycleStage.ACTIVE
    assert record.age_completed_bars == 2
    assert record.structurally_valid is True
    assert record.lifecycle_eligible_as_current is True


def test_expired_means_too_many_completed_bars_not_too_many_calendar_days():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=31 * 24)
    window = SIGNAL_ENTRY_WINDOW_BARS[Timeframe.DAILY]
    record = evaluate_signal_lifecycle(
        result("daily", [sig]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, window + 1),
    ).records[0]
    assert record.stage is SignalLifecycleStage.EXPIRED
    assert record.freshness is SignalFreshness.STALE
    assert record.age_completed_bars == window + 1
    assert record.structurally_valid is True
    assert record.lifecycle_eligible_as_current is False
    assert record.invalidated_by_signal_id is None


def test_long_calendar_gap_with_few_completed_bars_does_not_fake_expiry():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=31 * 24)
    record = evaluate_signal_lifecycle(
        result("daily", [sig]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, 3),
    ).records[0]
    assert record.stage is SignalLifecycleStage.ACTIVE
    assert record.age_completed_bars == 3
    assert record.freshness is SignalFreshness.FRESH


def test_exact_completed_bar_window_is_still_fresh_then_next_bar_expires():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=100)
    window = SIGNAL_ENTRY_WINDOW_BARS[Timeframe.M30]
    at_edge = evaluate_signal_lifecycle(
        result("30m", [sig]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, window),
    ).records[0]
    past_edge = evaluate_signal_lifecycle(
        result("30m", [sig]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, window + 1),
    ).records[0]
    assert at_edge.stage is SignalLifecycleStage.ACTIVE
    assert at_edge.freshness is SignalFreshness.FRESH
    assert past_edge.stage is SignalLifecycleStage.EXPIRED


def test_later_opposite_signal_same_timeframe_and_level_invalidates_old_signal():
    buy = signal("b1", "BUY", "SECOND_BUY", hours_ago=20)
    sell = signal("s1", "SELL", "FIRST_SELL", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(
        result("30m", [buy, sell]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(buy, 10),
    )
    records = {item.signal_id: item for item in lifecycle.records}
    assert records["b1"].stage is SignalLifecycleStage.INVALIDATED
    assert records["b1"].invalidated_by_signal_id == "s1"
    assert records["b1"].lifecycle_eligible_as_current is False


def test_opposite_signal_at_a_different_level_does_not_invalidate():
    buy = signal("b1", "BUY", "SECOND_BUY", hours_ago=6, level_rank=1)
    sell = signal("s1", "SELL", "FIRST_SELL", hours_ago=2, level_rank=2)
    lifecycle = evaluate_signal_lifecycle(
        result("120m", [buy, sell]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(buy, 3),
    )
    records = {item.signal_id: item for item in lifecycle.records}
    assert records["b1"].stage is SignalLifecycleStage.ACTIVE
    assert records["b1"].invalidated_by_signal_id is None


def test_newer_same_side_signal_matures_old_signal_and_becomes_current():
    older = signal("b1", "BUY", "SECOND_BUY", hours_ago=8)
    newer = signal("b2", "BUY", "THIRD_BUY", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(
        result("daily", [older, newer]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(older, 5),
    )
    records = {item.signal_id: item for item in lifecycle.records}
    assert records["b1"].stage is SignalLifecycleStage.MATURE
    assert records["b1"].superseded_by_signal_id == "b2"
    assert records["b2"].stage is SignalLifecycleStage.ACTIVE
    assert lifecycle.current_buy().signal_id == "b2"


def test_same_time_second_and_third_buy_use_type_priority_only_as_tie_break():
    second = signal("b2", "BUY", "SECOND_BUY", hours_ago=2)
    third = signal("b3", "BUY", "THIRD_BUY", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(
        result("120m", [third, second]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(second, 1),
    )
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
    lifecycle = evaluate_signal_lifecycle(
        result("120m", [second]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(second, 1),
    )
    assert len(lifecycle.records) == 1
    assert lifecycle.records[0].standard_types == ("SECOND_BUY",)
    assert lifecycle.records[0].extended_types == ("STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY")


def test_forming_or_candidate_signal_is_never_current():
    forming = signal("b1", "BUY", "SECOND_BUY", hours_ago=1, state="FORMING")
    record = evaluate_signal_lifecycle(
        result("30m", [forming]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(forming, 1),
    ).records[0]
    assert record.stage is SignalLifecycleStage.FORMING
    assert record.lifecycle_eligible_as_current is False


def test_unavailable_timeframe_never_leaks_old_signal_as_current():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=1)
    record = evaluate_signal_lifecycle(
        result("30m", [sig], status="DATA_INCOMPLETE"),
        as_of=NOW,
        completed_bar_timestamps=completed_after(sig, 1),
    ).records[0]
    assert record.stage is SignalLifecycleStage.UNRESOLVED
    assert record.lifecycle_eligible_as_current is False


def test_simultaneous_opposite_signals_are_unresolved_instead_of_arbitrarily_picking_one():
    buy = signal("b1", "BUY", "SECOND_BUY", hours_ago=2)
    sell = signal("s1", "SELL", "SECOND_SELL", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(
        result("30m", [buy, sell]),
        as_of=NOW,
        completed_bar_timestamps=completed_after(buy, 1),
    )
    assert {item.stage for item in lifecycle.records} == {SignalLifecycleStage.UNRESOLVED}
    assert lifecycle.current_buy() is None
    assert lifecycle.current_sell() is None


def test_missing_exact_bar_sequence_keeps_structure_but_cannot_select_current_opportunity():
    sig = signal("b1", "BUY", "SECOND_BUY", hours_ago=2)
    lifecycle = evaluate_signal_lifecycle(
        result("30m", [sig]),
        as_of=NOW,
        latest_completed_bar_timestamp=NOW,
    )
    record = lifecycle.records[0]
    assert record.stage is SignalLifecycleStage.ACTIVE
    assert record.freshness is SignalFreshness.UNKNOWN
    assert record.structurally_valid is True
    assert lifecycle.current_buy() is None


def test_book_builds_all_five_timeframes_but_does_not_decide_primary_trade_cycle():
    signals = {
        tf: signal(f"{tf.value}-b", "BUY", "THIRD_BUY", hours_ago=1)
        for tf in Timeframe
    }
    results = {tf.value: result(tf.value, [signals[tf]]) for tf in Timeframe}
    times = {tf: completed_after(signals[tf], 1) for tf in Timeframe}
    book = build_signal_lifecycle_book(results, as_of=NOW, completed_bar_times_by_timeframe=times)
    assert set(book) == set(Timeframe)
    assert book[Timeframe.M5].current_buy() is not None
    # 5m拥有自己的信号生命周期，但是否允许成为主交易周期由strategy_policy决定，不由4C篡改4A信号。
