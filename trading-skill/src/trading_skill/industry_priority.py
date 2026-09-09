from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping, Sequence

from trading_skill.a_share_universe import BROAD_INDUSTRIES
from trading_skill.industry_profiles import profile_for
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


class IndustryPool(StrEnum):
    QUALITY = "QUALITY"
    EMERGING = "EMERGING"
    TACTICAL = "TACTICAL"
    DEFERRED = "DEFERRED"
    WATCH = "WATCH"


@dataclass(frozen=True, slots=True)
class IndustryPriority:
    code: str
    name: str
    canonical_name: str
    industry_family: str
    state: IndustryState
    pool: IndustryPool
    priority_score: float
    quality_score: float
    timing_score: float
    long_term_prior_score: float
    market_confirmation_score: float
    early_stage_score: float
    crowding_penalty: float
    event_adjustment: float
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
        data["pool"] = self.pool.value
        return data


PROFILE_PRIORS = {
    "创新药/生物医药": 90.0, "造船与海工": 72.0, "半导体设备与材料": 91.0,
    "AI基础设施/通信硬件": 89.0, "机器人与高端自动化": 87.0, "电网设备与储能": 88.0,
    "商业航天与军工电子": 82.0, "智能驾驶与汽车电子": 84.0, "医疗器械": 85.0,
    "先进能源装备": 82.0, "光伏与新能源制造": 68.0, "新材料/周期制造": 74.0,
    "化工/橡胶": 61.0, "工业软件/网络安全": 86.0, "影视院线/传媒": 62.0,
    "零售/专业连锁": 60.0, "食品饮料/白酒": 72.0, "家电/消费电子": 73.0,
    "机械/工程机械": 74.0, "航运/港口": 58.0, "农业/养殖": 57.0, "银行": 74.0,
    "券商/资产管理": 65.0, "保险": 69.0, "煤炭/油气/资源品": 62.0,
    "地产/建筑重资产": 45.0, "通用制造/消费": 55.0,
}

CYCLE_SENSITIVE_PROFILES = {
    "造船与海工", "光伏与新能源制造", "新材料/周期制造", "化工/橡胶", "影视院线/传媒",
    "航运/港口", "农业/养殖", "券商/资产管理", "煤炭/油气/资源品", "地产/建筑重资产",
}

FAMILY_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("煤炭焦化", ("煤炭", "动力煤", "焦煤", "焦炭")),
    ("贵金属", ("黄金", "白银", "贵金属")),
    ("工业金属", ("铜", "铝", "镍", "锂", "钴")),
    ("油气炼化", ("油气", "石油", "天然气", "炼化")),
    ("航运港口", ("航运", "港口")),
    ("渔业水产", ("渔业", "海洋捕捞", "水产")),
    ("证券资管", ("证券", "券商", "资产管理")),
    ("航空航天", ("航空装备", "航天装备", "商业航天")),
    ("船舶海工", ("航海装备", "船舶", "海工")),
)

PROFILE_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("煤炭/油气/资源品", ("动力煤", "焦煤", "焦炭", "白银", "贵金属", "黄金", "镍", "铜", "铝")),
    ("航运/港口", ("航运", "港口")),
    ("农业/养殖", ("渔业", "海洋捕捞", "种植", "养殖")),
    ("食品饮料/白酒", ("白酒", "酒类", "啤酒", "饮料", "食品")),
    ("家电/消费电子", ("空调", "家电")),
    ("化工/橡胶", ("复合肥", "化肥", "焦化", "橡胶", "化工")),
    ("券商/资产管理", ("证券", "券商", "资产管理")),
)


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
    numerator = denominator = 0.0
    for value, weight in parts:
        if value is None:
            continue
        numerator += float(value) * weight
        denominator += weight
    return numerator / denominator if denominator else fallback


def _breadth(row: Mapping[str, object]) -> float | None:
    up = _num(row.get("f104")); down = _num(row.get("f105"))
    if up is None or down is None or up + down <= 0:
        return None
    return up / (up + down)


def canonical_industry_name(name: str) -> str:
    text = str(name or "").strip()
    for suffix in ("Ⅰ", "Ⅱ", "Ⅲ", "IV", "III", "II"):
        if text.endswith(suffix):
            text = text[:-len(suffix)].strip()
            break
    return text


def industry_family(name: str) -> str:
    canonical = canonical_industry_name(name)
    for family, tokens in FAMILY_RULES:
        if any(token in canonical for token in tokens):
            return family
    return canonical


def _profile_name(industry_name: str, prospect_theme: str | None) -> str:
    profile = profile_for(industry_name, prospect_theme)
    if profile.name != "通用制造/消费":
        return profile.name
    text = str(industry_name or "")
    for profile_name, tokens in PROFILE_HINTS:
        if any(token in text for token in tokens):
            return profile_name
    return profile.name


