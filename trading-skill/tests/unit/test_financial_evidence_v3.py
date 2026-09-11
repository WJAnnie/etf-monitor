from scripts.financial_evidence_v3 import (
    _merge_detail,
    company_type_for_industry,
    secucode_for_a_share,
)


def test_company_type_routes_financial_industries_to_special_f10_tables():
    assert company_type_for_industry("股份制银行") == "bank"
    assert company_type_for_industry("保险") == "insurance"
    assert company_type_for_industry("证券券商") == "security"
    assert company_type_for_industry("半导体设备") == "general"


def test_secucode_supports_sh_sz_and_bj():
    assert secucode_for_a_share("600519") == "600519.SH"
    assert secucode_for_a_share("300750") == "300750.SZ"
    assert secucode_for_a_share("835185") == "835185.BJ"


def test_statement_rows_merge_by_report_date_without_overwriting_valid_base_values():
    base = [{"REPORTDATE": "2026-06-30", "YSTZ": 20, "XSMLL": 30}]
    main = [{"REPORT_DATE": "2026-06-30", "ROEJQ": 16, "XSMLL": 99}]
    balance = [{"REPORT_DATE": "2026-06-30", "INVENTORY": 100, "FIXED_ASSET": 500, "CONTRACT_LIAB": 80}]
    income = [{"REPORT_DATE": "2026-06-30", "RESEARCH_EXPENSE": 12, "TOTAL_OPERATE_INCOME": 100}]
    cashflow = [{"REPORT_DATE": "2026-06-30", "CONSTRUCT_LONG_ASSET": 20}]
    rows = _merge_detail(base, [main, balance, income, cashflow], company_type="general", errors=[])
    assert len(rows) == 1
    row = rows[0]
    assert row["FINANCIAL_DETAIL_STATUS"] == "COMPLETE"
    assert row["XSMLL"] == 30
    assert row["ROEJQ"] == 16
    assert row["INVENTORY"] == 100
    assert row["RESEARCH_EXPENSE"] == 12
    assert row["CONSTRUCT_LONG_ASSET"] == 20


def test_partial_statement_failure_is_explicit_not_silently_clear():
    base = [{"REPORTDATE": "2026-06-30", "YSTZ": 20}]
    main = [{"REPORT_DATE": "2026-06-30", "ROEJQ": 16}]
    rows = _merge_detail(base, [main, [], [], []], company_type="general", errors=["balance failed"])
    assert rows[0]["FINANCIAL_DETAIL_STATUS"] == "PARTIAL"
    assert rows[0]["FINANCIAL_DETAIL_ERRORS"] == ("balance failed",)
