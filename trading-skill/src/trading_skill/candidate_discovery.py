from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from math import isfinite
from typing import Iterable, Mapping, Sequence

from trading_skill.market_universe import MarketSecurity, SecurityType


class CandidateRoute(StrEnum):
    MARKET_STRENGTH = "MARKET_STRENGTH"
    EARLY_TURN = "EARLY_TURN"
    INDUSTRY = "INDUSTRY"
    EVENT = "EVENT"
    FUND_RELATIVE = "FUND_RELATIVE"


class PositionStage(StrEnum):
    UNKNOWN = "UNKNOWN"
    EARLY = "EARLY"
    NORMAL = "NORMAL"
    EXTENDED = "EXTENDED"
    OVERHEATED = "OVERHEATED"


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    code: str
    name: str
    security_type: SecurityType
    board: str
    source_routes: tuple[str, ...]
    route_scores: dict[str, float]
    research_priority: str
    position_stage: PositionStage
    data_quality: str
    price: float | None
    change_pct: float | None
    change_60d: float | None
    amount: float | None
    turnover_rate: float | None
    industry_code: str | None = None
    industry_name: str | None = None
    industry_state: str | None = None
    industry_priority_score: float | None = None
    event_tags: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        data = asdict(self)
        data["security_type"] = self.security_type.value
        data["position_stage"] = self.position_stage.value
        return data


def _percentile(values: Sequence[float], value: float) -> float:
    clean = sorted(x for x in values if isfinite(x))
    if not clean:
        return 50.0
    if len(clean) == 1:
        return 50.0
    less = sum(1 for item in clean if item < value)
    equal = sum(1 for item in clean if item == value)
    return 100.0 * (less + 0.5 * equal) / len(clean)


def _weighted_score(parts: Iterable[tuple[float | None, float]]) -> float:
    numerator = 0.0
    denominator = 0.0
    for value, weight in parts:
        if value is None:
            continue
        numerator += float(value) * weight
        denominator += weight
    return numerator / denominator if denominator else 0.0


def _quantile(values: Sequence[float], q: float) -> float:
    clean = sorted(x for x in values if isfinite(x))
    if not clean:
        return 0.0
    q = min(1.0, max(0.0, q))
    pos = (len(clean) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(clean) - 1)
    fraction = pos - lo
    return clean[lo] * (1 - fraction) + clean[hi] * fraction


def position_stage(item: MarketSecurity) -> PositionStage:
    ch60 = item.change_60d
    day = item.change_pct or 0.0
    if ch60 is None:
        return PositionStage.UNKNOWN
    if ch60 >= 50 or day >= 7.0:
        return PositionStage.OVERHEATED
    if ch60 >= 25:
        return PositionStage.EXTENDED
    if -10 <= ch60 <= 20 and day >= -1.5:
        return PositionStage.EARLY
    return PositionStage.NORMAL


def _research_priority(route_scores: Mapping[str, float], stage: PositionStage) -> str:
    if not route_scores:
        return "LOW"
    best = max(route_scores.values())
    multi_route_bonus = min(8.0, max(0, len(route_scores) - 1) * 4.0)
    effective = best + multi_route_bonus
    if stage is PositionStage.OVERHEATED:
        effective -= 6.0
    if effective >= 82:
        return "HIGH"
    if effective >= 68:
        return "MEDIUM"
    return "LOW"


def _fund_family_key(item: MarketSecurity) -> str:
    name = "".join(str(item.name or "").upper().split())
    if "ETF" in name:
        base = name.split("ETF", 1)[0]
    elif "LOF" in name:
        base = name.split("LOF", 1)[0]
    else:
        base = name
    for token in ("基金", "交易型开放式指数", "交易型开放式", "指数型"):
        base = base.replace(token, "")
    return f"{item.security_type.value}:{base or item.code}"


def _merge_candidate(store: dict[tuple[str, SecurityType], dict], item: MarketSecurity, *, route: CandidateRoute, score: float, industry: Mapping[str, object] | None = None, event_tags: Iterable[str] = ()) -> None:
    key = (item.code, item.security_type)
    row = store.setdefault(key, {"item": item, "route_scores": {}, "industry": None, "event_tags": set()})
    row["route_scores"][route.value] = round(float(score), 2)
    if industry is not None:
        row["industry"] = dict(industry)
    row["event_tags"].update(str(x) for x in event_tags if str(x))


