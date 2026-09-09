from trading_skill.industry_intelligence import _impact


def test_negative_compound_phrase_beats_generic_positive_word():
    assert _impact("船舶行业新接订单下降，需求下滑") == "利空"
    assert _impact("某行业大额订单落地，需求增长并上调指引") == "利好"


def test_mixed_event_can_remain_neutral_when_evidence_conflicts():
    result = _impact("政策支持力度增加，但企业同时下调指引并提示需求下滑")
    assert result in {"利空", "中性/待观察"}
