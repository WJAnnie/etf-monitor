from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import (
    CN_TZ,
    DATACENTER,
    MAX_WORKERS,
    _get_json,
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
from trading_skill.industry_financial_metrics import compare_snapshots, statement_snapshot
from trading_skill.industry_intelligence import (
    fetch_eastmoney_news,
    fetch_sina_7x24,
    match_industry_events,
    recent_report_event,
)
from trading_skill.industry_profiles import profile_dict
from trading_skill.industry_prospects import industry_rotation_state, select_industries_v3
from trading_skill.sector_fundamental_gate import sector_observation_override


def _serialize_industry(item, events: dict[str, list[dict]]) -> dict:
    data = asdict(item)
    data["rotation_state"] = industry_rotation_state(item)
    data["analysis_profile"] = profile_dict(item.name, item.prospect_theme)
    data["events"] = events.get(item.name, [])
    return data


def _statement_rows(code: str, report_name: str, *, page_size: int = 8) -> list[dict]:
    payload = _get_json(
        DATACENTER,
        {
            "sortColumns": "REPORT_DATE",
            "sortTypes": "-1",
            "pageSize": page_size,
            "pageNumber": 1,
            "reportName": report_name,
            "columns": "ALL",
            "filter": f'(SECURITY_CODE="{code}")',
        },
    )
    result = payload.get("result") or {}
    return [row for row in (result.get("data") or []) if isinstance(row, dict)]


def fetch_detailed_financial_metrics(code: str, report_date: str) -> dict:
    """仅对近期刚披露财报的候选补抓明细，避免对全候选发起大量重报表请求。"""
    balance_rows = _statement_rows(code, "RPT_DMSK_FN_BALANCE")
    cash_rows = _statement_rows(code, "RPT_DMSK_FN_CASHFLOW")

    def row_date(row: dict) -> str:
        return str(row.get("REPORT_DATE") or row.get("REPORTDATE") or "")[:10]

    def choose(rows: list[dict], target: str) -> tuple[dict | None, dict | None]:
        current = next((row for row in rows if row_date(row) == target), None)
        if current is None:
            current = next(iter(rows), None)
        if current is None:
            return None, None
        cur_date = row_date(current)
        try:
            year = int(cur_date[:4])
            prior_same_period = f"{year-1}{cur_date[4:]}"
        except (TypeError, ValueError):
            prior_same_period = ""
        previous = next((row for row in rows if row_date(row) == prior_same_period), None)
        if previous is None:
            previous = next((row for row in rows if row_date(row) < cur_date), None)
        return current, previous

    cur_balance, prev_balance = choose(balance_rows, report_date)
    cur_cash, prev_cash = choose(cash_rows, report_date)
    current = statement_snapshot(cur_balance, cur_cash)
    previous = statement_snapshot(prev_balance, prev_cash) if (prev_balance or prev_cash) else None
    return {
        "current": current,
        "previous": previous,
        "changes": compare_snapshots(current, previous),
        "data_note": "固定资产/在建工程/合同负债/存货/应收/货币资金来自资产负债表；资本开支与经营现金流来自现金流量表。合同负债只作订单景气辅助，不等同真实订单量。",
    }


def _industry_news(selected, *, now: datetime) -> tuple[list[dict], dict[str, list[dict]], list[str], dict[str, int]]:
    """资讯是行业上下文，不是交易信号。任一来源故障都不能阻断全A扫描。"""
    errors: list[str] = []
    all_rows: list[dict] = []
    source_counts: dict[str, int] = {}

    try:
        sina = fetch_sina_7x24(page_size=120)
        all_rows.extend(sina)
        source_counts["新浪财经7x24"] = len(sina)
    except Exception as exc:
        errors.append(f"新浪财经7x24:{exc}")

    # 第二来源只查询最重要的一部分行业，控制网络请求量；新浪流覆盖全部重点行业。
    queries: list[str] = []
    for item in selected:
        query = str(item.prospect_theme or item.name).strip()
        if query and query not in queries:
            queries.append(query)
        if len(queries) >= 16:
            break
    east_rows: list[dict] = []
    if queries:
        with ThreadPoolExecutor(max_workers=min(4, len(queries))) as pool:
            futures = {pool.submit(fetch_eastmoney_news, query, page_size=6): query for query in queries}
            for future in as_completed(futures):
                query = futures[future]
                try:
                    east_rows.extend(future.result())
                except Exception as exc:
                    errors.append(f"东方财富资讯[{query}]:{exc}")
    if east_rows:
        all_rows.extend(east_rows)
    source_counts["东方财富资讯"] = len(east_rows)

    # 去掉跨源重复新闻。
    deduped: list[dict] = []
    seen: set[str] = set()
    for row in all_rows:
        content = str(row.get("content") or "").strip()
        fingerprint = "".join(content.split())[:140]
        if not fingerprint or fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(row)

    events = match_industry_events(
        [asdict(item) for item in selected], deduped, as_of=now, max_age_hours=36, max_per_industry=3
    )
    return deduped, events, errors, source_counts


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

    news_rows, industry_events, intelligence_errors, news_source_counts = _industry_news(selected, now=now)

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

    # 最近10天新披露的一季报/中报/三季报/年报触发行业复核。
    recent_report_by_code: dict[str, dict] = {}
    for leader in leaders:
        events = [event for row in finance_rows.get(leader.code, []) if (event := recent_report_event(row, as_of=now))]
        events.sort(key=lambda x: x["notice_date"], reverse=True)
        if events:
            recent_report_by_code[leader.code] = events[0]

    detailed_metrics: dict[str, dict] = {}
    detailed_metric_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(recent_report_by_code)))) as pool:
        futures = {
            pool.submit(fetch_detailed_financial_metrics, code, report["report_date"]): code
            for code, report in recent_report_by_code.items()
        }
        for future in as_completed(futures):
            code = futures[future]
            try:
                detailed_metrics[code] = future.result()
            except Exception as exc:
                detailed_metric_errors.append({"code": code, "error": str(exc)})

    candidates = []
    industry_recent_reports: dict[str, list[dict]] = {}
    sector_override_count = 0
    for leader in leaders:
        rows = finance_rows.get(leader.code, [])
        prefilter = evaluate_prefilter(rows, as_of=now, pe=leader.pe, pb=leader.pb)
        reasons = list(prefilter.reasons)
        industry = selected_map.get(leader.industry_code)
        is_cross = leader.industry_code == "CROSS_MARKET"
        industry_name = leader.industry_name
        theme = industry.prospect_theme if industry else None
        profile = profile_dict(industry_name, theme)

        generic_observe = prefilter.grade == "D" and not _has_hard_financial_problem(reasons)
        sector_override, sector_override_reason = sector_observation_override(profile.get("profile", ""), prefilter)
        deep_scan_eligible = prefilter.eligible or generic_observe or sector_override
        if sector_override and not prefilter.eligible:
            sector_override_count += 1

        recent_report = recent_report_by_code.get(leader.code)
        detailed = detailed_metrics.get(leader.code)
        if recent_report and not is_cross:
            industry_recent_reports.setdefault(industry_name, []).append(
                {"code": leader.code, "name": leader.name, **recent_report, "sector_metrics": detailed}
            )
        candidates.append(
            {
                **asdict(leader),
                "industry_selection_reason": (
                    "跨行业结构补充：行业不是硬准入门槛，允许个股先于板块出现结构机会"
                    if is_cross else industry.selection_reason if industry else ""
                ),
                "industry_rotation_state": "跨行业个股路线" if is_cross else industry_rotation_state(industry),
                "industry_analysis_profile": profile,
                "prospect_theme": theme,
                "candidate_route": "跨行业结构补充" if is_cross else "动态行业路线",
                "recent_report": recent_report,
                "sector_financial_metrics": detailed,
                "sector_observation_override": sector_override and not prefilter.eligible,
                "sector_observation_reason": sector_override_reason,
                "fundamental_prefilter": {
                    # eligible 保持原值；行业专属观察绝不偷偷改成“基本面通过”。
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
            "news_sources": news_source_counts,
            "news_rows_loaded": len(news_rows),
            "errors": intelligence_errors,
            "detailed_report_metrics_loaded": len(detailed_metrics),
            "detailed_report_metric_errors": detailed_metric_errors,
        },
        "leader_candidates": candidates,
        "fundamental_eligible_candidates": len(strict_eligible),
        "deep_scan_eligible_candidates": len(deep_eligible),
        "sector_observation_overrides": sector_override_count,
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
            "industry_news_is_context_not_signal": True,
            "sector_override_allows_observation_not_buy": True,
            "recent_reports_use_statement_metrics_when_available": True,
            "contract_liabilities_are_not_claimed_as_order_volume": True,
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
    print(f"严格基本面通过: {len(strict_eligible)}，允许观察性深扫: {len(deep_eligible)}，行业专属观察覆盖: {sector_override_count}")
    print(f"资讯匹配行业: {len(industry_events)}，资讯来源: {news_source_counts}，近期财报行业: {len(industry_recent_reports)}，财报明细: {len(detailed_metrics)}，资讯异常: {len(intelligence_errors)}")

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
