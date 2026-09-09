from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping, Sequence

from trading_skill.a_share_universe import BROAD_INDUSTRIES
from trading_skill.industry_prospects import match_theme


class IndustryState(StrEnum):
    NEW = "NEW"
    EARLY = "EARLY"
    TRENDING = "TRENDING"
    MATURE = "MATURE"
    OVERHEATED = "OVERHEATED"
    COOLING = "COOLING"
    DETERIORATING = "DETERIORATING"
    WATCH = "WATCH"


@dataclass(frozen=True, slots=True)
class IndustryPriority:
    code: str
    name: str
    canonical_name: str
    state: IndustryState
    priority_score: float
    long_term_prior_score: float
    market_confirmation_score: float
    early_stage_score: float
    crowding_penalty: float
    heat_score: float
    change_pct: float | None
    change_60d: float | None
    change_ytd: float | None
    main_flow_ratio: float | None
    breadth: float | None
    prospect_theme: str | None
    selection_reason: str

    def as_dict(self) -> dict:
        data = asdict(self)
        data["state"] = self.state.value
        return data


def _num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _percentile(values: Sequence[float], value: float | None) -> float | None:
    if value is None:
        return None
    clean = sorted(x for x in values if isfinite(x))
    if not clean:
        return None
    if len(clean) == 1:
        return 50.0
    less = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return 100.0 * (less + 0.5 * equal) / len(clean)


def _weighted(parts: Iterable[tuple[float | None, float]], *, fallback: float = 50.0) -> float:
    numerator = 0.0
    denominator = 0.0
    for value, weight in parts:
        if value is None:
            continue
        numerator += float(value) * weight
        denominator += weight
    return numerator / denominator if denominator else fallback


def _breadth(row: Mapping[str, object]) -> float | None:
    up = _num(row.get("f104"))
    down = _num(row.get("f105"))
    if up is None or down is None or up + down <= 0:
        return None
    return up / (up + down)


def canonical_industry_name(name: str) -> str:
    text = str(name or "").strip()
    for suffix in ("Ⅰ", "Ⅱ", "Ⅲ", "IV", "III", "II"):
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text


def _state(*, market_confirmation: float, change_pct: float | None, change_60d: float | None, change_ytd: float | None, long_term_prior: float) -> IndustryState:
    day = change_pct or 0.0
    ch60 = change_60d
    ytd = change_ytd
    if ch60 is not None and (ch60 >= 50 or day >= 7 or ((ytd or 0) >= 80 and ch60 >= 20)):
        return IndustryState.OVERHEATED
    if ch60 is not None and ch60 < -18 and market_confirmation < 35:
        return IndustryState.DETERIORATING
    if ch60 is not None and ch60 < 0 and long_term_prior >= 75 and market_confirmation < 50:
        return IndustryState.COOLING
    if ch60 is not None and -8 <= ch60 <= 20 and market_confirmation >= 60:
        return IndustryState.EARLY
    if market_confirmation >= 70 and (ch60 is None or ch60 < 45):
        return IndustryState.TRENDING
    if ch60 is not None and ch60 >= 25:
        return IndustryState.MATURE
    if long_term_prior < 60 and market_confirmation >= 68:
        return IndustryState.NEW
    return IndustryState.WATCH


