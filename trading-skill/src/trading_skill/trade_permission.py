from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence

from trading_skill.technical_opportunity import TechnicalOpportunityState


class EntryMode(StrEnum):
    NONE = "NONE"
    STANDARD = "STANDARD"
    TEST = "TEST"  # historical artifact decoding only; new policy never emits it


class EventEntryState(StrEnum):
    CLEAR = "CLEAR"
    CAUTION = "CAUTION"
    BLOCK_NEW_ENTRY = "BLOCK_NEW_ENTRY"
    UNKNOWN = "UNKNOWN"


class TradePermissionState(StrEnum):
    WAIT_TECHNICAL = "WAIT_TECHNICAL"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    BLOCKED = "BLOCKED"
    ELIGIBLE = "ELIGIBLE"
    ELIGIBLE_WITH_CAUTION = "ELIGIBLE_WITH_CAUTION"
    TEST_ENTRY_ELIGIBLE = "TEST_ENTRY_ELIGIBLE"  # legacy artifact state only


class TradePermissionBlocker(StrEnum):
    QUALITY_REJECTED = "QUALITY_REJECTED"
    QUALITY_REVIEW_REQUIRED = "QUALITY_REVIEW_REQUIRED"
    EVENT_CONTEXT_UNAVAILABLE = "EVENT_CONTEXT_UNAVAILABLE"
    MAJOR_NEGATIVE_EVENT = "MAJOR_NEGATIVE_EVENT"
    STRUCTURAL_STOP_UNDEFINED = "STRUCTURAL_STOP_UNDEFINED"
    EXECUTION_STOP_UNDEFINED = "EXECUTION_STOP_UNDEFINED"
    ACCOUNT_CONTEXT_UNAVAILABLE = "ACCOUNT_CONTEXT_UNAVAILABLE"
    ACCOUNT_SECURITY_NOT_ALLOWED = "ACCOUNT_SECURITY_NOT_ALLOWED"
    PORTFOLIO_CONTEXT_UNAVAILABLE = "PORTFOLIO_CONTEXT_UNAVAILABLE"
    PORTFOLIO_RISK_FULL = "PORTFOLIO_RISK_FULL"


HARD_BLOCKERS = {
    TradePermissionBlocker.QUALITY_REJECTED,
    TradePermissionBlocker.MAJOR_NEGATIVE_EVENT,
    TradePermissionBlocker.ACCOUNT_SECURITY_NOT_ALLOWED,
    TradePermissionBlocker.PORTFOLIO_RISK_FULL,
}

CONTEXT_BLOCKERS = {
    TradePermissionBlocker.QUALITY_REVIEW_REQUIRED,
    TradePermissionBlocker.EVENT_CONTEXT_UNAVAILABLE,
    TradePermissionBlocker.STRUCTURAL_STOP_UNDEFINED,
    TradePermissionBlocker.EXECUTION_STOP_UNDEFINED,
    TradePermissionBlocker.ACCOUNT_CONTEXT_UNAVAILABLE,
    TradePermissionBlocker.PORTFOLIO_CONTEXT_UNAVAILABLE,
}

PRIMARY_ENTRY_TIMEFRAMES = {"daily"}
STANDARD_ENTRY_SIGNALS = {"SECOND_BUY"}


