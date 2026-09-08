from __future__ import annotations

from dataclasses import dataclass, replace

from trading_skill.domain.bar import ProcessedBar, ValidatedBar
from trading_skill.domain.enums import Direction
from trading_skill.domain.models import ValidationResult, stable_id, unique_preserve_order


@dataclass(frozen=True, slots=True)
class InclusionOutput:
    bars: tuple[ProcessedBar, ...]
    result: ValidationResult


def _inclusive(a_low: int, a_high: int, b_low: int, b_high: int) -> bool:
    return (a_low <= b_low and a_high >= b_high) or (b_low <= a_low and b_high >= a_high)


def _direction_between(a_low: int, a_high: int, b_low: int, b_high: int) -> Direction:
    if b_high > a_high and b_low > a_low:
        return Direction.UP
    if b_high < a_high and b_low < a_low:
        return Direction.DOWN
    return Direction.UNRESOLVED


def _initial_direction(bars: tuple[ValidatedBar, ...]) -> Direction:
    for left, right in zip(bars, bars[1:]):
        if _inclusive(left.low_ticks, left.high_ticks, right.low_ticks, right.high_ticks):
            continue
        direction = _direction_between(left.low_ticks, left.high_ticks, right.low_ticks, right.high_ticks)
        if direction is not Direction.UNRESOLVED:
            return direction
    return Direction.UNRESOLVED


def _from_validated(bar: ValidatedBar, direction: Direction) -> ProcessedBar:
    return ProcessedBar(
        id=stable_id("pb", bar.id),
        symbol=bar.raw.symbol,
        timeframe=bar.raw.timeframe,
        start_timestamp=bar.raw.timestamp,
        end_timestamp=bar.raw.timestamp,
        open_ticks=bar.open_ticks,
        high_ticks=bar.high_ticks,
        low_ticks=bar.low_ticks,
        close_ticks=bar.close_ticks,
        volume=bar.raw.volume,
        amount=bar.raw.amount,
        raw_bar_count=1,
        source_raw_bar_ids=(bar.id,),
        extreme_high_raw_bar_ids=(bar.id,),
        extreme_low_raw_bar_ids=(bar.id,),
        direction_context=direction,
        is_complete=bar.raw.is_complete,
    )


def _merge(left: ProcessedBar, right: ProcessedBar, direction: Direction) -> ProcessedBar:
    if direction is Direction.UP:
        high = max(left.high_ticks, right.high_ticks)
        low = max(left.low_ticks, right.low_ticks)
    elif direction is Direction.DOWN:
        high = min(left.high_ticks, right.high_ticks)
        low = min(left.low_ticks, right.low_ticks)
    else:
        raise ValueError("cannot merge inclusion with unresolved direction")

    high_ids: tuple[str, ...] = ()
    low_ids: tuple[str, ...] = ()
    if left.high_ticks == high:
        high_ids += left.extreme_high_raw_bar_ids
    if right.high_ticks == high:
        high_ids += right.extreme_high_raw_bar_ids
    if left.low_ticks == low:
        low_ids += left.extreme_low_raw_bar_ids
    if right.low_ticks == low:
        low_ids += right.extreme_low_raw_bar_ids

    source_ids = unique_preserve_order((*left.source_raw_bar_ids, *right.source_raw_bar_ids))
    return ProcessedBar(
        id=stable_id("pb", *source_ids, direction),
        symbol=left.symbol,
        timeframe=left.timeframe,
        start_timestamp=left.start_timestamp,
        end_timestamp=right.end_timestamp,
        open_ticks=left.open_ticks,
        high_ticks=high,
        low_ticks=low,
        close_ticks=right.close_ticks,
        volume=left.volume + right.volume,
        amount=left.amount + right.amount,
        raw_bar_count=left.raw_bar_count + right.raw_bar_count,
        source_raw_bar_ids=source_ids,
        extreme_high_raw_bar_ids=unique_preserve_order(high_ids),
        extreme_low_raw_bar_ids=unique_preserve_order(low_ids),
        direction_context=direction,
        is_complete=left.is_complete and right.is_complete,
    )


def process_inclusions(validated_bars: tuple[ValidatedBar, ...]) -> InclusionOutput:
    if not validated_bars:
        return InclusionOutput((), ValidationResult(False, ("EMPTY_INPUT",)))

    direction = _initial_direction(validated_bars)
    if direction is Direction.UNRESOLVED:
        return InclusionOutput((), ValidationResult(False, ("DIRECTION_UNRESOLVED",)))

    processed: list[ProcessedBar] = []
    for vb in validated_bars:
        current = _from_validated(vb, direction)
        if not processed:
            processed.append(current)
            continue

        prev = processed[-1]
        if _inclusive(prev.low_ticks, prev.high_ticks, current.low_ticks, current.high_ticks):
            processed[-1] = _merge(prev, current, direction)
            continue

        new_direction = _direction_between(prev.low_ticks, prev.high_ticks, current.low_ticks, current.high_ticks)
        if new_direction is Direction.UNRESOLVED:
            return InclusionOutput(tuple(processed), ValidationResult(False, ("DIRECTION_UNRESOLVED",)))
        direction = new_direction
        processed.append(replace(current, direction_context=direction))

    return InclusionOutput(tuple(processed), ValidationResult(True))
