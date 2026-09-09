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


class FundCategory(StrEnum):
    EQUITY_BROAD = "EQUITY_BROAD"
    EQUITY_SECTOR = "EQUITY_SECTOR"
    EQUITY_STRATEGY = "EQUITY_STRATEGY"
    ACTIVE_MIXED = "ACTIVE_MIXED"
    COMMODITY = "COMMODITY"
    BOND = "BOND"
    CASH = "CASH"
    OTHER = "OTHER"


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
    valuation_pe: float | None = None
    valuation_pb: float | None = None
    industry_code: str | None = None
    industry_name: str | None = None
    industry_state: str | None = None
    industry_pool: str | None = None
    industry_priority_score: float | None = None
    industry_quality_score: float | None = None
    fund_category: str | None = None
    fund_family: str | None = None
    fund_liquidity_percentile: float | None = None
    fund_risk_tags: tuple[str, ...] = ()
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
        effective -= 8.0
    if effective >= 82:
        return "HIGH"
    if effective >= 68:
        return "MEDIUM"
    return "LOW"


CROSS_BORDER_TOKENS = (
    "港股", "恒生", "纳指", "纳斯达克", "标普", "道琼斯", "日经", "德国", "法国", "英国",
    "越南", "印度", "沙特", "东南亚", "巴西", "韩国", "日本", "美国", "全球", "中概",
    "海外", "亚太", "亚洲", "QDII",
)
BOND_TOKENS = (
    "国债", "政金债", "信用债", "债券", "可转债", "公司债", "城投债", "短融", "利率债", "地方债",
    "纯债", "双债", "债基", "综债", "美元债",
)
CASH_TOKENS = ("货币", "同业存单", "日利", "添益", "保证金", "现金管理")
COMMODITY_TOKENS = (
    "黄金", "金ETF", "白银", "豆粕", "原油", "石油", "油气", "商品", "能源化工ETF", "有色期货",
)
STRATEGY_TOKENS = (
    "红利", "低波", "价值", "质量", "自由现金流", "现金流", "ESG", "央企", "国企", "高股息", "增强",
)
BROAD_TOKENS = (
    "中证A500", "A500", "中证A50", "沪深300", "中证500", "中证1000", "中证2000",
    "上证50", "上证180", "上证指数", "深证成指", "创业板50", "创业板", "科创50", "科创100", "北证50", "全指",
    "纳指", "纳斯达克", "标普", "道琼斯", "日经", "德国", "法国", "英国", "越南", "印度", "沙特", "巴西",
    "韩国", "日本", "美国", "亚太", "亚洲", "恒生指数", "恒生ETF", "恒指", "港股通50", "港股通ETF",
)
SECTOR_TOKENS = (
    "白酒", "食品", "消费", "医药", "医疗", "创新药", "证券", "券商", "银行", "保险", "金融", "地产",
    "科技", "互联网", "软件", "通信", "半导体", "芯片", "电子", "军工", "航天", "新能源", "光伏",
    "电池", "汽车", "机械", "农业", "养殖", "粮食", "煤炭", "有色", "化工", "能源", "电力", "船舶",
    "传媒", "游戏", "人工智能", "机器人", "黄金股",
)


def _has_token(text: str, tokens: Iterable[str]) -> bool:
    return any(token.upper() in text for token in tokens)


def _is_cash_fund(text: str) -> bool:
    if _has_token(text, CASH_TOKENS):
        return True
    # “自由现金流/全指现金流”是权益策略，不是货币基金。
    return "现金" in text and "现金流" not in text


def classify_fund_category(item: MarketSecurity) -> FundCategory:
    """只描述底层资产/产品类型；跨境/QDII风险完全由 ``fund_risk_tags`` 表达。"""
    text = "".join(str(item.name or "").upper().split())
    if _has_token(text, BOND_TOKENS):
        return FundCategory.BOND
    if "黄金股" not in text and _has_token(text, COMMODITY_TOKENS):
        return FundCategory.COMMODITY
    if _has_token(text, STRATEGY_TOKENS):
        return FundCategory.EQUITY_STRATEGY
    if _is_cash_fund(text):
        return FundCategory.CASH
    if _has_token(text, SECTOR_TOKENS):
        return FundCategory.EQUITY_SECTOR
    if _has_token(text, BROAD_TOKENS):
        return FundCategory.EQUITY_BROAD
    # 名称只能确认“跨境”而无法进一步识别时，不再创造一个CROSS_BORDER资产类。
    # ETF通常是指数型产品，先落到宽基观察；LOF/FUND保守落到主动/混合观察。
    if _has_token(text, CROSS_BORDER_TOKENS):
        if item.security_type is SecurityType.ETF:
            return FundCategory.EQUITY_BROAD
        return FundCategory.ACTIVE_MIXED
    # 场内LOF若没有债券/商品/指数等明确线索，最常见的是主动权益或混合型产品。
    # 该分类只决定同类比较，不会直接让产品PASS；第三步仍要求产品规模等证据。
    if item.security_type in {SecurityType.LOF, SecurityType.FUND}:
        return FundCategory.ACTIVE_MIXED
    # 未能识别的ETF不要再默认伪装成行业ETF。
    return FundCategory.OTHER


