from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from trading_skill.domain.bar import ProcessedBar
from trading_skill.domain.enums import FractalType, StructureState
from trading_skill.domain.models import ValidationResult, stable_id, unique_preserve_order


@dataclass(frozen=True, slots=True)
class Fractal:
    id: str
    type: FractalType
    state: StructureState
    center_processed_bar_id: str
    source_processed_bar_ids: tuple[str, str, str]
    center_index: int
    extreme_ticks: int
    extreme_source_raw_bar_ids: tuple[str, ...]
    center_timestamp: datetime
    confirmation_timestamp: datetime
    revision: int = 1


@dataclass(frozen=True, slots=True)
class FractalOutput:
    fractals: tuple[Fractal, ...]
    result: ValidationResult
    equal_edge_windows: tuple[int, ...] = ()


def detect_fractals(bars: tuple[ProcessedBar, ...]) -> FractalOutput:
    if len(bars) < 3:
        return FractalOutput((), ValidationResult(True))

    fractals: list[Fractal] = []
    equal_edges: list[int] = []
    for i in range(1, len(bars) - 1):
        a, b, c = bars[i - 1], bars[i], bars[i + 1]
        if not c.is_complete:
            continue

        top = b.high_ticks > a.high_ticks and b.high_ticks > c.high_ticks and b.low_ticks > a.low_ticks and b.low_ticks > c.low_ticks
        bottom = b.high_ticks < a.high_ticks and b.high_ticks < c.high_ticks and b.low_ticks < a.low_ticks and b.low_ticks < c.low_ticks

        if top:
            fractals.append(Fractal(
                id=stable_id("fr", FractalType.TOP, b.id, a.id, c.id),
                type=FractalType.TOP,
                state=StructureState.CONFIRMED,
                center_processed_bar_id=b.id,
                source_processed_bar_ids=(a.id, b.id, c.id),
                center_index=i,
                extreme_ticks=b.high_ticks,
                extreme_source_raw_bar_ids=unique_preserve_order(b.extreme_high_raw_bar_ids),
                center_timestamp=b.end_timestamp,
                confirmation_timestamp=c.end_timestamp,
            ))
        elif bottom:
            fractals.append(Fractal(
                id=stable_id("fr", FractalType.BOTTOM, b.id, a.id, c.id),
                type=FractalType.BOTTOM,
                state=StructureState.CONFIRMED,
                center_processed_bar_id=b.id,
                source_processed_bar_ids=(a.id, b.id, c.id),
                center_index=i,
                extreme_ticks=b.low_ticks,
                extreme_source_raw_bar_ids=unique_preserve_order(b.extreme_low_raw_bar_ids),
                center_timestamp=b.end_timestamp,
                confirmation_timestamp=c.end_timestamp,
            ))
        else:
            equal = (
                b.high_ticks == a.high_ticks
                or b.high_ticks == c.high_ticks
                or b.low_ticks == a.low_ticks
                or b.low_ticks == c.low_ticks
            )
            if equal:
                equal_edges.append(i)

    return FractalOutput(tuple(fractals), ValidationResult(True), tuple(equal_edges))
