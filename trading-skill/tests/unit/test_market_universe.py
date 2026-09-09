from trading_skill.market_universe import (
    DataQuality,
    SecurityType,
    StockBoard,
    TradePermissions,
    build_tradeable_universe,
    classify_fund_security_type,
    classify_stock_board,
    normalize_fund_row,
    normalize_stock_row,
)


def stock(code, name="测试", market=0, price=10, amount=100_000_000, ch60=10, ytd=20):
    return {"f12": code, "f13": market, "f14": name, "f2": price, "f3": 1.2, "f6": amount, "f8": 2.3, "f20": 10_000_000_000, "f21": 8_000_000_000, "f24": ch60, "f25": ytd}


def test_default_permissions_only_keep_sh_sz_main_stock_boards():
    permissions = TradePermissions()
    rows = [stock("600000", market=1), stock("000001", market=0), stock("688001", market=1), stock("300001", market=0), stock("920001", market=0)]
    _, tradeable = build_tradeable_universe(rows, stock_source="test", permissions=permissions)
    assert {x.code for x in tradeable if x.security_type is SecurityType.STOCK} == {"600000", "000001"}


def test_stock_board_classification_matches_account_scope():
    assert classify_stock_board("600000", 1) is StockBoard.SH_MAIN
    assert classify_stock_board("000001", 0) is StockBoard.SZ_MAIN
    assert classify_stock_board("688001", 1) is StockBoard.STAR
    assert classify_stock_board("300001", 0) is StockBoard.CHINEXT
    assert classify_stock_board("920001", 0) is StockBoard.BSE


def test_st_and_delisting_are_hard_exclusions():
    st = normalize_stock_row(stock("600001", name="*ST测试", market=1), source="test")
    retired = normalize_stock_row(stock("600002", name="测试退", market=1), source="test")
    assert not st.tradable and "ST" in st.exclusion_reasons
    assert not retired.tradable and "DELISTING" in retired.exclusion_reasons


def test_funds_tracking_restricted_boards_are_still_allowed():
    rows = [
        {"f12": "588000", "f13": 1, "f14": "科创50ETF", "f2": 1.0, "f6": 80_000_000},
        {"f12": "159915", "f13": 0, "f14": "创业板ETF", "f2": 2.0, "f6": 90_000_000},
        {"f12": "159376", "f13": 0, "f14": "北证50ETF", "f2": 1.2, "f6": 60_000_000},
    ]
    items = [normalize_fund_row(row, security_type=SecurityType.ETF, source="test") for row in rows]
    assert all(item.tradable for item in items)


def test_explicit_etf_name_overrides_wrong_source_bucket():
    row = {"f12": "159999", "f13": 0, "f14": "测试ETF", "f2": 1.0, "f6": 50_000_000, "f24": 8, "f25": 12}
    item = normalize_fund_row(row, security_type=SecurityType.LOF, source="wrong-bucket")
    assert classify_fund_security_type("测试ETF", SecurityType.LOF) is SecurityType.ETF
    assert item.security_type is SecurityType.ETF


def test_missing_market_history_stays_missing_instead_of_becoming_zero():
    item = normalize_stock_row(stock("600003", market=1, ch60=None, ytd=None), source="新浪兜底")
    assert item.change_60d is None
    assert item.change_ytd is None
    assert item.data_quality is DataQuality.PARTIAL
    assert "change_60d" in item.missing_fields
    assert "change_ytd" in item.missing_fields
