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
    assert universe._valid_price_count(rows) == 2


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
    assert "valid_prices=0" in str(exc.value)
    assert "price_coverage=0.0%" in str(exc.value)


def test_partial_batch_above_health_threshold_is_allowed(monkeypatch):
    rows = _rows(85, "10") + _rows(15, "-")
    monkeypatch.setattr(universe, "fetch_paginated", lambda *args, **kwargs: rows)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    result = universe.fetch_paginated_price_healthy("x", "f2", fid="f6")
    assert universe._price_coverage(result) == pytest.approx(0.85)


def test_absolute_valid_quote_policy_allows_illiquid_listing_when_enough_quotes_exist(monkeypatch):
    rows = _rows(30, "10") + _rows(70, "-")
    monkeypatch.setattr(universe, "fetch_paginated", lambda *args, **kwargs: rows)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    result = universe.fetch_paginated_price_healthy(
        "x",
        "f2",
        fid="f3",
        min_coverage=None,
        min_valid_prices=20,
    )
    assert len(result) == 100
    assert universe._price_coverage(result) == pytest.approx(0.30)
    assert universe._valid_price_count(result) == 30


def test_absolute_valid_quote_policy_still_rejects_source_wide_price_collapse(monkeypatch):
    rows = _rows(10, "10") + _rows(90, "-")
    calls = 0

    def fake_fetch(*args, **kwargs):
        nonlocal calls
        calls += 1
        return rows

    monkeypatch.setattr(universe, "fetch_paginated", fake_fetch)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    with pytest.raises(RuntimeError, match="批量行情语义不健康") as exc:
        universe.fetch_paginated_price_healthy(
            "x",
            "f2",
            fid="f3",
            min_coverage=None,
            min_valid_prices=20,
        )
    assert calls == universe.SEMANTIC_PRICE_RETRIES
    assert "valid_prices=10" in str(exc.value)


def test_price_healthy_batch_forwards_source_specific_hosts(monkeypatch):
    calls = []
    hosts = ("https://88.push2.eastmoney.com/api/qt/clist/get",)

    def fake_fetch(*args, **kwargs):
        calls.append(kwargs)
        return _rows(100, "10")

    monkeypatch.setattr(universe, "fetch_paginated", fake_fetch)
    monkeypatch.setattr(universe.time, "sleep", lambda *_: None)

    result = universe.fetch_paginated_price_healthy("x", "f2", fid="f6", hosts=hosts)
    assert len(result) == 100
    assert calls[0]["hosts"] == hosts


def test_exchange_funds_preserves_distinct_etf_and_lof_quote_health_contracts(monkeypatch):
    calls = []

    def fake_healthy(fs, fields, **kwargs):
        calls.append(
            (
                fs,
                kwargs.get("fid"),
                kwargs.get("hosts"),
                kwargs.get("min_coverage", universe.MIN_BATCH_PRICE_COVERAGE),
                kwargs.get("min_valid_prices", 1),
            )
        )
        return _rows(100, "10")

    monkeypatch.setattr(universe, "fetch_paginated_price_healthy", fake_healthy)

    etfs, lofs, errors = universe.fetch_exchange_funds()

    assert len(etfs) == 100
    assert len(lofs) == 100
    assert errors == []
    assert calls == [
        (universe.ETF_FS, "f6", None, universe.MIN_BATCH_PRICE_COVERAGE, 1),
        (
            universe.LOF_FS,
            "f3",
            universe.LOF_PUSH2_HOSTS,
            None,
            universe.MIN_TRADEABLE_LOF_COUNT,
        ),
    ]
