from trading_skill.candidate_discovery import (
    CandidateRoute,
    FundCategory,
    PositionStage,
    add_fund_candidates,
    classify_fund_category,
    discover_stock_candidates,
    finalize_candidates,
    fund_risk_tags,
)
from trading_skill.market_universe import DataQuality, MarketSecurity, SecurityType, StockBoard


def sec(code, *, name=None, security_type=SecurityType.STOCK, market=0, amount=100_000_000, turnover=3.0, ch60=10.0, day=1.0, pe=None, pb=None):
    return MarketSecurity(
        code=code,
        name=name or code,
        market=market,
        security_type=security_type,
        board=StockBoard.SZ_MAIN if security_type is SecurityType.STOCK else StockBoard.NOT_APPLICABLE,
        price=10.0,
        change_pct=day,
        amount=amount,
        turnover_rate=turnover,
        total_market_cap=10_000_000_000,
        float_market_cap=8_000_000_000,
        change_60d=ch60,
        change_ytd=20.0,
        tradable=True,
        exclusion_reasons=(),
        data_quality=DataQuality.COMPLETE if ch60 is not None else DataQuality.PARTIAL,
        missing_fields=() if ch60 is not None else ("change_60d",),
        source="test",
        pe=pe,
        pb=pb,
    )


def test_high_position_is_tagged_not_hard_excluded():
    items = [
        sec("000001", amount=1_000_000_000, turnover=8, ch60=60, day=6),
        sec("000002", amount=500_000_000, turnover=5, ch60=30),
        sec("000003", amount=300_000_000, turnover=4, ch60=20),
        sec("000004", amount=200_000_000, turnover=3, ch60=10),
        sec("000005", amount=100_000_000, turnover=2, ch60=0),
    ]
    store = discover_stock_candidates(items, score_floor=0, market_strength_cap=10, early_turn_cap=10)
    out = {x.code: x for x in finalize_candidates(store)}
    assert "000001" in out
    assert out["000001"].position_stage is PositionStage.OVERHEATED
    assert CandidateRoute.MARKET_STRENGTH.value in out["000001"].source_routes


def test_candidate_reuses_stock_valuation_from_universe():
    items = [
        sec("000001", amount=500_000_000, turnover=5, ch60=15, day=2, pe=32.0, pb=4.1),
        sec("000002", amount=200_000_000, turnover=3, ch60=8, day=1),
    ]
    store = discover_stock_candidates(items, score_floor=0, market_strength_cap=10, early_turn_cap=10)
    out = {x.code: x for x in finalize_candidates(store)}
    assert out["000001"].valuation_pe == 32.0
    assert out["000001"].valuation_pb == 4.1


def test_missing_60d_history_cannot_fake_market_strength():
    items = [
        sec("000001", amount=2_000_000_000, turnover=10, ch60=None, day=5),
        sec("000002", amount=500_000_000, turnover=5, ch60=12, day=2),
        sec("000003", amount=300_000_000, turnover=4, ch60=8, day=1),
    ]
    store = discover_stock_candidates(items, score_floor=0, market_strength_cap=10, early_turn_cap=10)
    assert ("000001", SecurityType.STOCK) not in store


def test_same_etf_family_keeps_one_more_tradeable_representative():
    funds = [
        sec("510300", name="沪深300ETF", security_type=SecurityType.ETF, amount=100_000_000, ch60=8),
        sec("510310", name="沪深300ETF华夏", security_type=SecurityType.ETF, amount=800_000_000, ch60=8),
        sec("512000", name="券商ETF", security_type=SecurityType.ETF, amount=300_000_000, ch60=15),
    ]
    store = {}
    add_fund_candidates(store, funds, cap=10, score_floor=0)
    out = finalize_candidates(store)
    codes = {x.code for x in out}
    assert len(codes & {"510300", "510310"}) == 1
    assert "510310" in codes
    assert all(CandidateRoute.FUND_RELATIVE.value in x.source_routes for x in out)


def test_same_family_prefers_liquidity_even_when_weaker_product_has_more_momentum():
    funds = [
        sec("510300", name="沪深300ETF", security_type=SecurityType.ETF, amount=900_000_000, ch60=5, day=0.2),
        sec("510310", name="沪深300ETF华夏", security_type=SecurityType.ETF, amount=100_000_000, ch60=45, day=6.0),
        sec("512000", name="券商ETF", security_type=SecurityType.ETF, amount=300_000_000, ch60=10),
    ]
    store = {}
    add_fund_candidates(store, funds, cap=10, score_floor=0)
    out = {x.code: x for x in finalize_candidates(store)}
    assert "510300" in out
    assert "510310" not in out
    assert out["510300"].fund_liquidity_percentile is not None


