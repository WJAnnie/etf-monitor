from __future__ import annotations

import json

import pytest

from scripts.analyze_step5b_entry_sizing import _load_context, _sizing_context_for_symbol


def _item(*, market=1, code="600000", security_type="STOCK"):
    return {"market": market, "code": code, "security_type": security_type}


def test_symbol_override_requires_full_market_code_security_type_identity(tmp_path):
    path = tmp_path / "risk.json"
    path.write_text(
        json.dumps({"symbols": {"600000": {"cash_available_cny": "1000"}}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="market:code:security_type"):
        _load_context(path)


def test_exact_symbol_override_wins_over_global_defaults():
    context = {
        "cash_available_cny": "10000",
        "standard_trade_risk_limit_cny": "1000",
        "symbols": {
            "1:600000:STOCK": {
                "cash_available_cny": "2500",
                "standard_trade_risk_limit_cny": "400",
            }
        },
    }
    resolved = _sizing_context_for_symbol(context, _item())
    assert resolved is not None
    assert resolved["cash_available_cny"] == "2500"
    assert resolved["standard_trade_risk_limit_cny"] == "400"


def test_same_code_other_market_does_not_receive_override():
    context = {
        "cash_available_cny": "10000",
        "symbols": {"1:600000:STOCK": {"cash_available_cny": "2500"}},
    }
    sh = _sizing_context_for_symbol(context, _item(market=1))
    other = _sizing_context_for_symbol(context, _item(market=0))
    assert sh is not None and sh["cash_available_cny"] == "2500"
    assert other is not None and other["cash_available_cny"] == "10000"


def test_same_market_code_other_security_type_does_not_receive_override():
    context = {
        "lot_size": 100,
        "symbols": {"1:510300:ETF": {"lot_size": 10}},
    }
    etf = _sizing_context_for_symbol(context, _item(market=1, code="510300", security_type="ETF"))
    lof = _sizing_context_for_symbol(context, _item(market=1, code="510300", security_type="LOF"))
    assert etf is not None and etf["lot_size"] == 10
    assert lof is not None and lof["lot_size"] == 100


def test_explicit_null_override_is_preserved_instead_of_falling_back():
    context = {
        "industry_risk_remaining_cny": "5000",
        "industry_value_remaining_cny": "50000",
        "symbols": {
            "1:600000:STOCK": {
                "industry_risk_remaining_cny": None,
                "industry_value_remaining_cny": None,
            }
        },
    }
    resolved = _sizing_context_for_symbol(context, _item())
    assert resolved is not None
    assert "industry_risk_remaining_cny" in resolved
    assert resolved["industry_risk_remaining_cny"] is None
    assert "industry_value_remaining_cny" in resolved
    assert resolved["industry_value_remaining_cny"] is None


def test_context_adapter_does_not_expose_unknown_keys_to_sizing_core():
    context = {
        "cash_available_cny": "10000",
        "password": "must-not-flow",
        "symbols": {
            "1:600000:STOCK": {
                "cash_available_cny": "2500",
                "private_note": "must-not-flow",
            }
        },
    }
    resolved = _sizing_context_for_symbol(context, _item())
    assert resolved == {"cash_available_cny": "2500"}
