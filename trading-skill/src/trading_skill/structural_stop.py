from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping


@dataclass(frozen=True, slots=True)
class StructuralStopEvidence:
    found: bool
    valid_for_new_entry: bool
    timeframe: str | None
    signal_id: str | None
    price_ticks: int | None
    tick_size: float | None
    stop_price: float | None
    latest_close: float | None
    reason: str
    purpose: str = "AUTHORITY_INVALIDATION"
    authority_signal_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _decimal(value: object) -> Decimal | None:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return result if result.is_finite() else None


def _parse_time(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _chan_row(structure_row: Mapping[str, Any], timeframe: str) -> Mapping[str, Any]:
    raw = (structure_row.get("chan") or {}).get(timeframe) or {}
    return raw if isinstance(raw, Mapping) else {}


def _signal_by_id(structure_row: Mapping[str, Any], *, timeframe: str, signal_id: str) -> Mapping[str, Any] | None:
    for signal in list(_chan_row(structure_row, timeframe).get("signals") or []):
        if str(signal.get("id") or "") == signal_id:
            return signal
    return None


def _evidence(
    *,
    found: bool,
    valid: bool,
    timeframe: str | None,
    signal_id: str | None,
    price_ticks: int | None,
    tick_decimal: Decimal | None,
    latest_close_decimal: Decimal | None,
    reason: str,
    purpose: str,
    authority_signal_id: str | None,
) -> StructuralStopEvidence:
    stop_price = float(Decimal(price_ticks) * tick_decimal) if price_ticks and tick_decimal else None
    return StructuralStopEvidence(
        found,
        valid,
        timeframe,
        signal_id,
        price_ticks,
        float(tick_decimal) if tick_decimal is not None else None,
        stop_price,
        float(latest_close_decimal) if latest_close_decimal is not None else None,
        reason,
        purpose,
        authority_signal_id,
    )


def resolve_authority_stop(
    structure_row: Mapping[str, Any],
    technical_candidate: Mapping[str, Any] | None,
) -> StructuralStopEvidence:
    """Resolve the DAILY authority invalidation level.

    New-entry authority is DAILY SECOND_BUY. This stop defines whether the core thesis
    remains valid; it is not automatically the stop used to size the initial TEST tranche.
    """
    if not isinstance(technical_candidate, Mapping) or not technical_candidate:
        return _evidence(
            found=False, valid=False, timeframe=None, signal_id=None, price_ticks=None,
            tick_decimal=None, latest_close_decimal=None, reason="没有候选日线授权信号，不生成核心结构止损",
            purpose="AUTHORITY_INVALIDATION", authority_signal_id=None,
        )

    timeframe = str(technical_candidate.get("timeframe") or "") or None
    signal_id = str(technical_candidate.get("signal_id") or "") or None
    if timeframe != "daily" or not signal_id:
        return _evidence(
            found=False, valid=False, timeframe=timeframe, signal_id=signal_id, price_ticks=None,
            tick_decimal=None, latest_close_decimal=None,
            reason="新开仓核心失效位只接受日线授权信号；120m/30m/5m不能冒充authority stop",
            purpose="AUTHORITY_INVALIDATION", authority_signal_id=signal_id,
        )

    matched = _signal_by_id(structure_row, timeframe="daily", signal_id=signal_id)
    if matched is None:
        return _evidence(
            found=False, valid=False, timeframe="daily", signal_id=signal_id, price_ticks=None,
            tick_decimal=None, latest_close_decimal=None,
            reason="STEP4B中找不到对应日线signal_id的原始结构证据",
            purpose="AUTHORITY_INVALIDATION", authority_signal_id=signal_id,
        )

    try:
        price_ticks = int(matched.get("structural_price_ticks"))
    except (TypeError, ValueError):
        price_ticks = None
    tick_decimal = _decimal(structure_row.get("tick_size"))
    latest_close_decimal = _decimal(_chan_row(structure_row, "daily").get("latest_close"))
    if not price_ticks or price_ticks <= 0 or tick_decimal is None or tick_decimal <= 0:
        return _evidence(
            found=True, valid=False, timeframe="daily", signal_id=signal_id, price_ticks=price_ticks,
            tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
            reason="日线结构失效位或最小价格单位不可用",
            purpose="AUTHORITY_INVALIDATION", authority_signal_id=signal_id,
        )
    stop_decimal = Decimal(price_ticks) * tick_decimal
    if latest_close_decimal is None or latest_close_decimal <= 0:
        return _evidence(
            found=True, valid=False, timeframe="daily", signal_id=signal_id, price_ticks=price_ticks,
            tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
            reason="缺少当前价格，无法验证日线核心失效位是否位于现价下方",
            purpose="AUTHORITY_INVALIDATION", authority_signal_id=signal_id,
        )
    if stop_decimal >= latest_close_decimal:
        return _evidence(
            found=True, valid=False, timeframe="daily", signal_id=signal_id, price_ticks=price_ticks,
            tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
            reason="日线核心失效位不低于当前价格，不能允许新开仓",
            purpose="AUTHORITY_INVALIDATION", authority_signal_id=signal_id,
        )
    return _evidence(
        found=True, valid=True, timeframe="daily", signal_id=signal_id, price_ticks=price_ticks,
        tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
        reason="日线标准二买自身structural_price_ticks作为核心交易逻辑失效位；不用于替代5分钟首笔执行止损",
        purpose="AUTHORITY_INVALIDATION", authority_signal_id=signal_id,
    )


def resolve_execution_stop(
    structure_row: Mapping[str, Any],
    technical_candidate: Mapping[str, Any] | None,
) -> StructuralStopEvidence:
    """Resolve the current 5m formal-BUY invalidation used to size the initial TEST tranche.

    The 5m signal must belong to the current DAILY structure: its confirmation must be
    on/after the authority signal's structural timestamp, and no later 5m formal SELL may
    have replaced it. A technical indicator SUPPORT state is never sufficient by itself.
    """
    authority = resolve_authority_stop(structure_row, technical_candidate)
    if not authority.valid_for_new_entry or not authority.signal_id:
        return _evidence(
            found=False, valid=False, timeframe="5m", signal_id=None, price_ticks=None,
            tick_decimal=_decimal(structure_row.get("tick_size")), latest_close_decimal=None,
            reason="日线authority stop尚未有效，不能向下解析5分钟执行止损",
            purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
        )

    daily_signal = _signal_by_id(structure_row, timeframe="daily", signal_id=authority.signal_id)
    anchor = _parse_time((daily_signal or {}).get("structural_timestamp"))
    if anchor is None:
        anchor = _parse_time((daily_signal or {}).get("confirmation_timestamp"))
    if anchor is None:
        return _evidence(
            found=False, valid=False, timeframe="5m", signal_id=None, price_ticks=None,
            tick_decimal=_decimal(structure_row.get("tick_size")), latest_close_decimal=None,
            reason="日线授权信号缺少结构时间，无法证明5分钟信号属于当前日线结构",
            purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
        )

    five = _chan_row(structure_row, "5m")
    candidates: list[tuple[datetime, Mapping[str, Any]]] = []
    sells: list[tuple[datetime, Mapping[str, Any]]] = []
    for signal in list(five.get("signals") or []):
        when = _parse_time(signal.get("confirmation_timestamp"))
        if when is None or when < anchor:
            continue
        side = str(signal.get("side") or "")
        types = set(str(item) for item in (signal.get("types") or signal.get("standard_types") or []))
        if side == "BUY" and types & {"FIRST_BUY", "SECOND_BUY", "THIRD_BUY"}:
            candidates.append((when, signal))
        elif side == "SELL" and types & {"FIRST_SELL", "SECOND_SELL", "THIRD_SELL"}:
            sells.append((when, signal))

    if not candidates:
        return _evidence(
            found=False, valid=False, timeframe="5m", signal_id=None, price_ticks=None,
            tick_decimal=_decimal(structure_row.get("tick_size")), latest_close_decimal=_decimal(five.get("latest_close")),
            reason="当前日线结构锚点之后没有正式5分钟BUY；技术指标不能替代执行买点",
            purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
        )

    buy_time, buy = max(candidates, key=lambda item: (item[0], str(item[1].get("id") or "")))
    if sells:
        sell_time, _ = max(sells, key=lambda item: (item[0], str(item[1].get("id") or "")))
        if sell_time >= buy_time:
            return _evidence(
                found=True, valid=False, timeframe="5m", signal_id=str(buy.get("id") or "") or None,
                price_ticks=None, tick_decimal=_decimal(structure_row.get("tick_size")),
                latest_close_decimal=_decimal(five.get("latest_close")),
                reason="5分钟正式BUY之后已有更新SELL，当前执行触发已失效",
                purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
            )

    try:
        structural_ticks = int(buy.get("structural_price_ticks"))
    except (TypeError, ValueError):
        structural_ticks = 0
    # Execution order is triggered by breaking the formal BUY structural low, so use one
    # minimum tick below the low as the TEST risk price. Core authority remains the daily stop.
    price_ticks = structural_ticks - 1
    tick_decimal = _decimal(structure_row.get("tick_size"))
    latest_close_decimal = _decimal(five.get("latest_close"))
    if price_ticks <= 0 or tick_decimal is None or tick_decimal <= 0:
        return _evidence(
            found=True, valid=False, timeframe="5m", signal_id=str(buy.get("id") or "") or None,
            price_ticks=price_ticks or None, tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
            reason="5分钟执行结构失效位或最小价格单位不可用",
            purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
        )
    stop_decimal = Decimal(price_ticks) * tick_decimal
    if latest_close_decimal is None or latest_close_decimal <= stop_decimal:
        return _evidence(
            found=True, valid=False, timeframe="5m", signal_id=str(buy.get("id") or "") or None,
            price_ticks=price_ticks, tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
            reason="5分钟执行止损不低于当前价格，不能用于首笔风险预算",
            purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
        )
    return _evidence(
        found=True, valid=True, timeframe="5m", signal_id=str(buy.get("id") or "") or None,
        price_ticks=price_ticks, tick_decimal=tick_decimal, latest_close_decimal=latest_close_decimal,
        reason="使用当前日线结构内最新有效5分钟正式BUY低点下一最小价位作为首笔TEST执行止损",
        purpose="EXECUTION_TEST_STOP", authority_signal_id=authority.signal_id,
    )


# Backwards-compatible API name: older callers expected one structural stop. It now
# explicitly means the DAILY authority/core-thesis stop. New sizing code must use
# resolve_execution_stop for the first TEST tranche.
def resolve_structural_stop(
    structure_row: Mapping[str, Any],
    technical_candidate: Mapping[str, Any] | None,
) -> StructuralStopEvidence:
    return resolve_authority_stop(structure_row, technical_candidate)
