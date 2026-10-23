from __future__ import annotations

import argparse
import json
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import (
    CN_TZ,
    MAX_WORKERS,
    STOCK_FIELDS,
    _get_json,
    _safe_float,
    atomic_json,
    fetch_all_a_shares,
    fetch_financial_reports,
    fetch_industries,
    fetch_paginated,
)
from trading_skill.a_share_events import summarize_announcements
from trading_skill.a_share_fundamentals import evaluate_prefilter
from trading_skill.a_share_universe import LeaderCandidate, rank_industry_leaders, valid_stock_name
from trading_skill.industry_prospects import parked_prospect_industries, select_industries_v2


ANNOUNCEMENTS_API = "https://np-anotice-stock.eastmoney.com/api/security/ann"
ANNOUNCEMENT_LIMIT = 30


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


def fetch_company_announcements(code: str, *, limit: int = ANNOUNCEMENT_LIMIT) -> list[dict]:
    payload = _get_json(
        ANNOUNCEMENTS_API,
        {
            "sr": -1,
            "page_size": max(1, min(int(limit), 100)),
            "page_index": 1,
            "ann_type": "A",
            "client_source": "web",
            "stock_list": str(code),
            "f_node": 0,
            "s_node": 0,
        },
    )
    data = payload.get("data") or {}
    return [row for row in (data.get("list") or []) if isinstance(row, dict)]


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
    selected = select_industries_v2(
        industry_rows,
        prospect_limit=args.prospect_limit,
        dynamic_supplement=args.dynamic_supplement,
    )
    parked = parked_prospect_industries(industry_rows)
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

    dedup_leaders = {}
    for item in raw_leaders:
        industry = selected_map.get(item.industry_code)
        industry_score = float(industry.rank_score if industry else 0)
        composite = item.leader_score + 0.15 * industry_score
        previous = dedup_leaders.get(item.code)
        if previous is None or composite > previous[0]:
            dedup_leaders[item.code] = (composite, item)
    industry_leaders = [pair[1] for pair in dedup_leaders.values()]
    industry_leaders.sort(key=lambda item: item.leader_score, reverse=True)

    cross_market = build_cross_market_candidates(
        all_stocks,
        existing_codes={item.code for item in industry_leaders},
        limit=args.cross_market_limit,
    )
    leaders = industry_leaders + cross_market

    finance_rows: dict[str, list[dict]] = {}
    finance_errors: list[dict] = []
    announcement_rows: dict[str, list[dict]] = {}
    event_errors: list[dict] = []

    # Financials and announcements are independent evidence sources. Both are fail-soft
    # at collection time; data gaps stay explicit in the output instead of being silently
    # treated as "no bad news" or "no report".
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(leaders)))) as pool:
        futures = {}
        for item in leaders:
            futures[pool.submit(fetch_financial_reports, item.code)] = ("finance", item)
            futures[pool.submit(fetch_company_announcements, item.code)] = ("events", item)
        for future in as_completed(futures):
            kind, item = futures[future]
            try:
                value = future.result()
                if kind == "finance":
                    finance_rows[item.code] = value
                else:
                    announcement_rows[item.code] = value
            except Exception as exc:
                if kind == "finance":
                    finance_errors.append({"code": item.code, "name": item.name, "error": str(exc)})
                    finance_rows[item.code] = []
                else:
                    event_errors.append({"code": item.code, "name": item.name, "error": str(exc)})
                    announcement_rows[item.code] = []

    event_error_codes = {str(item.get("code")) for item in event_errors}
    candidates = []
    for leader in leaders:
        industry = selected_map.get(leader.industry_code)
        prospect_theme = industry.prospect_theme if industry else None
        prefilter = evaluate_prefilter(
            finance_rows.get(leader.code, []),
            as_of=now,
            pe=leader.pe,
            pb=leader.pb,
            industry_name=leader.industry_name,
            prospect_theme=prospect_theme,
        )
        reasons = list(prefilter.reasons)
        deep_scan_eligible = prefilter.eligible or (prefilter.grade == "D" and not _has_hard_financial_problem(reasons))
        event_summary = summarize_announcements(
            announcement_rows.get(leader.code, []),
            as_of=now,
            data_complete=leader.code not in event_error_codes,
        )
        is_cross = leader.industry_code == "CROSS_MARKET"
        candidates.append(
            {
                **asdict(leader),
                "industry_selection_reason": (
                    "跨行业结构补充：行业不是硬准入门槛，允许个股先于板块出现结构机会"
                    if is_cross
                    else industry.selection_reason if industry else ""
                ),
                "prospect_theme": prospect_theme,
                "candidate_route": "跨行业结构补充" if is_cross else "前景行业路线",
                "fundamental_prefilter": {
                    "eligible": prefilter.eligible,
                    "deep_scan_eligible": deep_scan_eligible,
                    "grade": prefilter.grade,
                    "reasons": reasons,
                    "policy_name": prefilter.policy_name,
                    "valuation_basis": prefilter.valuation_basis,
                    "focus_metrics": list(prefilter.focus_metrics),
                    "external_metrics_required": list(prefilter.external_metrics_required),
                    "annual": asdict(prefilter.annual) if prefilter.annual else None,
                    "interim": asdict(prefilter.interim) if prefilter.interim else None,
                },
                "event_summary": asdict(event_summary),
            }
        )

    strict_eligible = [item for item in candidates if item["fundamental_prefilter"]["eligible"]]
    deep_eligible = [item for item in candidates if item["fundamental_prefilter"]["deep_scan_eligible"]]
    advancers = sum(1 for row in all_stocks if _safe_float(row.get("f3")) > 0)
    decliners = sum(1 for row in all_stocks if _safe_float(row.get("f3")) < 0)
    payload = {
        "mode": "FULL_A_PROSPECT_FIRST_V2",
        "generated_at": now.isoformat(),
        "all_a_stocks_loaded": len(all_stocks),
        "all_a_source": all_a_source,
        "market_breadth": {"advancers": advancers, "decliners": decliners},
        "industry_rows_loaded": len(industry_rows),
        "selected_industries": [asdict(item) for item in selected],
        "parked_overextended_industries": [asdict(item) for item in parked],
        "leader_candidates": candidates,
        "fundamental_eligible_candidates": len(strict_eligible),
        "deep_scan_eligible_candidates": len(deep_eligible),
        "raw_leader_rows_before_dedup": len(raw_leaders),
        "deduped_industry_candidates": len(industry_leaders),
        "cross_market_candidates": len(cross_market),
        "deduped_leader_candidates": len(leaders),
        "member_errors": member_errors,
        "finance_errors": finance_errors,
        "event_errors": event_errors,
        "guardrails": {
            "full_market_first": True,
            "prospect_pool_is_primary": True,
            "market_heat_is_secondary": True,
            "overheated_long_term_themes_are_parked_not_deleted": True,
            "industry_is_not_hard_entry_gate": True,
            "industry_specific_financial_policies": True,
            "missing_industry_kpis_are_explicit_data_gaps": True,
            "announcement_events_are_risk_context_not_signal_creators": True,
            "announcement_fetch_is_fail_soft_and_explicit": True,
            "leaders_per_industry": args.leaders_per_industry,
            "duplicate_stocks_removed_before_deep_scan": True,
            "cross_market_not_size_dominated": True,
            "grade_d_may_be_observed_but_cannot_directly_trigger_buy": True,
            "st_stocks_excluded": True,
        },
    }
    atomic_json(args.output, payload)

    prospect_count = sum(1 for item in selected if item.prospect_theme)
    supplement_count = len(selected) - prospect_count
    print(f"全A加载: {len(all_stocks)}（{all_a_source}）")
    print(f"行业加载: {len(industry_rows)}")
    print(f"长期前景行业: {prospect_count}，市场补充行业: {supplement_count}，过热停车: {len(parked)}")
    print("入选行业:", ", ".join(item.name for item in selected))
    if parked:
        print("过热停车:", ", ".join(item.name for item in parked[:12]))
    print(f"行业前五原始候选: {len(raw_leaders)}，行业去重后: {len(industry_leaders)}，跨行业补充: {len(cross_market)}")
    print(f"严格基本面通过: {len(strict_eligible)}，允许观察性深扫: {len(deep_eligible)}")
    print(f"行业成员异常: {len(member_errors)}，财报异常: {len(finance_errors)}，公告异常: {len(event_errors)}")

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
