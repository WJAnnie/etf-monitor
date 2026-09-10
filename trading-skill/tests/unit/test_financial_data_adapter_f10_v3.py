from trading_skill.industry_financial_metrics import compare_snapshots, statement_snapshot
from scripts.financial_data_adapter import _profile_company_type, secucode


def test_secucode_preserves_sh_sz_and_bj_markets():
    assert secucode("600519", "SH_MAIN") == "600519.SH"
    assert secucode("300750", "SZ_GEM") == "300750.SZ"
    assert secucode("835185", "BJ") == "835185.BJ"
    assert secucode("430047", "") == "430047.BJ"


def test_company_type_routes_financial_profiles_without_using_general_statements():
    assert _profile_company_type("银行") == "bank"
    assert _profile_company_type("保险") == "insurance"
    assert _profile_company_type("券商/资产管理") == "security"
    assert _profile_company_type("造船与海工") == "general"


def test_statement_snapshot_includes_real_rd_intensity_from_income_statement():
    balance = {
        "REPORT_DATE": "2026-06-30",
        "TOTAL_ASSETS": 1000,
        "TOTAL_LIABILITIES": 400,
        "MONETARYFUNDS": 200,
        "INVENTORY": 100,
    }
    income = {
        "REPORT_DATE": "2026-06-30",
        "TOTAL_OPERATE_INCOME": 500,
        "RESEARCH_EXPENSE": 50,
    }
    cashflow = {
        "REPORT_DATE": "2026-06-30",
        "NETCASH_OPERATE": 80,
        "CONSTRUCT_LONG_ASSET": 30,
    }
    out = statement_snapshot(balance, cashflow, income)
    assert out["revenue"] == 500
    assert out["research_expense"] == 50
    assert out["rd_intensity_pct"] == 10.0
    assert out["debt_asset_ratio_pct"] == 40.0


def test_rd_and_revenue_changes_are_explicit_period_evidence():
    current = {"research_expense": 60, "revenue": 600}
    previous = {"research_expense": 50, "revenue": 500}
    changes = compare_snapshots(current, previous)
    assert changes["research_expense_change_pct"] == 20.0
    assert changes["revenue_change_pct"] == 20.0
