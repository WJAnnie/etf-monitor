from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping, Sequence


BROAD_INDUSTRIES = {
    "电子", "有色金属", "通信", "基础化工", "建筑装饰", "机械设备", "电力设备", "汽车", "计算机",
    "医药生物", "食品饮料", "银行", "非银金融", "房地产", "公用事业", "交通运输", "轻工制造",
    "纺织服饰", "商贸零售", "社会服务", "传媒", "综合", "农林牧渔", "钢铁", "煤炭", "石油石化",
    "环保", "美容护理", "国防军工", "建筑材料",
}


@dataclass(frozen=True, slots=True)
class IndustryCandidate:
    code: str
    name: str
    heat_score: float
    low_position_score: float
    prospects_score: float
    rank_score: float
    heat_state: str
    change_pct: float
    change_60d: float
    change_ytd: float
    main_flow_ratio: float
    breadth: float
    selection_reason: str = "市场结构筛选"
    prospect_theme: str | None = None


@dataclass(frozen=True, slots=True)
class LeaderCandidate:
    code: str
    name: str
    market: int
    industry_code: str
    industry_name: str
    leader_rank: int
    leader_score: float
    price: float
    change_pct: float
    amount: float
    turnover_rate: float
    pe: float | None
    pb: float | None
    total_market_cap: float
    float_market_cap: float
    change_60d: float


