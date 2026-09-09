from trading_skill.fund_product_quality import (
    FundProductStatus,
    ProductEvidenceCoverage,
    ProductQuality,
    TradingQuality,
    UnderlyingAssetState,
    assess_fund_product,
)


def fund(**overrides):
    item = {
        "code": "510300",
        "name": "沪深300ETF",
        "security_type": "ETF",
        "fund_category": "EQUITY_BROAD",
        "fund_family": "EQUITY_BROAD:沪深300",
        "amount": 800_000_000,
        "position_stage": "NORMAL",
        "research_priority": "HIGH",
    }
    item.update(overrides)
    return item


def test_liquid_broad_etf_can_pass_without_stock_fundamental_model():
    result = assess_fund_product(fund())
    assert result.status is FundProductStatus.PASS
    assert result.underlying_state is UnderlyingAssetState.SUPPORTIVE
    assert result.trading_quality is TradingQuality.STRONG
    assert result.product_quality in {ProductQuality.STRONG, ProductQuality.ADEQUATE}
    assert result.deep_analysis_eligible is True


def test_peer_relative_liquidity_overrides_large_absolute_turnover():
    result = assess_fund_product(fund(amount=2_000_000_000, fund_liquidity_percentile=20.0))
    assert result.trading_quality is TradingQuality.WEAK
    assert result.status is FundProductStatus.WATCH


def test_cross_border_without_fresh_premium_must_watch():
    result = assess_fund_product(
        fund(
            code="513100",
            name="纳指ETF",
            fund_category="CROSS_BORDER",
            fund_family="CROSS_BORDER:纳斯达克100",
        )
    )
    assert result.status is FundProductStatus.WATCH
    assert any("折溢价" in text for text in result.warnings)


def test_commodity_qdii_keeps_commodity_asset_class_but_requires_premium_check():
    result = assess_fund_product(
        fund(
            code="161125",
            name="标普油气LOF",
            security_type="LOF",
            fund_category="COMMODITY",
            fund_family="COMMODITY:油气",
            fund_risk_tags=("CROSS_BORDER_QDII", "LOF_PREMIUM"),
            fund_liquidity_percentile=85.0,
        )
    )
    assert result.status is FundProductStatus.WATCH
    assert result.underlying_state is UnderlyingAssetState.NEUTRAL
    assert any("折溢价" in text for text in result.warnings)


def test_fresh_extreme_premium_cannot_pass():
    result = assess_fund_product(
        fund(code="161130", name="纳斯达克100LOF", security_type="LOF", fund_category="CROSS_BORDER"),
        reference={"premium_discount_pct": 12.5, "premium_is_fresh": True},
    )
    assert result.status is FundProductStatus.WATCH
    assert result.premium_discount_pct == 12.5


def test_stale_premium_is_not_used_as_if_current():
    result = assess_fund_product(
        fund(code="161130", name="纳斯达克100LOF", security_type="LOF", fund_category="CROSS_BORDER"),
        reference={"premium_discount_pct": 1.0, "premium_is_fresh": False},
    )
    assert result.status is FundProductStatus.WATCH
    assert result.premium_discount_pct is None


def test_tiny_fund_size_is_hard_product_risk():
    result = assess_fund_product(
        fund(),
        reference={"fund_size_cny": 8_000_000, "management_fee_pct": 0.2, "custody_fee_pct": 0.05},
    )
    assert result.status is FundProductStatus.REJECT
    assert result.deep_analysis_eligible is False
    assert result.evidence_coverage in {ProductEvidenceCoverage.FULL, ProductEvidenceCoverage.PARTIAL}


def test_cash_fund_is_valid_product_but_skips_chan_deep_scan_by_default():
    result = assess_fund_product(
        fund(code="511990", name="华宝添益ETF", fund_category="CASH", fund_family="CASH:货币")
    )
    assert result.status is FundProductStatus.PASS
    assert result.deep_analysis_eligible is False


def test_sector_etf_can_use_dynamic_quality_industry_context():
    result = assess_fund_product(
        fund(code="512480", name="半导体ETF", fund_category="EQUITY_SECTOR", fund_family="EQUITY_SECTOR:半导体"),
        selected_industries=[{"name": "半导体材料"}],
    )
    assert result.underlying_state is UnderlyingAssetState.SUPPORTIVE
    assert result.status is FundProductStatus.PASS


def test_sector_etf_without_resolved_theme_stays_watch_not_fake_pass():
    result = assess_fund_product(
        fund(code="159999", name="神秘主题ETF", fund_category="EQUITY_SECTOR", fund_family="EQUITY_SECTOR:神秘主题")
    )
    assert result.underlying_state is UnderlyingAssetState.UNKNOWN
    assert result.status is FundProductStatus.WATCH
