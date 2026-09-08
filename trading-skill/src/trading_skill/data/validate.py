from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from trading_skill.domain.bar import RawBar, ValidatedBar
from trading_skill.domain.models import ValidationResult


@dataclass(frozen=True, slots=True)
class BarValidationOutput:
    bars: tuple[ValidatedBar, ...]
    result: ValidationResult


def validate_raw_bars(raw_bars: list[RawBar] | tuple[RawBar, ...], tick_size: Decimal) -> BarValidationOutput:
    reasons: list[str] = []
    if not raw_bars:
        return BarValidationOutput((), ValidationResult(False, ("EMPTY_INPUT",)))

    sorted_bars = sorted(raw_bars, key=lambda b: b.timestamp)
    if list(raw_bars) != sorted_bars:
        reasons.append("TIMESTAMP_NOT_STRICTLY_SORTED")

    seen_ts = set()
    validated: list[ValidatedBar] = []
    prev_ts = None
    symbol = sorted_bars[0].symbol
    timeframe = sorted_bars[0].timeframe

    for bar in sorted_bars:
        if bar.symbol != symbol:
            reasons.append("SYMBOL_MISMATCH")
        if bar.timeframe != timeframe:
            reasons.append("TIMEFRAME_MISMATCH")
        if bar.timestamp.tzinfo is None or bar.timestamp.utcoffset() is None:
            reasons.append("NAIVE_TIMESTAMP")
        if bar.timestamp in seen_ts:
            reasons.append("DUPLICATE_TIMESTAMP")
        seen_ts.add(bar.timestamp)
        if prev_ts is not None and bar.timestamp <= prev_ts:
            reasons.append("TIMESTAMP_NOT_STRICTLY_INCREASING")
        prev_ts = bar.timestamp

        if bar.volume < 0 or bar.amount < 0:
            reasons.append("NEGATIVE_VOLUME_OR_AMOUNT")
        if min(bar.open, bar.high, bar.low, bar.close) <= 0:
            reasons.append("NON_POSITIVE_PRICE")
        if bar.high < max(bar.open, bar.close, bar.low):
            reasons.append("INVALID_OHLC_HIGH")
        if bar.low > min(bar.open, bar.close, bar.high):
            reasons.append("INVALID_OHLC_LOW")
        validated.append(ValidatedBar.from_raw(bar, tick_size))

    deduped = tuple(dict.fromkeys(reasons))
    return BarValidationOutput(tuple(validated), ValidationResult(not deduped, deduped))
