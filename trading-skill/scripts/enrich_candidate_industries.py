from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from scripts.collect_full_a_universe import atomic_json
from trading_skill.industry_profiles import profile_dict
from trading_skill.industry_prospects import match_theme


# 行业归属属于慢变公司主数据，不应该依赖逐股实时行情接口。
# 主路径一次分页拉取东财 F10 公司概况，构建 code -> industry 映射；
# 只有主数据缺口才使用少量逐股 f127 兜底。
ORGINFO_URLS = (
    "https://datacenter.eastmoney.com/securities/api/data/v1/get",
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
)
ORGINFO_PAGE_SIZE = 500
ORGINFO_MAX_PAGES = 20
QUOTE_HOSTS = (
    "https://push2.eastmoney.com/api/qt/stock/get",
    "https://82.push2.eastmoney.com/api/qt/stock/get",
)
UT = "fa5fd1943c7b386f172d6893dbfba10b"
TIMEOUT = 5.0
FALLBACK_WORKERS = 6


def _headers(*, f10: bool = False) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://emweb.securities.eastmoney.com/" if f10 else "https://quote.eastmoney.com/",
        "Origin": "https://emweb.securities.eastmoney.com" if f10 else "https://quote.eastmoney.com",
        "Connection": "close",
    }


def _market_from_candidate(item: dict) -> int:
    board = str(item.get("board") or "")
    if board == "SH_MAIN" or str(item.get("code") or "").startswith(("5", "6")):
        return 1
    return 0


def normalize_em_industry(value: object) -> str | None:
    """EM2016 常是层级行业字符串；第三步使用最具体的末级行业。"""
    text = str(value or "").strip()
    if not text or text in {"-", "--", "None"}:
        return None
    parts = [part.strip() for part in re.split(r"\s*[-—>/|]+\s*", text) if part.strip()]
    return parts[-1] if parts else text


def industry_master_from_rows(rows: list[dict]) -> dict[str, str]:
    out: dict[str, str] = {}
    for row in rows:
        code = str(row.get("SECURITY_CODE") or row.get("STR_CODEA") or "").strip()
        if not re.fullmatch(r"\d{6}", code):
            continue
        industry = normalize_em_industry(row.get("EM2016")) or normalize_em_industry(row.get("INDUSTRYCSRC1"))
        if industry:
            out[code] = industry
    return out


def _fetch_orginfo_page(page_number: int) -> tuple[list[dict], int | None, str]:
    params = {
        "reportName": "RPT_F10_BASIC_ORGINFO",
        "columns": "SECURITY_CODE,SECUCODE,STR_CODEA,EM2016,INDUSTRYCSRC1",
        "quoteColumns": "",
        "pageNumber": page_number,
        "pageSize": ORGINFO_PAGE_SIZE,
        # 该表没有 REPORT_DATE，不能附带 sortColumns。
        "sortTypes": "",
        "sortColumns": "",
        "source": "HSF10",
        "client": "PC",
    }
    errors: list[str] = []
    for url in ORGINFO_URLS:
        try:
            response = requests.get(url, params=params, headers=_headers(f10=True), timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, dict):
                errors.append(f"{url}:empty-result")
                continue
            data = result.get("data") or []
            if not isinstance(data, list):
                errors.append(f"{url}:invalid-data")
                continue
            pages_raw = result.get("pages")
            try:
                pages = int(pages_raw) if pages_raw not in (None, "") else None
            except (TypeError, ValueError):
                pages = None
            return [row for row in data if isinstance(row, dict)], pages, url
        except Exception as exc:
            errors.append(f"{url}:{exc}")
    raise RuntimeError("；".join(errors))


def fetch_industry_master() -> tuple[dict[str, str], dict]:
    """一次拉公司主数据，避免对候选逐股请求行业。"""
    rows: list[dict] = []
    source = ""
    pages_expected: int | None = None
    page = 1
    while page <= ORGINFO_MAX_PAGES:
        page_rows, pages, page_source = _fetch_orginfo_page(page)
        if page == 1:
            source = page_source
            pages_expected = pages
        rows.extend(page_rows)
        if not page_rows:
            break
        if pages_expected is not None and page >= pages_expected:
            break
        if pages_expected is None and len(page_rows) < ORGINFO_PAGE_SIZE:
            break
        page += 1
    mapping = industry_master_from_rows(rows)
    return mapping, {
        "source": source or None,
        "rows_loaded": len(rows),
        "codes_mapped": len(mapping),
        "pages_loaded": page if rows else 0,
        "pages_expected": pages_expected,
    }


