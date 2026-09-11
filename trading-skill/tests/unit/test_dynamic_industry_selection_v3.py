from scripts.collect_full_a_universe_v3 import (
    _event_adjustment,
    _major_negative_risk,
    _select_dynamic_industries,
)
from trading_skill.a_share_universe import IndustryCandidate
from trading_skill.industry_intelligence import (
    EventPolarity,
    IndustryIntelligence,
    IndustryNewsItem,
)


def _industry(code, name, *, theme=None, rank=80, ch60=10, ytd=20, heat="正常", low=60):
    return IndustryCandidate(
        code=code,
        name=name,
        heat_score=50,
        low_position_score=low,
        prospects_score=70,
        rank_score=rank,
        heat_state=heat,
        change_pct=1,
        change_60d=ch60,
        change_ytd=ytd,
        main_flow_ratio=0.5,
        breadth=0.6,
        selection_reason="test",
        prospect_theme=theme,
    )


def _intel(industry, *, positive=(), negative=()):
    positive_items = tuple(
        IndustryNewsItem(
            title=title,
            published_at="2026-09-10 09:00:00",
            source="test",
            url="",
            polarity=EventPolarity.POSITIVE,
            impact=impact,
            is_report_event=False,
        )
        for title, impact in positive
    )
    negative_items = tuple(
        IndustryNewsItem(
            title=title,
            published_at="2026-09-10 09:00:00",
            source="test",
            url="",
            polarity=EventPolarity.NEGATIVE,
            impact=impact,
            is_report_event=False,
        )
        for title, impact in negative
    )
    positive_score = sum(item.impact for item in positive_items)
    negative_score = sum(item.impact for item in negative_items)
    return IndustryIntelligence(
        industry_code=industry.code,
        industry_name=industry.name,
        keyword=industry.name,
        positive_score=positive_score,
        negative_score=negative_score,
        net_event_score=positive_score - negative_score,
        major_positive=positive_items,
        major_negative=negative_items,
        report_events=(),
        fetched_count=len(positive_items) + len(negative_items),
    )


def test_fresh_major_positive_can_promote_a_nonselected_low_position_industry():
    prospect = _industry("p", "前景行业", theme="长期前景", rank=95)
    dynamic = _industry("d", "新升温行业", rank=90)
    event = _industry("e", "事件新方向", rank=60)
    normal = _industry("n", "普通后排", rank=70)
    intelligence = {"e": _intel(event, positive=(("重大合同", 3),))}

    selected, quarantined, promoted = _select_dynamic_industries(
        [prospect, dynamic, event, normal],
        intelligence,
        prospect_limit=1,
        dynamic_supplement=1,
        event_promotion_limit=2,
    )
    assert quarantined == []
    assert [item.code for item in selected[:2]] == ["p", "d"]
    assert [item.code for item in promoted] == ["e"]
    assert "e" in {item.code for item in selected}


def test_major_positive_does_not_override_high_position_quarantine():
    prospect = _industry("p", "前景行业", theme="长期前景")
    dynamic = _industry("d", "动态行业")
    overheated = _industry("h", "高位事件行业", ch60=50, rank=99)
    intelligence = {"h": _intel(overheated, positive=(("重大合同", 3),))}

    selected, quarantined, promoted = _select_dynamic_industries(
        [prospect, dynamic, overheated],
        intelligence,
        prospect_limit=1,
        dynamic_supplement=1,
    )
    assert "h" in {item.code for item in quarantined}
    assert "h" not in {item.code for item in selected}
    assert "h" not in {item.code for item in promoted}


def test_single_impact3_major_negative_is_high_risk_without_waiting_for_two_headlines():
    industry = _industry("x", "测试行业")
    intel = _intel(industry, negative=(("重大事故", 3),))
    assert _major_negative_risk(intel) == "HIGH"


def test_lesser_negative_can_be_caution_and_missing_intelligence_is_unknown_not_normal():
    industry = _industry("x", "测试行业")
    intel = _intel(industry, negative=(("产能过剩", 2),))
    assert _major_negative_risk(intel) == "CAUTION"
    assert _major_negative_risk(None) == "UNKNOWN"


def test_event_score_really_changes_research_priority_direction():
    industry = _industry("x", "测试行业")
    positive = _intel(industry, positive=(("重大合同", 3),))
    negative = _intel(industry, negative=(("监管处罚", 3),))
    assert _event_adjustment(positive) > 0
    assert _event_adjustment(negative) < 0
