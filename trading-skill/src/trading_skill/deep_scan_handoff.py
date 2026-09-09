from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Iterable, Mapping


class DeepScanTier(StrEnum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"
    OBSERVE = "OBSERVE"
    EXCLUDE = "EXCLUDE"


_PRIORITY_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
_STATUS_RANK = {"PASS": 3, "WATCH": 2, "REJECT": 0}
_EVIDENCE_RANK = {"FULL": 3, "PARTIAL": 2, "ADEQUATE": 2, "LIMITED": 1}


@dataclass(frozen=True, slots=True)
class DeepScanCandidate:
    code: str
    name: str
    security_type: str
    status: str
    research_priority: str
    tier: DeepScanTier
    score: float
    reasons: tuple[str, ...]
    source_routes: tuple[str, ...]
    category: str | None = None

    def as_dict(self) -> dict:
        data = asdict(self)
        data["tier"] = self.tier.value
        return data


def _stock_candidate(row: Mapping[str, object]) -> DeepScanCandidate:
    final = row.get("final_decision") or {}
    phase_a = row.get("phase_a_assessment") or {}
    specialized = row.get("specialized_evidence") or {}
    status = str(final.get("status") or "WATCH")
    priority = str(row.get("research_priority") or "LOW")
    risk = str(phase_a.get("risk_level") or "MEDIUM")
    evidence = str(phase_a.get("evidence_coverage") or "LIMITED")
    specialized_coverage = str(specialized.get("coverage") or "")
    routes = tuple(str(x) for x in (row.get("source_routes") or ()))
    reasons: list[str] = []

    if status == "REJECT":
        return DeepScanCandidate(str(row.get("code") or ""), str(row.get("name") or ""), "STOCK", status, priority, DeepScanTier.EXCLUDE, 0.0, ("基本面REJECT，不进入重技术分析",), routes, str(row.get("industry_name") or "") or None)
    if risk == "HIGH":
        return DeepScanCandidate(str(row.get("code") or ""), str(row.get("name") or ""), "STOCK", status, priority, DeepScanTier.EXCLUDE, 0.0, ("基本面风险等级HIGH，先解决风险证据再深扫",), routes, str(row.get("industry_name") or "") or None)

    score = 35.0 * _STATUS_RANK.get(status, 1) / 3.0
    score += 25.0 * _PRIORITY_RANK.get(priority, 1) / 3.0
    score += 15.0 * _EVIDENCE_RANK.get(evidence, 1) / 3.0
    score += min(10.0, max(0, len(routes) - 1) * 4.0)
    if specialized_coverage in {"FULL", "ADEQUATE", "PARTIAL"}:
        score += 8.0
    if str(row.get("industry_context_complete")).lower() == "true" or row.get("industry_context_complete") is True:
        score += 5.0

    if status == "PASS" and priority in {"HIGH", "MEDIUM"}:
        tier = DeepScanTier.PRIMARY
        reasons.append("质量PASS且研究优先级足够高")
    elif status == "PASS":
        tier = DeepScanTier.SECONDARY
        reasons.append("质量PASS，但第二步优先级较低")
    elif status == "WATCH" and priority == "HIGH" and evidence != "LIMITED":
        tier = DeepScanTier.SECONDARY
        reasons.append("WATCH但优先级HIGH，且不是严重证据缺失")
    elif status == "WATCH" and priority in {"HIGH", "MEDIUM"}:
        tier = DeepScanTier.OBSERVE
        reasons.append("WATCH保留技术观察，只有容量允许才进入重扫")
    else:
        tier = DeepScanTier.EXCLUDE
        reasons.append("低优先级WATCH不消耗五周期重扫资源")

    return DeepScanCandidate(
        str(row.get("code") or ""),
        str(row.get("name") or ""),
        "STOCK",
        status,
        priority,
        tier,
        round(score, 2),
        tuple(reasons),
        routes,
        str(row.get("industry_name") or "") or None,
    )


def _fund_candidate(row: Mapping[str, object]) -> DeepScanCandidate:
    assessment = row.get("assessment") or {}
    status = str(assessment.get("status") or "WATCH")
    priority = str(row.get("research_priority") or "LOW")
    risk = str(assessment.get("risk_level") or "MEDIUM")
    product = str(assessment.get("product_quality") or "UNKNOWN")
    trading = str(assessment.get("trading_quality") or "UNKNOWN")
    evidence = str(assessment.get("evidence_coverage") or "LIMITED")
    category = str(row.get("fund_category") or "OTHER")
    routes = tuple(str(x) for x in (row.get("source_routes") or ()))
    deep_eligible = bool(assessment.get("deep_analysis_eligible"))
    reasons: list[str] = []

    if status == "REJECT" or not deep_eligible:
        why = "产品质量REJECT" if status == "REJECT" else "该产品默认不进入多周期重扫"
        return DeepScanCandidate(str(row.get("code") or ""), str(row.get("name") or ""), str(row.get("security_type") or "FUND"), status, priority, DeepScanTier.EXCLUDE, 0.0, (why,), routes, category)
    if risk == "HIGH" or product in {"WEAK", "UNKNOWN"} or trading in {"WEAK", "UNKNOWN"}:
        return DeepScanCandidate(str(row.get("code") or ""), str(row.get("name") or ""), str(row.get("security_type") or "FUND"), status, priority, DeepScanTier.EXCLUDE, 0.0, ("产品或交易质量风险偏高，先不消耗重扫资源",), routes, category)

    score = 35.0 * _STATUS_RANK.get(status, 1) / 3.0
    score += 25.0 * _PRIORITY_RANK.get(priority, 1) / 3.0
    score += 15.0 * _EVIDENCE_RANK.get(evidence, 1) / 3.0
    if product == "STRONG":
        score += 10.0
    elif product == "ADEQUATE":
        score += 6.0
    if trading == "STRONG":
        score += 10.0
    elif trading == "ADEQUATE":
        score += 6.0

    if status == "PASS" and priority in {"HIGH", "MEDIUM"}:
        tier = DeepScanTier.PRIMARY
        reasons.append("基金产品PASS且研究优先级足够高")
    elif status == "PASS":
        tier = DeepScanTier.SECONDARY
        reasons.append("基金产品PASS，但当前研究优先级较低")
    elif status == "WATCH" and priority == "HIGH":
        tier = DeepScanTier.SECONDARY
        reasons.append("高优先级WATCH可进入技术观察；交易前仍必须补齐风险证据")
    elif status == "WATCH" and priority == "MEDIUM":
        tier = DeepScanTier.OBSERVE
        reasons.append("中优先级WATCH仅在容量允许时深扫")
    else:
        tier = DeepScanTier.EXCLUDE
        reasons.append("低优先级WATCH暂不进入五周期重扫")

    return DeepScanCandidate(
        str(row.get("code") or ""),
        str(row.get("name") or ""),
        str(row.get("security_type") or "FUND"),
        status,
        priority,
        tier,
        round(score, 2),
        tuple(reasons),
        routes,
        category,
    )


def build_deep_scan_queue(
    stock_rows: Iterable[Mapping[str, object]],
    fund_rows: Iterable[Mapping[str, object]],
    *,
    capacity_max: int = 150,
    soft_target_min: int = 80,
) -> tuple[DeepScanCandidate, ...]:
    """Build the STEP3->STEP4 queue.

    The range is a resource target, not a quota. Weak WATCH names are never added
    merely to reach ``soft_target_min``. PRIMARY comes first, then SECONDARY; OBSERVE
    is used only when the queue is still below the soft target and the candidate is
    otherwise safe enough for technical observation.
    """
    material = [_stock_candidate(x) for x in stock_rows] + [_fund_candidate(x) for x in fund_rows]
    eligible = [x for x in material if x.tier is not DeepScanTier.EXCLUDE]
    tier_rank = {DeepScanTier.PRIMARY: 0, DeepScanTier.SECONDARY: 1, DeepScanTier.OBSERVE: 2}
    eligible.sort(key=lambda x: (tier_rank[x.tier], -x.score, x.code))

    selected: list[DeepScanCandidate] = []
    for item in eligible:
        if len(selected) >= capacity_max:
            break
        if item.tier is DeepScanTier.OBSERVE and len(selected) >= soft_target_min:
            continue
        selected.append(item)
    return tuple(selected)
