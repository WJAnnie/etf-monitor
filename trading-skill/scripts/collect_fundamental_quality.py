from __future__ import annotations

import argparse
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import CN_TZ, MAX_WORKERS, atomic_json, fetch_financial_reports
from scripts.financial_data_adapter import fetch_detailed_statement_metrics, fetch_main_financial_data
from trading_skill.fundamental_decision import combine_fundamental_decision
from trading_skill.fundamental_quality import (
    CompanyQuality,
    EvidenceCoverage,
    FundamentalStatus,
    GrowthState,
    RiskLevel,
    StockFundamentalAssessment,
    ValuationState,
    assess_stock_fundamentals,
)
from trading_skill.industry_fundamental_evidence import (
    EvidenceFamily,
    SpecializedCoverage,
    SpecializedEvidenceAssessment,
    SpecializedQuality,
    assess_specialized_evidence,
    evidence_family,
)


SPECIALIZED_REQUIRED_FAMILIES = {
    EvidenceFamily.FINANCIAL,
    EvidenceFamily.ORDER_MANUFACTURING,
    EvidenceFamily.RND,
    EvidenceFamily.CYCLICAL,
    EvidenceFamily.CONSUMER_CASHFLOW,
}


def _unresolved_phase_a() -> StockFundamentalAssessment:
    return StockFundamentalAssessment(
        status=FundamentalStatus.WATCH,
        profile="UNRESOLVED_INDUSTRY",
        company_quality=CompanyQuality.UNKNOWN,
        growth_state=GrowthState.UNKNOWN,
        valuation_state=ValuationState.UNKNOWN,
        risk_level=RiskLevel.MEDIUM,
        evidence_coverage=EvidenceCoverage.LIMITED,
        latest_period=None,
        previous_period=None,
        visible_period_count=0,
        positive_evidence=(),
        warnings=("真实行业尚未解析，禁止套用通用行业模型形成PASS结论",),
        hard_risks=(),
        followups=("补全真实行业后再运行行业化基本面",),
        rationale=("行业上下文缺失：仅允许WATCH",),
    )


def _empty_specialized(profile: str) -> SpecializedEvidenceAssessment:
    return assess_specialized_evidence(profile, main_financial_rows=(), detailed_metrics=None)


def _fetch_phase_a_reports(stocks: list[dict]) -> tuple[dict[str, list[dict]], list[dict]]:
    reports_by_code: dict[str, list[dict]] = {}
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(stocks)))) as pool:
        futures = {pool.submit(fetch_financial_reports, str(item.get("code"))): item for item in stocks}
        for future in as_completed(futures):
            item = futures[future]
            code = str(item.get("code") or "")
            try:
                reports_by_code[code] = future.result()
            except Exception as exc:
                reports_by_code[code] = []
                errors.append({"code": code, "name": item.get("name"), "stage": "PHASE_A_REPORT", "error": str(exc)[:500]})
    return reports_by_code, errors


def _phase_a_assessments(stocks: list[dict], reports_by_code: dict[str, list[dict]], now: datetime) -> dict[str, StockFundamentalAssessment]:
    out: dict[str, StockFundamentalAssessment] = {}
    for item in stocks:
        code = str(item.get("code") or "")
        industry_name = str(item.get("fundamental_industry_name") or item.get("industry_name") or "").strip()
        if not industry_name:
            out[code] = _unresolved_phase_a()
            continue
        out[code] = assess_stock_fundamentals(
            reports_by_code.get(code, []),
            as_of=now,
            industry_name=industry_name,
            prospect_theme=item.get("prospect_theme"),
            pe=item.get("valuation_pe"),
            pb=item.get("valuation_pb"),
            detailed_metrics=None,
            industry_state=item.get("industry_state"),
        )
    return out


def _needs_network_3b(item: dict, phase_a: StockFundamentalAssessment) -> bool:
    if phase_a.status is FundamentalStatus.REJECT or phase_a.profile == "UNRESOLVED_INDUSTRY":
        return False
    family = evidence_family(phase_a.profile)
    if family is EvidenceFamily.FINANCIAL:
        return True
    if family not in SPECIALIZED_REQUIRED_FAMILIES:
        return False
    # 第二步优先级低的标的先保留WATCH，不为凑结论大量抓两张报表。
    # PASS若属于专属行业仍补3B，避免通用底座漏掉行业风险。
    return phase_a.status is FundamentalStatus.PASS or str(item.get("research_priority") or "") in {"HIGH", "MEDIUM"}


