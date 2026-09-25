from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Iterable, Mapping


class EventImpact(StrEnum):
    MAJOR_POSITIVE = "MAJOR_POSITIVE"
    POSITIVE = "POSITIVE"
    NEUTRAL = "NEUTRAL"
    CAUTION = "CAUTION"
    MAJOR_NEGATIVE = "MAJOR_NEGATIVE"


@dataclass(frozen=True, slots=True)
class EventEvidence:
    art_code: str
    title: str
    published_at: str
    impact: EventImpact
    risk_level: int
    category: str
    is_financial_report: bool
    refresh_fundamentals: bool
    source: str = "Eastmoney 公告"


@dataclass(frozen=True, slots=True)
class EventSummary:
    data_complete: bool
    max_risk_level: int
    major_positive_count: int
    caution_count: int
    major_negative_count: int
    latest_financial_report: EventEvidence | None
    items: tuple[EventEvidence, ...]
    reasons: tuple[str, ...]


FINANCIAL_REPORT_WORDS = (
    "年度报告", "年报", "半年度报告", "半年报", "第一季度报告", "一季度报告",
    "第三季度报告", "三季度报告", "业绩快报", "业绩预告", "业绩修正公告",
)

# Order matters. Safe/reversal phrases are evaluated before broad risk keywords so
# "撤销退市风险警示" or "解除冻结" never become false negative events.
SAFE_OR_REVERSAL_PHRASES = (
    "撤销退市风险警示", "申请撤销退市风险警示", "不触及退市", "未触及退市",
    "解除冻结", "解除质押", "终止减持", "提前终止减持", "撤回减持计划",
)

L4_PATTERNS = (
    "重大违法强制退市", "终止上市决定", "股票终止上市", "退市风险警示",
)
L3_PATTERNS = (
    "立案告知书", "立案调查", "被立案", "行政处罚决定书", "无法表示意见",
    "否定意见", "债务逾期", "未能清偿", "重大诉讼", "重大仲裁",
)
L2_PATTERNS = (
    "减持计划", "股份减持", "司法冻结", "轮候冻结", "质押风险", "诉讼公告",
    "仲裁公告", "监管问询函", "问询函", "纪律处分", "警示函", "预计亏损",
    "业绩预亏", "大幅下降", "商誉减值", "信用减值",
)
L1_PATTERNS = (
    "限售股解禁", "解除限售", "风险提示公告", "股票交易异常波动", "异常波动公告",
)

MAJOR_POSITIVE_PATTERNS = (
    "中标重大项目", "重大合同", "签订重大合同", "获得批复", "获批上市", "获准注册",
    "获得医疗器械注册证", "获得药品注册证书", "业绩预增", "扭亏为盈", "大幅增长",
    "股份回购", "增持计划", "获得定点", "战略合作协议",
)
POSITIVE_PATTERNS = (
    "中标", "签署合同", "签订合同", "回购进展", "增持", "获得专利", "取得注册证",
)


def _text(value: object) -> str:
    return str(value or "").strip()


def _parse_dt(value: object, *, as_of: datetime) -> datetime | None:
    text = _text(value)
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalized)
    except ValueError:
        dt = None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(text[: len(datetime.now().strftime(fmt))], fmt)
                break
            except ValueError:
                continue
        if dt is None:
            return None
    if as_of.tzinfo is not None:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=as_of.tzinfo)
        else:
            dt = dt.astimezone(as_of.tzinfo)
    return dt


def _is_financial_report(title: str) -> bool:
    return any(word in title for word in FINANCIAL_REPORT_WORDS)


