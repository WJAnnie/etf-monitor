from __future__ import annotations

import argparse
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
from trading_skill.industry_priority import ROTATION_REFRESH_POLICY, select_priority_industries
from trading_skill.market_universe import SecurityType, TradePermissions, build_tradeable_universe, normalize_stock_row

ETF_FS = "b:MK0021,b:MK0022,b:MK0024,b:MK0827"
LOF_FS = "b:MK0023"


def fetch_raw_stock_rows() -> tuple[list[dict], str, list[str]]:
    rows: list[dict] = []
    errors: list[str] = []
    for fs in A_SHARE_MARKETS:
        try:
            rows.extend(fetch_paginated(fs, STOCK_FIELDS, fid="f3"))
        except Exception as exc:
            errors.append(f"东方财富[{fs}]:{exc}")
            rows = []
            break
    if len(rows) >= 4000:
        return rows, "东方财富分市场原始证券", errors

    # 新浪兜底只填真实提供的字段。缺少60日/年内涨幅时保持None，绝不伪造为0。
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
            sina_rows.append({
                "f12": code,
                "f13": market,
                "f14": item.get("name"),
                "f2": item.get("trade"),
                "f3": item.get("changepercent"),
                "f6": item.get("amount"),
                "f8": item.get("turnoverratio"),
                "f9": item.get("per"),
                "f20": (_safe_float(item.get("mktcap")) * 10_000) if item.get("mktcap") not in (None, "", "-") else None,
                "f21": (_safe_float(item.get("nmc")) * 10_000) if item.get("nmc") not in (None, "", "-") else None,
                "f23": item.get("pb"),
                "f24": None,
                "f25": None,
            })
    if len(sina_rows) >= 4000:
        return sina_rows, "新浪全A原始证券兜底", errors
    raise RuntimeError(f"股票主备数据源均不足：东财={len(rows)} 新浪={len(sina_rows)}")


def fetch_exchange_funds() -> tuple[list[dict], list[dict], list[str]]:
    errors: list[str] = []
    etfs: list[dict] = []
    lofs: list[dict] = []
    try:
        etfs = fetch_paginated(ETF_FS, STOCK_FIELDS, fid="f6")
    except Exception as exc:
        errors.append(f"ETF:{exc}")
    try:
        lofs = fetch_paginated(LOF_FS, STOCK_FIELDS, fid="f6")
    except Exception as exc:
        errors.append(f"LOF:{exc}")
    return etfs, lofs, errors


def fetch_industry_members(board_code: str) -> list[dict]:
    return fetch_paginated(f"b:{board_code} f:!50", STOCK_FIELDS, fid="f6", max_pages=4)


def _event_map(selected: list[dict]) -> tuple[dict[str, list[dict]], list[str]]:
    try:
        news_rows = fetch_sina_7x24(page_size=160)
        return match_industry_events(selected, news_rows, as_of=datetime.now(CN_TZ), max_age_hours=36, max_per_industry=3), []
    except Exception as exc:
        return {}, [f"行业事件:{exc}"]


