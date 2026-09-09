from __future__ import annotations

from trading_skill.data.market_bars import (
    BarCollection,
    SecurityIdentity,
    _first_usable,
)


def row(index: int) -> dict:
    return {
        "time": f"2026-09-{index + 1:02d}",
        "open": 10.0,
        "close": 10.1,
        "high": 10.2,
        "low": 9.9,
        "volume": 100.0,
        "amount": 1000.0,
        "_complete": True,
        "_adjustment": "forward",
    }


def test_first_usable_preserves_best_real_short_history_when_no_provider_meets_target():
    short3 = [row(i) for i in range(3)]
    short7 = [row(i) for i in range(7)]
    rows, source, warnings = _first_usable(
        (
            ("source3", "forward", lambda: list(short3)),
            ("source7", "forward", lambda: list(short7)),
        ),
        normalize=lambda values, _source: values,
        minimum=120,
    )
    assert len(rows) == 7
    assert source == "source7"
    assert any("SHORT_HISTORY_ACCEPTED:source7:7<120" in warning for warning in warnings)


def test_full_history_provider_still_wins_over_earlier_short_provider():
    short = [row(i) for i in range(3)]
    full = [row(i % 20) | {"time": f"2026-{(i // 28) + 1:02d}-{(i % 28) + 1:02d}"} for i in range(120)]
    rows, source, _ = _first_usable(
        (
            ("short", "forward", lambda: list(short)),
            ("full", "forward", lambda: list(full)),
        ),
        normalize=lambda values, _source: values,
        minimum=120,
    )
    assert len(rows) == 120
    assert source == "full"


def test_short_history_is_explicitly_marked_and_never_faked_as_complete():
    daily = tuple(row(i) for i in range(7))
    collection = BarCollection(
        identity=SecurityIdentity("601123", "马矿股份", 1, "STOCK"),
        daily=daily,
        weekly=(),
        m120=(),
        m30=(),
        m5=(),
        sources={"daily": "test", "weekly": "test", "120m": "test", "30m": "test", "5m": "test"},
        warnings=("SHORT_HISTORY_ACCEPTED:test:7<120",),
    ).as_dict()
    quality = collection["quality"]
    assert quality["history_limited"] is True
    assert quality["daily_history_ok"] is False
    assert quality["history_quality"]["daily_count"] == 7
    assert quality["history_quality"]["long_term_tier"] == "长期证据不足"
