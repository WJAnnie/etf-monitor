from trading_skill.industry_intelligence import (
    EventPolarity,
    classify_news_title,
    parse_news_rows,
)


def test_major_positive_order_event_is_detected():
    polarity, impact, terms, is_report = classify_news_title("船舶行业签下重大合同，大额订单增长")
    assert polarity is EventPolarity.POSITIVE
    assert impact == 3
    assert "重大合同" in terms
    assert not is_report


def test_major_negative_regulatory_event_is_detected():
    polarity, impact, terms, _ = classify_news_title("某行业龙头遭立案调查并面临监管处罚")
    assert polarity is EventPolarity.NEGATIVE
    assert impact == 3
    assert "立案调查" in terms


def test_report_event_is_separate_from_directional_classification():
    polarity, impact, _, is_report = classify_news_title("半导体设备公司披露三季报")
    assert polarity is EventPolarity.NEUTRAL
    assert impact == 0
    assert is_report


def test_news_parser_supports_eastmoney_article_shape_and_deduplicates():
    payload = {
        "result": {
            "cmsArticleWebOld": [
                {"title": "创新药获批上市", "date": "2026-09-09 10:00:00", "mediaName": "测试源", "url": "https://example.com/a"},
                {"title": "创新药获批上市", "date": "2026-09-09 10:00:00", "mediaName": "测试源", "url": "https://example.com/a"},
            ]
        }
    }
    items = parse_news_rows(payload)
    assert len(items) == 1
    assert items[0].polarity is EventPolarity.POSITIVE
    assert items[0].impact >= 2