def _fetch_specialized_inputs(
    stocks: list[dict],
    phase_a_by_code: dict[str, StockFundamentalAssessment],
) -> tuple[dict[str, list[dict]], dict[str, dict], list[dict], int]:
    financial_rows: dict[str, list[dict]] = {}
    detailed_metrics: dict[str, dict] = {}
    errors: list[dict] = []
    targets = [item for item in stocks if _needs_network_3b(item, phase_a_by_code[str(item.get("code") or "")])]

    def task(item: dict) -> tuple[str, str, object]:
        code = str(item.get("code") or "")
        phase_a = phase_a_by_code[code]
        family = evidence_family(phase_a.profile)
        if family is EvidenceFamily.FINANCIAL:
            rows, source = fetch_main_financial_data(code, str(item.get("board") or ""))
            return code, "MAIN_FINANCIAL", {"rows": rows, "source": source}
        target_date = phase_a.latest_period.report_date if phase_a.latest_period else None
        metrics = fetch_detailed_statement_metrics(code, target_report_date=target_date)
        return code, "DETAILED_STATEMENTS", metrics

    with ThreadPoolExecutor(max_workers=min(6, max(1, len(targets)))) as pool:
        futures = {pool.submit(task, item): item for item in targets}
        for future in as_completed(futures):
            item = futures[future]
            code = str(item.get("code") or "")
            try:
                result_code, kind, payload = future.result()
                if kind == "MAIN_FINANCIAL":
                    financial_rows[result_code] = list(payload.get("rows") or [])
                else:
                    detailed_metrics[result_code] = dict(payload or {})
            except Exception as exc:
                errors.append({"code": code, "name": item.get("name"), "stage": "PHASE_B_SPECIALIZED", "error": str(exc)[:500]})
    return financial_rows, detailed_metrics, errors, len(targets)