def test_funds_are_compared_inside_underlying_asset_categories_and_cross_border_is_a_risk_tag():
    funds = [
        sec("510300", name="沪深300ETF", security_type=SecurityType.ETF, amount=100_000_000, ch60=8),
        sec("511010", name="国债ETF", security_type=SecurityType.ETF, amount=2_000_000_000, ch60=1),
        sec("518880", name="黄金ETF", security_type=SecurityType.ETF, amount=700_000_000, ch60=18),
        sec("513100", name="纳指ETF", security_type=SecurityType.ETF, amount=500_000_000, ch60=12),
    ]
    assert classify_fund_category(funds[0]) is FundCategory.EQUITY_BROAD
    assert classify_fund_category(funds[1]) is FundCategory.BOND
    assert classify_fund_category(funds[2]) is FundCategory.COMMODITY
    assert classify_fund_category(funds[3]) is FundCategory.EQUITY_BROAD
    assert "CROSS_BORDER_QDII" in fund_risk_tags(funds[3])
    store = {}
    add_fund_candidates(store, funds, cap=10, score_floor=0)
    categories = {x.fund_category for x in finalize_candidates(store)}
    assert {c.value for c in (FundCategory.EQUITY_BROAD, FundCategory.BOND, FundCategory.COMMODITY)} <= categories


def test_fund_name_edge_cases_do_not_confuse_asset_class_with_keywords():
    samples = [
        (sec("159201", name="自由现金流ETF华夏", security_type=SecurityType.ETF), FundCategory.EQUITY_STRATEGY),
        (sec("563760", name="全指现金流ETF中银", security_type=SecurityType.ETF), FundCategory.EQUITY_STRATEGY),
        (sec("511880", name="银华日利ETF", security_type=SecurityType.ETF), FundCategory.CASH),
        (sec("510210", name="上证指数ETF富国", security_type=SecurityType.ETF), FundCategory.EQUITY_BROAD),
        (sec("162719", name="石油LOF", security_type=SecurityType.LOF), FundCategory.COMMODITY),
        (sec("161119", name="易方达新综债LOF", security_type=SecurityType.LOF), FundCategory.BOND),
        (sec("164906", name="海外科技LOF", security_type=SecurityType.LOF), FundCategory.EQUITY_SECTOR),
    ]
    for item, expected in samples:
        assert classify_fund_category(item) is expected


def test_lofs_are_classified_by_underlying_asset_and_risk_tags_are_separate():
    samples = [
        (sec("160216", name="国泰商品LOF", security_type=SecurityType.LOF), FundCategory.OTHER),
        (sec("160723", name="嘉实原油LOF", security_type=SecurityType.LOF), FundCategory.COMMODITY),
        (sec("161125", name="标普油气LOF", security_type=SecurityType.LOF), FundCategory.COMMODITY),
        (sec("161716", name="招商双债LOF", security_type=SecurityType.LOF), FundCategory.BOND),
        (sec("161725", name="招商白酒LOF", security_type=SecurityType.LOF), FundCategory.EQUITY_SECTOR),
        (sec("164906", name="海外科技LOF", security_type=SecurityType.LOF), FundCategory.EQUITY_SECTOR),
    ]
    for item, expected in samples:
        assert classify_fund_category(item) is expected
        assert "LOF_PREMIUM" in fund_risk_tags(item)

    oil_qdii = sec("161125", name="标普油气LOF", security_type=SecurityType.LOF)
    assert "CROSS_BORDER_QDII" in fund_risk_tags(oil_qdii)

    overseas_tech = sec("164906", name="海外科技LOF", security_type=SecurityType.LOF)
    assert set(fund_risk_tags(overseas_tech)) == {"CROSS_BORDER_QDII", "LOF_PREMIUM"}

    bond_qdii = sec("160140", name="美元债QDII-LOF", security_type=SecurityType.LOF)
    assert classify_fund_category(bond_qdii) is FundCategory.BOND
    assert set(fund_risk_tags(bond_qdii)) == {"CROSS_BORDER_QDII", "LOF_PREMIUM"}
