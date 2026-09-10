from scripts.collect_candidate_bars_v3 import _account_stock_allowed
from trading_skill.market_universe import TradePermissions


def test_legacy_deep_scan_permission_bridge_only_allows_sh_sz_main_stock():
    permissions = TradePermissions(sh_main=True, sz_main=True, star=False, chinext=False, bse=False)
    cases = [
        ({"code": "600000", "market": 1}, True, "SH_MAIN"),
        ({"code": "000001", "market": 0}, True, "SZ_MAIN"),
        ({"code": "688001", "market": 1}, False, "STAR"),
        ({"code": "300001", "market": 0}, False, "CHINEXT"),
        ({"code": "920001", "market": 0}, False, "BSE"),
    ]
    for item, expected, board in cases:
        allowed, actual_board = _account_stock_allowed(item, permissions)
        assert allowed is expected
        assert actual_board == board
