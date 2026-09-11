from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

import requests

from scripts.collect_full_a_universe import (
    CN_TZ,
    MAX_WORKERS,
    STOCK_FIELDS,
    UT,
    _safe_float,
    atomic_json,
    fetch_all_a_shares,
    fetch_financial_reports,
    fetch_industries,
    fetch_paginated,
)
from trading_skill.a_share_fundamentals import evaluate_prefilter
from trading_skill.a_share_universe import IndustryCandidate, LeaderCandidate, rank_industry_leaders, valid_stock_name
from trading_skill.industry_intelligence import IndustryIntelligence, build_industry_intelligence
from trading_skill.industry_prospects import select_industries_v2


STOCK_QUOTE_HOSTS = (
    "https://push2.eastmoney.com/api/qt/stock/get",
    "https://82.push2.eastmoney.com/api/qt/stock/get",
    "https://73.push2.eastmoney.com/api/qt/stock/get",
)
INDUSTRY_LOOKUP_TIMEOUT = 4.0
EVENT_PROMOTION_LIMIT = 3


def fetch_industry_members_v2(board_code: str) -> list[dict]:
    rows = []
    for fid in ("f20", "f6"):
        rows.extend(fetch_paginated(f"b:{board_code} f:!50", STOCK_FIELDS, fid=fid, max_pages=2))
    dedup = {}
    for row in rows:
        code = str(row.get("f12") or "")
        if code:
            dedup[code] = row
    return list(dedup.values())


def _optional_float(value):
    if value in (None, "", "-"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _bell(value: float, *, center: float, width: float, maximum: float) -> float:
    if width <= 0:
        return 0.0
    return max(0.0, maximum * (1.0 - abs(value - center) / width))


def build_cross_market_candidates(all_stocks: list[dict], *, existing_codes: set[str], limit: int) -> list[LeaderCandidate]:
    """行业不是硬准入门槛：额外寻找流动性好、尚未过度上涨、位置与相对强度均衡的结构候选。"""
    provisional = []
    live_amount_available = any(_safe_float(row.get("f6")) >= 50_000_000 for row in all_stocks)
    for row in all_stocks:
        code = str(row.get("f12") or "").strip()
        name = str(row.get("f14") or "").strip()
        if not code or code in existing_codes or not valid_stock_name(name):
            continue
        price = _safe_float(row.get("f2"))
        pct = _safe_float(row.get("f3"))
        amount = _safe_float(row.get("f6"))
        turnover = _safe_float(row.get("f8"))
        total_cap = _safe_float(row.get("f20"))
        float_cap = _safe_float(row.get("f21"))
        ch60 = _safe_float(row.get("f24"))
        if price <= 0 or total_cap < 3_000_000_000:
            continue
        if live_amount_available and amount < 50_000_000:
            continue
        if ch60 < -18 or ch60 > 32 or pct < -5.5 or pct > 7.0:
            continue

        liquidity = min(24.0, math.log10(max(amount, 1.0)) * 2.8) if live_amount_available else 12.0
        log_cap = math.log10(max(total_cap, 1.0))
        size_balance = _bell(log_cap, center=10.8, width=1.45, maximum=12.0)
        relative_strength = _bell(ch60, center=10.0, width=24.0, maximum=26.0)
        day_balance = _bell(pct, center=1.0, width=6.0, maximum=16.0)
        turnover_score = _bell(turnover, center=3.0, width=5.0, maximum=14.0) if turnover > 0 else 7.0
        float_balance = _bell(math.log10(max(float_cap, 1.0)), center=10.5, width=1.5, maximum=8.0)
        score = liquidity + size_balance + relative_strength + day_balance + turnover_score + float_balance
        provisional.append((score, row))

    provisional.sort(key=lambda pair: pair[0], reverse=True)
    out = []
    for rank, (score, row) in enumerate(provisional[: max(0, limit)], 1):
        out.append(
            LeaderCandidate(
                code=str(row.get("f12") or ""),
                name=str(row.get("f14") or ""),
                market=int(_safe_float(row.get("f13"), 0)),
                industry_code="CROSS_MARKET",
                industry_name="跨行业结构补充",
                leader_rank=rank,
                leader_score=round(score, 2),
                price=round(_safe_float(row.get("f2")), 4),
                change_pct=round(_safe_float(row.get("f3")), 2),
                amount=round(_safe_float(row.get("f6")), 2),
                turnover_rate=round(_safe_float(row.get("f8")), 2),
                pe=_optional_float(row.get("f9")),
                pb=_optional_float(row.get("f23")),
                total_market_cap=round(_safe_float(row.get("f20")), 2),
                float_market_cap=round(_safe_float(row.get("f21")), 2),
                change_60d=round(_safe_float(row.get("f24")), 2),
            )
        )
    return out


def _has_hard_financial_problem(reasons: list[str]) -> bool:
    hard = ("营收同比明显下滑", "净利润同比明显下滑", "每股收益非正", "毛利率异常")
    return any(reason in hard for reason in reasons)


def _too_extended(industry: IndustryCandidate) -> bool:
    """高位行业临时退出重点池；回落后下一轮会自动重新参与。"""
    if industry.change_60d >= 45:
        return True
    if industry.change_ytd >= 80:
        return True
    if industry.heat_state == "过热" and industry.low_position_score < 25:
        return True
    return False


def _expanded_industry_pool(
    industry_rows: list[dict], *, prospect_limit: int, dynamic_supplement: int
) -> list[IndustryCandidate]:
    """先扩大行业池，事件发现必须发生在最终筛选之前。"""
    return list(
        select_industries_v2(
            industry_rows,
            prospect_limit=max(prospect_limit + 10, int(prospect_limit * 1.5)),
            dynamic_supplement=max(dynamic_supplement + 5, dynamic_supplement * 2),
        )
    )


def _collect_industry_intelligence(
    industries: list[IndustryCandidate], *, as_of: datetime
) -> tuple[dict[str, IndustryIntelligence], list[dict]]:
    intelligence: dict[str, IndustryIntelligence] = {}
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(industries)))) as pool:
        futures = {}
        for industry in industries:
            keyword = industry.prospect_theme or industry.name
            future = pool.submit(
                build_industry_intelligence,
                industry_code=industry.code,
                industry_name=industry.name,
                keyword=keyword,
                as_of=as_of,
            )
            futures[future] = industry
        for future in as_completed(futures):
            industry = futures[future]
            try:
                intelligence[industry.code] = future.result()
            except Exception as exc:
                errors.append({"industry": industry.name, "code": industry.code, "error": str(exc)})
    return intelligence, errors