def _specialized_for(
    item: dict,
    phase_a: StockFundamentalAssessment,
    financial_rows: dict[str, list[dict]],
    detailed_metrics: dict[str, dict],
) -> SpecializedEvidenceAssessment | None:
    if phase_a.status is FundamentalStatus.REJECT or phase_a.profile == "UNRESOLVED_INDUSTRY":
        return None
    family = evidence_family(phase_a.profile)
    if family not in SPECIALIZED_REQUIRED_FAMILIES:
        return None
    code = str(item.get("code") or "")
    if family is EvidenceFamily.FINANCIAL:
        return assess_specialized_evidence(phase_a.profile, main_financial_rows=financial_rows.get(code, ()))
    return assess_specialized_evidence(phase_a.profile, detailed_metrics=detailed_metrics.get(code))


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

    reports_by_code, phase_a_errors = _fetch_phase_a_reports(stocks)
    phase_a_by_code = _phase_a_assessments(stocks, reports_by_code, now)
    financial_rows, detailed_metrics, phase_b_errors, specialized_requested = _fetch_specialized_inputs(stocks, phase_a_by_code)

    assessed: list[dict] = []
    upgraded = 0
    downgraded = 0
    hard_rejects = 0
    specialized_completed = 0
    for item in stocks:
        code = str(item.get("code") or "")
        industry_name = str(item.get("fundamental_industry_name") or item.get("industry_name") or "").strip()
        phase_a = phase_a_by_code[code]
        specialized = _specialized_for(item, phase_a, financial_rows, detailed_metrics)
        if specialized is not None and specialized.coverage is not SpecializedCoverage.LIMITED:
            specialized_completed += 1
        final = combine_fundamental_decision(
            phase_a,
            specialized,
            industry_context_complete=bool(industry_name),
        )
        if phase_a.status is FundamentalStatus.WATCH and final.status is FundamentalStatus.PASS:
            upgraded += 1
        if phase_a.status is FundamentalStatus.PASS and final.status is FundamentalStatus.WATCH:
            downgraded += 1
        if final.status is FundamentalStatus.REJECT and phase_a.status is not FundamentalStatus.REJECT:
            hard_rejects += 1

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
            "phase_a_assessment": phase_a.as_dict(),
            "specialized_evidence": specialized.as_dict() if specialized else None,
            "final_decision": final.as_dict(),
        })

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

    phase_a_counts = Counter(row["phase_a_assessment"]["status"] for row in assessed)
    final_counts = Counter(row["final_decision"]["status"] for row in assessed)
    profile_counts = Counter(row["phase_a_assessment"]["profile"] for row in assessed)
    phase_a_coverage = Counter(row["phase_a_assessment"]["evidence_coverage"] for row in assessed)
    specialized_family_counts = Counter(
        row["specialized_evidence"]["family"] for row in assessed if row["specialized_evidence"]
    )
    specialized_coverage_counts = Counter(
        row["specialized_evidence"]["coverage"] for row in assessed if row["specialized_evidence"]
    )
    report_covered = sum(1 for item in stocks if reports_by_code.get(str(item.get("code") or "")))
    industry_covered = sum(1 for row in assessed if row["industry_context_complete"])
    specialized_fetch_success_pct = round(specialized_completed / specialized_requested * 100.0, 2) if specialized_requested else 100.0

    result = {
        "mode": "STEP3_FUNDAMENTAL_QUALITY_PHASE_AB",
        "generated_at": now.isoformat(),
        "source_candidate_file": str(args.input),
        "design_contract": {
            "valuation_is_not_standalone_veto": True,
            "different_industries_use_different_profiles": True,
            "financial_companies_require_specialized_metrics": True,
            "rnd_loss_is_not_mechanical_reject": True,
            "cyclical_low_pe_is_not_mechanical_value": True,
            "missing_industry_means_watch": True,
            "limited_evidence_cannot_pass": True,
            "phase_a_reject_cannot_be_overridden": True,
            "specialized_hard_risk_can_reject": True,
            "rnd_and_cyclical_require_external_core_evidence_before_pass": True,
            "funds_do_not_use_stock_financial_model": True,
            "this_stage_emits_trade_signal": False,
        },
        "summary": {
            "stock_candidates": len(stocks),
            "fund_candidates_pending_product_model": len(funds),
            "phase_a_status": dict(phase_a_counts),
            "final_stock_status": dict(final_counts),
            "profile_counts": dict(profile_counts),
            "phase_a_evidence_coverage": dict(phase_a_coverage),
            "specialized_family_counts": dict(specialized_family_counts),
            "specialized_evidence_coverage": dict(specialized_coverage_counts),
            "financial_report_coverage": report_covered,
            "financial_report_coverage_pct": round(report_covered / len(stocks) * 100.0, 2) if stocks else 100.0,
            "industry_context_coverage": industry_covered,
            "industry_context_coverage_pct": round(industry_covered / len(stocks) * 100.0, 2) if stocks else 100.0,
            "specialized_fetch_requested": specialized_requested,
            "specialized_fetch_sufficient": specialized_completed,
            "specialized_fetch_sufficient_pct": specialized_fetch_success_pct,
            "watch_upgraded_to_pass": upgraded,
            "pass_downgraded_to_watch": downgraded,
            "specialized_hard_risk_rejects": hard_rejects,
            "phase_a_errors": len(phase_a_errors),
            "phase_b_errors": len(phase_b_errors),
        },
        "stock_assessments": assessed,
        "fund_product_quality_pending": fund_pending,
        "data_errors": phase_a_errors + phase_b_errors,
    }
    atomic_json(args.output, result)

    print("第三步3A股票状态:", dict(phase_a_counts))
    print("第三步最终股票状态:", dict(final_counts))
    print("第三步行业模型:", dict(profile_counts))
    print("3B证据家族:", dict(specialized_family_counts))
    print("财报覆盖:", result["summary"]["financial_report_coverage_pct"], "%")
    print("行业覆盖:", result["summary"]["industry_context_coverage_pct"], "%")
    print("3B充分证据:", specialized_completed, "/", specialized_requested, f"({specialized_fetch_success_pct}%)")
    print("WATCH升级PASS:", upgraded, "PASS降WATCH:", downgraded, "3B硬风险REJECT:", hard_rejects)
    print("ETF/LOF待产品模型:", len(funds))

    if args.strict:
        problems: list[str] = []
        if stocks and result["summary"]["financial_report_coverage_pct"] < 90:
            problems.append(f"财报覆盖不足:{result['summary']['financial_report_coverage_pct']}%")
        if stocks and result["summary"]["industry_context_coverage_pct"] < 95:
            problems.append(f"行业上下文覆盖不足:{result['summary']['industry_context_coverage_pct']}%")
        if specialized_requested >= 10 and specialized_fetch_success_pct < 70:
            problems.append(f"3B专属证据充分覆盖不足:{specialized_fetch_success_pct}%")
        if final_counts.get("PASS", 0) + final_counts.get("WATCH", 0) < max(1, int(len(stocks) * 0.5)):
            problems.append("基本面模型异常地淘汰了过多候选")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
