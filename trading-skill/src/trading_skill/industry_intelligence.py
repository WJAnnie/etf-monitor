from __future__ import annotations

import html
import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from typing import Iterable, Mapping

import requests

from trading_skill.industry_profiles import profile_for


SINA_7X24 = "https://zhibo.sina.com.cn/api/zhibo/feed"
EASTMONEY_SEARCH = "https://search-api-web.eastmoney.com/search/jsonp"
STRONG_POSITIVE_PHRASES = (
    "政策利好", "大额订单", "订单增长", "新接订单增长", "需求增长", "出口增长", "超预期",
    "纳入医保", "获批上市", "中标重大项目", "提高补贴", "加大支持", "上调指引",
)
STRONG_NEGATIVE_PHRASES = (
    "订单下降", "需求下滑", "低于预期", "库存高企", "集采降价", "价格战", "出口受限",
    "暂停审批", "停止采购", "取消订单", "下调指引", "重大事故", "停产整顿", "制裁", "禁令",
)
POSITIVE_WORDS = (
    "支持", "加码", "上调", "增长", "突破", "中标", "获批", "放量", "扩产", "回暖", "提价", "降税",
    "补贴", "签约", "创新高", "增持", "回购",
)
NEGATIVE_WORDS = (
    "下调", "下降", "亏损", "减产", "停产", "取消", "限制", "调查", "处罚", "召回", "违约", "爆雷",
    "事故", "风险提示", "减持",
)
MAJOR_WORDS = (
    "国务院", "央行", "国家发改委", "工信部", "财政部", "证监会", "医保局", "国资委", "海关总署", "重大", "首次",
    "正式发布", "获批", "中标", "大额订单", "制裁", "禁令", "停产", "召回", "并购", "重组", "回购", "增持", "减持",
)
GENERIC_NON_IDENTITY_WORDS = {
    "订单", "毛利率", "研发", "研发投入", "资本开支", "固定资产", "在建工程", "存货", "应收账款", "经营现金流",
    "合同负债", "销量", "收入", "利润", "增长", "政策", "价格", "产品", "扩产", "中标", "回购", "增持",
}


@dataclass(frozen=True, slots=True)
class IndustryEvent:
    industry: str
    theme: str | None
    time: str
    impact: str
    importance: str
    content: str
    source: str
    matched_keyword: str | None = None


def _strip_html(text: str) -> str:
    text = html.unescape(str(text or ""))
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"〖[^〗]*〗", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _parse_time(value: str, *, as_of: datetime) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    for candidate in (raw, raw.replace("/", "-")):
        try:
            dt = datetime.fromisoformat(candidate)
        except ValueError:
            continue
        if as_of.tzinfo and dt.tzinfo is None:
            dt = dt.replace(tzinfo=as_of.tzinfo)
        return dt
    return None


def fetch_sina_7x24(*, page_size: int = 100, timeout: int = 10, max_pages: int = 8) -> list[dict]:
    """抓取宽市场快讯并自动翻页。

    新浪接口实际可能忽略较大的 page_size（生产日志曾请求120却只返回10条），所以不能把单页参数
    当作真实覆盖。这里按页继续抓取、按id/正文去重，直到达到目标数量、遇到空页/重复页或达到页数上限。
    """
    target = max(1, int(page_size))
    out: list[dict] = []
    seen: set[str] = set()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Referer": "https://finance.sina.com.cn/7x24/",
        "Accept": "application/json,text/plain,*/*",
    }
    for page in range(1, max(1, max_pages) + 1):
        response = requests.get(
            SINA_7X24,
            params={"page": page, "page_size": min(target, 100), "zhibo_id": 152},
            headers=headers,
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        rows = (((payload.get("result") or {}).get("data") or {}).get("feed") or {}).get("list") or []
        if not rows:
            break
        added = 0
        for row in rows:
            if not isinstance(row, dict):
                continue
            content = _strip_html(row.get("rich_text"))
            if not content:
                continue
            fingerprint = str(row.get("id") or "") or re.sub(r"\W+", "", content)[:140]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            out.append({
                "id": row.get("id"), "time": str(row.get("create_time") or ""), "content": content,
                "source": "新浪财经7x24", "query": None,
            })
            added += 1
            if len(out) >= target:
                return out
        if added == 0:
            break
    return out


def _decode_jsonp(text: str) -> dict:
    raw = str(text or "").strip()
    start = raw.find("(")
    end = raw.rfind(")")
    if start >= 0 and end > start:
        raw = raw[start + 1 : end]
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def fetch_eastmoney_news(query: str, *, page_size: int = 8, timeout: int = 10) -> list[dict]:
    """东财公开资讯搜索兜底。失败由调用层降级，不允许资讯源故障阻断选股。"""
    param = {
        "uid": "", "keyword": query, "type": ["cmsArticleWebOld"], "client": "web", "clientType": "web",
        "clientVersion": "curr",
        "param": {"cmsArticleWebOld": {"searchScope": "default", "sort": "time", "pageIndex": 1,
                                          "pageSize": page_size, "preTag": "", "postTag": ""}},
    }
    response = requests.get(
        EASTMONEY_SEARCH,
        params={"cb": "jQuery_trade_skill", "param": json.dumps(param, ensure_ascii=False, separators=(",", ":"))},
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
                 "Referer": "https://so.eastmoney.com/", "Accept": "*/*"},
        timeout=timeout,
    )
    response.raise_for_status()
    payload = _decode_jsonp(response.text)
    result = payload.get("result") or payload.get("Result") or {}
    block = result.get("cmsArticleWebOld") or result.get("CmsArticleWebOld") or {}
    rows = block.get("list") or block.get("data") or block.get("items") or []
    if isinstance(block, list):
        rows = block
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = _strip_html(row.get("title") or row.get("Title") or "")
        body = _strip_html(row.get("content") or row.get("summary") or row.get("Content") or "")
        content = "；".join(part for part in (title, body) if part)
        if not content:
            continue
        out.append({
            "id": row.get("code") or row.get("id") or row.get("articleId"),
            "time": str(row.get("date") or row.get("showTime") or row.get("publishTime") or row.get("time") or ""),
            "content": content[:500], "source": "东方财富资讯", "query": query,
        })
    return out