def discover_stock_candidates(securities: Iterable[MarketSecurity], *, market_strength_cap: int = 120, early_turn_cap: int = 100, score_floor: float = 66.0) -> dict[tuple[str, SecurityType], dict]:
    stocks = [x for x in securities if x.tradable and x.security_type is SecurityType.STOCK]
    if not stocks:
        return {}
    amounts = [x.amount for x in stocks if x.amount is not None]
    turnovers = [x.turnover_rate for x in stocks if x.turnover_rate is not None]
    strengths = [x.change_60d for x in stocks if x.change_60d is not None]
    days = [x.change_pct for x in stocks if x.change_pct is not None]
    liquidity_floor = max(5_000_000.0, _quantile(amounts, 0.15)) if amounts else 0.0

    scored_strength: list[tuple[float, MarketSecurity]] = []
    scored_early: list[tuple[float, MarketSecurity]] = []
    for item in stocks:
        if item.amount is not None and item.amount < liquidity_floor:
            continue
        amount_p = _percentile(amounts, item.amount) if item.amount is not None else None
        turnover_p = _percentile(turnovers, item.turnover_rate) if item.turnover_rate is not None else None
        strength_p = _percentile(strengths, item.change_60d) if item.change_60d is not None else None
        day_p = _percentile(days, item.change_pct) if item.change_pct is not None else None

        strength_score = _weighted_score(((strength_p, 0.42), (amount_p, 0.30), (turnover_p, 0.18), (day_p, 0.10)))
        if strength_score >= score_floor:
            scored_strength.append((strength_score, item))

        stage = position_stage(item)
        if stage is PositionStage.EARLY:
            early_position = None if item.change_60d is None else max(0.0, 100.0 - abs(item.change_60d - 10.0) * 4.0)
            early_score = _weighted_score(((amount_p, 0.30), (turnover_p, 0.25), (day_p, 0.20), (early_position, 0.25)))
            if early_score >= score_floor - 4:
                scored_early.append((early_score, item))

    store: dict[tuple[str, SecurityType], dict] = {}
    scored_strength.sort(key=lambda x: x[0], reverse=True)
    scored_early.sort(key=lambda x: x[0], reverse=True)
    for score, item in scored_strength[:market_strength_cap]:
        _merge_candidate(store, item, route=CandidateRoute.MARKET_STRENGTH, score=score)
    for score, item in scored_early[:early_turn_cap]:
        _merge_candidate(store, item, route=CandidateRoute.EARLY_TURN, score=score)
    return store


def add_industry_candidates(store: dict[tuple[str, SecurityType], dict], industry_members: Mapping[str, Iterable[MarketSecurity]], industries: Iterable[Mapping[str, object]], *, event_map: Mapping[str, Iterable[Mapping[str, object]]] | None = None) -> None:
    event_map = event_map or {}
    for industry in industries:
        code = str(industry.get("code") or "")
        name = str(industry.get("name") or "")
        members = [x for x in industry_members.get(code, ()) if x.tradable and x.security_type is SecurityType.STOCK]
        if not members:
            continue
        amounts = [x.amount for x in members if x.amount is not None]
        strengths = [x.change_60d for x in members if x.change_60d is not None]
        turnovers = [x.turnover_rate for x in members if x.turnover_rate is not None]
        if len(members) < 50:
            limit = 3
        elif len(members) < 120:
            limit = 5
        else:
            limit = 8
        ranked: list[tuple[float, MarketSecurity]] = []
        for item in members:
            amount_p = _percentile(amounts, item.amount) if item.amount is not None else None
            strength_p = _percentile(strengths, item.change_60d) if item.change_60d is not None else None
            turnover_p = _percentile(turnovers, item.turnover_rate) if item.turnover_rate is not None else None
            score = _weighted_score(((amount_p, 0.40), (strength_p, 0.38), (turnover_p, 0.22)))
            ranked.append((score, item))
        ranked.sort(key=lambda x: x[0], reverse=True)

        events = list(event_map.get(name, ()))
        event_tags = tuple(str(event.get("title") or event.get("content") or "")[:80] for event in events[:3] if isinstance(event, Mapping))
        meta = {"code": code, "name": name, "state": industry.get("state"), "priority_score": industry.get("priority_score")}
        for score, item in ranked[:limit]:
            _merge_candidate(store, item, route=CandidateRoute.INDUSTRY, score=score, industry=meta)
            if event_tags:
                _merge_candidate(store, item, route=CandidateRoute.EVENT, score=min(100.0, score + 5.0), industry=meta, event_tags=event_tags)