def _structural_prior(industry_name: str, prospect_theme: str | None) -> float:
    profile_name = _profile_name(industry_name, prospect_theme)
    base = PROFILE_PRIORS.get(profile_name, 55.0)
    theme = match_theme(industry_name)
    if theme:
        base = max(base, min(94.0, float(theme.score)))
    return base


def _event_adjustment(events: Iterable[Mapping[str, object]]) -> float:
    score = 0.0
    for event in list(events)[:3]:
        impact = str(event.get("impact") or "")
        importance = str(event.get("importance") or "")
        weight = 4.0 if importance == "重大" else 2.0
        if impact == "利好": score += weight
        elif impact == "利空": score -= weight
    return max(-8.0, min(8.0, score))


def _state(*, market_confirmation: float, change_pct: float | None, change_60d: float | None, change_ytd: float | None, long_term_prior: float) -> IndustryState:
    day = change_pct or 0.0; ch60 = change_60d; ytd = change_ytd or 0.0
    if ch60 is not None and (ch60 >= 50 or day >= 7 or (ytd >= 80 and ch60 >= 20)):
        return IndustryState.OVERHEATED
    if ch60 is not None and ch60 < -18 and market_confirmation < 35:
        return IndustryState.DETERIORATING
    if ch60 is not None and ch60 < 0 and long_term_prior >= 72 and market_confirmation < 50:
        return IndustryState.COOLING
    if ch60 is not None and -8 <= ch60 <= 20 and market_confirmation >= 60:
        return IndustryState.EARLY
    if market_confirmation >= 70 and (ch60 is None or ch60 < 45):
        return IndustryState.TRENDING
    if ch60 is not None and ch60 >= 25:
        return IndustryState.MATURE
    if long_term_prior < 62 and market_confirmation >= 70:
        return IndustryState.NEW
    return IndustryState.WATCH


def _pool(*, quality: float, market_confirmation: float, state: IndustryState, event_adjustment: float) -> IndustryPool:
    if state in {IndustryState.OVERHEATED, IndustryState.DETERIORATING}:
        return IndustryPool.DEFERRED
    if quality >= 72:
        return IndustryPool.QUALITY
    if quality >= 61 and market_confirmation >= 62 and state in {IndustryState.NEW, IndustryState.EARLY, IndustryState.TRENDING, IndustryState.MATURE}:
        return IndustryPool.EMERGING
    if market_confirmation >= 74 or event_adjustment >= 4:
        return IndustryPool.TACTICAL
    return IndustryPool.WATCH


def rank_industries(rows: Iterable[Mapping[str, object]], *, event_map: Mapping[str, Iterable[Mapping[str, object]]] | None = None) -> tuple[IndustryPriority, ...]:
    event_map = event_map or {}
    normalized: list[dict] = []
    for row in rows:
        code = str(row.get("f12") or "").strip(); name = str(row.get("f14") or "").strip()
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
        theme = match_theme(item["name"]); theme_name = theme.name if theme else None
        profile_name = _profile_name(item["name"], theme_name); prior = _structural_prior(item["name"], theme_name)
        pct_p = _percentile(pcts, item["pct"]); flow_p = _percentile(flows, item["flow"])
        breadth_p = _percentile(breadths, item["breadth"]); ch60_p = _percentile(ch60s, item["change_60d"])
        market_confirmation = _weighted(((pct_p, .18), (flow_p, .27), (breadth_p, .25), (ch60_p, .30)))
        ch60 = item["change_60d"]
        early = 50.0 if ch60 is None else max(0.0, 100.0 - abs(ch60 - 12.0) * 3.2)
        day = item["pct"] or 0.0; ytd = item["change_ytd"] or 0.0
        crowding = 0.0
        if ch60 is not None: crowding += min(10.0, max(0.0, ch60 - 35.0) * .30)
        crowding += min(5.0, max(0.0, ytd - 70.0) * .10)
        crowding += min(4.0, max(0.0, day - 5.0) * .8)
        event_adj = _event_adjustment(event_map.get(item["name"], ()))
        state = _state(market_confirmation=market_confirmation, change_pct=item["pct"], change_60d=ch60, change_ytd=item["change_ytd"], long_term_prior=prior)
        quality = (.58 * prior + .42 * market_confirmation) if profile_name in CYCLE_SENSITIVE_PROFILES else (.74 * prior + .26 * market_confirmation)
        timing = .62 * market_confirmation + .30 * early + event_adj - crowding
        pool = _pool(quality=quality, market_confirmation=market_confirmation, state=state, event_adjustment=event_adj)
        priority = .60 * quality + .40 * timing
        if pool is IndustryPool.DEFERRED: priority -= 12.0
        elif pool is IndustryPool.WATCH: priority -= 4.0
        parts = [f"质量{quality:.0f}", f"时点{timing:.0f}", f"市场确认{market_confirmation:.0f}", f"阶段{state.value}", f"池{pool.value}"]
        if theme_name: parts.append(f"长期主题:{theme_name}")
        if event_adj: parts.append(f"事件调整{event_adj:+.0f}")
        if crowding: parts.append(f"拥挤惩罚{crowding:.1f}")
        out.append(IndustryPriority(
            code=item["code"], name=item["name"], canonical_name=canonical_industry_name(item["name"]), industry_family=industry_family(item["name"]),
            state=state, pool=pool, priority_score=round(priority, 2), quality_score=round(quality, 2), timing_score=round(timing, 2),
            long_term_prior_score=round(prior, 2), market_confirmation_score=round(market_confirmation, 2), early_stage_score=round(early, 2),
            crowding_penalty=round(crowding, 2), event_adjustment=round(event_adj, 2),
            heat_score=round(_weighted(((pct_p, .35), (flow_p, .35), (breadth_p, .30))), 2),
            change_pct=item["pct"], change_60d=ch60, change_ytd=item["change_ytd"], main_flow_ratio=item["flow"], breadth=item["breadth"],
            prospect_theme=theme_name, selection_reason="｜".join(parts),
        ))
    out.sort(key=lambda x: (x.priority_score, x.quality_score, x.timing_score), reverse=True)
    return tuple(out)