def _summary(all_items, tradeable, candidates) -> dict:
    raw_types = Counter(item.security_type.value for item in all_items)
    tradeable_types = Counter(item.security_type.value for item in tradeable)
    quality = Counter(item.data_quality.value for item in all_items)
    exclusions = Counter(reason for item in all_items for reason in item.exclusion_reasons)
    candidate_types = Counter(item.security_type.value for item in candidates)
    routes = Counter(route for item in candidates for route in item.source_routes)
    return {
        "raw_by_security_type": dict(raw_types),
        "tradeable_by_security_type": dict(tradeable_types),
        "data_quality": dict(quality),
        "exclusions": dict(exclusions),
        "candidate_by_security_type": dict(candidate_types),
        "candidate_by_route": dict(routes),
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

    stock_rows, stock_source, stock_errors = fetch_raw_stock_rows()
    etf_rows, lof_rows, fund_errors = fetch_exchange_funds()
    permissions = TradePermissions(sh_main=True, sz_main=True, star=False, chinext=False, bse=False, etf=True, lof=True, fund=True)
    all_items, tradeable = build_tradeable_universe(stock_rows, etf_rows, lof_rows, stock_source=stock_source, fund_source="东方财富场内基金", permissions=permissions)

    industry_rows = fetch_industries()
    selected, deferred = select_priority_industries(industry_rows, limit=args.industry_limit)
    selected_dicts = [item.as_dict() for item in selected]

    industry_members: dict[str, list] = {}
    member_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(selected)))) as pool:
        futures = {pool.submit(fetch_industry_members, item.code): item for item in selected}
        for future in as_completed(futures):
            item = futures[future]
            try:
                rows = future.result()
                industry_members[item.code] = [normalize_stock_row(row, source=f"东方财富行业成分:{item.name}", permissions=permissions) for row in rows]
            except Exception as exc:
                member_errors.append({"code": item.code, "name": item.name, "error": str(exc)})

    events, event_errors = _event_map(selected_dicts)
    candidates = discover_candidates(tradeable, industry_members=industry_members, industries=selected_dicts, event_map=events, stock_market_strength_cap=args.stock_strength_cap, stock_early_turn_cap=args.stock_early_cap, fund_cap=args.fund_cap)

    summary = _summary(all_items, tradeable, candidates)
    payload = {
        "mode": "TRADEABLE_CANDIDATE_UNIVERSE",
        "generated_at": datetime.now(CN_TZ).isoformat(),
        "permissions": asdict(permissions),
        "design_contract": {
            "step1": "只回答当前账户权限下哪些证券可进入扫描：沪深主板股票+ETF/LOF/场内基金；排除科创板、创业板、北交所个股、ST/*ST、退市和无有效行情证券。",
            "step2": "只回答哪些证券值得继续研究；股票走市场强势/刚启动/行业/事件多路线，ETF/场内基金独立同类筛选；不产生基本面最终结论、缠论买点或交易建议。",
            "missing_data_is_never_zero": True,
            "industry_is_not_hard_stock_gate": True,
            "high_position_is_risk_tag_not_hard_veto": True,
            "static_industry_themes_are_prior_not_whitelist": True,
        },
        "sources": {"stocks": stock_source, "funds": "东方财富ETF/LOF板块", "stock_errors": stock_errors, "fund_errors": fund_errors, "industry_member_errors": member_errors, "industry_event_errors": event_errors},
        "summary": summary,
        "selected_industries": selected_dicts,
        "deferred_industries": [item.as_dict() for item in deferred[:40]],
        "industry_refresh_policy": ROTATION_REFRESH_POLICY,
        "industry_events": events,
        "candidates": [item.as_dict() for item in candidates],
    }
    atomic_json(args.output, payload)

    print("第一步可交易池:", summary["tradeable_by_security_type"])
    print("权限剔除:", {k: v for k, v in summary["exclusions"].items() if "BOARD_NOT_ALLOWED" in k or k in {"ST", "DELISTING"}})
    print("数据质量:", summary["data_quality"])
    print("动态重点行业:", len(selected), [(x.name, x.state.value) for x in selected[:12]])
    print("第二步候选:", summary["candidate_by_security_type"], summary["candidate_by_route"])

    if args.strict:
        problems: list[str] = []
        raw_stock_count = summary["raw_by_security_type"].get(SecurityType.STOCK.value, 0)
        tradeable_stock_count = summary["tradeable_by_security_type"].get(SecurityType.STOCK.value, 0)
        fund_count = sum(summary["tradeable_by_security_type"].get(kind.value, 0) for kind in (SecurityType.ETF, SecurityType.LOF, SecurityType.FUND))
        if raw_stock_count < 4000:
            problems.append(f"原始股票数量异常:{raw_stock_count}")
        if tradeable_stock_count < 2500:
            problems.append(f"沪深主板可交易股票过少:{tradeable_stock_count}")
        if fund_count < 100:
            problems.append(f"ETF/场内基金数量异常:{fund_count}")
        if len(selected) < 12:
            problems.append(f"动态重点行业过少:{len(selected)}")
        if len(candidates) < 80:
            problems.append(f"初始候选池过少:{len(candidates)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