def _positive_event_strength(intel: IndustryIntelligence | None) -> tuple[int, int, int]:
    if intel is None or not intel.major_positive:
        return (0, 0, 0)
    strongest = max((item.impact for item in intel.major_positive), default=0)
    return (strongest, intel.net_event_score, intel.positive_score)


def _select_dynamic_industries(
    expanded: list[IndustryCandidate],
    intelligence: dict[str, IndustryIntelligence],
    *,
    prospect_limit: int,
    dynamic_supplement: int,
    event_promotion_limit: int = EVENT_PROMOTION_LIMIT,
) -> tuple[list[IndustryCandidate], list[IndustryCandidate], list[IndustryCandidate]]:
    """动态行业池 = 前景 + 新升温 + 重大正向事件提入；高位方向不因新闻豁免。"""
    quarantined = [item for item in expanded if _too_extended(item)]
    usable = [item for item in expanded if not _too_extended(item)]
    prospects = [item for item in usable if item.prospect_theme][:prospect_limit]
    dynamic = [item for item in usable if not item.prospect_theme][:dynamic_supplement]
    selected = prospects + dynamic

    target = prospect_limit + dynamic_supplement
    used = {item.code for item in selected}
    for item in usable:
        if len(selected) >= target:
            break
        if item.code not in used:
            selected.append(item)
            used.add(item.code)

    event_candidates = []
    for item in usable:
        if item.code in used:
            continue
        intel = intelligence.get(item.code)
        strength = _positive_event_strength(intel)
        if strength[0] < 2 or strength[1] <= 0:
            continue
        event_candidates.append((strength, item.rank_score, item))
    event_candidates.sort(key=lambda pair: (pair[0], pair[1]), reverse=True)

    promoted = []
    for _, _, item in event_candidates[: max(0, event_promotion_limit)]:
        selected.append(item)
        used.add(item.code)
        promoted.append(item)
    return selected, quarantined, promoted


def _event_adjustment(intelligence: IndustryIntelligence | None) -> float:
    if intelligence is None:
        return 0.0
    # Positive/negative news may re-order research priority. Major negative risk is also
    # emitted separately and can pause new entry; neither path can fabricate a Chan BUY.
    return max(-3.0, min(3.0, intelligence.net_event_score * 0.35))


def _major_negative_risk(intel: IndustryIntelligence | None) -> str:
    if intel is None:
        return "UNKNOWN"
    if any(item.impact >= 3 for item in intel.major_negative):
        return "HIGH"
    if intel.major_negative and intel.negative_score > intel.positive_score:
        return "CAUTION"
    return "NORMAL"