@dataclass(frozen=True, slots=True)
class TradePermission:
    state: TradePermissionState
    entry_mode: EntryMode
    new_entry_allowed: bool
    selected_timeframe: str | None
    signal_id: str | None
    signal_type: str | None
    technical_state: str | None
    quality_status: str
    event_state: EventEntryState
    blockers: tuple[TradePermissionBlocker, ...]
    cautions: tuple[str, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["entry_mode"] = self.entry_mode.value
        payload["event_state"] = self.event_state.value
        payload["blockers"] = [item.value for item in self.blockers]
        payload["cautions"] = list(self.cautions)
        payload["reasons"] = list(self.reasons)
        return payload


def evaluate_event_entry_state(
    events: Sequence[Mapping[str, Any]],
    *,
    data_complete: bool,
) -> EventEntryState:
    """Major positive events never create permission; major negative events may block it."""
    if not data_complete:
        return EventEntryState.UNKNOWN
    normalized = [dict(item) for item in events]
    if any(
        str(item.get("importance") or "") == "重大"
        and str(item.get("impact") or "") == "利空"
        for item in normalized
    ):
        return EventEntryState.BLOCK_NEW_ENTRY
    if any(str(item.get("impact") or "") == "利空" for item in normalized):
        return EventEntryState.CAUTION
    return EventEntryState.CLEAR


def _proposed_entry(technical_row: Mapping[str, Any]) -> tuple[EntryMode, dict[str, Any] | None]:
    executable = technical_row.get("best_executable_candidate")
    if not isinstance(executable, Mapping) or not executable:
        return EntryMode.NONE, None
    state = str(executable.get("state") or "")
    timeframe = str(executable.get("timeframe") or "")
    signal_type = str(executable.get("signal_type") or "")
    if (
        state in {
            TechnicalOpportunityState.READY.value,
            TechnicalOpportunityState.READY_WITH_CAUTION.value,
        }
        and bool(executable.get("executable_candidate"))
        and timeframe in PRIMARY_ENTRY_TIMEFRAMES
        and signal_type in STANDARD_ENTRY_SIGNALS
    ):
        return EntryMode.STANDARD, dict(executable)
    return EntryMode.NONE, None


def evaluate_trade_permission(
    technical_row: Mapping[str, Any],
    *,
    quality_status: str,
    quality_deep_analysis_eligible: bool,
    event_state: EventEntryState,
    # structural_stop_defined is retained for tests/historical callers. New callers
    # should pass authority_stop_defined + execution_stop_defined explicitly.
    structural_stop_defined: bool | None = None,
    authority_stop_defined: bool | None = None,
    execution_stop_defined: bool | None = None,
    account_context_known: bool,
    account_allows_security: bool,
    portfolio_context_known: bool,
    portfolio_allows_new_risk: bool,
) -> TradePermission:
    """STEP5A: combine independent gates without rescoring or inventing a buy point."""
    mode, candidate = _proposed_entry(technical_row)
    quality = str(quality_status or "UNKNOWN").upper()
    authority_stop_ok = (
        bool(authority_stop_defined)
        if authority_stop_defined is not None
        else bool(structural_stop_defined)
    )
    execution_stop_ok = (
        bool(execution_stop_defined)
        if execution_stop_defined is not None
        else bool(structural_stop_defined)
    )

    if candidate is None:
        dominant = technical_row.get("dominant_current_buy")
        dominant_state = str(dominant.get("state") or "") if isinstance(dominant, Mapping) else "NONE"
        return TradePermission(
            state=TradePermissionState.WAIT_TECHNICAL,
            entry_mode=EntryMode.NONE,
            new_entry_allowed=False,
            selected_timeframe=None,
            signal_id=None,
            signal_type=None,
            technical_state=dominant_state if dominant_state != "NONE" else None,
            quality_status=quality,
            event_state=event_state,
            blockers=(),
            cautions=(),
            reasons=(
                "当前没有完成周线环境 + 日线标准二买/类二买授权 + 120分钟 + 30分钟 + 5分钟执行链的候选；"
                "120分钟/30分钟买点、日线一买和日线三买均不能替代新开仓授权",
            ),
        )

    blockers: list[TradePermissionBlocker] = []
    cautions: list[str] = []
    reasons: list[str] = []

    if quality == "REJECT":
        blockers.append(TradePermissionBlocker.QUALITY_REJECTED)
        reasons.append("STEP3质量层明确REJECT，后续技术信号不得覆盖")
    elif quality != "PASS" or not quality_deep_analysis_eligible:
        blockers.append(TradePermissionBlocker.QUALITY_REVIEW_REQUIRED)
        reasons.append("STEP3尚未形成一致的PASS自动交易资格；允许继续观察，但必须补齐/复核质量上下文")

    if event_state is EventEntryState.UNKNOWN:
        blockers.append(TradePermissionBlocker.EVENT_CONTEXT_UNAVAILABLE)
        reasons.append("近期重大事件数据不可验证，不能静默当成无风险")
    elif event_state is EventEntryState.BLOCK_NEW_ENTRY:
        blockers.append(TradePermissionBlocker.MAJOR_NEGATIVE_EVENT)
        reasons.append("近期存在重大利空；暂停新开仓，保留原技术结构观察")
    elif event_state is EventEntryState.CAUTION:
        cautions.append("近期存在非硬阻断利空事件，若其他门通过也必须保留事件谨慎标签")

    if not authority_stop_ok:
        blockers.append(TradePermissionBlocker.STRUCTURAL_STOP_UNDEFINED)
        reasons.append("日线授权信号缺少真实结构失效位；禁止用固定百分比、成本价或ATR替代核心结构止损")
    if not execution_stop_ok:
        blockers.append(TradePermissionBlocker.EXECUTION_STOP_UNDEFINED)
        reasons.append("当前日线结构内没有可验证的5分钟正式BUY执行止损，首笔试仓无法计算真实单位风险")

    if not account_context_known:
        blockers.append(TradePermissionBlocker.ACCOUNT_CONTEXT_UNAVAILABLE)
        reasons.append("真实账户交易权限上下文未知")
    elif not account_allows_security:
        blockers.append(TradePermissionBlocker.ACCOUNT_SECURITY_NOT_ALLOWED)
        reasons.append("当前账户权限不允许该证券新开仓")

    if not portfolio_context_known:
        blockers.append(TradePermissionBlocker.PORTFOLIO_CONTEXT_UNAVAILABLE)
        reasons.append("组合当前持仓与剩余风险容量未知，不能假设还有空间")
    elif not portfolio_allows_new_risk:
        blockers.append(TradePermissionBlocker.PORTFOLIO_RISK_FULL)
        reasons.append("组合风险容量已满，技术机会成立也不得新增风险")

    technical_state = str(candidate.get("state") or "")
    if technical_state == TechnicalOpportunityState.READY_WITH_CAUTION.value:
        cautions.append("STEP4D周线战略环境为CAUTION；执行链成立但高周期并非完全顺风")

    blocker_set = set(blockers)
    if blocker_set & HARD_BLOCKERS:
        state = TradePermissionState.BLOCKED
    elif blocker_set & CONTEXT_BLOCKERS:
        state = TradePermissionState.CONTEXT_REQUIRED
    elif cautions:
        state = TradePermissionState.ELIGIBLE_WITH_CAUTION
    else:
        state = TradePermissionState.ELIGIBLE

    allowed = state in {
        TradePermissionState.ELIGIBLE,
        TradePermissionState.ELIGIBLE_WITH_CAUTION,
    }
    if allowed:
        reasons.append("日线二买授权、低周期执行链、双止损、质量、事件、账户权限与组合风险许可均已具备")

    return TradePermission(
        state=state,
        entry_mode=mode,
        new_entry_allowed=allowed,
        selected_timeframe=str(candidate.get("timeframe") or "") or None,
        signal_id=str(candidate.get("signal_id") or "") or None,
        signal_type=str(candidate.get("signal_type") or "") or None,
        technical_state=technical_state or None,
        quality_status=quality,
        event_state=event_state,
        blockers=tuple(dict.fromkeys(blockers)),
        cautions=tuple(dict.fromkeys(cautions)),
        reasons=tuple(dict.fromkeys(reasons)),
    )