def resolve_industry_fallback(code: str, market: int) -> str | None:
    """仅用于 F10 主数据缺口；失败即降级 WATCH，不做长重试。"""
    params = {
        "secid": f"{market}.{code}",
        "fields": "f57,f58,f127",
        "ut": UT,
        "fltt": 2,
        "invt": 2,
    }
    errors: list[str] = []
    for host in QUOTE_HOSTS:
        try:
            response = requests.get(host, params=params, headers=_headers(), timeout=3.0)
            response.raise_for_status()
            payload = response.json()
            data = payload.get("data") if isinstance(payload, dict) else None
            if isinstance(data, dict) and str(data.get("f57") or "").strip():
                industry = normalize_em_industry(data.get("f127"))
                if industry:
                    return industry
            errors.append(f"{host}:empty")
        except Exception as exc:
            errors.append(f"{host}:{exc}")
    if errors:
        raise RuntimeError("；".join(errors))
    return None


def _apply_context(
    item: dict,
    industry: str | None,
    *,
    source: str | None = None,
    error: str | None = None,
) -> dict:
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
        out["industry_context_source"] = source or ("第二步行业路线" if existing_industry else "UNKNOWN")
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

    master: dict[str, str] = {}
    master_meta: dict = {"source": None, "rows_loaded": 0, "codes_mapped": 0, "pages_loaded": 0, "pages_expected": None}
    master_error: str | None = None
    if missing:
        try:
            master, master_meta = fetch_industry_master()
        except Exception as exc:
            master_error = str(exc)[:1000]

    unresolved_after_master = [item for item in missing if str(item.get("code") or "") not in master]
    fallback_resolved: dict[str, str] = {}
    fallback_errors: dict[str, str] = {}
    if unresolved_after_master:
        with ThreadPoolExecutor(max_workers=min(FALLBACK_WORKERS, len(unresolved_after_master))) as pool:
            futures = {
                pool.submit(resolve_industry_fallback, str(item.get("code")), _market_from_candidate(item)): item
                for item in unresolved_after_master
            }
            for future in as_completed(futures):
                item = futures[future]
                code = str(item.get("code") or "")
                try:
                    industry = future.result()
                    if industry:
                        fallback_resolved[code] = industry
                    else:
                        fallback_errors[code] = "未返回行业"
                except Exception as exc:
                    fallback_errors[code] = str(exc)

    enriched: list[dict] = []
    industry_complete = 0
    valuation_complete = 0
    resolved_from_master = 0
    for item in candidates:
        if item.get("security_type") != "STOCK":
            enriched.append(item)
            continue
        code = str(item.get("code") or "")
        existing = str(item.get("industry_name") or "").strip()
        if existing:
            industry = None
            source = "第二步行业路线"
        elif code in master:
            industry = master[code]
            source = "东方财富F10公司主数据EM2016"
            resolved_from_master += 1
        else:
            industry = fallback_resolved.get(code)
            source = "东方财富个股f127兜底" if industry else None
        out = _apply_context(item, industry, source=source, error=fallback_errors.get(code))
        if out.get("industry_context_complete"):
            industry_complete += 1
        if out.get("valuation_pe") is not None or out.get("valuation_pb") is not None:
            valuation_complete += 1
        enriched.append(out)

    stats = {
        "stock_candidates": len(stocks),
        "already_had_industry": len(stocks) - len(missing),
        "industry_lookup_requested": len(missing),
        "industry_master": master_meta,
        "industry_master_error": master_error,
        "resolved_from_master": resolved_from_master,
        "fallback_requested": len(unresolved_after_master),
        "fallback_resolved": len(fallback_resolved),
        "industry_complete": industry_complete,
        "industry_coverage_pct": round(industry_complete / len(stocks) * 100.0, 2) if stocks else 100.0,
        "valuation_complete_from_step1": valuation_complete,
        "valuation_coverage_pct": round(valuation_complete / len(stocks) * 100.0, 2) if stocks else 100.0,
        "fallback_errors": [
            {"code": code, "error": error[:500]} for code, error in sorted(fallback_errors.items())
        ],
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
    payload["design_contract"]["industry_master_data_preferred_over_per_stock_quote_lookup"] = True
    payload["design_contract"]["per_stock_industry_lookup_is_gap_only_fail_fast_fallback"] = True
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