def fund_risk_tags(item: MarketSecurity) -> tuple[str, ...]:
    text = "".join(str(item.name or "").upper().split())
    tags: list[str] = []
    if _has_token(text, CROSS_BORDER_TOKENS):
        tags.append("CROSS_BORDER_QDII")
    if item.security_type is SecurityType.LOF:
        tags.append("LOF_PREMIUM")
    return tuple(tags)


FAMILY_BENCHMARK_TOKENS = (
    "沪深300", "中证500", "中证1000", "中证2000", "中证A500", "A500", "中证A50",
    "上证50", "上证指数", "科创50", "科创100", "创业板50", "创业板", "北证50", "恒生科技",
    "恒生互联网", "恒生指数", "纳斯达克100", "纳指100", "标普500", "日经225",
    "红利低波", "中证红利", "黄金", "白银", "原油", "石油", "国债", "政金债", "综债", "白酒", "美元债",
)


def _fund_family_key(item: MarketSecurity, category: FundCategory | None = None) -> str:
    text = "".join(str(item.name or "").upper().split())
    category = category or classify_fund_category(item)
    for token in FAMILY_BENCHMARK_TOKENS:
        if token.upper() in text:
            return f"{category.value}:{token.upper()}"
    if "ETF" in text:
        base = text.split("ETF", 1)[0]
    elif "LOF" in text:
        base = text.split("LOF", 1)[0]
    else:
        base = text
    for token in ("基金", "交易型开放式指数", "交易型开放式", "指数型", "联接"):
        base = base.replace(token, "")
    return f"{category.value}:{base or item.code}"


def _merge_candidate(
    store: dict[tuple[str, SecurityType], dict],
    item: MarketSecurity,
    *,
    route: CandidateRoute,
    score: float,
    industry: Mapping[str, object] | None = None,
    fund_category: FundCategory | None = None,
    fund_family: str | None = None,
    fund_liquidity_percentile: float | None = None,
    fund_tags: Iterable[str] = (),
    event_tags: Iterable[str] = (),
) -> None:
    key = (item.code, item.security_type)
    row = store.setdefault(
        key,
        {
            "item": item,
            "route_scores": {},
            "industry": None,
            "fund_category": None,
            "fund_family": None,
            "fund_liquidity_percentile": None,
            "fund_risk_tags": set(),
            "event_tags": set(),
        },
    )
    row["route_scores"][route.value] = round(float(score), 2)
    if industry is not None:
        row["industry"] = dict(industry)
    if fund_category is not None:
        row["fund_category"] = fund_category.value
    if fund_family:
        row["fund_family"] = fund_family
    if fund_liquidity_percentile is not None:
        row["fund_liquidity_percentile"] = round(float(fund_liquidity_percentile), 2)
    row["fund_risk_tags"].update(str(x) for x in fund_tags if str(x))
    row["event_tags"].update(str(x) for x in event_tags if str(x))


