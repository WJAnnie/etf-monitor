from __future__ import annotations

from dataclasses import asdict, dataclass
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

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def resolve_structural_stop(
    structure_row: Mapping[str, Any],
    technical_candidate: Mapping[str, Any] | None,
) -> StructuralStopEvidence:
    """Resolve the Chan signal's own structural invalidation price from STEP4B.

    The signal engine already stores SECOND_BUY retracement lows / THIRD_BUY return lows /
    FIRST_BUY reversal lows in structural_price_ticks. We reuse that evidence; we do not
    invent a fixed-percent, cost-basis or ATR stop here.
    """
    if not isinstance(technical_candidate, Mapping) or not technical_candidate:
        return StructuralStopEvidence(False, False, None, None, None, None, None, None, "没有候选主买点，不生成结构止损")

    timeframe = str(technical_candidate.get("timeframe") or "") or None
    signal_id = str(technical_candidate.get("signal_id") or "") or None
    if timeframe is None or signal_id is None:
        return StructuralStopEvidence(False, False, timeframe, signal_id, None, None, None, None, "候选买点缺少周期或signal_id")

    chan = (structure_row.get("chan") or {}).get(timeframe) or {}
    signals = list(chan.get("signals") or [])
    matched = next((item for item in signals if str(item.get("id") or "") == signal_id), None)
    if matched is None:
        return StructuralStopEvidence(False, False, timeframe, signal_id, None, None, None, None, "STEP4B中找不到对应signal_id的原始结构证据")

    try:
        price_ticks = int(matched.get("structural_price_ticks"))
    except (TypeError, ValueError):
        price_ticks = None
    try:
        tick_size = float(structure_row.get("tick_size"))
    except (TypeError, ValueError):
        tick_size = None
    try:
        latest_close = float(chan.get("latest_close"))
    except (TypeError, ValueError):
        latest_close = None

    if not price_ticks or price_ticks <= 0 or not tick_size or tick_size <= 0:
        return StructuralStopEvidence(True, False, timeframe, signal_id, price_ticks, tick_size, None, latest_close, "结构失效位或最小价格单位不可用")

    stop_price = price_ticks * tick_size
    if latest_close is None or latest_close <= 0:
        return StructuralStopEvidence(True, False, timeframe, signal_id, price_ticks, tick_size, stop_price, latest_close, "缺少当前价格，无法验证结构止损是否位于入场价下方")
    if stop_price >= latest_close:
        return StructuralStopEvidence(True, False, timeframe, signal_id, price_ticks, tick_size, stop_price, latest_close, "结构失效位不低于当前价格，不能用于新开仓风险预算")

    return StructuralStopEvidence(
        True,
        True,
        timeframe,
        signal_id,
        price_ticks,
        tick_size,
        stop_price,
        latest_close,
        "使用主买点自身structural_price_ticks作为同周期结构失效位；未使用固定百分比/成本价/ATR替代",
    )
