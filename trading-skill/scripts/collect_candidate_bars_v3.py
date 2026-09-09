from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import scripts.collect_candidate_bars_v2 as v2
from trading_skill.history_policy import classify_history_counts
from trading_skill.market_universe import StockBoard, TradePermissions, classify_stock_board


V3_METADATA_KEYS = (
    "industry_rotation_state",
    "industry_analysis_profile",
    "candidate_route",
    "recent_report",
    "sector_financial_metrics",
    "sector_observation_override",
    "sector_observation_reason",
    "pe",
    "pb",
    "industry_events",
    "prospect_theme",
    "industry_name",
    "industry_selection_reason",
    "industry_context_complete",
    "industry_context_note",
)

TENCENT_DAILY_HOSTS = (
    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
)

_fetch_daily_v2 = v2.fetch_daily_v2
_collect_one_v2 = v2.collect_one
v2.MAX_WORKERS = min(int(getattr(v2, "MAX_WORKERS", 6)), 4)


def _fetch_tencent_daily_page(symbol: str, *, end_date: str, count: int) -> tuple[list[dict], list[str]]:
    errors: list[str] = []
    for url in TENCENT_DAILY_HOSTS:
        try:
            response = v2._request(
                url,
                params={"param": f"{symbol},day,,{end_date},{count},qfq"},
                referer="https://gu.qq.com/",
            )
            payload = v2._decode_json_or_jsonp(response.text)
            stock = (payload.get("data") or {}).get(symbol) or {}
            rows = v2._parse_array(stock.get("qfqday") or stock.get("day") or [])
            if rows:
                return rows, errors
            errors.append(f"{url}:空页")
        except Exception as exc:
            errors.append(f"{url}:{exc}")
    return [], errors


