from scripts import fund_reference_adapter as adapter
from scripts.fund_reference_adapter import (
    _parse_jsonp,
    _reference_from_eastmoney_row,
    _reference_from_row,
)


def test_sina_jsonp_parser_accepts_standard_payload():
    payload = _parse_jsonp("cb({\"data\":[{\"symbol\":\"510300\"}]});")
    assert payload["data"][0]["symbol"] == "510300"


def test_fund_size_uses_recent_shares_times_nav_in_cny():
    parsed = _reference_from_row(
        {
            "symbol": "510300",
            "dwjz": "4.25",
            "zjzfe": "1000000000",
            "jzrq": "2026-09-09",
        }
    )
    assert parsed is not None
    code, reference = parsed
    assert code == "510300"
    assert reference["fund_size_cny"] == 4_250_000_000
    assert reference["fund_size_as_of"] == "2026-09-09"
    assert reference["fund_size_basis"] == "recent_shares_x_nav"
    assert reference["fund_size_estimated"] is False


def test_missing_shares_never_fabricates_fund_size_from_initial_raise():
    _, reference = _reference_from_row(
        {
            "symbol": "510300",
            "dwjz": "4.25",
            "zjzfe": "-",
            "zmjgm": "999999",
        }
    )
    assert reference["fund_size_cny"] is None


def test_eastmoney_fallback_prefers_latest_shares_times_iopv():
    parsed = _reference_from_eastmoney_row(
        {
            "f12": "510300",
            "f38": 1_000_000_000,
            "f441": 4.20,
            "f2": 4.25,
            "f20": 4_250_000_000,
            "f297": 20260910,
        }
    )
    assert parsed is not None
    code, reference = parsed
    assert code == "510300"
    assert reference["fund_size_cny"] == 4_200_000_000
    assert reference["fund_size_basis"] == "latest_shares_x_iopv"
    assert reference["fund_size_estimated"] is True
    assert reference["fund_size_as_of"] == "2026-09-10"


def test_eastmoney_fallback_uses_market_price_then_market_cap_without_fabrication():
    _, price_reference = _reference_from_eastmoney_row(
        {"f12": "510300", "f38": 1_000_000_000, "f441": "-", "f2": 4.25, "f20": 4_260_000_000}
    )
    assert price_reference["fund_size_cny"] == 4_250_000_000
    assert price_reference["fund_size_basis"] == "latest_shares_x_market_price"

    _, cap_reference = _reference_from_eastmoney_row(
        {"f12": "510300", "f38": "-", "f441": "-", "f2": "-", "f20": 4_260_000_000}
    )
    assert cap_reference["fund_size_cny"] == 4_260_000_000
    assert cap_reference["fund_size_basis"] == "exchange_total_market_cap"

    assert _reference_from_eastmoney_row(
        {"f12": "510300", "f38": "-", "f441": "-", "f2": "-", "f20": "-"}
    ) is None


def test_sina_timeout_is_backfilled_by_eastmoney_for_missing_exchange_funds(monkeypatch):
    wanted = {"510300", "512890"}

    def fake_fetch_type(type_code: str):
        if type_code == adapter.SINA_FUND_TYPES["BOND"]:
            return []
        raise TimeoutError("sina timeout")

    fallback = {
        code: {
            "fund_size_cny": 1_000_000_000,
            "fund_size_source": "东方财富场内基金份额/市值批量接口",
            "fund_size_basis": "latest_shares_x_iopv",
            "fund_size_estimated": True,
        }
        for code in wanted
    }

    monkeypatch.setattr(adapter, "_fetch_type", fake_fetch_type)
    monkeypatch.setattr(adapter, "_fetch_eastmoney_references", lambda missing: (dict(fallback), []))

    references, errors = adapter.fetch_fund_scale_references(wanted)

    assert set(references) == wanted
    assert all(ref["fund_size_source"].startswith("东方财富") for ref in references.values())
    assert errors
    assert any("timeout" in error for error in errors)
