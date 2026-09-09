from scripts.enrich_candidate_industries import _apply_context, industry_master_from_rows, normalize_em_industry


def test_market_wide_candidate_gets_real_industry_and_reuses_step1_valuation():
    item = {
        "code": "600000",
        "name": "测试股份",
        "security_type": "STOCK",
        "board": "SH_MAIN",
        "industry_name": None,
        "valuation_pe": 45.0,
        "valuation_pb": 5.0,
    }
    out = _apply_context(item, "半导体设备", source="东方财富F10公司主数据EM2016")
    assert out["industry_context_complete"] is True
    assert out["fundamental_industry_name"] == "半导体设备"
    assert out["valuation_pe"] == 45.0
    assert out["valuation_pb"] == 5.0
    assert out["valuation_source"] == "Step1全市场行情f9/f23"
    assert out["industry_context_source"] == "东方财富F10公司主数据EM2016"
    assert out["industry_analysis_profile"]["profile"] == "半导体设备与材料"


def test_existing_industry_keeps_step3_context_without_extra_industry_lookup():
    item = {
        "code": "000001",
        "name": "测试股份",
        "security_type": "STOCK",
        "board": "SZ_MAIN",
        "industry_name": "银行",
        "valuation_pe": 6.0,
        "valuation_pb": 0.7,
    }
    out = _apply_context(item, None)
    assert out["industry_context_complete"] is True
    assert out["fundamental_industry_name"] == "银行"
    assert out["industry_context_source"] == "第二步行业路线"
    assert out["valuation_pb"] == 0.7


def test_missing_industry_never_falls_back_to_fake_generic_pass_context():
    item = {
        "code": "000002",
        "name": "测试股份",
        "security_type": "STOCK",
        "board": "SZ_MAIN",
        "industry_name": None,
        "valuation_pe": 20.0,
        "valuation_pb": 2.0,
    }
    out = _apply_context(item, None, error="source down")
    assert out["industry_context_complete"] is False
    assert out["fundamental_industry_name"] is None
    assert out["valuation_pe"] == 20.0
    assert "只能WATCH" in out["industry_context_note"]


def test_f10_hierarchical_industry_uses_most_specific_leaf():
    assert normalize_em_industry("制造业-计算机、通信和其他电子设备制造业-半导体设备") == "半导体设备"
    assert normalize_em_industry("金融业—银行") == "银行"
    assert normalize_em_industry(None) is None


def test_industry_master_prefers_em2016_and_falls_back_to_csrc():
    rows = [
        {"SECURITY_CODE": "600000", "EM2016": "金融业-银行", "INDUSTRYCSRC1": "货币金融服务"},
        {"STR_CODEA": "000001", "EM2016": None, "INDUSTRYCSRC1": "银行"},
        {"SECURITY_CODE": "BAD", "EM2016": "半导体"},
    ]
    master = industry_master_from_rows(rows)
    assert master["600000"] == "银行"
    assert master["000001"] == "银行"
    assert "BAD" not in master