def _fetch_actual_industry(code: str, market: int) -> str | None:
    params = {
        "secid": f"{int(market)}.{code}",
        "fields": "f57,f58,f127",
        "ut": UT,
        "invt": 2,
        "fltt": 2,
    }
    errors = []
    for host in STOCK_QUOTE_HOSTS:
        try:
            response = requests.get(
                host,
                params=params,
                headers={
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
                    "Accept": "application/json,text/plain,*/*",
                    "Referer": "https://quote.eastmoney.com/",
                    "Connection": "close",
                },
                timeout=INDUSTRY_LOOKUP_TIMEOUT,
            )
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") or {}
            industry = str(data.get("f127") or "").strip()
            if industry:
                return industry
            errors.append(f"{host}:未返回f127")
        except Exception as exc:
            errors.append(f"{host}:{exc}")
    if errors:
        raise RuntimeError("；".join(errors))
    return None


def _resolve_cross_market_industries(
    cross: list[LeaderCandidate],
) -> tuple[list[LeaderCandidate], dict[str, str], list[dict]]:
    resolved_by_code: dict[str, str] = {}
    errors: list[dict] = []
    if not cross:
        return [], resolved_by_code, errors
    with ThreadPoolExecutor(max_workers=min(4, len(cross))) as pool:
        futures = {
            pool.submit(_fetch_actual_industry, item.code, item.market): item
            for item in cross
        }
        for future in as_completed(futures):
            item = futures[future]
            try:
                industry = future.result()
                if industry:
                    resolved_by_code[item.code] = industry
                else:
                    errors.append({"code": item.code, "name": item.name, "error": "未返回真实细分行业"})
            except Exception as exc:
                errors.append({"code": item.code, "name": item.name, "error": str(exc)})
    enriched = [replace(item, industry_name=resolved_by_code.get(item.code, item.industry_name)) for item in cross]
    return enriched, resolved_by_code, errors