def _take_pool(ranked: Sequence[IndustryPriority], *, pool: IndustryPool, quota: int, selected: list[IndustryPriority], canonical_used: set[str], family_counts: dict[str, int], max_per_family: int) -> None:
    for item in ranked:
        if sum(1 for x in selected if x.pool is pool) >= quota: break
        if item.pool is not pool or item.canonical_name in canonical_used or family_counts.get(item.industry_family, 0) >= max_per_family: continue
        selected.append(item); canonical_used.add(item.canonical_name); family_counts[item.industry_family] = family_counts.get(item.industry_family, 0) + 1


def select_priority_industries(rows: Iterable[Mapping[str, object]], *, limit: int = 28, event_map: Mapping[str, Iterable[Mapping[str, object]]] | None = None, max_per_family: int = 2) -> tuple[tuple[IndustryPriority, ...], tuple[IndustryPriority, ...]]:
    """质量池、成长新方向、短期战术池分层轮换；短期热门不能冒充长期优质行业。"""
    ranked = rank_industries(rows, event_map=event_map)
    if not ranked: return (), ()
    quality_quota = max(1, round(limit * .60)); emerging_quota = max(1, round(limit * .25)); tactical_quota = max(1, limit - quality_quota - emerging_quota)
    selected: list[IndustryPriority] = []; canonical_used: set[str] = set(); family_counts: dict[str, int] = {}
    _take_pool(ranked, pool=IndustryPool.QUALITY, quota=quality_quota, selected=selected, canonical_used=canonical_used, family_counts=family_counts, max_per_family=max_per_family)
    _take_pool(ranked, pool=IndustryPool.EMERGING, quota=emerging_quota, selected=selected, canonical_used=canonical_used, family_counts=family_counts, max_per_family=max_per_family)
    _take_pool(ranked, pool=IndustryPool.TACTICAL, quota=tactical_quota, selected=selected, canonical_used=canonical_used, family_counts=family_counts, max_per_family=max_per_family)
    if len(selected) < limit:
        for item in ranked:
            if item.pool not in {IndustryPool.QUALITY, IndustryPool.EMERGING, IndustryPool.TACTICAL}: continue
            if item.canonical_name in canonical_used or family_counts.get(item.industry_family, 0) >= max_per_family: continue
            selected.append(item); canonical_used.add(item.canonical_name); family_counts[item.industry_family] = family_counts.get(item.industry_family, 0) + 1
            if len(selected) >= limit: break
    selected_codes = {item.code for item in selected}
    return tuple(selected), tuple(item for item in ranked if item.code not in selected_codes)


ROTATION_REFRESH_POLICY = {
    "daily": "每日只重算市场确认、时点、拥挤和事件影响；允许排序变化，不把单日暴涨自动定义为优质行业",
    "weekly": "每周重新生成QUALITY/EMERGING/TACTICAL活动池，景气恶化或过热行业可退出，新方向可晋级",
    "monthly": "每月至少一次复核长期产业先验和行业质量权重，长期主题不是永久白名单",
    "earnings_season": "年报/中报/季报集中披露期触发行业质量复核，重点看行业专属订单、产能、资产质量、现金流等指标",
    "event_driven": "重大政策、产业、价格、订单或业绩事件即时影响时点/战术优先级，但事件本身不能证明长期优质，也不能产生交易买点",
}
