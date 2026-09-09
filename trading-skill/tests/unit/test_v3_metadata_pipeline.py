from scripts.collect_candidate_bars_v3 import merge_v3_metadata
from scripts.enrich_cross_industry_v3 import apply_cross_industry_context


def test_bar_collection_preserves_industry_report_and_valuation_metadata():
    base = {"code": "600000", "daily": [], "30m": [], "120m": [], "5m": []}
    source = {
        "industry_name": "船舶制造",
        "industry_rotation_state": "刚开始升温",
        "industry_analysis_profile": {"profile": "造船与海工"},
        "candidate_route": "动态行业路线",
        "recent_report": {"report_type": "中报"},
        "sector_financial_metrics": {"changes": {"construction_in_progress_change_pct": 12}},
        "sector_observation_override": False,
        "sector_observation_reason": None,
        "pe": 18.2,
        "pb": 2.1,
        "industry_events": [{"importance": "重大", "impact": "利好", "content": "新接订单增长"}],
    }
    merged = merge_v3_metadata(base, source)
    assert merged["industry_name"] == "船舶制造"
    assert merged["industry_analysis_profile"]["profile"] == "造船与海工"
    assert merged["recent_report"]["report_type"] == "中报"
    assert merged["sector_financial_metrics"]["changes"]["construction_in_progress_change_pct"] == 12
    assert merged["pe"] == 18.2 and merged["pb"] == 2.1
    assert merged["industry_events"][0]["impact"] == "利好"


def test_cross_market_candidate_gets_real_industry_profile_and_events():
    item = {
        "code": "600000",
        "name": "测试船企",
        "industry_code": "CROSS_MARKET",
        "industry_name": "跨行业结构补充",
        "candidate_route": "跨行业结构补充",
        "fundamental_prefilter": {
            "eligible": True,
            "deep_scan_eligible": True,
            "grade": "B",
            "reasons": [],
            "annual": {
                "report_date": "2025-12-31", "notice_date": "2026-03-30",
                "revenue_growth": 8, "profit_growth": 12, "roe": 10,
                "gross_margin": 18, "eps": 1.0, "operating_cash_per_share": 1.1,
            },
            "interim": {
                "report_date": "2026-06-30", "notice_date": "2026-08-30",
                "revenue_growth": 15, "profit_growth": 20, "roe": 9,
                "gross_margin": 20, "eps": 0.8, "operating_cash_per_share": 0.9,
            },
        },
    }
    events = [{"importance": "重大", "impact": "利空", "content": "行业重大限制"}]
    enriched = apply_cross_industry_context(item, actual_industry="船舶制造", events=events)
    assert enriched["industry_context_complete"] is True
    assert enriched["industry_name"] == "船舶制造"
    assert enriched["industry_analysis_profile"]["profile"] == "造船与海工"
    assert enriched["industry_events"] == events
    assert enriched["fundamental_prefilter"]["eligible"] is True
    assert enriched["fundamental_prefilter"]["deep_scan_eligible"] is True


def test_unresolved_cross_market_industry_is_observation_only_context():
    item = {
        "code": "000001",
        "name": "测试",
        "industry_code": "CROSS_MARKET",
        "candidate_route": "跨行业结构补充",
        "fundamental_prefilter": {"eligible": True, "grade": "B", "reasons": []},
    }
    enriched = apply_cross_industry_context(item, actual_industry=None, events=[])
    assert enriched["industry_context_complete"] is False
    assert "禁止" in enriched["industry_context_note"]
    assert enriched["industry_events"] == []