def _collect_cross_intelligence(
    actual_industries: set[str], *, as_of: datetime
) -> tuple[dict[str, IndustryIntelligence], list[dict]]:
    intelligence: dict[str, IndustryIntelligence] = {}
    errors: list[dict] = []
    names = sorted(name for name in actual_industries if name)
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(names)))) as pool:
        futures = {
            pool.submit(
                build_industry_intelligence,
                industry_code=f"CROSS:{name}",
                industry_name=name,
                keyword=name,
                as_of=as_of,
            ): name
            for name in names
        }
        for future in as_completed(futures):
            name = futures[future]
            try:
                intelligence[name] = future.result()
            except Exception as exc:
                errors.append({"industry": name, "code": f"CROSS:{name}", "error": str(exc)})
    return intelligence, errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--prospect-limit", type=int, default=20)
    parser.add_argument("--dynamic-supplement", type=int, default=5)
    parser.add_argument("--leaders-per-industry", type=int, default=5)
    parser.add_argument("--cross-market-limit", type=int, default=20)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    all_stocks, all_a_source = fetch_all_a_shares()
    industry_rows = fetch_industries()

    # Discovery -> event intelligence -> final pool. This ordering allows a newly
    # positive industry to enter the daily focus list without letting news create a BUY.
    expanded = _expanded_industry_pool(
        industry_rows,
        prospect_limit=args.prospect_limit,
        dynamic_supplement=args.dynamic_supplement,
    )
    intelligence_map, industry_event_errors = _collect_industry_intelligence(expanded, as_of=now)
    selected, quarantined, event_promoted = _select_dynamic_industries(
        expanded,
        intelligence_map,
        prospect_limit=args.prospect_limit,
        dynamic_supplement=args.dynamic_supplement,
    )
    selected_map = {item.code: item for item in selected}

    raw_leaders = []
    member_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(selected)))) as pool:
        futures = {pool.submit(fetch_industry_members_v2, industry.code): industry for industry in selected}
        for future in as_completed(futures):
            industry = futures[future]
            try:
                members = future.result()
                raw_leaders.extend(rank_industry_leaders(members, industry=industry, limit=args.leaders_per_industry))
            except Exception as exc:
                member_errors.append({"industry": industry.name, "code": industry.code, "error": str(exc)})

    dedup_leaders: dict[str, tuple[float, LeaderCandidate]] = {}
    for item in raw_leaders:
        industry = selected_map.get(item.industry_code)
        industry_score = float(industry.rank_score if industry else 0)
        event_adj = _event_adjustment(intelligence_map.get(item.industry_code))
        composite = item.leader_score + 0.15 * industry_score + event_adj
        previous = dedup_leaders.get(item.code)
        if previous is None or composite > previous[0]:
            dedup_leaders[item.code] = (composite, item)
    industry_ranked = sorted(dedup_leaders.values(), key=lambda pair: pair[0], reverse=True)
    industry_leaders = [pair[1] for pair in industry_ranked]
    priority_by_code = {pair[1].code: pair[0] for pair in industry_ranked}

    cross_market = build_cross_market_candidates(
        all_stocks,
        existing_codes={item.code for item in industry_leaders},
        limit=args.cross_market_limit,
    )
    cross_market, resolved_cross, cross_industry_errors = _resolve_cross_market_industries(cross_market)
    cross_intelligence, cross_intelligence_errors = _collect_cross_intelligence(
        set(resolved_cross.values()),
        as_of=now,
    )
    for item in cross_market:
        intel = cross_intelligence.get(resolved_cross.get(item.code, ""))
        priority_by_code[item.code] = item.leader_score + _event_adjustment(intel)

    leaders = sorted(
        industry_leaders + cross_market,
        key=lambda item: priority_by_code.get(item.code, item.leader_score),
        reverse=True,
    )

    finance_rows: dict[str, list[dict]] = {}
    finance_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(leaders)))) as pool:
        futures = {pool.submit(fetch_financial_reports, item.code): item for item in leaders}
        for future in as_completed(futures):
            item = futures[future]
            try:
                finance_rows[item.code] = future.result()
            except Exception as exc:
                finance_errors.append({"code": item.code, "name": item.name, "error": str(exc)})
                finance_rows[item.code] = []

    candidates = []
    for leader in leaders:
        is_cross = leader.industry_code == "CROSS_MARKET"
        actual_industry = resolved_cross.get(leader.code) if is_cross else leader.industry_name
        industry_context_complete = bool(actual_industry) and actual_industry != "跨行业结构补充"
        analysis_industry = actual_industry or leader.industry_name
        prefilter = evaluate_prefilter(
            finance_rows.get(leader.code, []),
            as_of=now,
            pe=leader.pe,
            pb=leader.pb,
            industry_name=analysis_industry,
        )
        reasons = list(prefilter.reasons)
        deep_scan_eligible = prefilter.eligible or (prefilter.grade == "D" and not _has_hard_financial_problem(reasons))
        industry = selected_map.get(leader.industry_code)
        intel = cross_intelligence.get(actual_industry or "") if is_cross else intelligence_map.get(leader.industry_code)
        event_risk = _major_negative_risk(intel)
        if is_cross and not industry_context_complete:
            event_risk = "UNKNOWN"

        candidates.append(
            {
                **asdict(leader),
                "industry_name": analysis_industry,
                "actual_industry_name": actual_industry,
                "industry_context_complete": industry_context_complete,
                "industry_context_note": (
                    "跨行业结构候选已解析真实细分行业；估值、财务与事件按真实行业执行"
                    if is_cross and industry_context_complete
                    else "跨行业结构候选真实细分行业暂未解析；允许结构观察，但禁止直接新开仓"
                    if is_cross
                    else "重点行业上下文完整"
                ),
                "industry_selection_reason": (
                    "跨行业结构补充：行业不是发现候选的硬门槛，但新开仓前必须补全真实细分行业"
                    if is_cross
                    else industry.selection_reason if industry else ""
                ),
                "prospect_theme": industry.prospect_theme if industry else None,
                "candidate_route": "跨行业结构补充" if is_cross else "前景/动态行业路线",
                "industry_event_risk": event_risk,
                "industry_event_score": intel.net_event_score if intel else None,
                "industry_event_context_complete": intel is not None,
                "daily_priority_score": round(priority_by_code.get(leader.code, leader.leader_score), 4),
                "fundamental_prefilter": {
                    "eligible": prefilter.eligible,
                    "deep_scan_eligible": deep_scan_eligible,
                    "grade": prefilter.grade,
                    "reasons": reasons,
                    "annual": asdict(prefilter.annual) if prefilter.annual else None,
                    "interim": asdict(prefilter.interim) if prefilter.interim else None,
                    "latest": asdict(prefilter.latest) if prefilter.latest else None,
                    "industry_policy": prefilter.industry_policy,
                    "valuation_focus": list(prefilter.valuation_focus),
                    "metric_focus": list(prefilter.metric_focus),
                    "report_focus": list(prefilter.report_focus),
                },
            }
        )

    strict_eligible = [item for item in candidates if item["fundamental_prefilter"]["eligible"]]
    deep_eligible = [item for item in candidates if item["fundamental_prefilter"]["deep_scan_eligible"]]
    advancers = sum(1 for row in all_stocks if _safe_float(row.get("f3")) > 0)
    decliners = sum(1 for row in all_stocks if _safe_float(row.get("f3")) < 0)

    all_intelligence = list(intelligence_map.values()) + list(cross_intelligence.values())
    payload = {
        "mode": "FULL_A_PROSPECT_INTELLIGENCE_V3",
        "generated_at": now.isoformat(),
        "all_a_stocks_loaded": len(all_stocks),
        "all_a_source": all_a_source,
        "market_breadth": {"advancers": advancers, "decliners": decliners},
        "industry_rows_loaded": len(industry_rows),
        "expanded_industry_discovery_count": len(expanded),
        "selected_industries": [asdict(item) for item in selected],
        "event_promoted_industries": [asdict(item) for item in event_promoted],
        "quarantined_high_position_industries": [asdict(item) for item in quarantined],
        "industry_intelligence": [item.as_payload() for item in all_intelligence],
        "leader_candidates": candidates,
        "fundamental_eligible_candidates": len(strict_eligible),
        "deep_scan_eligible_candidates": len(deep_eligible),
        "raw_leader_rows_before_dedup": len(raw_leaders),
        "deduped_industry_candidates": len(industry_leaders),
        "cross_market_candidates": len(cross_market),
        "cross_market_industry_resolved": len(resolved_cross),
        "deduped_leader_candidates": len(leaders),
        "member_errors": member_errors,
        "finance_errors": finance_errors,
        "industry_event_errors": industry_event_errors + cross_intelligence_errors,
        "cross_market_industry_errors": cross_industry_errors,
        "guardrails": {
            "full_market_first": True,
            "prospect_pool_is_primary": True,
            "market_heat_is_secondary": True,
            "industry_is_not_hard_discovery_gate": True,
            "overextended_industries_temporarily_quarantined": True,
            "quarantined_industries_reenter_after_position_normalizes": True,
            "major_positive_event_can_promote_daily_focus_but_never_create_chan_signal": True,
            "major_negative_event_can_pause_new_entry_but_does_not_delete_structure": True,
            "missing_event_context_is_not_treated_as_clear": True,
            "industry_event_score_affects_actual_candidate_priority": True,
            "industry_specific_financial_metric_policy": True,
            "latest_visible_quarter_is_used": True,
            "leaders_per_industry": args.leaders_per_industry,
            "duplicate_stocks_removed_before_deep_scan": True,
            "cross_market_not_size_dominated": True,
            "cross_market_real_industry_required_for_new_entry": True,
            "grade_d_may_be_observed_but_cannot_directly_trigger_buy": True,
            "st_stocks_excluded": True,
        },
    }
    atomic_json(args.output, payload)

    prospect_count = sum(1 for item in selected if item.prospect_theme)
    supplement_count = len(selected) - prospect_count
    print(f"全A加载: {len(all_stocks)}（{all_a_source}）")
    print(f"行业加载: {len(industry_rows)}，扩大事件发现池: {len(expanded)}")
    print(
        f"长期前景行业: {prospect_count}，动态/事件补充: {supplement_count}，"
        f"事件提入: {len(event_promoted)}，高位临时隔离: {len(quarantined)}"
    )
    print("入选行业:", ", ".join(item.name for item in selected))
    if event_promoted:
        print("事件提入行业:", ", ".join(item.name for item in event_promoted))
    print(
        f"行业前五原始候选: {len(raw_leaders)}，行业去重后: {len(industry_leaders)}，"
        f"跨行业补充: {len(cross_market)}（真实行业已解析{len(resolved_cross)}）"
    )
    print(f"严格基本面通过: {len(strict_eligible)}，允许观察性深扫: {len(deep_eligible)}")
    print(
        f"行业成员异常: {len(member_errors)}，财报异常: {len(finance_errors)}，"
        f"行业资讯异常: {len(industry_event_errors) + len(cross_intelligence_errors)}，"
        f"跨行业解析异常: {len(cross_industry_errors)}"
    )

    if args.strict:
        problems = []
        if len(all_stocks) < 4000:
            problems.append(f"全A股票数量异常:{len(all_stocks)}")
        if len(industry_rows) < 100:
            problems.append(f"行业数量异常:{len(industry_rows)}")
        if len(selected) < 12:
            problems.append(f"前景/补充行业过少:{len(selected)}")
        if len(leaders) < 35:
            problems.append(f"总候选过少:{len(leaders)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
