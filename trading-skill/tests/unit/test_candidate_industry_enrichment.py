from scripts.enrich_candidate_industries import _apply_context


def test_market_wide_candidate_gets_real_industry_profile_and_valuation():
    item = {
        "code": "600000",
        "name": "测试股份",
        "security_type": "STOCK",
        "board": "SH_MAIN",
        "industry_name": None,
    }
    out = _apply_context(item, {"industry": "半导体设备", "pe": 45.0, "pb": 5.0})
    assert out["industry_context_complete"] is True
    assert out["fundamental_industry_name"] == "半导体设备"
    assert out["valuation_pe"] == 45.0
    assert out["valuation_pb"] == 5.0
    assert out["industry_analysis_profile"]["profile"] == "半导体设备与材料"


def test_existing_industry_keeps_step3_context_when_quote_industry_is_missing():
    item = {
        "code": "000001",
        "name": "测试股份",
        "security_type": "STOCK",
        "board": "SZ_MAIN",
        "industry_name": "银行",
    }
    out = _apply_context(item, {"industry": None, "pe": 6.0, "pb": 0.7})
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
    }
    out = _apply_context(item, {"industry": None, "pe": 20.0, "pb": 2.0}, error="source down")
    assert out["industry_context_complete"] is False
    assert out["fundamental_industry_name"] is None
    assert "只能WATCH" in out["industry_context_note"]
