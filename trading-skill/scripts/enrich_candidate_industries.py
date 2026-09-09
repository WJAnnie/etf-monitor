from __future__ import annotations

import argparse
import json
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from scripts.collect_full_a_universe import atomic_json
from trading_skill.industry_profiles import profile_dict
from trading_skill.industry_prospects import match_theme


HOSTS = (
    "https://push2.eastmoney.com/api/qt/stock/get",
    "https://82.push2.eastmoney.com/api/qt/stock/get",
    "https://73.push2.eastmoney.com/api/qt/stock/get",
)
# fa5... 是东财个股信息接口常用公开查询参数；保留旧token作第二选择，避免单token失效。
UT_TOKENS = (
    "fa5fd1943c7b386f172d6893dbfba10b",
    "bd1d9ddb04089700cf9c27f6f7426281",
)
TIMEOUT = 5.0
MAX_WORKERS = 8


def _market_from_candidate(item: dict) -> int:
    board = str(item.get("board") or "")
    if board == "SH_MAIN" or str(item.get("code") or "").startswith(("5", "6")):
        return 1
    return 0


def _request_industry(code: str, market: int) -> str | None:
    last_error: Exception | None = None
    for token in UT_TOKENS:
        params = {
            "secid": f"{market}.{code}",
            "fields": "f57,f58,f127",
            "ut": token,
            "fltt": 2,
            "invt": 2,
        }
        for host in HOSTS:
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
                    timeout=TIMEOUT,
                )
                response.raise_for_status()
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, dict):
                    industry = str(data.get("f127") or "").strip()
                    if industry:
                        return industry
            except Exception as exc:
                last_error = exc
    if last_error:
        raise RuntimeError(str(last_error))
    return None


def resolve_industry(code: str, market: int, *, retries: int = 1) -> str | None:
    for attempt in range(retries + 1):
        try:
            result = _request_industry(code, market)
            if result:
                return result
        except Exception:
            if attempt >= retries:
                raise
        if attempt < retries:
            time.sleep(0.12 + random.uniform(0.02, 0.10))
    return None


def _apply_context(item: dict, industry: str | None, *, error: str | None = None) -> dict:
    out = dict(item)
    if industry:
        theme_match = match_theme(industry)
        theme = theme_match.name if theme_match else None
        out["industry_name"] = industry
        out["prospect_theme"] = theme
        out["industry_analysis_profile"] = profile_dict(industry, theme)
        out["industry_context_complete"] = True
        out["industry_context_source"] = "东方财富个股f127"
        out["industry_context_note"] = "全市场候选已补全真实行业；该行业标签仅用于后续行业化基本面与风险分析，不改变第二步候选资格。"
        return out
    out["industry_context_complete"] = False
    out["industry_context_source"] = None
    out["industry_context_note"] = "真实行业暂未解析；第三步只能WATCH，禁止套用通用行业模型伪装成完整基本面结论。"
    if error:
        out["industry_context_error"] = error[:500]
    return out


def enrich_candidates(candidates: list[dict]) -> tuple[list[dict], dict]:
    stocks = [item for item in candidates if item.get("security_type") == "STOCK"]
    missing = [item for item in stocks if not str(item.get("industry_name") or "").strip()]
    resolved: dict[str, str] = {}
    errors: dict[str, str] = {}

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(missing)))) as pool:
        futures = {
            pool.submit(resolve_industry, str(item.get("code")), _market_from_candidate(item)): item
            for item in missing
        }
        for future in as_completed(futures):
            item = futures[future]
            code = str(item.get("code") or "")
            try:
                industry = future.result()
                if industry:
                    resolved[code] = industry
                else:
                    errors[code] = "未返回行业"
            except Exception as exc:
                errors[code] = str(exc)

    enriched: list[dict] = []
    for item in candidates:
        if item.get("security_type") != "STOCK":
            enriched.append(item)
            continue
        current = str(item.get("industry_name") or "").strip()
        if current:
            out = dict(item)
            out.setdefault("industry_context_complete", True)
            out.setdefault("industry_context_source", "第二步行业路线")
            enriched.append(out)
            continue
        code = str(item.get("code") or "")
        enriched.append(_apply_context(item, resolved.get(code), error=errors.get(code)))

    stats = {
        "stock_candidates": len(stocks),
        "already_had_industry": len(stocks) - len(missing),
        "requested": len(missing),
        "resolved": len(resolved),
        "unresolved": len(missing) - len(resolved),
        "coverage_pct": round((len(stocks) - len(missing) + len(resolved)) / len(stocks) * 100.0, 2) if stocks else 100.0,
        "errors": [{"code": code, "error": error[:500]} for code, error in sorted(errors.items())],
    }
    return enriched, stats


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("full-a-results/candidate_universe_latest.json"))
    parser.add_argument("--output", type=Path, default=None)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    candidates = list(payload.get("candidates") or [])
    enriched, stats = enrich_candidates(candidates)
    payload["candidates"] = enriched
    payload["candidate_industry_enrichment"] = stats
    payload.setdefault("design_contract", {})["market_wide_candidates_get_real_industry_before_step3"] = True
    payload["design_contract"]["industry_resolution_failure_means_watch_not_generic_pass"] = True
    output = args.output or args.input
    atomic_json(output, payload)

    print("候选行业补全:", stats)
    if args.strict and stats["stock_candidates"] and stats["coverage_pct"] < 95:
        raise SystemExit(f"股票候选行业覆盖不足:{stats['coverage_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