def industry_identity_keywords(industry: Mapping[str, object]) -> tuple[str, ...]:
    name = str(industry.get("name") or "").strip()
    theme = str(industry.get("prospect_theme") or "").strip()
    profile = profile_for(name, theme or None)
    words = {name, theme}
    words.update(profile.keywords)
    return tuple(sorted({word.strip() for word in words if word and len(word.strip()) >= 2 and word.strip() not in GENERIC_NON_IDENTITY_WORDS}, key=len, reverse=True))


def _impact(text: str) -> str:
    positive = 3 * sum(1 for phrase in STRONG_POSITIVE_PHRASES if phrase in text)
    negative = 3 * sum(1 for phrase in STRONG_NEGATIVE_PHRASES if phrase in text)
    positive += sum(1 for word in POSITIVE_WORDS if word in text)
    negative += sum(1 for word in NEGATIVE_WORDS if word in text)
    if positive > negative:
        return "利好"
    if negative > positive:
        return "利空"
    return "中性/待观察"


def _importance(text: str) -> str:
    score = sum(1 for word in MAJOR_WORDS if word in text)
    if score >= 2 or any(authority in text for authority in ("国务院", "国家发改委", "工信部", "医保局", "证监会")):
        return "重大"
    return "重要"


def match_industry_events(
    selected_industries: Iterable[Mapping[str, object]], news_rows: Iterable[Mapping[str, object]], *,
    as_of: datetime, max_age_hours: int = 36, max_per_industry: int = 3,
) -> dict[str, list[dict]]:
    cutoff = as_of - timedelta(hours=max_age_hours)
    material = [dict(row) for row in news_rows]
    result: dict[str, list[dict]] = {}
    for industry in selected_industries:
        name = str(industry.get("name") or "")
        theme = str(industry.get("prospect_theme") or "") or None
        keywords = industry_identity_keywords(industry)
        matched: list[IndustryEvent] = []
        seen: set[str] = set()
        for row in material:
            content = str(row.get("content") or "")
            if not content:
                continue
            hit = next((word for word in keywords if word in content), None)
            if hit is None:
                continue
            raw_time = str(row.get("time") or "")
            dt = _parse_time(raw_time, as_of=as_of)
            if dt is not None and (dt < cutoff or dt > as_of + timedelta(minutes=5)):
                continue
            fingerprint = re.sub(r"\W+", "", content)[:100]
            if fingerprint in seen:
                continue
            seen.add(fingerprint)
            matched.append(IndustryEvent(
                name, theme, raw_time, _impact(content), _importance(content), content[:220],
                str(row.get("source") or "财经资讯"), hit,
            ))
        matched.sort(key=lambda item: (item.importance == "重大", item.time), reverse=True)
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
        "notice_date": notice, "report_date": report_date, "report_type": report_type,
        "revenue_growth": num("YSTZ"), "profit_growth": num("SJLTZ"), "roe": num("WEIGHTAVG_ROE"),
        "gross_margin": num("XSMLL"), "eps": num("BASIC_EPS"), "operating_cash_per_share": num("MGJYXJJE"),
    }
