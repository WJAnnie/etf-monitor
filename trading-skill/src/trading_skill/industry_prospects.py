from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from math import isfinite
from typing import Iterable, Mapping

from trading_skill.a_share_universe import IndustryCandidate, screen_industries


@dataclass(frozen=True, slots=True)
class ProspectTheme:
    name: str
    score: float
    keywords: tuple[str, ...]
    rationale: str


# 这里表达的是“值得中长期持续扫描的产业方向”，不是当日交易热点榜。
# 分数只决定进入深扫池的优先级；真正买点仍由缠论、风险和执行条件决定。
PROSPECT_THEMES: tuple[ProspectTheme, ...] = (
    ProspectTheme("创新药", 96, ("创新药", "生物药", "生物制品", "化学制剂", "医药研发"), "创新管线、授权出海与支付机制改善带来长期成长空间"),
    ProspectTheme("造船与海工", 95, ("船舶制造", "船舶", "海工装备", "航海装备"), "船队更新、环保规则与高附加值订单支撑中长期景气"),
    ProspectTheme("半导体设备与材料", 94, ("半导体设备", "半导体材料", "电子特气", "光刻", "晶圆制造"), "国产替代与先进制造扩产形成长期资本开支需求"),
    ProspectTheme("人工智能基础设施", 93, ("服务器", "光模块", "光通信", "数据中心", "液冷", "算力", "高速连接", "通信网络设备", "其他通信设备", "通信线缆", "印制电路板"), "算力资本开支、网络升级和AI应用扩张形成结构性需求"),
    ProspectTheme("机器人与高端自动化", 92, ("机器人", "工业自动化", "数控机床", "减速器", "伺服", "机器视觉"), "自动化率提升、设备更新和智能制造带来长期渗透率机会"),
    ProspectTheme("电网升级与储能", 91, ("电网设备", "特高压", "配电", "储能", "电力电子", "变压器", "综合电力设备商"), "电力系统升级、新能源消纳和电网投资构成持续需求"),
    ProspectTheme("商业航天与军工电子", 90, ("商业航天", "航天装备", "军工电子", "卫星", "航空电子"), "卫星互联网和高端电子装备具备产业扩张空间"),
    ProspectTheme("智能驾驶与汽车电子", 89, ("汽车电子", "智能驾驶", "线控", "车载", "汽车芯片", "汽车零部件"), "汽车智能化提升单车价值量并推动零部件重构"),
    ProspectTheme("医疗器械", 88, ("医疗器械", "医疗设备", "体外诊断", "医学影像"), "国产替代、设备更新和人口结构变化支撑长期需求"),
    ProspectTheme("先进能源装备", 87, ("核电", "核能", "燃气轮机", "氢能", "燃料电池", "风电整机", "风电设备"), "能源安全、清洁化和高端装备国产化带来中长期机会"),
    ProspectTheme("新材料", 86, ("碳纤维", "高温合金", "先进封装材料", "复合材料", "特种材料", "膜材料", "金属新材料", "非金属材料"), "高端制造升级推动关键材料国产化和性能迭代"),
    ProspectTheme("工业软件与网络安全", 85, ("工业软件", "网络安全", "信息安全", "基础软件", "数据库", "垂直应用软件", "横向通用软件"), "数字化升级与自主可控带来持续软件需求"),
)


def _num(value: object, default: float = 0.0) -> float:
    if value in (None, "", "-"):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if isfinite(number) else default


def _breadth(row: Mapping[str, object]) -> float:
    up = _num(row.get("f104"))
    down = _num(row.get("f105"))
    total = up + down
    return up / total if total > 0 else 0.5


def match_theme(name: str) -> ProspectTheme | None:
    text = str(name or "").strip()
    matches = [theme for theme in PROSPECT_THEMES if any(keyword in text for keyword in theme.keywords)]
    if not matches:
        return None
    return max(matches, key=lambda item: item.score)


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


def is_overextended_industry(item: IndustryCandidate) -> bool:
    """A long-term theme can be valid but temporarily too extended for new deep scans."""
    return item.heat_state == "过热" or item.change_pct >= 7.0 or item.change_60d >= 35.0


def prospect_industry_candidates(rows: Iterable[Mapping[str, object]]) -> tuple[IndustryCandidate, ...]:
    out: list[IndustryCandidate] = []
    for row in rows:
        code = str(row.get("f12") or "").strip()
        name = str(row.get("f14") or "").strip()
        if not code or not name:
            continue
        theme = match_theme(name)
        if theme is None:
            continue
        pct = _num(row.get("f3"))
        ch60 = _num(row.get("f24"))
        ytd = _num(row.get("f25"))
        flow = _num(row.get("f184"))
        breadth = _breadth(row)
        extension_penalty = max(0.0, ch60 - 30.0) * 0.12 + max(0.0, pct - 5.0) * 0.8
        low_position = max(0.0, min(100.0, 60.0 - ch60 * 0.8 - ytd * 0.2))
        heat_score = max(0.0, min(100.0, 50.0 + pct * 4.0 + (breadth - 0.5) * 40.0 + flow * 0.1))
        rank = theme.score - extension_penalty + low_position * 0.03
        out.append(
            IndustryCandidate(
                code=code,
                name=name,
                heat_score=round(heat_score, 2),
                low_position_score=round(low_position, 2),
                prospects_score=round(theme.score, 2),
                rank_score=round(rank, 2),
                heat_state=_heat_state(pct, ch60, breadth),
                change_pct=round(pct, 2),
                change_60d=round(ch60, 2),
                change_ytd=round(ytd, 2),
                main_flow_ratio=round(flow, 2),
                breadth=round(breadth, 4),
                selection_reason=f"长期前景池：{theme.name}｜{theme.rationale}",
                prospect_theme=theme.name,
            )
        )
    out.sort(key=lambda item: (item.rank_score, item.low_position_score), reverse=True)
    return tuple(out)


