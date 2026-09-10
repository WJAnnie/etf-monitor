from __future__ import annotations

import pytest

from scripts import collect_candidate_universe as universe


def _rows(count: int, price) -> list[dict]:
    return [{"f12": f"{i:06d}", "f2": price} for i in range(count)]


def test_price_coverage_treats_missing_dash_zero_and_invalid_as_unhealthy():
    rows = [
        {"f2": "10.2"},
        {"f2": 5},
        {"f2": "-"},
        {"f2": None},
        {"f2": "0"},
        {"f2": "bad"},
    ]
    assert universe._price_coverage(rows) == pytest.approx(2 / 6)


def test_price_healthy_batch_passes_without_extra_retry(monkeypatch):
    calls = []

    def fake_fetch(*args, **kwargs):
        calls.append((args, kwargs))
        return _rows(100, "10")

    monkeypatch.setattr(universe, "fetch_paginated", fake_fetch)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    result = universe.fetch_paginated_price_healthy("x", "f2", fid="f6")
    assert len(result) == 100
    assert len(calls) == 1


def test_semantically_bad_http_success_is_retried_until_price_recovers(monkeypatch):
    batches = [_rows(100, "-"), _rows(100, "10")]
    sleeps = []

    def fake_fetch(*args, **kwargs):
        return batches.pop(0)

    monkeypatch.setattr(universe, "fetch_paginated", fake_fetch)
    monkeypatch.setattr(universe.time, "sleep", lambda seconds: sleeps.append(seconds))

    result = universe.fetch_paginated_price_healthy("x", "f2", fid="f6")
    assert universe._price_coverage(result) == 1.0
    assert sleeps == [2.0]


def test_repeated_semantically_bad_batches_fail_instead_of_becoming_all_no_valid_price(monkeypatch):
    calls = 0

    def fake_fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        return _rows(100, "-")

    monkeypatch.setattr(universe, "fetch_paginated", fake_fetch)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="批量行情语义不健康") as exc:
        universe.fetch_paginated_price_healthy("x", "f2", fid="f6")
    assert calls == universe.SEMANTIC_PRICE_RETRIES
    assert "price_coverage=0.0%" in str(exc.value)


def test_partial_batch_above_health_threshold_is_allowed(monkeypatch):
    rows = _rows(85, "10") + _rows(15, "-")
    monkeypatch.setattr(universe, "fetch_paginated", lambda *args, **kwargs: rows)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    result = universe.fetch_paginated_price_healthy("x", "f2", fid="f6")
    assert universe._price_coverage(result) == pytest.approx(0.85)
