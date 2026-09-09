from scripts.fund_reference_adapter import _parse_jsonp, _reference_from_row


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
