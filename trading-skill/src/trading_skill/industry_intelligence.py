from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterable

import requests


EASTMONEY_NEWS = "https://search-api-web.eastmoney.com/search/jsonp"


class EventPolarity(StrEnum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


@dataclass(frozen=True, slots=True)
class IndustryNewsItem:
    title: str
    published_at: str
    source: str
    url: str
    polarity: EventPolarity
    impact: int
    is_report_event: bool
    matched_terms: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class IndustryIntelligence:
    industry_code: str
    industry_name: str
    keyword: str
    positive_score: int
    negative_score: int
    net_event_score: int
    major_positive: tuple[IndustryNewsItem, ...]
    major_negative: tuple[IndustryNewsItem, ...]
    report_events: tuple[IndustryNewsItem, ...]
    fetched_count: int
    status: str = "OK"

    def as_payload(self) -> dict:
        payload = asdict(self)
        for key in ("major_positive", "major_negative", "report_events"):
            for item in payload[key]:
                item["polarity"] = str(item["polarity"])
        return payload


# 保守词典：只把标题中较明确、方向性强的事件归类为重大利好/利空。
# 普通“看好/关注/上涨”等情绪词不计入，避免把市场噪声当基本面事件。
POSITIVE_TERMS: tuple[tuple[str, int], ...] = (
    ("中标", 2), ("重大合同", 3), ("大额订单", 3), ("订单增长", 2),
    ("获批", 2), ("批准上市", 3), ("政策支持", 2), ("专项支持", 2),
    ("补贴", 1), ("回购", 1), ("业绩预增", 2), ("扭亏", 2),
    ("上调评级", 1), ("涨价", 1), ("突破", 1), ("国产替代", 1),
    ("签署战略合作", 1), ("减税", 2), ("降费", 1),
)
NEGATIVE_TERMS: tuple[tuple[str, int], ...] = (
    ("立案调查", 3), ("行政处罚", 3), ("监管处罚", 3), ("重大事故", 3),
    ("召回", 2), ("停产", 2), ("减产", 1), ("禁令", 3), ("制裁", 3),
    ("违约", 3), ("债务逾期", 3), ("业绩预减", 2), ("预亏", 2),
    ("亏损扩大", 2), ("下调评级", 1), ("产能过剩", 2), ("价格战", 1),
    ("客户流失", 2), ("订单取消", 3), ("退市风险", 3),
)
REPORT_TERMS = ("年报", "年度报告", "半年报", "中报", "一季报", "三季报", "季度报告", "季报", "业绩预告", "业绩快报")


def _clean(text: object) -> str:
    raw = html.unescape(str(text or ""))
    raw = re.sub(r"<[^>]+>", "", raw)
    return re.sub(r"\s+", " ", raw).strip()


def classify_news_title(title: str) -> tuple[EventPolarity, int, tuple[str, ...], bool]:
    text = _clean(title)
    positives = [(term, weight) for term, weight in POSITIVE_TERMS if term in text]
    negatives = [(term, weight) for term, weight in NEGATIVE_TERMS if term in text]
    positive_score = sum(weight for _, weight in positives)
    negative_score = sum(weight for _, weight in negatives)
    matched = tuple(term for term, _ in positives + negatives)
    if positive_score > negative_score:
        polarity = EventPolarity.POSITIVE
        impact = min(3, positive_score)
    elif negative_score > positive_score:
        polarity = EventPolarity.NEGATIVE
        impact = min(3, negative_score)
    else:
        polarity = EventPolarity.NEUTRAL
        impact = 0
    return polarity, impact, matched, any(term in text for term in REPORT_TERMS)


def _jsonp_payload(text: str) -> dict:
    stripped = text.strip()
    match = re.match(r"^[^(]*\((.*)\)\s*;?\s*$", stripped, re.S)
    if not match:
        raise ValueError("EASTMONEY_NEWS_JSONP_INVALID")
    payload = json.loads(match.group(1))
    if not isinstance(payload, dict):
        raise ValueError("EASTMONEY_NEWS_PAYLOAD_INVALID")
    return payload


def _article_rows(payload: dict) -> list[dict]:
    result = payload.get("result") or {}
    for key in ("cmsArticleWebOld", "cmsArticle"):
        rows = result.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def _field(row: dict, *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return _clean(value)
    return ""


def _parse_time(value: str) -> datetime | None:
    text = value.strip().replace("/", "-")
    if not text:
        return None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def parse_news_rows(payload: dict) -> tuple[IndustryNewsItem, ...]:
    items: list[IndustryNewsItem] = []
    seen: set[tuple[str, str]] = set()
    for row in _article_rows(payload):
        title = _field(row, "title", "ArticleTitle", "articleTitle", "name")
        if not title:
            continue
        published = _field(row, "date", "showTime", "publishTime", "dateTime", "Art_ShowTime", "displayTime")
        source = _field(row, "mediaName", "source", "Source", "media")
        url = _field(row, "url", "articleUrl", "ArticleUrl", "href")
        key = (title, published)
        if key in seen:
            continue
        seen.add(key)
        polarity, impact, matched, is_report = classify_news_title(title)
        items.append(IndustryNewsItem(title, published, source, url, polarity, impact, is_report, matched))
    return tuple(items)


def fetch_eastmoney_industry_news(keyword: str, *, page_size: int = 16, timeout: int = 10) -> tuple[IndustryNewsItem, ...]:
    callback = "jQuery_industry_intelligence"
    inner = {
        "uid": "",
        "keyword": keyword,
        "type": ["cmsArticleWebOld"],
        "client": "web",
        "clientType": "web",
        "clientVersion": "curr",
        "param": {
            "cmsArticleWebOld": {
                "searchScope": "default",
                "sort": "default",
                "pageIndex": 1,
                "pageSize": page_size,
                "preTag": "",
                "postTag": "",
            }
        },
    }
    response = requests.get(
        EASTMONEY_NEWS,
        params={"cb": callback, "param": json.dumps(inner, ensure_ascii=False, separators=(",", ":"))},
        headers={"User-Agent": "Mozilla/5.0", "Referer": "https://so.eastmoney.com/"},
        timeout=timeout,
    )
    response.raise_for_status()
    return parse_news_rows(_jsonp_payload(response.text))


def _recent(items: Iterable[IndustryNewsItem], *, as_of: datetime, days: int) -> list[IndustryNewsItem]:
    cutoff = as_of.replace(tzinfo=None) - timedelta(days=days)
    out = []
    for item in items:
        stamp = _parse_time(item.published_at)
        if stamp is None or stamp >= cutoff:
            out.append(item)
    return out


def build_industry_intelligence(
    *, industry_code: str, industry_name: str, keyword: str, as_of: datetime,
    page_size: int = 16, recent_days: int = 10,
) -> IndustryIntelligence:
    items = _recent(fetch_eastmoney_industry_news(keyword, page_size=page_size), as_of=as_of, days=recent_days)
    positives = sorted(
        (item for item in items if item.polarity is EventPolarity.POSITIVE and item.impact >= 2),
        key=lambda item: (item.impact, item.published_at), reverse=True,
    )
    negatives = sorted(
        (item for item in items if item.polarity is EventPolarity.NEGATIVE and item.impact >= 2),
        key=lambda item: (item.impact, item.published_at), reverse=True,
    )
    reports = sorted((item for item in items if item.is_report_event), key=lambda item: item.published_at, reverse=True)
    positive_score = sum(item.impact for item in positives[:5])
    negative_score = sum(item.impact for item in negatives[:5])
    return IndustryIntelligence(
        industry_code=industry_code,
        industry_name=industry_name,
        keyword=keyword,
        positive_score=positive_score,
        negative_score=negative_score,
        net_event_score=positive_score - negative_score,
        major_positive=tuple(positives[:5]),
        major_negative=tuple(negatives[:5]),
        report_events=tuple(reports[:5]),
        fetched_count=len(items),
    )