def rank_industries(rows: Iterable[Mapping[str, object]]) -> tuple[IndustryPriority, ...]:
    normalized: list[dict] = []
    for row in rows:
        code = str(row.get("f12") or "").strip()
        name = str(row.get("f14") or "").strip()
        if not code or not name or name in BROAD_INDUSTRIES:
            continue
        normalized.append({"code": code, "name": name, "pct": _num(row.get("f3")), "flow": _num(row.get("f184")), "change_60d": _num(row.get("f24")), "change_ytd": _num(row.get("f25")), "breadth": _breadth(row)})
    if not normalized:
        return ()

    pcts = [x["pct"] for x in normalized if x["pct"] is not None]
    flows = [x["flow"] for x in normalized if x["flow"] is not None]
    ch60s = [x["change_60d"] for x in normalized if x["change_60d"] is not None]
    breadths = [x["breadth"] for x in normalized if x["breadth"] is not None]

    out: list[IndustryPriority] = []
    for item in normalized:
        theme = match_theme(item["name"])
        prior = float(theme.score if theme else 50.0)
        pct_p = _percentile(pcts, item["pct"])
        flow_p = _percentile(flows, item["flow"])
        breadth_p = _percentile(breadths, item["breadth"])
        ch60_p = _percentile(ch60s, item["change_60d"])
        market_confirmation = _weighted(((pct_p, 0.20), (flow_p, 0.25), (breadth_p, 0.25), (ch60_p, 0.30)))

        ch60 = item["change_60d"]
        early = 50.0 if ch60 is None else max(0.0, 100.0 - abs(ch60 - 12.0) * 3.2)
        day = item["pct"] or 0.0
        ytd = item["change_ytd"] or 0.0
        crowding = 0.0
        if ch60 is not None:
            crowding += min(8.0, max(0.0, ch60 - 35.0) * 0.25)
        crowding += min(4.0, max(0.0, ytd - 70.0) * 0.08)
        crowding += min(3.0, max(0.0, day - 5.0) * 0.6)

        priority = 0.20 * prior + 0.55 * market_confirmation + 0.25 * early - crowding
        state = _state(market_confirmation=market_confirmation, change_pct=item["pct"], change_60d=ch60, change_ytd=item["change_ytd"], long_term_prior=prior)
        if state is IndustryState.DETERIORATING:
            priority -= 10.0
        elif state is IndustryState.COOLING:
            priority -= 5.0

        reason_parts = [f"长期先验{prior:.0f}", f"市场确认{market_confirmation:.0f}", f"阶段{state.value}"]
        if theme:
            reason_parts.append(f"长期主题:{theme.name}")
        else:
            reason_parts.append("非静态主题也可凭市场确认进入")
        if crowding:
            reason_parts.append(f"拥挤惩罚{crowding:.1f}")

        out.append(IndustryPriority(code=item["code"], name=item["name"], canonical_name=canonical_industry_name(item["name"]), state=state, priority_score=round(priority, 2), long_term_prior_score=round(prior, 2), market_confirmation_score=round(market_confirmation, 2), early_stage_score=round(early, 2), crowding_penalty=round(crowding, 2), heat_score=round(_weighted(((pct_p, 0.35), (flow_p, 0.35), (breadth_p, 0.30))), 2), change_pct=item["pct"], change_60d=ch60, change_ytd=item["change_ytd"], main_flow_ratio=item["flow"], breadth=item["breadth"], prospect_theme=theme.name if theme else None, selection_reason="｜".join(reason_parts)))
    out.sort(key=lambda x: (x.priority_score, x.market_confirmation_score), reverse=True)
    return tuple(out)


def select_priority_industries(rows: Iterable[Mapping[str, object]], *, limit: int = 28, max_per_theme: int = 2) -> tuple[tuple[IndustryPriority, ...], tuple[IndustryPriority, ...]]:
    """每次运行都重新轮换；静态长期主题只占20%先验权重，从不构成白名单。"""
    ranked = rank_industries(rows)
    selected: list[IndustryPriority] = []
    deferred: list[IndustryPriority] = []
    canonical_used: set[str] = set()
    theme_counts: dict[str, int] = {}

    for item in ranked:
        theme_key = item.prospect_theme or f"UNMAPPED:{item.canonical_name}"
        if item.canonical_name in canonical_used:
            deferred.append(item)
            continue
        if item.prospect_theme and theme_counts.get(theme_key, 0) >= max_per_theme:
            deferred.append(item)
            continue
        if item.state is IndustryState.DETERIORATING and len(selected) >= max(8, limit // 2):
            deferred.append(item)
            continue
        selected.append(item)
        canonical_used.add(item.canonical_name)
        theme_counts[theme_key] = theme_counts.get(theme_key, 0) + 1
        if len(selected) >= limit:
            break

    selected_codes = {item.code for item in selected}
    deferred.extend(item for item in ranked if item.code not in selected_codes and item not in deferred)
    return tuple(selected), tuple(deferred)


ROTATION_REFRESH_POLICY = {
    "daily": "每次运行都用最新市场确认、扩散度、资金与位置重新排序；重点池可每日轻微换位",
    "weekly": "每周重点检查新进入、持续升温、降温和高位拥挤行业，允许明显换池",
    "monthly": "每月至少一次重新审视长期产业先验；静态主题仅作低权重先验，不能阻止新行业进入",
    "event_driven": "重大政策、产业或业绩事件可即时抬升观察优先级，但不能直接产生交易买点",
}
