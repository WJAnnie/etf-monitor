from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import CN_TZ, MAX_WORKERS, atomic_json, fetch_financial_reports
from trading_skill.fundamental_quality import assess_stock_fundamentals


def _unresolved_industry_assessment(item: dict) -> dict:
    return {
        "status": "WATCH",
        "profile": "UNRESOLVED_INDUSTRY",
        "company_quality": "UNKNOWN",
        "growth_state": "UNKNOWN",
        "valuation_state": "UNKNOWN",
        "risk_level": "MEDIUM",
        "evidence_coverage": "LIMITED",
        "latest_period": None,
        "previous_period": None,
        "visible_period_count": 0,
        "positive_evidence": [],
        "warnings": ["真实行业尚未解析，禁止套用通用行业模型形成PASS结论"],
        "hard_risks": [],
        "followups": ["补全真实行业后再运行行业化基本面"],
        "rationale": ["行业上下文缺失：仅允许WATCH"],
        "deep_analysis_eligible": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("full-a-results/candidate_universe_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/fundamental_quality_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    candidates = list(payload.get("candidates") or [])
    stocks = [item for item in candidates if item.get("security_type") == "STOCK"]
    funds = [item for item in candidates if item.get("security_type") in {"ETF", "LOF", "FUND"}]

    reports_by_code: dict[str, list[dict]] = {}
    report_errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(stocks)))) as pool:
        futures = {pool.submit(fetch_financial_reports, str(item.get("code"))): item for item in stocks}
        for future in as_completed(futures):
            item = futures[future]
            code = str(item.get("code") or "")
            try:
                reports_by_code[code] = future.result()
            except Exception as exc:
                reports_by_code[code] = []
                report_errors.append({"code": code, "name": item.get("name"), "error": str(exc)[:500]})

    assessed: list[dict] = []
    for item in stocks:
        code = str(item.get("code") or "")
        industry_name = str(item.get("fundamental_industry_name") or item.get("industry_name") or "").strip()
        if not industry_name:
            assessment = _unresolved_industry_assessment(item)
        else:
            assessment = assess_stock_fundamentals(
                reports_by_code.get(code, []),
                as_of=now,
                industry_name=industry_name,
                prospect_theme=item.get("prospect_theme"),
                pe=item.get("valuation_pe"),
                pb=item.get("valuation_pb"),
                detailed_metrics=None,
                industry_state=item.get("industry_state"),
            ).as_dict()
        assessed.append({
            "code": code,
            "name": item.get("name"),
            "security_type": "STOCK",
            "source_routes": item.get("source_routes") or [],
            "research_priority": item.get("research_priority"),
            "industry_name": industry_name or None,
            "industry_context_complete": bool(industry_name),
            "prospect_theme": item.get("prospect_theme"),
            "valuation_pe": item.get("valuation_pe"),
            "valuation_pb": item.get("valuation_pb"),
            "assessment": assessment,
        })

    # ETF/LOF第三步不使用股票财务模型；先保留为产品质量待评估，绝不因为没有公司财报而REJECT。
    fund_pending = [
        {
            "code": item.get("code"),
            "name": item.get("name"),
            "security_type": item.get("security_type"),
            "fund_category": item.get("fund_category"),
            "fund_family": item.get("fund_family"),
            "status": "PENDING_PRODUCT_QUALITY",
            "note": "ETF/LOF走底层资产+产品质量模型，不套用股票财务报表。",
        }
        for item in funds
    ]

    status_counts = Counter(row["assessment"]["status"] for row in assessed)
    profile_counts = Counter(row["assessment"]["profile"] for row in assessed)
    coverage_counts = Counter(row["assessment"]["evidence_coverage"] for row in assessed)
    report_covered = sum(1 for item in stocks if reports_by_code.get(str(item.get("code") or "")))
    industry_covered = sum(1 for row in assessed if row["industry_context_complete"])

    result = {
        "mode": "STEP3_FUNDAMENTAL_QUALITY_PHASE_A",
        "generated_at": now.isoformat(),
        "source_candidate_file": str(args.input),
        "design_contract": {
            "valuation_is_not_standalone_veto": True,
            "different_industries_use_different_profiles": True,
            "financial_companies_require_specialized_metrics": True,
            "rnd_loss_is_not_mechanical_reject": True,
            "cyclical_low_pe_is_not_mechanical_value": True,
            "missing_industry_means_watch": True,
            "funds_do_not_use_stock_financial_model": True,
            "this_stage_emits_trade_signal": False,
        },
        "summary": {
            "stock_candidates": len(stocks),
            "fund_candidates_pending_product_model": len(funds),
            "stock_status": dict(status_counts),
            "profile_counts": dict(profile_counts),
            "evidence_coverage": dict(coverage_counts),
            "financial_report_coverage": report_covered,
            "financial_report_coverage_pct": round(report_covered / len(stocks) * 100.0, 2) if stocks else 100.0,
            "industry_context_coverage": industry_covered,
            "industry_context_coverage_pct": round(industry_covered / len(stocks) * 100.0, 2) if stocks else 100.0,
            "report_errors": len(report_errors),
        },
        "stock_assessments": assessed,
        "fund_product_quality_pending": fund_pending,
        "report_errors": report_errors,
    }
    atomic_json(args.output, result)

    print("第三步3A股票状态:", dict(status_counts))
    print("第三步行业模型:", dict(profile_counts))
    print("财报覆盖:", result["summary"]["financial_report_coverage_pct"], "%")
    print("行业覆盖:", result["summary"]["industry_context_coverage_pct"], "%")
    print("ETF/LOF待产品模型:", len(funds))

    if args.strict:
        problems: list[str] = []
        if stocks and result["summary"]["financial_report_coverage_pct"] < 90:
            problems.append(f"财报覆盖不足:{result['summary']['financial_report_coverage_pct']}%")
        if stocks and result["summary"]["industry_context_coverage_pct"] < 95:
            problems.append(f"行业上下文覆盖不足:{result['summary']['industry_context_coverage_pct']}%")
        if status_counts.get("PASS", 0) + status_counts.get("WATCH", 0) < max(1, int(len(stocks) * 0.5)):
            problems.append("基本面模型异常地淘汰了过多候选")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
