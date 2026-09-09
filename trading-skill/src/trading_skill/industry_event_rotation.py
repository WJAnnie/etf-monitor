from __future__ import annotations

from datetime import datetime
from typing import Iterable, Mapping

from trading_skill.a_share_universe import IndustryCandidate
from trading_skill.industry_intelligence import match_industry_events
from trading_skill.industry_prospects import match_theme


def _num(value: object, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _breadth(row: Mapping[str, object]) -> float:
    up = _num(row.get("f104"))
    down = _num(row.get("f105"))
    total = up + down
    return up / total if total > 0 else 0.5


def _too_high(pct: float, ch60: float, ytd: float) -> bool:
    # 年内涨幅很高并不意味着永远高位；若近60日已经明显冷却，应允许重新进入观察池。
    return ch60 >= 48 or (ytd >= 75 and ch60 >= 20) or (ch60 >= 35 and pct >= 5)


def _heat_state(pct: float, ch60: float, breadth: float) -> str:
    if pct >= 7 or ch60 >= 35:
        return "过热"
    if pct >= 2.5 or breadth >= 0.72:
        return "热门"
    if pct >= 0.5 or breadth >= 0.58:
        return "升温"
    if pct <= -2.5:
        return "回落"
    return "温和"


def promote_major_event_industries(
    rows: Iterable[Mapping[str, object]],
    news_rows: Iterable[Mapping[str, object]],
    *,
    existing_codes: set[str],
    as_of: datetime,
    limit: int = 4,
) -> tuple[tuple[IndustryCandidate, ...], dict[str, list[dict]], tuple[str, ...]]:
    """重大事件可把原本未入选、但位置不过高的行业提入“事件观察池”。

    重要边界：
    - 事件只是扩大观察范围，不创造买点；最终仍需基本面、缠论、风险和执行条件。
    - 重大利空也会进入观察，供风险判断使用；V3交易层会暂停该行业新开仓。
    - 已明显高位的行业不会因为重大利好重新追进去，待位置冷却后再恢复资格。
    """
    material = [row for row in rows if str(row.get("f12") or "") and str(row.get("f14") or "")]
    contexts = []
    row_by_name: dict[str, Mapping[str, object]] = {}
    for row in material:
        name = str(row.get("f14") or "").strip()
        if not name:
            continue
        row_by_name[name] = row
        theme = match_theme(name)
        contexts.append({"name": name, "prospect_theme": theme.name if theme else None})

    all_events = match_industry_events(contexts, news_rows, as_of=as_of, max_age_hours=36, max_per_industry=3)
    candidates: list[tuple[float, IndustryCandidate]] = []
    skipped_high: list[str] = []
    for name, events in all_events.items():
        row = row_by_name.get(name)
        if row is None:
            continue
        code = str(row.get("f12") or "").strip()
        if not code or code in existing_codes:
            continue
        major = [event for event in events if event.get("importance") == "重大"]
        if not major:
            continue
        pct = _num(row.get("f3"))
        ch60 = _num(row.get("f24"))
        ytd = _num(row.get("f25"))
        breadth = _breadth(row)
        if _too_high(pct, ch60, ytd):
            skipped_high.append(name)
            continue

        theme = match_theme(name)
        prospects = float(theme.score if theme else 72.0)
        low_position = max(0.0, min(100.0, 65.0 - ch60 * 0.8 - ytd * 0.15))
        heat = max(0.0, min(100.0, 50.0 + pct * 4.0 + (breadth - 0.5) * 40.0))
        impacts = {str(event.get("impact") or "") for event in major}
        if "利空" in impacts:
            event_label = "重大利空"
            event_bonus = 5.0
        elif "利好" in impacts:
            event_label = "重大利好"
            event_bonus = 10.0
        else:
            event_label = "重大事件"
            event_bonus = 7.0
        rank = prospects * 0.65 + low_position * 0.20 + min(heat, 80.0) * 0.15 + event_bonus
        candidate = IndustryCandidate(
            code=code,
            name=name,
            heat_score=round(heat, 2),
            low_position_score=round(low_position, 2),
            prospects_score=round(prospects, 2),
            rank_score=round(rank, 2),
            heat_state=_heat_state(pct, ch60, breadth),
            change_pct=round(pct, 2),
            change_60d=round(ch60, 2),
            change_ytd=round(ytd, 2),
            main_flow_ratio=round(_num(row.get("f184")), 2),
            breadth=round(breadth, 4),
            selection_reason=f"重大事件观察池：{event_label}触发当日复核；事件不构成买点，仍需完整交易链确认",
            prospect_theme=theme.name if theme else None,
        )
        candidates.append((rank, candidate))

    candidates.sort(key=lambda pair: pair[0], reverse=True)
    promoted = tuple(item for _, item in candidates[:max(0, limit)])
    promoted_events = {item.name: all_events.get(item.name, []) for item in promoted}
    return promoted, promoted_events, tuple(skipped_high)