def tencent_daily_long(code: str, *, limit: int = v2.DAILY_LIMIT, page_size: int = 500) -> tuple[list[dict], list[str]]:
    symbol = v2.tx_symbol(code)
    collected: dict[str, dict] = {}
    warnings: list[str] = []
    end_date = ""
    max_pages = max(2, (max(1, limit) + max(1, page_size) - 1) // max(1, page_size) + 2)

    for page_no in range(1, max_pages + 1):
        remaining = max(1, limit - len(collected))
        count = min(max(1, page_size), remaining)
        page_rows, page_errors = _fetch_tencent_daily_page(symbol, end_date=end_date, count=count)
        if page_errors:
            warnings.extend(f"腾讯前复权分段第{page_no}页:{item}" for item in page_errors)
        if not page_rows:
            break
        before = len(collected)
        for row in page_rows:
            day = str(row.get("time") or "")[:10]
            if day:
                collected[day] = row
        if len(collected) == before or len(collected) >= limit:
            break
        dated_rows = [str(row.get("time") or "")[:10] for row in page_rows if row.get("time")]
        if not dated_rows:
            warnings.append(f"腾讯前复权分段第{page_no}页缺少日期")
            break
        earliest_text = min(dated_rows)
        try:
            earliest = datetime.fromisoformat(earliest_text)
        except ValueError:
            warnings.append(f"腾讯前复权分段日期异常:{earliest_text}")
            break
        end_date = (earliest - timedelta(days=1)).strftime("%Y-%m-%d")

    rows = [collected[key] for key in sorted(collected)]
    return rows[-limit:], warnings


def fetch_daily_v3(code: str, market: int) -> tuple[list[dict], str, list[str]]:
    warnings: list[str] = []
    rows, tx_warnings = tencent_daily_long(code, limit=v2.DAILY_LIMIT)
    warnings.extend(tx_warnings)
    if len(rows) >= v2.MIN_DAILY:
        return rows[-v2.DAILY_LIMIT :], "腾讯前复权分段", warnings
    try:
        fallback_rows, fallback_source, fallback_warnings = _fetch_daily_v2(code, market)
        warnings.extend(fallback_warnings)
        return fallback_rows, fallback_source, warnings
    except Exception as exc:
        warnings.append(f"V2日线回退:{exc}")
        raise RuntimeError("；".join(warnings)) from exc


def merge_v3_metadata(result: dict, source: dict) -> dict:
    merged = dict(result)
    for key in V3_METADATA_KEYS:
        if key in source:
            merged[key] = source.get(key)
    return merged


def collect_one_v3(item: dict, now):
    result = merge_v3_metadata(_collect_one_v2(item, now), item)
    history = classify_history_counts(
        daily=len(result.get("daily") or []),
        weekly=len(result.get("weekly") or []),
        m120=len(result.get("120m") or []),
        m30=len(result.get("30m") or []),
        m5=len(result.get("5m") or []),
    ).to_dict()
    result["history_quality"] = history
    quality = dict(result.get("quality") or {})
    quality.update(
        {
            "long_term_history_complete": history["long_term_complete"],
            "long_term_history_tier": history["long_term_tier"],
            "daily_primary_history_ok": history["daily_primary_ok"],
            "m120_primary_history_ok": history["m120_primary_ok"],
            "m30_primary_history_ok": history["m30_primary_ok"],
        }
    )
    result["quality"] = quality
    return result


def _account_stock_allowed(item: dict, permissions: TradePermissions) -> tuple[bool, str]:
    code = str(item.get("code") or "")
    market = int(item.get("market") or 0)
    board = classify_stock_board(code, market)
    return permissions.board_allowed(board), board.value


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/candidate_bars_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(v2.CN_TZ)
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    raw_candidates = [
        item for item in universe.get("leader_candidates", [])
        if (item.get("fundamental_prefilter") or {}).get(
            "deep_scan_eligible", (item.get("fundamental_prefilter") or {}).get("eligible")
        )
    ]

    # 过渡权限桥：旧V3候选合同还未整体替换前，深扫入口必须服从新版账户权限。
    permissions = TradePermissions(sh_main=True, sz_main=True, star=False, chinext=False, bse=False)
    candidates: list[dict] = []
    permission_excluded: list[dict] = []
    for item in raw_candidates:
        allowed, board = _account_stock_allowed(item, permissions)
        if allowed:
            candidates.append(item)
        else:
            permission_excluded.append({"code": item.get("code"), "name": item.get("name"), "board": board})

    results: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=v2.MAX_WORKERS) as pool:
        futures = {pool.submit(collect_one_v3, item, now): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append({"code": item.get("code"), "name": item.get("name"), "error": str(exc)})

    results.sort(
        key=lambda x: (
            str(x.get("prospect_theme") or "ZZZ"),
            str(x.get("industry_name")),
            int(x.get("leader_rank") or 99),
            str(x.get("code")),
        )
    )
    payload = {
        "mode": "FULL_A_CANDIDATE_LONG_HISTORY_V3",
        "generated_at": now.isoformat(),
        "upstream_candidate_count": len(raw_candidates),
        "permission_excluded_count": len(permission_excluded),
        "permission_excluded": permission_excluded,
        "candidate_count": len(candidates),
        "loaded_count": len(results),
        "errors": errors,
        "symbols": results,
        "guardrails": {
            "account_stock_boards": [StockBoard.SH_MAIN.value, StockBoard.SZ_MAIN.value],
            "star_chinext_bse_blocked_before_kline_download": True,
            "real_5m_only": True,
            "30m_direct_real_history": True,
            "120m_from_real_30m": True,
            "prefer_adjusted_long_history": True,
            "newer_stocks_can_use_shorter_daily_history": True,
            "no_15m_to_5m": True,
            "daily_target": v2.DAILY_LIMIT,
            "m5_target": v2.M5_LIMIT,
            "m30_target": v2.M30_LIMIT,
        },
    }
    v2.atomic_json(args.output, payload)
    print(
        f"旧链权限桥剔除={len(permission_excluded)}，允许深扫候选={len(candidates)}，"
        f"长历史五周期成功={len(results)}，异常={len(errors)}"
    )
    if permission_excluded:
        print("受限板块前10:", permission_excluded[:10])
    for item in results:
        print(
            item["code"], item["name"], item["sources"], len(item["daily"]), len(item["weekly"]),
            len(item["120m"]), len(item["30m"]), len(item["5m"]), item["quality"]["latest_m5"]
        )
    if errors:
        print("异常前10:", errors[:10])

    restricted_results = [
        item for item in results
        if classify_stock_board(str(item.get("code") or ""), int(item.get("market") or 0))
        in {StockBoard.STAR, StockBoard.CHINEXT, StockBoard.BSE}
    ]
    if args.strict:
        if restricted_results:
            raise SystemExit(f"旧链权限桥失效:{len(restricted_results)}")
        if candidates and len(results) / len(candidates) < 0.75:
            raise SystemExit(f"长历史五周期成功率过低:{len(results)}/{len(candidates)}")
    return 0


v2.fetch_daily_v2 = fetch_daily_v3
v2.collect_one = collect_one_v3


if __name__ == "__main__":
    raise SystemExit(main())
