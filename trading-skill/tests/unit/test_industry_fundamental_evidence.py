from trading_skill.industry_fundamental_evidence import (
    EvidenceFamily,
    SpecializedCoverage,
    SpecializedQuality,
    assess_specialized_evidence,
)


def detail(*, debt=45.0, ocf=1_000_000_000, receivables=10.0, inventory=8.0, contract=25.0, cash=5.0, capex=15.0, cip=12.0, cash_ratio=20.0):
    return {
        "current": {
            "debt_asset_ratio_pct": debt,
            "operating_cash_flow": ocf,
            "cash_to_assets_pct": cash_ratio,
        },
        "changes": {
            "accounts_receivable_change_pct": receivables,
            "inventory_change_pct": inventory,
            "contract_liabilities_change_pct": contract,
            "monetary_funds_change_pct": cash,
            "construct_long_asset_cash_change_pct": capex,
            "construction_in_progress_change_pct": cip,
        },
    }


def test_bank_good_specialized_metrics_can_upgrade_watch():
    rows = [{
        "REPORT_DATE": "2026-06-30",
        "BLDKBBL": 1.1,
        "HXYJBCZL": 11.5,
        "NEWCAPITALADER": 14.2,
        "ROEJQ": 11.0,
    }]
    out = assess_specialized_evidence("银行", main_financial_rows=rows)
    assert out.family is EvidenceFamily.FINANCIAL
    assert out.coverage is SpecializedCoverage.FULL
    assert out.quality is SpecializedQuality.STRONG
    assert out.can_upgrade_watch is True
    assert "净息差及其趋势" in out.missing_evidence


def test_bank_hard_asset_quality_or_capital_problem_cannot_upgrade():
    rows = [{
        "REPORT_DATE": "2026-06-30",
        "BLDKBBL": 4.5,
        "HXYJBCZL": 5.5,
        "NEWCAPITALADER": 9.0,
        "ROEJQ": 4.0,
    }]
    out = assess_specialized_evidence("银行", main_financial_rows=rows)
    assert out.quality is SpecializedQuality.WEAK
    assert out.hard_risks
    assert out.can_upgrade_watch is False


def test_insurance_nbv_solvency_and_roi_produce_specialized_support():
    rows = [{
        "REPORT_DATE": "2026-06-30",
        "SOLVENCY_AR": 185.0,
        "NBV_LIFE": 8_000_000_000,
        "NBV_RATE": 16.0,
        "TOTAL_ROI": 4.2,
        "ROEJQ": 10.0,
    }]
    out = assess_specialized_evidence("保险", main_financial_rows=rows)
    assert out.coverage is SpecializedCoverage.FULL
    assert out.quality is SpecializedQuality.STRONG
    assert out.can_upgrade_watch is True


def test_broker_positive_capital_and_net_assets_are_not_treated_as_manufacturing():
    rows = [{
        "REPORT_DATE": "2026-06-30",
        "JZB": 50_000_000_000,
        "JZC": 100_000_000_000,
        "JZBJZC": 50.0,
        "ROEJQ": 9.0,
    }]
    out = assess_specialized_evidence("券商/资产管理", main_financial_rows=rows)
    assert out.family is EvidenceFamily.FINANCIAL
    assert out.coverage is SpecializedCoverage.FULL
    assert out.quality is SpecializedQuality.STRONG
    assert out.can_upgrade_watch is True


def test_order_driven_company_can_upgrade_with_coherent_statement_evidence():
    out = assess_specialized_evidence("半导体设备与材料", detailed_metrics=detail())
    assert out.family is EvidenceFamily.ORDER_MANUFACTURING
    assert out.coverage is SpecializedCoverage.FULL
    assert out.quality is SpecializedQuality.STRONG
    assert out.can_upgrade_watch is True
    assert any("合同负债" in x for x in out.positive_evidence)
    assert any("真实手持订单" in x for x in out.missing_evidence)


def test_rnd_accounting_proxies_never_auto_upgrade_without_pipeline_or_product_evidence():
    out = assess_specialized_evidence("创新药/生物医药", detailed_metrics=detail(cash_ratio=30.0, cash=10.0))
    assert out.family is EvidenceFamily.RND
    assert out.coverage is SpecializedCoverage.FULL
    assert out.can_upgrade_watch is False
    assert any("研发管线" in x for x in out.missing_evidence)


def test_cyclical_accounting_proxies_never_auto_upgrade_without_price_cost_cycle_evidence():
    out = assess_specialized_evidence("煤炭/油气/资源品", detailed_metrics=detail(inventory=5.0, capex=10.0))
    assert out.family is EvidenceFamily.CYCLICAL
    assert out.coverage is SpecializedCoverage.FULL
    assert out.can_upgrade_watch is False
    assert any("商品价格" in x for x in out.missing_evidence)


def test_consumer_inventory_explosion_is_warning():
    out = assess_specialized_evidence("食品饮料/白酒", detailed_metrics=detail(inventory=85.0, contract=-5.0))
    assert out.family is EvidenceFamily.CONSUMER_CASHFLOW
    assert any("库存快速增长" in x for x in out.warnings)


def test_limited_specialized_evidence_is_insufficient_and_never_upgrades():
    out = assess_specialized_evidence("半导体设备与材料", detailed_metrics={"current": {}, "changes": {}})
    assert out.coverage is SpecializedCoverage.LIMITED
    assert out.quality is SpecializedQuality.INSUFFICIENT
    assert out.can_upgrade_watch is False
