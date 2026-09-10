from __future__ import annotations

import argparse
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import (
    A_SHARE_MARKETS,
    CN_TZ,
    MAX_WORKERS,
    STOCK_FIELDS,
    _safe_float,
    _sina_page,
    atomic_json,
    fetch_industries,
    fetch_paginated,
)
from trading_skill.candidate_discovery import discover_candidates
from trading_skill.industry_intelligence import fetch_sina_7x24, match_industry_events
from trading_skill.industry_priority import ROTATION_REFRESH_POLICY, rank_industries, select_priority_industries
from trading_skill.market_universe import SecurityType, TradePermissions, build_tradeable_universe, normalize_stock_row

# 东方财富 ETF 与 LOF 是两套独立板块。MK0021~24/MK0827 属于 ETF；LOF 使用 MK0404~0407。
ETF_FS = "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827"
LOF_FS = "b:MK0404,b:MK0405,b:MK0406,b:MK0407"
MIN_BATCH_PRICE_COVERAGE = 0.80
SEMANTIC_PRICE_RETRIES = 3


def _valid_price(value: object) -> bool:
    if value in (None, "", "-"):
        return False
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def _price_coverage(rows: list[dict]) -> float:
    if not rows:
        return 0.0
    return sum(1 for row in rows if _valid_price(row.get("f2"))) / len(rows)


def fetch_paginated_price_healthy(
    fs: str,
    fields: str,
    *,
    fid: str,
    max_pages: int | None = None,
    min_coverage: float = MIN_BATCH_PRICE_COVERAGE,
) -> list[dict]:
    """HTTP成功不等于行情可用；关键价格字段必须达到批量语义健康阈值。"""
    diagnostics: list[str] = []
    for attempt in range(SEMANTIC_PRICE_RETRIES):
        rows = fetch_paginated(fs, fields, fid=fid, max_pages=max_pages)
        coverage = _price_coverage(rows)
        if rows and coverage >= min_coverage:
            return rows
        diagnostics.append(f"attempt={attempt + 1},rows={len(rows)},price_coverage={coverage:.1%}")
        if attempt + 1 < SEMANTIC_PRICE_RETRIES:
            time.sleep(2.0 * (attempt + 1))
    raise RuntimeError("批量行情语义不健康:" + ";".join(diagnostics))