def classify_announcement(row: Mapping[str, object]) -> EventEvidence:
    title = _text(row.get("title_ch") or row.get("title") or row.get("notice_title"))
    art_code = _text(row.get("art_code") or row.get("artCode"))
    published = _text(row.get("notice_date") or row.get("eiTime") or row.get("display_time"))
    financial = _is_financial_report(title)

    if any(phrase in title for phrase in SAFE_OR_REVERSAL_PHRASES):
        impact = EventImpact.POSITIVE if any(word in title for word in ("撤销", "解除", "终止减持")) else EventImpact.NEUTRAL
        return EventEvidence(art_code, title, published, impact, 0, "RISK_REVERSAL", financial, financial)

    if any(pattern in title for pattern in L4_PATTERNS):
        return EventEvidence(art_code, title, published, EventImpact.MAJOR_NEGATIVE, 4, "HARD_RISK", financial, financial)
    if any(pattern in title for pattern in L3_PATTERNS):
        return EventEvidence(art_code, title, published, EventImpact.MAJOR_NEGATIVE, 3, "MAJOR_RISK", financial, financial)
    if any(pattern in title for pattern in L2_PATTERNS):
        return EventEvidence(art_code, title, published, EventImpact.CAUTION, 2, "RISK", financial, financial)
    if any(pattern in title for pattern in L1_PATTERNS):
        return EventEvidence(art_code, title, published, EventImpact.CAUTION, 1, "WATCH_RISK", financial, financial)
    if any(pattern in title for pattern in MAJOR_POSITIVE_PATTERNS):
        return EventEvidence(art_code, title, published, EventImpact.MAJOR_POSITIVE, 0, "CATALYST", financial, financial)
    if any(pattern in title for pattern in POSITIVE_PATTERNS):
        return EventEvidence(art_code, title, published, EventImpact.POSITIVE, 0, "CATALYST", financial, financial)
    if financial:
        return EventEvidence(art_code, title, published, EventImpact.NEUTRAL, 0, "FINANCIAL_REPORT", True, True)
    return EventEvidence(art_code, title, published, EventImpact.NEUTRAL, 0, "OTHER", False, False)


def summarize_announcements(
    rows: Iterable[Mapping[str, object]],
    *,
    as_of: datetime,
    data_complete: bool = True,
    risk_lookback_days: int = 45,
    report_lookback_days: int = 180,
    max_items: int = 8,
) -> EventSummary:
    risk_cutoff = as_of - timedelta(days=risk_lookback_days)
    report_cutoff = as_of - timedelta(days=report_lookback_days)
    evidence: list[tuple[datetime, EventEvidence]] = []
    reasons: list[str] = []

    for row in rows:
        event = classify_announcement(row)
        published = _parse_dt(event.published_at, as_of=as_of)
        if published is None or published > as_of:
            continue
        keep = published >= risk_cutoff or (event.is_financial_report and published >= report_cutoff)
        if keep:
            evidence.append((published, event))

    evidence.sort(
        key=lambda pair: (
            pair[1].risk_level,
            pair[1].impact is EventImpact.MAJOR_POSITIVE,
            pair[0],
        ),
        reverse=True,
    )
    items = tuple(event for _, event in evidence[:max_items])
    latest_report_pair = max(
        ((dt, event) for dt, event in evidence if event.is_financial_report),
        key=lambda pair: pair[0],
        default=None,
    )
    max_risk = max((event.risk_level for _, event in evidence), default=0)
    major_positive = sum(event.impact is EventImpact.MAJOR_POSITIVE for _, event in evidence)
    caution = sum(event.impact is EventImpact.CAUTION for _, event in evidence)
    major_negative = sum(event.impact is EventImpact.MAJOR_NEGATIVE for _, event in evidence)

    if not data_complete:
        reasons.append("公告数据获取失败或不完整；本轮不得把‘未发现重大事件’当成确定结论")
    if max_risk >= 4:
        reasons.append("存在硬风险公告：禁止新开仓，已有持仓进入退出级别复核")
    elif max_risk >= 3:
        reasons.append("存在重大负面公告：至少进入L3风险复核")
    elif max_risk >= 2:
        reasons.append("存在风险公告：暂停把技术买点直接视为可执行机会")
    if major_positive:
        reasons.append("存在正面催化，但催化剂不能创造缠论买点，也不能降低结构风险")
    if latest_report_pair is not None:
        reasons.append("检测到近期财报/业绩类公告，应以最新已披露财务数据重新评估基本面")

    return EventSummary(
        data_complete=data_complete,
        max_risk_level=max_risk,
        major_positive_count=major_positive,
        caution_count=caution,
        major_negative_count=major_negative,
        latest_financial_report=latest_report_pair[1] if latest_report_pair else None,
        items=items,
        reasons=tuple(reasons),
    )