def parked_prospect_industries(rows: Iterable[Mapping[str, object]]) -> tuple[IndustryCandidate, ...]:
    """Return overextended long-term themes that are temporarily parked, not deleted.

    Since this is recomputed from the latest market snapshot, a parked theme automatically
    returns to the active prospect pool after its extension/heat falls below the threshold.
    """
    parked = [item for item in prospect_industry_candidates(rows) if is_overextended_industry(item)]
    parked.sort(key=lambda item: (item.change_60d, item.change_pct), reverse=True)
    return tuple(parked)


def _diversified_prospect_selection(
    candidates: Iterable[IndustryCandidate], *, limit: int, max_per_theme: int = 2
) -> list[IndustryCandidate]:
    """按主题轮询选取，避免一个大主题用多个相近板块挤掉其他长期方向。"""
    groups: dict[str, list[IndustryCandidate]] = defaultdict(list)
    for item in candidates:
        if item.prospect_theme:
            groups[item.prospect_theme].append(item)
    for items in groups.values():
        items.sort(key=lambda item: (item.rank_score, item.low_position_score), reverse=True)

    chosen: list[IndustryCandidate] = []
    for round_index in range(max_per_theme):
        for theme in PROSPECT_THEMES:
            items = groups.get(theme.name) or []
            if round_index < len(items):
                chosen.append(items[round_index])
                if len(chosen) >= limit:
                    return chosen

    remaining = [item for items in groups.values() for item in items[max_per_theme:]]
    remaining.sort(key=lambda item: (item.rank_score, item.low_position_score), reverse=True)
    for item in remaining:
        chosen.append(item)
        if len(chosen) >= limit:
            break
    return chosen


def _dynamic_emergence_score(item: IndustryCandidate) -> float:
    """Prefer newly warming themes over already-extended hot themes."""
    if item.heat_state == "升温":
        state_bonus = 14.0
    elif item.heat_state == "热门" and item.change_60d <= 22:
        state_bonus = 10.0
    elif item.heat_state == "温和":
        state_bonus = 5.0
    else:
        state_bonus = 0.0
    early_trend_bonus = max(0.0, 12.0 - abs(item.change_60d - 10.0) * 0.5)
    extension_penalty = max(0.0, item.change_60d - 25.0) * 1.2
    return item.rank_score + state_bonus + early_trend_bonus - extension_penalty


def select_industries_v2(
    rows: Iterable[Mapping[str, object]], *, prospect_limit: int = 24, dynamic_supplement: int = 6
) -> tuple[IndustryCandidate, ...]:
    material = list(rows)
    all_prospects = prospect_industry_candidates(material)

    # Long-term identity and current entry eligibility are deliberately separate.
    # Overheated themes go to the parking list for this run and automatically return
    # after they cool down; they are not removed from PROSPECT_THEMES.
    active_prospects = [item for item in all_prospects if not is_overextended_industry(item)]
    prospects = _diversified_prospect_selection(active_prospects, limit=max(1, prospect_limit), max_per_theme=2)
    used = {item.code for item in prospects}

    dynamic = []
    if dynamic_supplement > 0:
        market_pool = list(screen_industries(material, limit=max(dynamic_supplement * 8, 48)))
        market_pool = [item for item in market_pool if item.code not in used and not is_overextended_industry(item)]
        market_pool.sort(key=_dynamic_emergence_score, reverse=True)
        for item in market_pool:
            dynamic.append(
                IndustryCandidate(
                    code=item.code,
                    name=item.name,
                    heat_score=item.heat_score,
                    low_position_score=item.low_position_score,
                    prospects_score=item.prospects_score,
                    rank_score=item.rank_score,
                    heat_state=item.heat_state,
                    change_pct=item.change_pct,
                    change_60d=item.change_60d,
                    change_ytd=item.change_ytd,
                    main_flow_ratio=item.main_flow_ratio,
                    breadth=item.breadth,
                    selection_reason="市场结构补充池：近期升温/新趋势优先，用于发现尚未写入长期前景表的新方向",
                    prospect_theme=None,
                )
            )
            if len(dynamic) >= dynamic_supplement:
                break
    return tuple(prospects + dynamic)
