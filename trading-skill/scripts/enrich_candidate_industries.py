from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from scripts.collect_full_a_universe import atomic_json
from trading_skill.industry_profiles import profile_dict
from trading_skill.industry_prospects import match_theme


HOSTS = (
    "https://push2.eastmoney.com/api/qt/stock/get",
    "https://82.push2.eastmoney.com/api/qt/stock/get",
)
UT = "fa5fd1943c7b386f172d6893dbfba10b"
TIMEOUT = 3.0
MAX_WORKERS = 16


def _market_from_candidate(item: dict) -> int:
    board = str(item.get("board") or "")
    if board == "SH_MAIN" or str(item.get("code") or "").startswith(("5", "6")):
        return 1
    return 0


def resolve_industry(code: str, market: int) -> str | None:
    params = {
        "secid": f"{market}.{code}",
        "fields": "f57,f58,f127",
        "ut": UT,
        "fltt": 2,
        "invt": 2,
    }
    errors: list[str] = []
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
            if isinstance(data, dict) and str(data.get("f57") or "").strip():
                industry = str(data.get("f127") or "").strip()
                if industry:
                    return industry
            errors.append(f"{host}:empty")
        except Exception as exc:
            errors.append(f"{host}:{exc}")
    if errors:
        raise RuntimeError("；".join(errors))
    return None


def _apply_context(item: dict, industry: str | None, *, error: str | None = None) -> dict:
    out = dict(item)
    resolved_industry = str(industry or "").strip()
    existing_industry = str(item.get("industry_name") or "").strip()
    fundamental_industry = resolved_industry or existing_industry

    # PE/PB来自Step1全市场行情并由Step2透传；这里不重复请求、也不覆盖。
    out["valuation_source"] = "Step1全市场行情f9/f23"

    if fundamental_industry:
        theme_match = match_theme(fundamental_industry)
        theme = theme_match.name if theme_match else None
        out["fundamental_industry_name"] = fundamental_industry
        out["prospect_theme"] = out.get("prospect_theme") or theme
        out["industry_analysis_profile"] = profile_dict(fundamental_industry, out.get("prospect_theme") or theme)
        out["industry_context_complete"] = True
        out["industry_context_source"] = "东方财富个股f127" if resolved_industry else "第二步行业路线"
        out["industry_context_note"] = "已具备第三步行业化基本面所需的行业上下文；行业标签不改变第二步候选资格。"
    else:
        out["fundamental_industry_name"] = None
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
    industry_complete = 0
    valuation_complete = 0
    for item in candidates:
        if item.get("security_type") != "STOCK":
            enriched.append(item)
            continue
        code = str(item.get("code") or "")
        out = _apply_context(item, resolved.get(code), error=errors.get(code))
        if out.get("industry_context_complete"):
            industry_complete += 1
        if out.get("valuation_pe") is not None or out.get("valuation_pb") is not None:
            valuation_complete += 1
        enriched.append(out)

    stats = {
        "stock_candidates": len(stocks),
        "already_had_industry": len(stocks) - len(missing),
        "industry_lookup_requested": len(missing),
        "resolved_by_api": len(resolved),
        "industry_complete": industry_complete,
        "industry_coverage_pct": round(industry_complete / len(stocks) * 100.0, 2) if stocks else 100.0,
        "valuation_complete_from_step1": valuation_complete,
        "valuation_coverage_pct": round(valuation_complete / len(stocks) * 100.0, 2) if stocks else 100.0,
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
    payload["design_contract"]["valuation_is_context_not_standalone_veto"] = True
    payload["design_contract"]["valuation_reused_from_step1_no_duplicate_lookup"] = True
    payload["design_contract"]["candidate_industry_lookup_is_fail_fast"] = True
    output = args.output or args.input
    atomic_json(output, payload)

    print("候选行业补全:", stats)
    if args.strict and stats["stock_candidates"]:
        if stats["industry_coverage_pct"] < 95:
            raise SystemExit(f"股票候选行业覆盖不足:{stats['industry_coverage_pct']}%")
        if stats["valuation_coverage_pct"] < 90:
            raise SystemExit(f"股票候选估值覆盖不足:{stats['valuation_coverage_pct']}%")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
