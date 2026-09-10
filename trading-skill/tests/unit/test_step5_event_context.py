from scripts.analyze_step5_trade_permission import _context_for_symbol, _event_facts
from trading_skill.trade_permission import EventEntryState


def _payload(*, events=None, errors=None, rows=10, selected=("银行", "半导体")):
    return {
        "sources": {
            "industry_news_rows": rows,
            "industry_event_errors": errors or [],
        },
        "selected_industries": [{"name": name} for name in selected],
        "industry_events": events or {},
    }


def test_stock_with_complete_feed_and_no_matched_event_is_clear_only_when_industry_scope_was_built():
    quality = {"security_type": "STOCK", "industry_name": "银行"}
    state, events, note = _event_facts(quality, _payload())
    assert state is EventEntryState.CLEAR
    assert events == []
    assert "银行" in note


def test_cross_industry_stock_outside_event_scope_stays_unknown():
    quality = {"security_type": "STOCK", "industry_name": "造纸"}
    state, events, note = _event_facts(quality, _payload(selected=("银行",)))
    assert state is EventEntryState.UNKNOWN
    assert events == []
    assert "不能因event_map无记录而判CLEAR" in note


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


def test_sector_fund_exact_mapping_can_use_industry_event_feed_even_when_no_event_matched():
    quality = {
        "security_type": "ETF",
        "fund_category": "EQUITY_SECTOR",
        "fund_family": "半导体",
    }
    state, events, _ = _event_facts(quality, _payload(events={}))
    assert state is EventEntryState.CLEAR
    assert events == []


def test_broad_cross_border_commodity_and_bond_funds_are_not_falsely_marked_event_clear():
    for category in ("EQUITY_BROAD", "EQUITY_STRATEGY", "CROSS_BORDER", "COMMODITY", "BOND"):
        quality = {"security_type": "ETF", "fund_category": category, "fund_family": category}
        state, events, note = _event_facts(quality, _payload())
        assert state is EventEntryState.UNKNOWN
        assert events == []
        assert "不足以证明" in note


def test_step1_strategy_permission_does_not_impersonate_real_account_or_portfolio_context():
    context = _context_for_symbol({}, {"code": "510300", "market": 1, "security_type": "ETF"})
    assert context["strategy_security_permission_known"] is True
    assert context["strategy_security_allowed"] is True
    assert context["account_context_known"] is False
    assert context["account_allows_security"] is False
    assert context["portfolio_context_known"] is False
    assert context["portfolio_allows_new_risk"] is False