def discover_stock_candidates(
    securities: Iterable[MarketSecurity],
    *,
    market_strength_cap: int = 120,
    early_turn_cap: int = 100,
    score_floor: float = 66.0,
    max_overheated_share: float = 0.33,
) -> dict[tuple[str, SecurityType], dict]:
    stocks = [
        x for x in securities
        if x.tradable and x.security_type is SecurityType.STOCK and x.change_60d is not None
    ]
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
        if item.amount is None or item.amount < liquidity_floor:
            continue
        amount_p = _percentile(amounts, item.amount)
        turnover_p = _percentile(turnovers, item.turnover_rate) if item.turnover_rate is not None else None
        strength_p = _percentile(strengths, item.change_60d)
        day_p = _percentile(days, item.change_pct) if item.change_pct is not None else None
        strength_score = _weighted_score(((strength_p, 0.42), (amount_p, 0.30), (turnover_p, 0.18), (day_p, 0.10)))
        if strength_score >= score_floor:
            scored_strength.append((strength_score, item))
        stage = position_stage(item)
        if stage is PositionStage.EARLY:
            early_position = max(0.0, 100.0 - abs(item.change_60d - 10.0) * 4.0)
            early_score = _weighted_score(((amount_p, 0.30), (turnover_p, 0.25), (day_p, 0.20), (early_position, 0.25)))
            if early_score >= score_floor - 4:
                scored_early.append((early_score, item))

    store: dict[tuple[str, SecurityType], dict] = {}
    scored_strength.sort(key=lambda x: x[0], reverse=True)
    scored_early.sort(key=lambda x: x[0], reverse=True)
    overheated_cap = max(1, int(market_strength_cap * max_overheated_share))
    picked = 0
    overheated_picked = 0
    for score, item in scored_strength:
        stage = position_stage(item)
        if stage is PositionStage.OVERHEATED and overheated_picked >= overheated_cap:
            continue
        _merge_candidate(store, item, route=CandidateRoute.MARKET_STRENGTH, score=score)
        picked += 1
        if stage is PositionStage.OVERHEATED:
            overheated_picked += 1
        if picked >= market_strength_cap:
            break
    for score, item in scored_early[:early_turn_cap]:
        _merge_candidate(store, item, route=CandidateRoute.EARLY_TURN, score=score)
    return store


def add_industry_candidates(
    store: dict[tuple[str, SecurityType], dict],
    industry_members: Mapping[str, Iterable[MarketSecurity]],
    industries: Iterable[Mapping[str, object]],
    *,
    event_map: Mapping[str, Iterable[Mapping[str, object]]] | None = None,
) -> None:
    event_map = event_map or {}
    for industry in industries:
        code = str(industry.get("code") or "")
        name = str(industry.get("name") or "")
        members = [
            x for x in industry_members.get(code, ())
            if x.tradable and x.security_type is SecurityType.STOCK and x.change_60d is not None
        ]
        if not members:
            continue
        amounts = [x.amount for x in members if x.amount is not None]
        strengths = [x.change_60d for x in members if x.change_60d is not None]
        turnovers = [x.turnover_rate for x in members if x.turnover_rate is not None]
        limit = 3 if len(members) < 50 else 5 if len(members) < 120 else 8
        industry_quality = float(industry.get("quality_score") or 50.0)
        industry_timing = float(industry.get("timing_score") or 50.0)
        ranked: list[tuple[float, MarketSecurity]] = []
        for item in members:
            amount_p = _percentile(amounts, item.amount) if item.amount is not None else None
            strength_p = _percentile(strengths, item.change_60d)
            turnover_p = _percentile(turnovers, item.turnover_rate) if item.turnover_rate is not None else None
            member_score = _weighted_score(((amount_p, 0.40), (strength_p, 0.38), (turnover_p, 0.22)))
            score = 0.62 * member_score + 0.23 * industry_quality + 0.15 * industry_timing
            ranked.append((score, item))
        ranked.sort(key=lambda x: x[0], reverse=True)
        events = list(event_map.get(name, ()))
        event_tags = tuple(str(event.get("content") or event.get("title") or "")[:80] for event in events[:3] if isinstance(event, Mapping))
        meta = {
            "code": code,
            "name": name,
            "state": industry.get("state"),
            "pool": industry.get("pool"),
            "priority_score": industry.get("priority_score"),
            "quality_score": industry.get("quality_score"),
        }
        for score, item in ranked[:limit]:
            _merge_candidate(store, item, route=CandidateRoute.INDUSTRY, score=score, industry=meta)
            if event_tags:
                _merge_candidate(store, item, route=CandidateRoute.EVENT, score=min(100.0, score + 4.0), industry=meta, event_tags=event_tags)


