from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import CN_TZ, atomic_json
from scripts.fund_reference_adapter import fetch_fund_scale_references
from trading_skill.fund_product_quality import assess_fund_product


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=Path("full-a-results/candidate_universe_latest.json"))
    parser.add_argument("--quality", type=Path, default=Path("full-a-results/fundamental_quality_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    candidate_payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    quality_payload = json.loads(args.quality.read_text(encoding="utf-8"))
    candidates = list(candidate_payload.get("candidates") or [])
    selected_industries = list(candidate_payload.get("selected_industries") or [])
    fund_candidates = [row for row in candidates if row.get("security_type") in {"ETF", "LOF", "FUND"}]
    candidate_map = {
        (str(row.get("code") or ""), str(row.get("security_type") or "")): row
        for row in fund_candidates
    }

    references, reference_errors = fetch_fund_scale_references(str(row.get("code") or "") for row in fund_candidates)
    assessed: list[dict] = []
    for old in list(quality_payload.get("fund_product_assessments") or []):
        key = (str(old.get("code") or ""), str(old.get("security_type") or ""))
        item = candidate_map.get(key)
        if item is None:
            # Do not invent a replacement candidate when the source chain is inconsistent.
            assessed.append(old)
            continue
        reference = dict(references.get(key[0]) or {})
        assessment = assess_fund_product(
            item,
            selected_industries=selected_industries,
            reference=reference,
        )
        row = dict(old)
        row["assessment"] = assessment.as_dict()
        row["product_reference"] = reference or None
        assessed.append(row)

    status_counts = Counter(row["assessment"]["status"] for row in assessed)
    coverage_counts = Counter(row["assessment"]["evidence_coverage"] for row in assessed)
    size_covered = sum(1 for row in assessed if (row.get("product_reference") or {}).get("fund_size_cny") is not None)
    size_coverage_pct = round(size_covered / len(assessed) * 100.0, 2) if assessed else 100.0
    fund_deep_eligible = sum(1 for row in assessed if row["assessment"].get("deep_analysis_eligible"))
    stock_deep_eligible = sum(
        1 for row in quality_payload.get("stock_assessments", [])
        if (row.get("final_decision") or {}).get("deep_analysis_eligible")
    )

    summary = dict(quality_payload.get("summary") or {})
    summary.update({
        "fund_product_status": dict(status_counts),
        "fund_product_evidence_coverage": dict(coverage_counts),
        "fund_size_reference_covered": size_covered,
        "fund_size_reference_coverage_pct": size_coverage_pct,
        "fund_reference_errors": len(reference_errors),
        "fund_deep_analysis_eligible": fund_deep_eligible,
        "total_deep_analysis_eligible": stock_deep_eligible + fund_deep_eligible,
    })

    quality_payload["mode"] = "STEP3_SECURITY_QUALITY_WITH_FUND_REFERENCE"
    quality_payload["fund_reference_enrichment"] = {
        "generated_at": datetime.now(CN_TZ).isoformat(),
        "source": "新浪基金规模批量接口",
        "requested": len(fund_candidates),
        "covered": size_covered,
        "coverage_pct": size_coverage_pct,
        "batch_request_count": 5,
        "errors": reference_errors,
        "design": "基金规模属于慢变量证据；批量采集，不对每只基金逐一请求F10。缺失保持缺失，不使用初始募集规模伪造当前规模。",
    }
    quality_payload["summary"] = summary
    quality_payload["fund_product_assessments"] = assessed
    data_errors = list(quality_payload.get("data_errors") or [])
    data_errors.extend({"stage": "FUND_REFERENCE", "error": error[:500]} for error in reference_errors)
    quality_payload["data_errors"] = data_errors
    atomic_json(args.quality, quality_payload)

    print("STEP3C基金规模参考:", size_covered, "/", len(assessed), f"({size_coverage_pct}%)")
    print("STEP3C基金状态:", dict(status_counts))
    if reference_errors:
        print("STEP3C参考源异常:", reference_errors)

    if args.strict:
        problems: list[str] = []
        if len(assessed) != len(fund_candidates):
            problems.append(f"基金评估链数量不一致:{len(assessed)}!={len(fund_candidates)}")
        if len(assessed) >= 20 and size_coverage_pct < 50:
            problems.append(f"基金规模批量参考覆盖不足:{size_coverage_pct}%")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
