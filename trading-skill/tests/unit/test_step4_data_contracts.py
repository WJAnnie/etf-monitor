from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_skill.candidate_discovery import discover_stock_candidates, finalize_candidates
from trading_skill.data.market_bars import SecurityIdentity, aggregate_m30_to_m120, provider_symbol
from trading_skill.data.validate import validate_raw_bars
from trading_skill.deep_scan_handoff import build_deep_scan_queue
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import Timeframe
from trading_skill.market_universe import DataQuality, MarketSecurity, SecurityType, StockBoard
from trading_skill.security_pricing import tick_size_for_security_type


def _security(code="600000", *, market=1):
    return MarketSecurity(
        code=code,
        name=code,
        market=market,
        security_type=SecurityType.STOCK,
        board=StockBoard.SH_MAIN if market == 1 else StockBoard.SZ_MAIN,
        price=10.0,
        change_pct=1.0,
        amount=100_000_000,
        turnover_rate=2.0,
        total_market_cap=10_000_000_000,
        float_market_cap=8_000_000_000,
        change_60d=10.0,
        change_ytd=20.0,
        tradable=True,
        exclusion_reasons=(),
        data_quality=DataQuality.COMPLETE,
        missing_fields=(),
        source="test",
    )


def test_candidate_preserves_authoritative_market_identity():
    item = _security("600000", market=1)
    store = discover_stock_candidates([item], score_floor=0, market_strength_cap=5, early_turn_cap=5)
    candidate = finalize_candidates(store)[0]
    assert candidate.market == 1
    assert candidate.as_dict()["market"] == 1


def test_step4_provider_symbol_obeys_market_not_code_prefix_guess():
    # 故意使用“看起来像沪市”的代码配深市market，验证适配器只服从上游明确身份。
    identity = SecurityIdentity("510300", "fixture", 0, "ETF")
    assert provider_symbol(identity) == "sz510300"
    assert provider_symbol(SecurityIdentity("159919", "fixture", 1, "ETF")) == "sh159919"


def test_step4_identity_rejects_unknown_market():
    with pytest.raises(ValueError, match="STEP4_IDENTITY_MARKET_REQUIRED"):
        SecurityIdentity("510300", "fixture", 2, "ETF")


def test_security_specific_tick_sizes_are_not_shared_between_stock_and_fund():
    assert tick_size_for_security_type("STOCK") == Decimal("0.01")
    assert tick_size_for_security_type("ETF") == Decimal("0.001")
    assert tick_size_for_security_type("LOF") == Decimal("0.001")
    assert tick_size_for_security_type("FUND") == Decimal("0.001")


def test_m120_is_strictly_built_from_four_real_m30_bars_and_keeps_basis():
    rows = []
    for clock, price in (("10:00:00", 10.0), ("10:30:00", 10.1), ("11:00:00", 10.2), ("11:30:00", 10.3)):
        rows.append({
            "time": f"2026-09-09 {clock}",
            "open": price,
            "close": price + 0.05,
            "high": price + 0.1,
            "low": price - 0.1,
            "volume": 100,
            "amount": 1000,
            "_complete": True,
            "_adjustment": "forward",
        })
    out = aggregate_m30_to_m120(rows)
    assert len(out) == 1
    assert out[0]["time"].endswith("11:30:00")
    assert out[0]["_adjustment"] == "forward"


def test_mixed_price_basis_blocks_chan_input():
    now = datetime(2026, 9, 9, 10, 0, tzinfo=timezone.utc)
    bars = (
        RawBar.make(symbol="X", timeframe=Timeframe.M30, timestamp=now, open=10, high=11, low=9, close=10.5, volume=1, adjustment="forward"),
        RawBar.make(symbol="X", timeframe=Timeframe.M30, timestamp=now.replace(hour=11), open=10.5, high=11, low=10, close=10.8, volume=1, adjustment="raw"),
    )
    result = validate_raw_bars(bars, Decimal("0.01"))
    assert not result.result.valid
    assert "PRICE_BASIS_MIXED" in result.result.reason_codes


def test_deep_scan_handoff_preserves_market_when_provided():
    stock = {
        "code": "600000",
        "name": "fixture",
        "market": 1,
        "security_type": "STOCK",
        "source_routes": ["MARKET_STRENGTH"],
        "research_priority": "HIGH",
        "industry_name": "银行",
        "phase_a_assessment": {"risk_level": "LOW", "evidence_coverage": "FULL"},
        "specialized_evidence": {"coverage": "FULL"},
        "final_decision": {"status": "PASS", "deep_analysis_eligible": True},
    }
    queue = build_deep_scan_queue([stock], [], capacity_max=5, soft_target_min=1)
    assert len(queue) == 1
    assert queue[0].market == 1