def fetch_raw_stock_rows() -> tuple[list[dict], str, list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for fs in A_SHARE_MARKETS:
        try:
            rows.extend(fetch_paginated_price_healthy(fs, STOCK_FIELDS, fid="f3"))
        except Exception as exc:
            errors.append(f"东方财富[{fs}]:{exc}")
            rows = []
            break
    if len(rows) >= 4000 and _price_coverage(rows) >= MIN_BATCH_PRICE_COVERAGE:
        return rows, "东方财富分市场原始证券", errors

    # 备用源只保留真实提供的字段；缺少60日/年内涨幅时必须是None，绝不伪造为0。
    sina_rows: list[dict] = []
    for page in range(1, 81):
        try:
            _, items = _sina_page(page)
        except Exception as exc:
            errors.append(f"新浪全A第{page}页:{exc}")
            break
        if not items:
            break
        for item in items:
            code = str(item.get("code") or "")
            symbol = str(item.get("symbol") or "")
            market = 1 if symbol.startswith("sh") else 0
            if not code:
                continue
            sina_rows.append(
                {
                    "f12": code,
                    "f13": market,
                    "f14": item.get("name"),
                    "f2": item.get("trade"),
                    "f3": item.get("changepercent"),
                    "f6": item.get("amount"),
                    "f8": item.get("turnoverratio"),
                    "f9": item.get("per"),
                    "f20": (_safe_float(item.get("mktcap")) * 10_000)
                    if item.get("mktcap") not in (None, "", "-")
                    else None,
                    "f21": (_safe_float(item.get("nmc")) * 10_000)
                    if item.get("nmc") not in (None, "", "-")
                    else None,
                    "f23": item.get("pb"),
                    "f24": None,
                    "f25": None,
                }
            )
    if len(sina_rows) >= 4000 and _price_coverage(sina_rows) >= MIN_BATCH_PRICE_COVERAGE:
        return sina_rows, "新浪全A原始证券兜底", errors
    raise RuntimeError(
        f"股票主备数据源均不足或价格语义不健康：东财={len(rows)} 新浪={len(sina_rows)} "
        f"新浪价格覆盖={_price_coverage(sina_rows):.1%}"
    )


def fetch_exchange_funds() -> tuple[list[dict], list[dict], list[str]]:
    errors: list[str] = []
    etfs: list[dict] = []
    lofs: list[dict] = []
    try:
        etfs = fetch_paginated_price_healthy(ETF_FS, STOCK_FIELDS, fid="f6")
    except Exception as exc:
        errors.append(f"ETF:{exc}")
    try:
        lofs = fetch_paginated_price_healthy(LOF_FS, STOCK_FIELDS, fid="f6")
    except Exception as exc:
        errors.append(f"LOF:{exc}")
    return etfs, lofs, errors


def fetch_industry_members(board_code: str) -> list[dict]:
    return fetch_paginated_price_healthy(f"b:{board_code} f:!50", STOCK_FIELDS, fid="f6", max_pages=4)


def _rotation_event_map(industry_rows: list[dict], *, now: datetime) -> tuple[dict[str, list[dict]], list[str], int]:
    """先用行情形成宽行业观察集，再用一份全市场快讯做事件校正；事件不决定长期质量。"""
    errors: list[str] = []
    try:
        news_rows = fetch_sina_7x24(page_size=160)
    except Exception as exc:
        return {}, [f"行业事件:{exc}"], 0
    ranked = rank_industries(industry_rows)
    targets = [item.as_dict() for item in ranked[:80]]
    try:
        events = match_industry_events(targets, news_rows, as_of=now, max_age_hours=36, max_per_industry=3)
    except Exception as exc:
        errors.append(f"行业事件匹配:{exc}")
        events = {}
    return events, errors, len(news_rows)


def _summary(all_items, tradeable, candidates, selected) -> dict:
    raw_types = Counter(item.security_type.value for item in all_items)
    tradeable_types = Counter(item.security_type.value for item in tradeable)
    quality = Counter(item.data_quality.value for item in all_items)
    exclusions = Counter(reason for item in all_items for reason in item.exclusion_reasons)
    candidate_types = Counter(item.security_type.value for item in candidates)
    routes = Counter(route for item in candidates for route in item.source_routes)
    fund_categories = Counter(item.fund_category for item in candidates if item.fund_category)
    industry_pools = Counter(item.pool.value for item in selected)
    return {
        "raw_by_security_type": dict(raw_types),
        "tradeable_by_security_type": dict(tradeable_types),
        "data_quality": dict(quality),
        "exclusions": dict(exclusions),
        "candidate_by_security_type": dict(candidate_types),
        "candidate_by_route": dict(routes),
        "candidate_by_fund_category": dict(fund_categories),
        "selected_industry_pools": dict(industry_pools),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("full-a-results/candidate_universe_latest.json"))
    parser.add_argument("--industry-limit", type=int, default=28)
    parser.add_argument("--stock-strength-cap", type=int, default=120)
    parser.add_argument("--stock-early-cap", type=int, default=100)
    parser.add_argument("--fund-cap", type=int, default=100)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    stock_rows, stock_source, stock_errors = fetch_raw_stock_rows()
    etf_rows, lof_rows, fund_errors = fetch_exchange_funds()
    stock_price_coverage = _price_coverage(stock_rows)
    etf_price_coverage = _price_coverage(etf_rows)
    lof_price_coverage = _price_coverage(lof_rows)
    permissions = TradePermissions(
        sh_main=True,
        sz_main=True,
        star=False,
        chinext=False,
        bse=False,
        etf=True,
        lof=True,
        fund=True,
    )
    all_items, tradeable = build_tradeable_universe(
        stock_rows,
        etf_rows,
        lof_rows,
        stock_source=stock_source,
        fund_source="东方财富场内ETF/LOF",
        permissions=permissions,
    )

    industry_rows = fetch_industries()
    rotation_events, event_errors, news_count = _rotation_event_map(industry_rows, now=now)
    selected, deferred = select_priority_industries(
        industry_rows,
        limit=args.industry_limit,
        event_map=rotation_events,
    )
    selected_dicts = [item.as_dict() for item in selected]

    industry_members: dict[str, list] = {}
    member_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(selected)))) as pool:
        futures = {pool.submit(fetch_industry_members, item.code): item for item in selected}
        for future in as_completed(futures):
            item = futures[future]
            try:
                rows = future.result()
                industry_members[item.code] = [
                    normalize_stock_row(row, source=f"东方财富行业成分:{item.name}", permissions=permissions)
                    for row in rows
                ]
            except Exception as exc:
                member_errors.append({"code": item.code, "name": item.name, "error": str(exc)})

    candidates = discover_candidates(
        tradeable,
        industry_members=industry_members,
        industries=selected_dicts,
        event_map=rotation_events,
        stock_market_strength_cap=args.stock_strength_cap,
        stock_early_turn_cap=args.stock_early_cap,
        fund_cap=args.fund_cap,
    )

    summary = _summary(all_items, tradeable, candidates, selected)
    payload = {
        "mode": "TRADEABLE_CANDIDATE_UNIVERSE",
        "generated_at": now.isoformat(),
        "permissions": asdict(permissions),
        "design_contract": {
            "step1": "只回答当前账户权限下哪些证券可进入扫描：沪深主板股票+ETF+LOF/其他场内基金；排除科创板、创业板、北交所个股、ST/*ST、退市和无有效行情证券。",
            "step2": "只回答哪些证券值得继续研究；股票走市场强势/刚启动/行业/事件多路线，ETF/场内基金先按资产类别同类比较；不产生基本面最终结论、缠论买点或交易建议。",
            "missing_data_is_never_zero": True,
            "http_success_does_not_imply_quote_semantic_health": True,
            "batch_price_coverage_is_validated_before_use": True,
            "previous_close_is_not_used_as_fake_live_price": True,
            "industry_is_not_hard_stock_gate": True,
            "industry_quality_and_short_term_heat_are_separate": True,
            "quality_industries_rotate_periodically": True,
            "quality_score_never_uses_short_term_market_heat": True,
            "high_position_is_risk_tag_not_hard_veto": True,
            "static_industry_themes_are_prior_not_whitelist": True,
            "restricted_stock_boards_never_enter_candidates": True,
            "etf_and_lof_use_distinct_market_sources": True,
        },
        "sources": {
            "stocks": stock_source,
            "funds": "东方财富ETF(MK0021~24/MK0827)+LOF(MK0404~0407)",
            "stock_errors": stock_errors,
            "fund_errors": fund_errors,
            "stock_price_coverage_pct": round(stock_price_coverage * 100, 2),
            "etf_price_coverage_pct": round(etf_price_coverage * 100, 2),
            "lof_price_coverage_pct": round(lof_price_coverage * 100, 2),
            "industry_member_errors": member_errors,
            "industry_event_errors": event_errors,
            "industry_news_rows": news_count,
        },
        "summary": summary,
        "selected_industries": selected_dicts,
        "deferred_industries": [item.as_dict() for item in deferred[:60]],
        "industry_refresh_policy": ROTATION_REFRESH_POLICY,
        "industry_events": {
            name: rows for name, rows in rotation_events.items() if name in {item.name for item in selected}
        },
        "candidates": [item.as_dict() for item in candidates],
    }
    atomic_json(args.output, payload)

    print("第一步可交易池:", summary["tradeable_by_security_type"])
    print(
        "权限剔除:",
        {k: v for k, v in summary["exclusions"].items() if "BOARD_NOT_ALLOWED" in k or k in {"ST", "DELISTING"}},
    )
    print("数据质量:", summary["data_quality"])
    print(
        "行情价格覆盖:",
        {
            "STOCK": round(stock_price_coverage * 100, 2),
            "ETF": round(etf_price_coverage * 100, 2),
            "LOF": round(lof_price_coverage * 100, 2),
        },
    )
    print("动态行业分层:", summary["selected_industry_pools"])
    print(
        "动态重点行业:",
        len(selected),
        [
            (x.name, x.pool.value, x.state.value, round(x.quality_score, 1), round(x.timing_score, 1))
            for x in selected[:16]
        ],
    )
    print("第二步候选:", summary["candidate_by_security_type"], summary["candidate_by_route"])
    print("ETF/基金同类分组:", summary["candidate_by_fund_category"])

    if args.strict:
        problems: list[str] = []
        raw_stock_count = summary["raw_by_security_type"].get(SecurityType.STOCK.value, 0)
        tradeable_stock_count = summary["tradeable_by_security_type"].get(SecurityType.STOCK.value, 0)
        etf_count = summary["tradeable_by_security_type"].get(SecurityType.ETF.value, 0)
        lof_count = summary["tradeable_by_security_type"].get(SecurityType.LOF.value, 0)
        fund_count = sum(
            summary["tradeable_by_security_type"].get(kind.value, 0)
            for kind in (SecurityType.ETF, SecurityType.LOF, SecurityType.FUND)
        )
        restricted_stock = [
            item
            for item in tradeable
            if item.security_type is SecurityType.STOCK and item.board.value in {"STAR", "CHINEXT", "BSE"}
        ]
        restricted_candidates = [
            item
            for item in candidates
            if item.security_type is SecurityType.STOCK and item.board in {"STAR", "CHINEXT", "BSE"}
        ]
        if stock_price_coverage < MIN_BATCH_PRICE_COVERAGE:
            problems.append(f"股票批量价格覆盖异常:{stock_price_coverage:.1%}")
        if etf_price_coverage < MIN_BATCH_PRICE_COVERAGE:
            problems.append(f"ETF批量价格覆盖异常:{etf_price_coverage:.1%}")
        if lof_price_coverage < MIN_BATCH_PRICE_COVERAGE:
            problems.append(f"LOF批量价格覆盖异常:{lof_price_coverage:.1%}")
        if raw_stock_count < 4000:
            problems.append(f"原始股票数量异常:{raw_stock_count}")
        if tradeable_stock_count < 2500:
            problems.append(f"沪深主板可交易股票过少:{tradeable_stock_count}")
        if etf_count < 500:
            problems.append(f"ETF数量异常:{etf_count}")
        if lof_count < 20:
            problems.append(f"LOF数量异常:{lof_count}")
        if fund_count < 600:
            problems.append(f"ETF/场内基金总量异常:{fund_count}")
        if restricted_stock:
            problems.append(f"权限过滤失效:{len(restricted_stock)}")
        if restricted_candidates:
            problems.append(f"候选池混入受限板块:{len(restricted_candidates)}")
        if len(selected) < 12:
            problems.append(f"动态重点行业过少:{len(selected)}")
        if len(candidates) < 80:
            problems.append(f"初始候选池过少:{len(candidates)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
