from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import (
    CN_TZ,
    MAX_WORKERS,
    _safe_float,
    atomic_json,
    fetch_all_a_shares,
    fetch_financial_reports,
    fetch_industries,
)
from scripts.collect_full_a_universe_v2 import (
    _has_hard_financial_problem,
    build_cross_market_candidates,
    fetch_industry_members_v2,
)
from trading_skill.a_share_fundamentals import evaluate_prefilter
from trading_skill.a_share_universe import rank_industry_leaders
from trading_skill.industry_intelligence import fetch_sina_7x24, match_industry_events, recent_report_event
from trading_skill.industry_profiles import profile_dict
from trading_skill.industry_prospects import industry_rotation_state, select_industries_v3


def _serialize_industry(item, events: dict[str, list[dict]]) -> dict:
    data = asdict(item)
    data["rotation_state"] = industry_rotation_state(item)
    data["analysis_profile"] = profile_dict(item.name, item.prospect_theme)
    data["events"] = events.get(item.name, [])
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--prospect-limit", type=int, default=20)
    parser.add_argument("--early-heat-limit", type=int, default=8)
    parser.add_argument("--dynamic-supplement", type=int, default=4)
    parser.add_argument("--leaders-per-industry", type=int, default=5)
    parser.add_argument("--cross-market-limit", type=int, default=20)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    all_stocks, all_a_source = fetch_all_a_shares()
    industry_rows = fetch_industries()
    selected, paused = select_industries_v3(
        industry_rows,
        prospect_limit=args.prospect_limit,
        early_heat_limit=args.early_heat_limit,
        dynamic_supplement=args.dynamic_supplement,
    )
    selected_map = {item.code: item for item in selected}

    intelligence_errors: list[str] = []
    try:
        news_rows = fetch_sina_7x24(page_size=100)
        industry_events = match_industry_events(
            [asdict(item) for item in selected], news_rows, as_of=now, max_age_hours=36, max_per_industry=3
        )
    except Exception as exc:
        news_rows = []
        industry_events = {}
        intelligence_errors.append(f"新浪财经7x24:{exc}")

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
    industry_recent_reports: dict[str, list[dict]] = {}
    for leader in leaders:
        rows = finance_rows.get(leader.code, [])
        prefilter = evaluate_prefilter(rows, as_of=now, pe=leader.pe, pb=leader.pb)
        reasons = list(prefilter.reasons)
        deep_scan_eligible = prefilter.eligible or (prefilter.grade == "D" and not _has_hard_financial_problem(reasons))
        industry = selected_map.get(leader.industry_code)
        is_cross = leader.industry_code == "CROSS_MARKET"
        industry_name = leader.industry_name
        theme = industry.prospect_theme if industry else None
        recent_reports = [event for row in rows if (event := recent_report_event(row, as_of=now))]
        recent_reports.sort(key=lambda x: x["notice_date"], reverse=True)
        recent_report = recent_reports[0] if recent_reports else None
        if recent_report and not is_cross:
            industry_recent_reports.setdefault(industry_name, []).append(
                {"code": leader.code, "name": leader.name, **recent_report}
            )
        candidates.append(
            {
                **asdict(leader),
                "industry_selection_reason": (
                    "跨行业结构补充：行业不是硬准入门槛，允许个股先于板块出现结构机会"
                    if is_cross else industry.selection_reason if industry else ""
                ),
                "industry_rotation_state": "跨行业个股路线" if is_cross else industry_rotation_state(industry),
                "industry_analysis_profile": profile_dict(industry_name, theme),
                "prospect_theme": theme,
                "candidate_route": "跨行业结构补充" if is_cross else "动态行业路线",
                "recent_report": recent_report,
                "fundamental_prefilter": {
                    "eligible": prefilter.eligible,
                    "deep_scan_eligible": deep_scan_eligible,
                    "grade": prefilter.grade,
                    "reasons": reasons,
                    "annual": asdict(prefilter.annual) if prefilter.annual else None,
                    "interim": asdict(prefilter.interim) if prefilter.interim else None,
                },
            }
        )

    for reports in industry_recent_reports.values():
        reports.sort(key=lambda x: x["notice_date"], reverse=True)
        del reports[5:]

    strict_eligible = [item for item in candidates if item["fundamental_prefilter"]["eligible"]]
    deep_eligible = [item for item in candidates if item["fundamental_prefilter"]["deep_scan_eligible"]]
    advancers = sum(1 for row in all_stocks if _safe_float(row.get("f3")) > 0)
    decliners = sum(1 for row in all_stocks if _safe_float(row.get("f3")) < 0)
    payload = {
        "mode": "FULL_A_DYNAMIC_INDUSTRY_V3",
        "generated_at": now.isoformat(),
        "all_a_stocks_loaded": len(all_stocks),
        "all_a_source": all_a_source,
        "market_breadth": {"advancers": advancers, "decliners": decliners},
        "industry_rows_loaded": len(industry_rows),
        "selected_industries": [_serialize_industry(item, industry_events) for item in selected],
        "paused_high_industries": [_serialize_industry(item, industry_events) for item in paused[:20]],
        "industry_recent_reports": industry_recent_reports,
        "industry_intelligence": {
            "news_source": "新浪财经7x24",
            "news_rows_loaded": len(news_rows),
            "errors": intelligence_errors,
        },
        "leader_candidates": candidates,
        "fundamental_eligible_candidates": len(strict_eligible),
        "deep_scan_eligible_candidates": len(deep_eligible),
        "raw_leader_rows_before_dedup": len(raw_leaders),
        "deduped_industry_candidates": len(industry_leaders),
        "cross_market_candidates": len(cross_market),
        "deduped_leader_candidates": len(leaders),
        "member_errors": member_errors,
        "finance_errors": finance_errors,
        "guardrails": {
            "long_term_prospect_primary": True,
            "early_heating_industries_added_daily": True,
            "overextended_industries_temporarily_paused": True,
            "paused_industries_reenter_after_cooling": True,
            "industry_specific_analysis_profile": True,
            "major_news_and_recent_reports_included": True,
            "industry_is_not_hard_entry_gate": True,
            "leaders_per_industry": args.leaders_per_industry,
            "duplicate_stocks_removed_before_deep_scan": True,
            "grade_d_may_be_observed_but_cannot_directly_trigger_buy": True,
            "st_stocks_excluded": True,
        },
    }
    atomic_json(args.output, payload)

    print(f"全A加载: {len(all_stocks)}（{all_a_source}）")
    print(f"行业加载: {len(industry_rows)}")
    print(f"当前重点行业: {len(selected)}，高位暂退: {len(paused)}")
    print("入选行业:", ", ".join(f"{item.name}[{industry_rotation_state(item)}]" for item in selected))
    print(f"行业前五原始候选: {len(raw_leaders)}，行业去重后: {len(industry_leaders)}，跨行业补充: {len(cross_market)}")
    print(f"严格基本面通过: {len(strict_eligible)}，允许观察性深扫: {len(deep_eligible)}")
    print(f"资讯匹配行业: {len(industry_events)}，近期财报行业: {len(industry_recent_reports)}，资讯异常: {len(intelligence_errors)}")

    if args.strict:
        problems = []
        if len(all_stocks) < 4000:
            problems.append(f"全A股票数量异常:{len(all_stocks)}")
        if len(industry_rows) < 100:
            problems.append(f"行业数量异常:{len(industry_rows)}")
        if len(selected) < 12:
            problems.append(f"重点行业过少:{len(selected)}")
        if len(leaders) < 35:
            problems.append(f"总候选过少:{len(leaders)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
