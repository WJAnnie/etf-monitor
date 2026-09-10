from __future__ import annotations

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import atomic_json
from scripts.financial_evidence_v3 import fetch_financial_evidence
from trading_skill.a_share_fundamentals import evaluate_prefilter

MAX_WORKERS = 6


def _deep_scan_eligible(prefilter) -> bool:
    return not prefilter.hard_fail and prefilter.grade in {"A", "B", "C", "D"} and prefilter.latest is not None


def enrich_candidate(item: dict, *, as_of: datetime) -> tuple[dict, dict | None]:
    code = str(item.get("code") or "")
    industry = str(item.get("industry_name") or item.get("actual_industry_name") or "")
    rows = fetch_financial_evidence(code, industry)
    prefilter = evaluate_prefilter(
        rows,
        as_of=as_of,
        pe=item.get("pe"),
        pb=item.get("pb"),
        industry_name=industry,
    )
    out = dict(item)
    fp = dict(out.get("fundamental_prefilter") or {})
    fp.update(
        {
            "eligible": prefilter.eligible,
            "deep_scan_eligible": _deep_scan_eligible(prefilter),
            "grade": prefilter.grade,
            "reasons": list(prefilter.reasons),
            "annual": asdict(prefilter.annual) if prefilter.annual else None,
            "interim": asdict(prefilter.interim) if prefilter.interim else None,
            "latest": asdict(prefilter.latest) if prefilter.latest else None,
            "industry_policy": prefilter.industry_policy,
            "valuation_focus": list(prefilter.valuation_focus),
            "metric_focus": list(prefilter.metric_focus),
            "report_focus": list(prefilter.report_focus),
            "hard_fail": prefilter.hard_fail,
            "industry_evidence_status": prefilter.industry_evidence_status,
            "industry_evidence_reasons": list(prefilter.industry_evidence_reasons),
        }
    )
    out["fundamental_prefilter"] = fp

    detail_error = None
    if rows:
        latest_raw = rows[0]
        errors = list(latest_raw.get("FINANCIAL_DETAIL_ERRORS") or [])
        if errors:
            detail_error = {
                "code": code,
                "name": item.get("name"),
                "industry": industry,
                "errors": errors,
                "status": latest_raw.get("FINANCIAL_DETAIL_STATUS"),
            }
    return out, detail_error


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("full-a-results/universe_latest.json"))
    args = parser.parse_args()

    payload = json.loads(args.universe.read_text(encoding="utf-8"))
    generated_at = str(payload.get("generated_at") or "")
    if not generated_at:
        raise SystemExit("UNIVERSE_GENERATED_AT_MISSING")
    as_of = datetime.fromisoformat(generated_at)

    candidates = list(payload.get("leader_candidates") or [])
    enriched_by_code: dict[str, dict] = {}
    detail_errors: list[dict] = []
    worker_errors: list[dict] = []

    with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, max(1, len(candidates)))) as pool:
        futures = {pool.submit(enrich_candidate, item, as_of=as_of): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            code = str(item.get("code") or "")
            try:
                enriched, detail_error = future.result()
                enriched_by_code[code] = enriched
                if detail_error:
                    detail_errors.append(detail_error)
            except Exception as exc:
                fallback = dict(item)
                fp = dict(fallback.get("fundamental_prefilter") or {})
                fp["eligible"] = False
                fp["deep_scan_eligible"] = True
                fp["hard_fail"] = False
                fp["industry_evidence_status"] = "UNAVAILABLE"
                reasons = list(fp.get("reasons") or [])
                reasons.append("行业专属财务增强失败，禁止直接新开仓，仅保留结构观察")
                fp["reasons"] = list(dict.fromkeys(reasons))
                fallback["fundamental_prefilter"] = fp
                enriched_by_code[code] = fallback
                worker_errors.append({"code": code, "name": item.get("name"), "industry": item.get("industry_name"), "error": str(exc)})

    enriched_candidates = [enriched_by_code.get(str(item.get("code") or ""), item) for item in candidates]
    payload["leader_candidates"] = enriched_candidates
    payload["fundamental_eligible_candidates"] = sum(1 for item in enriched_candidates if (item.get("fundamental_prefilter") or {}).get("eligible"))
    payload["deep_scan_eligible_candidates"] = sum(1 for item in enriched_candidates if (item.get("fundamental_prefilter") or {}).get("deep_scan_eligible"))

    status_counts: dict[str, int] = {}
    for item in enriched_candidates:
        status = str((item.get("fundamental_prefilter") or {}).get("industry_evidence_status") or "UNKNOWN")
        status_counts[status] = status_counts.get(status, 0) + 1

    payload["financial_detail_enrichment"] = {
        "candidate_count": len(candidates),
        "status_counts": status_counts,
        "detail_errors": detail_errors,
        "worker_errors": worker_errors,
    }
    payload.setdefault("guardrails", {})["industry_specific_financial_evidence_affects_permission"] = True
    payload["guardrails"]["missing_industry_financial_evidence_blocks_direct_new_entry"] = True
    payload["guardrails"]["financial_detail_failure_preserves_observation_only"] = True
    atomic_json(args.universe, payload)
    print("行业专属财务增强:", f"候选={len(candidates)}", f"状态={status_counts}", f"明细警告={len(detail_errors)}", f"任务异常={len(worker_errors)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