def add_fund_candidates(
    store: dict[tuple[str, SecurityType], dict],
    securities: Iterable[MarketSecurity],
    *,
    cap: int = 100,
    score_floor: float = 58.0,
    max_category_share: float = 0.40,
) -> None:
    funds = [
        x for x in securities
        if x.tradable and x.security_type in {SecurityType.ETF, SecurityType.LOF, SecurityType.FUND}
        and x.amount is not None and x.change_60d is not None
    ]
    if not funds:
        return
    groups: dict[FundCategory, list[MarketSecurity]] = {}
    for item in funds:
        groups.setdefault(classify_fund_category(item), []).append(item)

    ranked_all: list[tuple[float, MarketSecurity, FundCategory, str, float, tuple[str, ...]]] = []
    for category, peers in groups.items():
        amounts = [x.amount for x in peers if x.amount is not None]
        strengths = [x.change_60d for x in peers if x.change_60d is not None]
        days = [x.change_pct for x in peers if x.change_pct is not None]
        liquidity_floor = max(2_000_000.0, _quantile(amounts, 0.20)) if amounts else 0.0
        # 同一指数/主题只保留一个代表产品。代表性先看同类流动性，再用机会分打破并列；
        # 不允许因为短期涨得更强而选中一个明显更难交易的同指数产品。
        best_by_family: dict[str, tuple[float, float, MarketSecurity]] = {}
        for item in peers:
            if item.amount is None or item.amount < liquidity_floor:
                continue
            amount_p = _percentile(amounts, item.amount)
            strength_p = _percentile(strengths, item.change_60d)
            day_p = _percentile(days, item.change_pct) if item.change_pct is not None else None
            score = _weighted_score(((amount_p, 0.55), (strength_p, 0.30), (day_p, 0.15)))
            if score < score_floor:
                continue
            family = _fund_family_key(item, category)
            previous = best_by_family.get(family)
            if previous is None or (amount_p, score) > (previous[0], previous[1]):
                best_by_family[family] = (amount_p, score, item)
        for family, (amount_p, score, item) in best_by_family.items():
            ranked_all.append((score, item, category, family, amount_p, fund_risk_tags(item)))

    ranked_all.sort(key=lambda x: x[0], reverse=True)
    category_cap = max(3, int(cap * max_category_share))
    category_counts: dict[FundCategory, int] = {}
    picked = 0
    for score, item, category, family, amount_p, risk_tags in ranked_all:
        if category_counts.get(category, 0) >= category_cap:
            continue
        _merge_candidate(
            store,
            item,
            route=CandidateRoute.FUND_RELATIVE,
            score=score,
            fund_category=category,
            fund_family=family,
            fund_liquidity_percentile=amount_p,
            fund_tags=risk_tags,
        )
        category_counts[category] = category_counts.get(category, 0) + 1
        picked += 1
        if picked >= cap:
            break


def finalize_candidates(store: Mapping[tuple[str, SecurityType], dict]) -> tuple[CandidateRecord, ...]:
    out: list[CandidateRecord] = []
    for row in store.values():
        item: MarketSecurity = row["item"]
        route_scores = dict(sorted(row["route_scores"].items()))
        industry = row.get("industry") or {}
        stage = position_stage(item)
        out.append(CandidateRecord(
            code=item.code,
            name=item.name,
            security_type=item.security_type,
            board=item.board.value,
            source_routes=tuple(route_scores),
            route_scores=route_scores,
            research_priority=_research_priority(route_scores, stage),
            position_stage=stage,
            data_quality=item.data_quality.value,
            price=item.price,
            change_pct=item.change_pct,
            change_60d=item.change_60d,
            amount=item.amount,
            turnover_rate=item.turnover_rate,
            valuation_pe=item.pe,
            valuation_pb=item.pb,
            industry_code=str(industry.get("code") or "") or None,
            industry_name=str(industry.get("name") or "") or None,
            industry_state=str(industry.get("state") or "") or None,
            industry_pool=str(industry.get("pool") or "") or None,
            industry_priority_score=float(industry["priority_score"]) if industry.get("priority_score") is not None else None,
            industry_quality_score=float(industry["quality_score"]) if industry.get("quality_score") is not None else None,
            fund_category=row.get("fund_category"),
            fund_family=row.get("fund_family"),
            fund_liquidity_percentile=row.get("fund_liquidity_percentile"),
            fund_risk_tags=tuple(sorted(row.get("fund_risk_tags") or ())),
            event_tags=tuple(sorted(row.get("event_tags") or ())),
        ))
    priority_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    out.sort(key=lambda x: (priority_rank.get(x.research_priority, 9), -max(x.route_scores.values(), default=0.0), x.code))
    return tuple(out)


def discover_candidates(
    securities: Iterable[MarketSecurity],
    *,
    industry_members: Mapping[str, Iterable[MarketSecurity]] | None = None,
    industries: Iterable[Mapping[str, object]] = (),
    event_map: Mapping[str, Iterable[Mapping[str, object]]] | None = None,
    stock_market_strength_cap: int = 120,
    stock_early_turn_cap: int = 100,
    fund_cap: int = 100,
) -> tuple[CandidateRecord, ...]:
    material = tuple(securities)
    store = discover_stock_candidates(material, market_strength_cap=stock_market_strength_cap, early_turn_cap=stock_early_turn_cap)
    add_industry_candidates(store, industry_members or {}, industries, event_map=event_map)
    add_fund_candidates(store, material, cap=fund_cap)
    return finalize_candidates(store)