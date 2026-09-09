from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from scripts.collect_full_a_universe import CN_TZ, _get_json, atomic_json
from scripts.collect_full_a_universe_v2 import _has_hard_financial_problem
from trading_skill.a_share_fundamentals import FinancialPeriod, FundamentalPrefilter
from trading_skill.industry_intelligence import fetch_sina_7x24, match_industry_events
from trading_skill.industry_profiles import profile_dict
from trading_skill.industry_prospects import match_theme
from trading_skill.sector_fundamental_gate import sector_observation_override


STOCK_QUOTE = "https://push2.eastmoney.com/api/qt/stock/get"


def fetch_actual_industry(code: str, market: int) -> str | None:
    payload = _get_json(
        STOCK_QUOTE,
        {
            "secid": f"{int(market)}.{code}",
            "fields": "f57,f58,f127",
        },
    )
    data = payload.get("data") or {}
    name = str(data.get("f127") or "").strip()
    return name or None


def _period(value: dict | None) -> FinancialPeriod | None:
    if not value:
        return None
    return FinancialPeriod(
        report_date=str(value.get("report_date") or ""),
        notice_date=str(value.get("notice_date") or ""),
        revenue_growth=value.get("revenue_growth"),
        profit_growth=value.get("profit_growth"),
        roe=value.get("roe"),
        gross_margin=value.get("gross_margin"),
        eps=value.get("eps"),
        operating_cash_per_share=value.get("operating_cash_per_share"),
    )


def rebuild_prefilter(value: dict) -> FundamentalPrefilter:
    return FundamentalPrefilter(
        eligible=bool(value.get("eligible")),
        grade=str(value.get("grade") or "E"),
        annual=_period(value.get("annual")),
        interim=_period(value.get("interim")),
        reasons=tuple(value.get("reasons") or ()),
    )


def apply_cross_industry_context(item: dict, *, actual_industry: str | None, events: list[dict]) -> dict:
    """给跨行业结构候选补回真实行业上下文，不改变其“跨行业补充”候选路线。"""
    out = dict(item)
    if not actual_industry:
        out["industry_context_complete"] = False
        out["industry_context_note"] = "跨行业候选真实细分行业暂未解析；允许继续结构观察，但禁止据此直接新开仓。"
        out["industry_events"] = []
        return out

    theme_match = match_theme(actual_industry)
    theme = theme_match.name if theme_match else None
    profile = profile_dict(actual_industry, theme)
    out["industry_name"] = actual_industry
    out["prospect_theme"] = theme
    out["industry_analysis_profile"] = profile
    out["industry_events"] = list(events or [])
    out["industry_context_complete"] = True
    out["industry_context_note"] = "跨行业结构候选已补全真实细分行业；行业事件、估值和财务口径按真实行业执行。"

    raw_prefilter = dict(out.get("fundamental_prefilter") or {})
    prefilter = rebuild_prefilter(raw_prefilter)
    override, reason = sector_observation_override(str(profile.get("profile") or ""), prefilter)
    generic_observe = prefilter.grade == "D" and not _has_hard_financial_problem(list(prefilter.reasons))
    raw_prefilter["deep_scan_eligible"] = bool(prefilter.eligible or generic_observe or override)
    out["fundamental_prefilter"] = raw_prefilter
    out["sector_observation_override"] = bool(override and not prefilter.eligible)
    out["sector_observation_reason"] = reason
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("full-a-results/universe_latest.json"))
    args = parser.parse_args()

    payload = json.loads(args.universe.read_text(encoding="utf-8"))
    candidates = list(payload.get("leader_candidates") or [])
    cross = [item for item in candidates if item.get("candidate_route") == "跨行业结构补充" or item.get("industry_code") == "CROSS_MARKET"]
    if not cross:
        payload["cross_market_industry_enrichment"] = {"requested": 0, "resolved": 0, "unresolved": 0, "errors": []}
        atomic_json(args.universe, payload)
        return 0

    resolved_by_code: dict[str, str] = {}
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(5, len(cross))) as pool:
        futures = {
            pool.submit(fetch_actual_industry, str(item.get("code")), int(item.get("market") or 0)): item
            for item in cross
        }
        for future in as_completed(futures):
            item = futures[future]
            code = str(item.get("code"))
            try:
                name = future.result()
                if name:
                    resolved_by_code[code] = name
                else:
                    errors.append({"code": code, "name": item.get("name"), "error": "未返回细分行业"})
            except Exception as exc:
                errors.append({"code": code, "name": item.get("name"), "error": str(exc)})

    # 跨行业候选数量很小，只拉一次全局7x24流，再按解析后的真实行业做归属；网络故障不阻断结构扫描。
    news_rows = []
    news_error = None
    try:
        news_rows = fetch_sina_7x24(page_size=120)
    except Exception as exc:
        news_error = str(exc)

    contexts = []
    seen_names = set()
    for name in resolved_by_code.values():
        if name in seen_names:
            continue
        seen_names.add(name)
        theme_match = match_theme(name)
        contexts.append({"name": name, "prospect_theme": theme_match.name if theme_match else None})
    now = __import__("datetime").datetime.now(CN_TZ)
    event_map = match_industry_events(contexts, news_rows, as_of=now, max_age_hours=36, max_per_industry=3) if contexts and news_rows else {}

    enriched = []
    resolved_count = 0
    for item in candidates:
        if not (item.get("candidate_route") == "跨行业结构补充" or item.get("industry_code") == "CROSS_MARKET"):
            enriched.append(item)
            continue
        actual = resolved_by_code.get(str(item.get("code")))
        if actual:
            resolved_count += 1
        enriched.append(apply_cross_industry_context(item, actual_industry=actual, events=event_map.get(actual, []) if actual else []))

    payload["leader_candidates"] = enriched
    payload["deep_scan_eligible_candidates"] = sum(
        1 for item in enriched if (item.get("fundamental_prefilter") or {}).get("deep_scan_eligible")
    )
    payload["cross_market_industry_enrichment"] = {
        "requested": len(cross),
        "resolved": resolved_count,
        "unresolved": len(cross) - resolved_count,
        "news_rows": len(news_rows),
        "news_error": news_error,
        "errors": errors,
    }
    payload.setdefault("guardrails", {})["cross_market_real_industry_required_for_new_entry"] = True
    payload["guardrails"]["cross_market_industry_specific_profile"] = True
    atomic_json(args.universe, payload)
    print(f"跨行业候选真实行业补全: {resolved_count}/{len(cross)}，未解析={len(cross)-resolved_count}，资讯={len(news_rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
