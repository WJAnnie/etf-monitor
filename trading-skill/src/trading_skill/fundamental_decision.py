from __future__ import annotations

from dataclasses import asdict, dataclass

from trading_skill.fundamental_quality import (
    CompanyQuality,
    EvidenceCoverage,
    FundamentalStatus,
    RiskLevel,
    StockFundamentalAssessment,
)
from trading_skill.industry_fundamental_evidence import (
    EvidenceFamily,
    SpecializedCoverage,
    SpecializedEvidenceAssessment,
    SpecializedQuality,
)


@dataclass(frozen=True, slots=True)
class FinalFundamentalDecision:
    status: FundamentalStatus
    phase_a_status: FundamentalStatus
    specialized_family: EvidenceFamily | None
    specialized_quality: SpecializedQuality | None
    specialized_coverage: SpecializedCoverage | None
    reasons: tuple[str, ...]
    deep_analysis_eligible: bool

    def as_dict(self) -> dict:
        data = asdict(self)
        data["status"] = self.status.value
        data["phase_a_status"] = self.phase_a_status.value
        data["specialized_family"] = self.specialized_family.value if self.specialized_family else None
        data["specialized_quality"] = self.specialized_quality.value if self.specialized_quality else None
        data["specialized_coverage"] = self.specialized_coverage.value if self.specialized_coverage else None
        return data


def combine_fundamental_decision(
    phase_a: StockFundamentalAssessment,
    specialized: SpecializedEvidenceAssessment | None,
    *,
    industry_context_complete: bool = True,
) -> FinalFundamentalDecision:
    reasons: list[str] = []

    if not industry_context_complete:
        status = FundamentalStatus.WATCH
        reasons.append("真实行业上下文不完整，只能WATCH")
        return FinalFundamentalDecision(status, phase_a.status, None, None, None, tuple(reasons), True)

    if phase_a.status is FundamentalStatus.REJECT:
        reasons.append("3A发现明确财务硬伤，行业插件不得覆盖REJECT")
        reasons.extend(phase_a.hard_risks)
        return FinalFundamentalDecision(FundamentalStatus.REJECT, phase_a.status, None, None, None, tuple(dict.fromkeys(reasons)), False)

    if specialized is not None and specialized.hard_risks:
        reasons.append("3B行业专属证据发现硬风险")
        reasons.extend(specialized.hard_risks)
        return FinalFundamentalDecision(
            FundamentalStatus.REJECT,
            phase_a.status,
            specialized.family,
            specialized.quality,
            specialized.coverage,
            tuple(dict.fromkeys(reasons)),
            False,
        )

    family = specialized.family if specialized else None

    # 金融、订单制造、研发、周期、消费这些行业必须有3B证据；不能只用通用报表宣布PASS。
    specialized_required = family in {
        EvidenceFamily.FINANCIAL,
        EvidenceFamily.ORDER_MANUFACTURING,
        EvidenceFamily.RND,
        EvidenceFamily.CYCLICAL,
        EvidenceFamily.CONSUMER_CASHFLOW,
    }

    if specialized is None:
        if phase_a.status is FundamentalStatus.PASS:
            # GENERAL画像可由通用底座完成；行业专属画像应在采集器侧传入3B。
            if phase_a.evidence_coverage is EvidenceCoverage.LIMITED:
                reasons.append("通用证据覆盖有限，不能PASS")
                status = FundamentalStatus.WATCH
            else:
                reasons.append("通用画像3A证据充分，暂保留PASS")
                status = FundamentalStatus.PASS
        else:
            reasons.append("3A仍为WATCH且没有足够3B证据")
            status = FundamentalStatus.WATCH
        return FinalFundamentalDecision(status, phase_a.status, None, None, None, tuple(reasons), status is not FundamentalStatus.REJECT)

    if specialized_required and specialized.coverage is SpecializedCoverage.LIMITED:
        reasons.append("行业专属证据覆盖有限，不能PASS")
        reasons.extend(specialized.missing_evidence[:4])
        return FinalFundamentalDecision(
            FundamentalStatus.WATCH,
            phase_a.status,
            specialized.family,
            specialized.quality,
            specialized.coverage,
            tuple(dict.fromkeys(reasons)),
            True,
        )

    # 研发型与周期型的核心变量存在于报表之外。当前3B只拿到会计代理证据时必须继续WATCH。
    if specialized.family in {EvidenceFamily.RND, EvidenceFamily.CYCLICAL}:
        reasons.append("该行业核心变量仍依赖报表外证据，当前不能升级为PASS")
        reasons.extend(specialized.missing_evidence[:4])
        return FinalFundamentalDecision(
            FundamentalStatus.WATCH,
            phase_a.status,
            specialized.family,
            specialized.quality,
            specialized.coverage,
            tuple(dict.fromkeys(reasons)),
            True,
        )

    if specialized.quality is SpecializedQuality.WEAK:
        reasons.append("行业专属证据偏弱，降为WATCH")
        reasons.extend(specialized.warnings[:4])
        return FinalFundamentalDecision(
            FundamentalStatus.WATCH,
            phase_a.status,
            specialized.family,
            specialized.quality,
            specialized.coverage,
            tuple(dict.fromkeys(reasons)),
            True,
        )

    if phase_a.risk_level is RiskLevel.HIGH:
        reasons.append("3A风险等级仍高，即使3B存在正向证据也不升级")
        return FinalFundamentalDecision(
            FundamentalStatus.WATCH,
            phase_a.status,
            specialized.family,
            specialized.quality,
            specialized.coverage,
            tuple(reasons),
            True,
        )

    if phase_a.status is FundamentalStatus.PASS:
        if specialized.quality in {SpecializedQuality.STRONG, SpecializedQuality.ADEQUATE}:
            reasons.append("3A通过且行业专属证据未发现矛盾")
            status = FundamentalStatus.PASS
        else:
            reasons.append("3B证据不足以确认行业质量")
            status = FundamentalStatus.WATCH
    else:
        financial_exception = specialized.family is EvidenceFamily.FINANCIAL
        phase_a_quality_ok = phase_a.company_quality not in {CompanyQuality.WEAK, CompanyQuality.UNKNOWN} or financial_exception
        if specialized.can_upgrade_watch and phase_a_quality_ok:
            reasons.append("3B行业专属证据补齐了3A通用模型的缺口，WATCH升级为PASS")
            status = FundamentalStatus.PASS
        else:
            reasons.append("3B不足以覆盖3A的WATCH原因")
            status = FundamentalStatus.WATCH

    reasons.extend(specialized.positive_evidence[:4] if status is FundamentalStatus.PASS else specialized.warnings[:4])
    return FinalFundamentalDecision(
        status,
        phase_a.status,
        specialized.family,
        specialized.quality,
        specialized.coverage,
        tuple(dict.fromkeys(reasons)),
        status is not FundamentalStatus.REJECT,
    )
