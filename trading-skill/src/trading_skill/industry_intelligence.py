from __future__ import annotations

import html
import re
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from typing import Iterable, Mapping

import requests

from trading_skill.industry_profiles import profile_dict


SINA_7X24 = "https://zhibo.sina.com.cn/api/zhibo/feed"
POSITIVE_WORDS = (
    "支持", "加码", "上调", "增长", "突破", "中标", "订单", "获批", "放量", "扩产", "回暖", "提价", "降税",
    "补贴", "签约", "创新高", "超预期", "增持", "回购", "政策利好", "出口增长", "需求增长",
)
NEGATIVE_WORDS = (
    "下调", "下降", "亏损", "减产", "停产", "取消", "制裁", "限制", "调查", "处罚", "召回", "违约", "爆雷",
    "低于预期", "价格战", "需求下滑", "订单下降", "库存高企", "事故", "禁令", "风险提示",
)
MAJOR_WORDS = (
    "国务院", "央行", "国家发改委", "工信部", "财政部", "证监会", "医保局", "国资委", "重大", "首次", "正式发布",
    "获批", "中标", "订单", "制裁", "禁令", "停产", "召回", "并购", "重组", "回购", "增持", "减持",
)


@dataclass(frozen=True, slots=True)
class IndustryEvent:
    industry: str
    theme: str | None
    time: str
    impact: str
    importance: str
    content: str
    source: str = "新浪财经7x24"


def _strip_html(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"〖[^〗]*〗", "", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch_sina_7x24(*, page_size: int = 100, timeout: int = 10) -> list[dict]:
    response = requests.get(
        SINA_7X24,
        params={"page": 1, "page_size": page_size, "zhibo_id": 152},
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
            "Referer": "https://finance.sina.com.cn/7x24/",
            "Accept": "application/json,text/plain,*/*",
        },
        timeout=timeout,
    )
    response.raise_for_status()
    payload = response.json()
    rows = (((payload.get("result") or {}).get("data") or {}).get("feed") or {}).get("list") or []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        content = _strip_html(row.get("rich_text"))
        if not content:
            continue
        out.append({"id": row.get("id"), "time": str(row.get("create_time") or ""), "content": content})
    return out


def _industry_keywords(industry: Mapping[str, object]) -> tuple[str, ...]:
    profile = profile_dict(str(industry.get("name") or ""), str(industry.get("prospect_theme") or "") or None)
    words = {str(industry.get("name") or ""), str(industry.get("prospect_theme") or "")}
    # 从行业画像的关注项中提取最有辨识度的中文词，避免只靠板块全名。
    for bucket in ("operating_focus", "report_focus"):
        for text in profile.get(bucket, []):
            for token in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]+", str(text)):
                if len(token) >= 3:
                    words.add(token)
    return tuple(word for word in words if len(word) >= 2)


def _impact(text: str) -> str:
    positive = sum(1 for word in POSITIVE_WORDS if word in text)
    negative = sum(1 for word in NEGATIVE_WORDS if word in text)
    if positive > negative:
        return "利好"
    if negative > positive:
        return "利空"
    return "中性/待观察"


def match_industry_events(
    selected_industries: Iterable[Mapping[str, object]],
    news_rows: Iterable[Mapping[str, object]],
    *,
    as_of: datetime,
    max_age_hours: int = 36,
    max_per_industry: int = 3,
) -> dict[str, list[dict]]:
    cutoff = as_of - timedelta(hours=max_age_hours)
    result: dict[str, list[dict]] = {}
    for industry in selected_industries:
        name = str(industry.get("name") or "")
        theme = str(industry.get("prospect_theme") or "") or None
        keywords = _industry_keywords(industry)
        matched: list[IndustryEvent] = []
        for row in news_rows:
            content = str(row.get("content") or "")
            if not content or not any(word in content for word in keywords):
                continue
            raw_time = str(row.get("time") or "")
            try:
                dt = datetime.fromisoformat(raw_time)
                if as_of.tzinfo and dt.tzinfo is None:
                    dt = dt.replace(tzinfo=as_of.tzinfo)
                if dt < cutoff or dt > as_of + timedelta(minutes=5):
                    continue
            except ValueError:
                pass
            importance = "重大" if any(word in content for word in MAJOR_WORDS) else "重要"
            matched.append(IndustryEvent(name, theme, raw_time, _impact(content), importance, content[:220]))
        matched.sort(key=lambda x: (x.importance == "重大", x.time), reverse=True)
        if matched:
            result[name] = [asdict(item) for item in matched[:max_per_industry]]
    return result


def recent_report_event(row: Mapping[str, object], *, as_of: datetime, days: int = 10) -> dict | None:
    notice = str(row.get("NOTICE_DATE") or row.get("UPDATE_DATE") or "")[:10]
    report_date = str(row.get("REPORTDATE") or "")[:10]
    if not notice or not report_date:
        return None
    try:
        notice_dt = datetime.fromisoformat(notice)
        if as_of.tzinfo:
            notice_dt = notice_dt.replace(tzinfo=as_of.tzinfo)
    except ValueError:
        return None
    if notice_dt < as_of - timedelta(days=days) or notice_dt > as_of:
        return None
    suffix = report_date[5:]
    report_type = {"03-31": "一季报", "06-30": "中报", "09-30": "三季报", "12-31": "年报"}.get(suffix, "定期报告")
    def num(key):
        value = row.get(key)
        try:
            return None if value in (None, "", "-") else round(float(value), 2)
        except (TypeError, ValueError):
            return None
    return {
        "notice_date": notice,
        "report_date": report_date,
        "report_type": report_type,
        "revenue_growth": num("YSTZ"),
        "profit_growth": num("SJLTZ"),
        "roe": num("WEIGHTAVG_ROE"),
        "gross_margin": num("XSMLL"),
        "eps": num("BASIC_EPS"),
        "operating_cash_per_share": num("MGJYXJJE"),
    }