def _num(value: object, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _optional_num(value: object) -> float | None:
    if value in (None, "", "-"):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if isfinite(number) else None


def _percentile(values: Sequence[float], value: float) -> float:
    if not values:
        return 50.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return 50.0
    less = sum(1 for item in ordered if item < value)
    equal = sum(1 for item in ordered if item == value)
    return round(100.0 * (less + 0.5 * equal) / len(ordered), 6)


def _breadth(row: Mapping[str, object]) -> float:
    up = _num(row.get("f104"))
    down = _num(row.get("f105"))
    total = up + down
    if total <= 0:
        return 0.5
    return up / total


def screen_industries(
    rows: Iterable[Mapping[str, object]], *, limit: int = 8, include_broad: bool = False
) -> tuple[IndustryCandidate, ...]:
    normalized: list[dict[str, float | str]] = []
    for row in rows:
        name = str(row.get("f14") or "").strip()
        code = str(row.get("f12") or "").strip()
        if not code or not name:
            continue
        if not include_broad and name in BROAD_INDUSTRIES:
            continue
        normalized.append(
            {
                "code": code,
                "name": name,
                "pct": _num(row.get("f3")),
                "flow_ratio": _num(row.get("f184")),
                "change_60d": _num(row.get("f24")),
                "change_ytd": _num(row.get("f25")),
                "breadth": _breadth(row),
            }
        )
    if not normalized:
        return ()

    pcts = [float(x["pct"]) for x in normalized]
    flows = [float(x["flow_ratio"]) for x in normalized]
    changes_60d = [float(x["change_60d"]) for x in normalized]
    changes_ytd = [float(x["change_ytd"]) for x in normalized]
    breadths = [float(x["breadth"]) for x in normalized]

    result: list[IndustryCandidate] = []
    for item in normalized:
        pct = float(item["pct"])
        flow = float(item["flow_ratio"])
        ch60 = float(item["change_60d"])
        ytd = float(item["change_ytd"])
        breadth = float(item["breadth"])

        heat = (
            0.40 * _percentile(pcts, pct)
            + 0.35 * _percentile(flows, flow)
            + 0.25 * _percentile(breadths, breadth)
        )
        low_position = 0.70 * _percentile([-x for x in changes_60d], -ch60) + 0.30 * _percentile(
            [-x for x in changes_ytd], -ytd
        )
        prospects = (
            0.45 * _percentile(changes_60d, ch60)
            + 0.30 * _percentile(flows, flow)
            + 0.25 * _percentile(breadths, breadth)
        )
        rank = 0.40 * prospects + 0.35 * low_position + 0.25 * heat

        overheated = pct >= 7.0 or ch60 >= 35.0
        if overheated:
            rank *= 0.78
            state = "过热"
        elif heat >= 75:
            state = "热门"
        elif heat >= 55:
            state = "升温"
        elif heat >= 40:
            state = "温和"
        else:
            state = "偏冷"

        if heat < 38 and prospects < 38:
            continue
        result.append(
            IndustryCandidate(
                code=str(item["code"]),
                name=str(item["name"]),
                heat_score=round(heat, 2),
                low_position_score=round(low_position, 2),
                prospects_score=round(prospects, 2),
                rank_score=round(rank, 2),
                heat_state=state,
                change_pct=round(pct, 2),
                change_60d=round(ch60, 2),
                change_ytd=round(ytd, 2),
                main_flow_ratio=round(flow, 2),
                breadth=round(breadth, 4),
            )
        )
    result.sort(key=lambda x: (x.rank_score, x.heat_score, x.prospects_score), reverse=True)
    return tuple(result[: max(1, limit)])


def valid_stock_name(name: str) -> bool:
    text = name.strip().upper()
    if not text:
        return False
    bad_tokens = ("*ST", "ST", "退", "退市")
    return not any(token in text for token in bad_tokens)


def rank_industry_leaders(
    rows: Iterable[Mapping[str, object]], *, industry: IndustryCandidate, limit: int = 5
) -> tuple[LeaderCandidate, ...]:
    provisional: list[dict[str, object]] = []
    for row in rows:
        code = str(row.get("f12") or "").strip()
        name = str(row.get("f14") or "").strip()
        market = int(_num(row.get("f13"), -1))
        price = _num(row.get("f2"))
        amount = _num(row.get("f6"))
        total_cap = _num(row.get("f20"))
        float_cap = _num(row.get("f21"))
        if not code or not valid_stock_name(name) or price <= 0 or total_cap <= 0:
            continue
        provisional.append(
            {
                "code": code,
                "name": name,
                "market": market,
                "price": price,
                "pct": _num(row.get("f3")),
                "amount": amount,
                "turnover": _num(row.get("f8")),
                "pe": _optional_num(row.get("f9")),
                "pb": _optional_num(row.get("f23")),
                "total_cap": total_cap,
                "float_cap": float_cap,
                "change_60d": _num(row.get("f24")),
            }
        )
    if not provisional:
        return ()

    live_liquidity_available = any(float(item["amount"]) >= 10_000_000 for item in provisional)
    if live_liquidity_available:
        clean = [item for item in provisional if float(item["amount"]) >= 10_000_000]
    else:
        clean = provisional
    if not clean:
        return ()

    caps = [float(x["total_cap"]) for x in clean]
    amounts = [float(x["amount"]) for x in clean]
    floats = [float(x["float_cap"]) for x in clean]
    turnovers = [float(x["turnover"]) for x in clean]
    strengths = [float(x["change_60d"]) for x in clean]
    scores: list[tuple[float, dict[str, object]]] = []
    for item in clean:
        cap_score = _percentile(caps, float(item["total_cap"]))
        float_score = _percentile(floats, float(item["float_cap"]))
        strength_score = _percentile(strengths, float(item["change_60d"]))
        # 超过45%的60日涨幅才明显惩罚，避免把刚成为新龙头的股票过早排除。
        extension_penalty = max(0.0, float(item["change_60d"]) - 45.0) * 0.45
        if live_liquidity_available:
            amount_score = _percentile(amounts, float(item["amount"]))
            liquidity_score = _percentile(turnovers, float(item["turnover"]))
            # 不再只偏向“老牌大市值龙头”：市值、成交活跃、相对强度共同识别产业龙头和市场新龙头。
            leader_score = (
                0.25 * cap_score
                + 0.25 * amount_score
                + 0.15 * float_score
                + 0.15 * liquidity_score
                + 0.20 * strength_score
            )
        else:
            leader_score = 0.55 * cap_score + 0.30 * float_score + 0.15 * strength_score
        leader_score -= extension_penalty
        scores.append((leader_score, item))

    scores.sort(key=lambda pair: pair[0], reverse=True)
    out: list[LeaderCandidate] = []
    for rank, (score, item) in enumerate(scores[: max(1, limit)], 1):
        out.append(
            LeaderCandidate(
                code=str(item["code"]),
                name=str(item["name"]),
                market=int(item["market"]),
                industry_code=industry.code,
                industry_name=industry.name,
                leader_rank=rank,
                leader_score=round(score, 2),
                price=round(float(item["price"]), 4),
                change_pct=round(float(item["pct"]), 2),
                amount=round(float(item["amount"]), 2),
                turnover_rate=round(float(item["turnover"]), 2),
                pe=item["pe"] if isinstance(item["pe"], float) else None,
                pb=item["pb"] if isinstance(item["pb"], float) else None,
                total_market_cap=round(float(item["total_cap"]), 2),
                float_market_cap=round(float(item["float_cap"]), 2),
                change_60d=round(float(item["change_60d"]), 2),
            )
        )
    return tuple(out)
