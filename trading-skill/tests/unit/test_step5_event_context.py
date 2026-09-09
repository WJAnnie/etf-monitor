from scripts.analyze_step5_trade_permission import _event_facts
from trading_skill.trade_permission import EventEntryState


def _payload(*, events=None, errors=None, rows=10):
    return {
        "sources": {
            "industry_news_rows": rows,
            "industry_event_errors": errors or [],
        },
        "industry_events": events or {},
    }


def test_stock_with_complete_feed_and_no_matched_event_is_clear_for_its_industry_gate():
    quality = {"security_type": "STOCK", "industry_name": "银行"}
    state, events, note = _event_facts(quality, _payload())
    assert state is EventEntryState.CLEAR
    assert events == []
    assert "银行" in note


def test_stock_event_feed_failure_stays_unknown_even_when_no_event_rows_match():
    quality = {"security_type": "STOCK", "industry_name": "银行"}
    state, _, _ = _event_facts(quality, _payload(errors=["feed failed"]))
    assert state is EventEntryState.UNKNOWN


def test_sector_fund_requires_exact_verified_industry_mapping():
    quality = {
        "security_type": "ETF",
        "fund_category": "EQUITY_SECTOR",
        "fund_family": "半导体ETF家族",
    }
    state, _, _ = _event_facts(quality, _payload(events={"半导体": []}))
    assert state is EventEntryState.UNKNOWN


def test_sector_fund_exact_mapping_can_use_industry_event_feed():
    quality = {
        "security_type": "ETF",
        "fund_category": "EQUITY_SECTOR",
        "fund_family": "半导体",
    }
    state, _, _ = _event_facts(quality, _payload(events={"半导体": []}))
    assert state is EventEntryState.CLEAR


def test_broad_cross_border_commodity_and_bond_funds_are_not_falsely_marked_event_clear():
    for category in ("EQUITY_BROAD", "EQUITY_STRATEGY", "CROSS_BORDER", "COMMODITY", "BOND"):
        quality = {"security_type": "ETF", "fund_category": category, "fund_family": category}
        state, events, note = _event_facts(quality, _payload())
        assert state is EventEntryState.UNKNOWN
        assert events == []
        assert "不足以证明" in note