def add_fund_candidates(store: dict[tuple[str, SecurityType], dict], securities: Iterable[MarketSecurity], *, cap: int = 100, score_floor: float = 58.0) -> None:
    funds = [x for x in securities if x.tradable and x.security_type in {SecurityType.ETF, SecurityType.LOF, SecurityType.FUND}]
    if not funds:
        return
    amounts = [x.amount for x in funds if x.amount is not None]
    strengths = [x.change_60d for x in funds if x.change_60d is not None]
    days = [x.change_pct for x in funds if x.change_pct is not None]
    liquidity_floor = max(2_000_000.0, _quantile(amounts, 0.20)) if amounts else 0.0

    best_by_family: dict[str, tuple[float, MarketSecurity]] = {}
    for item in funds:
        if item.amount is not None and item.amount < liquidity_floor:
            continue
        amount_p = _percentile(amounts, item.amount) if item.amount is not None else None
        strength_p = _percentile(strengths, item.change_60d) if item.change_60d is not None else None
        day_p = _percentile(days, item.change_pct) if item.change_pct is not None else None
        score = _weighted_score(((amount_p, 0.55), (strength_p, 0.30), (day_p, 0.15)))
        if score < score_floor:
            continue
        family = _fund_family_key(item)
        previous = best_by_family.get(family)
        if previous is None or score > previous[0]:
            best_by_family[family] = (score, item)

    ranked = sorted(best_by_family.values(), key=lambda x: x[0], reverse=True)[:cap]
    for score, item in ranked:
        _merge_candidate(store, item, route=CandidateRoute.FUND_RELATIVE, score=score)


def finalize_candidates(store: Mapping[tuple[str, SecurityType], dict]) -> tuple[CandidateRecord, ...]:
    out: list[CandidateRecord] = []
    for row in store.values():
        item: MarketSecurity = row["item"]
        route_scores = dict(sorted(row["route_scores"].items()))
        industry = row.get("industry") or {}
        stage = position_stage(item)
        out.append(CandidateRecord(code=item.code, name=item.name, security_type=item.security_type, board=item.board.value, source_routes=tuple(route_scores), route_scores=route_scores, research_priority=_research_priority(route_scores, stage), position_stage=stage, data_quality=item.data_quality.value, price=item.price, change_pct=item.change_pct, change_60d=item.change_60d, amount=item.amount, turnover_rate=item.turnover_rate, industry_code=str(industry.get("code") or "") or None, industry_name=str(industry.get("name") or "") or None, industry_state=str(industry.get("state") or "") or None, industry_priority_score=float(industry["priority_score"]) if industry.get("priority_score") is not None else None, event_tags=tuple(sorted(row.get("event_tags") or ()))))
    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    out.sort(key=lambda x: (priority_rank.get(x.research_priority, 9), -max(x.route_scores.values(), default=0.0), x.code))
    return tuple(out)


def discover_candidates(securities: Iterable[MarketSecurity], *, industry_members: Mapping[str, Iterable[MarketSecurity]] | None = None, industries: Iterable[Mapping[str, object]] = (), event_map: Mapping[str, Iterable[Mapping[str, object]]] | None = None, stock_market_strength_cap: int = 120, stock_early_turn_cap: int = 100, fund_cap: int = 100) -> tuple[CandidateRecord, ...]:
    material = tuple(securities)
    store = discover_stock_candidates(material, market_strength_cap=stock_market_strength_cap, early_turn_cap=stock_early_turn_cap)
    add_industry_candidates(store, industry_members or {}, industries, event_map=event_map)
    add_fund_candidates(store, material, cap=fund_cap)
    return finalize_candidates(store)
