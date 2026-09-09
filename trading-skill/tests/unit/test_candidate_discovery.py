from trading_skill.candidate_discovery import CandidateRoute, PositionStage, add_fund_candidates, discover_stock_candidates, finalize_candidates
from trading_skill.market_universe import DataQuality, MarketSecurity, SecurityType, StockBoard


def sec(code, *, name=None, security_type=SecurityType.STOCK, market=0, amount=100_000_000, turnover=3.0, ch60=10.0, day=1.0):
    return MarketSecurity(code=code, name=name or code, market=market, security_type=security_type, board=StockBoard.SZ_MAIN if security_type is SecurityType.STOCK else StockBoard.NOT_APPLICABLE, price=10.0, change_pct=day, amount=amount, turnover_rate=turnover, total_market_cap=10_000_000_000, float_market_cap=8_000_000_000, change_60d=ch60, change_ytd=20.0, tradable=True, exclusion_reasons=(), data_quality=DataQuality.COMPLETE, missing_fields=(), source="test")


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
