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
_EVIDENCE_RANK = {"FULL": 3, "PARTIAL": 2, "ADEQUATE": 2, "LIMITED": 1, "": 0}
_QUALITY_RANK = {"STRONG": 3, "ADEQUATE": 2, "WEAK": 1, "UNKNOWN": 0, "": 0}


@dataclass(frozen=True, slots=True)
class DeepScanCandidate:
    code: str
    name: str
    security_type: str
    status: str
    research_priority: str
    tier: DeepScanTier
    evidence_level: str
    confirmation_count: int
    reasons: tuple[str, ...]
    source_routes: tuple[str, ...]
    category: str | None = None
    product_quality: str | None = None
    trading_quality: str | None = None

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
    base_evidence = str(phase_a.get("evidence_coverage") or "LIMITED")
    specialized_coverage = str(specialized.get("coverage") or "")
    evidence = specialized_coverage or base_evidence
    routes = tuple(str(x) for x in (row.get("source_routes") or ()))
    reasons: list[str] = []

    if status == "REJECT":
        tier = DeepScanTier.EXCLUDE
        reasons.append("基本面REJECT，不进入重技术分析")
    elif risk == "HIGH":
        tier = DeepScanTier.EXCLUDE
        reasons.append("基本面风险等级HIGH，先解决风险证据再深扫")
    elif status == "PASS" and priority in {"HIGH", "MEDIUM"}:
        tier = DeepScanTier.PRIMARY
        reasons.append("质量PASS且研究优先级足够高")
    elif status == "PASS":
        tier = DeepScanTier.SECONDARY
        reasons.append("质量PASS，但第二步优先级较低")
    elif status == "WATCH" and priority == "HIGH" and base_evidence != "LIMITED":
        tier = DeepScanTier.SECONDARY
        reasons.append("WATCH但优先级HIGH，且不是严重证据缺失")
    elif status == "WATCH" and priority in {"HIGH", "MEDIUM"}:
        tier = DeepScanTier.OBSERVE
        reasons.append("WATCH保留技术观察，只有容量允许才进入重扫")
    else:
        tier = DeepScanTier.EXCLUDE
        reasons.append("低优先级WATCH不消耗五周期重扫资源")

    return DeepScanCandidate(
        code=str(row.get("code") or ""),
        name=str(row.get("name") or ""),
        security_type="STOCK",
        status=status,
        research_priority=priority,
        tier=tier,
        evidence_level=evidence,
        confirmation_count=len(routes),
        reasons=tuple(reasons),
        source_routes=routes,
        category=str(row.get("industry_name") or "") or None,
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

    if category == "OTHER":
        tier = DeepScanTier.EXCLUDE
        reasons.append("基金底层资产类别尚未可靠解析，先隔离，不进入多周期重扫")
    elif status == "REJECT" or not deep_eligible:
        tier = DeepScanTier.EXCLUDE
        reasons.append("产品质量REJECT" if status == "REJECT" else "该产品默认不进入多周期重扫")
    elif risk == "HIGH" or product in {"WEAK", "UNKNOWN"} or trading in {"WEAK", "UNKNOWN"}:
        tier = DeepScanTier.EXCLUDE
        reasons.append("产品或交易质量风险偏高，先不消耗重扫资源")
    elif status == "PASS" and priority in {"HIGH", "MEDIUM"}:
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
        code=str(row.get("code") or ""),
        name=str(row.get("name") or ""),
        security_type=str(row.get("security_type") or "FUND"),
        status=status,
        research_priority=priority,
        tier=tier,
        evidence_level=evidence,
        confirmation_count=len(routes),
        reasons=tuple(reasons),
        source_routes=routes,
        category=category,
        product_quality=product,
        trading_quality=trading,
    )


def _rank_key(item: DeepScanCandidate) -> tuple:
    """Explainable lexicographic ranking; no synthetic weighted score."""
    tier_rank = {
        DeepScanTier.PRIMARY: 0,
        DeepScanTier.SECONDARY: 1,
        DeepScanTier.OBSERVE: 2,
        DeepScanTier.EXCLUDE: 3,
    }
    return (
        tier_rank[item.tier],
        -_STATUS_RANK.get(item.status, 0),
        -_PRIORITY_RANK.get(item.research_priority, 0),
        -_EVIDENCE_RANK.get(item.evidence_level, 0),
        -_QUALITY_RANK.get(item.product_quality or "", 0),
        -_QUALITY_RANK.get(item.trading_quality or "", 0),
        -item.confirmation_count,
        item.code,
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
    eligible.sort(key=_rank_key)

    selected: list[DeepScanCandidate] = []
    for item in eligible:
        if len(selected) >= capacity_max:
            break
        if item.tier is DeepScanTier.OBSERVE and len(selected) >= soft_target_min:
            continue
        selected.append(item)
    return tuple(selected)
