from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime

from trading_skill.chan.fractal import Fractal
from trading_skill.domain.bar import ProcessedBar, ValidatedBar
from trading_skill.domain.enums import Direction, FractalType, StrokeMode, StrokePhase, StructureState
from trading_skill.domain.models import ValidationResult, stable_id


@dataclass(frozen=True, slots=True)
class Stroke:
    id: str
    direction: Direction
    state: StructureState
    start_fractal_id: str
    end_fractal_id: str
    start_ticks: int
    end_ticks: int
    structural_start_timestamp: datetime
    structural_end_timestamp: datetime
    confirmation_timestamp: datetime
    phase: StrokePhase
    mode: StrokeMode
    revision: int = 1


@dataclass(frozen=True, slots=True)
class StrokeValidation:
    valid: bool
    direction: Direction | None
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class StrokeOutput:
    strokes: tuple[Stroke, ...]
    result: ValidationResult


def _direction_for_pair(start: Fractal, end: Fractal) -> Direction | None:
    if start.type is FractalType.BOTTOM and end.type is FractalType.TOP:
        return Direction.UP
    if start.type is FractalType.TOP and end.type is FractalType.BOTTOM:
        return Direction.DOWN
    return None


def _price_separation_ok(start: Fractal, end: Fractal, direction: Direction) -> bool:
    if direction is Direction.UP:
        return end.extreme_ticks > start.extreme_ticks
    return start.extreme_ticks > end.extreme_ticks


def _raw_spacing(start: Fractal, end: Fractal, raw_index: dict[str, int]) -> int | None:
    start_ids = [x for x in start.extreme_source_raw_bar_ids if x in raw_index]
    end_ids = [x for x in end.extreme_source_raw_bar_ids if x in raw_index]
    if not start_ids or not end_ids:
        return None
    spacings = [abs(raw_index[e] - raw_index[s]) - 1 for s in start_ids for e in end_ids]
    return min(spacings)


def validate_stroke_pair(
    start: Fractal,
    end: Fractal,
    *,
    mode: StrokeMode,
    processed_bars: tuple[ProcessedBar, ...],
    raw_bars: tuple[ValidatedBar, ...],
) -> StrokeValidation:
    direction = _direction_for_pair(start, end)
    if direction is None:
        return StrokeValidation(False, None, ("SAME_TYPE",))
    if end.center_index <= start.center_index:
        return StrokeValidation(False, direction, ("END_BEFORE_START",))
    if set(start.source_processed_bar_ids) & set(end.source_processed_bar_ids):
        return StrokeValidation(False, direction, ("SHARED_PROCESSED_BAR",))
    if not _price_separation_ok(start, end, direction):
        return StrokeValidation(False, direction, ("INVALID_PRICE_SEPARATION",))

    if mode is StrokeMode.STRICT_OLD:
        union = set(start.source_processed_bar_ids) | set(end.source_processed_bar_ids)
        between = processed_bars[start.center_index + 1 : end.center_index]
        if not any(pb.id not in union for pb in between):
            return StrokeValidation(False, direction, ("NO_INDEPENDENT_PROCESSED_BAR",))
    else:
        raw_index = {bar.id: i for i, bar in enumerate(raw_bars)}
        spacing = _raw_spacing(start, end, raw_index)
        if spacing is None:
            return StrokeValidation(False, direction, ("RAW_MAPPING_MISSING",))
        if spacing < 3:
            return StrokeValidation(False, direction, ("INSUFFICIENT_RAW_BARS",))
    return StrokeValidation(True, direction)


def _make_stroke(start: Fractal, end: Fractal, direction: Direction, mode: StrokeMode) -> Stroke:
    return Stroke(
        id=stable_id("st", mode, start.id, direction),
        direction=direction,
        state=StructureState.ACTIVE,
        start_fractal_id=start.id,
        end_fractal_id=end.id,
        start_ticks=start.extreme_ticks,
        end_ticks=end.extreme_ticks,
        structural_start_timestamp=start.center_timestamp,
        structural_end_timestamp=end.center_timestamp,
        confirmation_timestamp=end.confirmation_timestamp,
        phase=StrokePhase.EXTENDING,
        mode=mode,
    )


def _more_extreme(stroke: Stroke, fractal: Fractal) -> bool:
    if stroke.direction is Direction.UP and fractal.type is FractalType.TOP:
        return fractal.extreme_ticks > stroke.end_ticks
    if stroke.direction is Direction.DOWN and fractal.type is FractalType.BOTTOM:
        return fractal.extreme_ticks < stroke.end_ticks
    return False


def build_strokes(
    fractals: tuple[Fractal, ...],
    *,
    mode: StrokeMode,
    processed_bars: tuple[ProcessedBar, ...],
    raw_bars: tuple[ValidatedBar, ...],
) -> StrokeOutput:
    if len(fractals) < 2:
        return StrokeOutput((), ValidationResult(True))

    ordered = tuple(sorted(fractals, key=lambda f: (f.center_index, f.confirmation_timestamp, f.id)))
    by_id = {f.id: f for f in ordered}
    strokes: list[Stroke] = []
    anchor = ordered[0]

    for f in ordered[1:]:
        if not strokes:
            if f.type is anchor.type:
                if (f.type is FractalType.BOTTOM and f.extreme_ticks < anchor.extreme_ticks) or (
                    f.type is FractalType.TOP and f.extreme_ticks > anchor.extreme_ticks
                ):
                    anchor = f
                continue
            validation = validate_stroke_pair(anchor, f, mode=mode, processed_bars=processed_bars, raw_bars=raw_bars)
            if validation.valid and validation.direction is not None:
                strokes.append(_make_stroke(anchor, f, validation.direction, mode))
            continue

        active = strokes[-1]
        active_end = by_id[active.end_fractal_id]

        if f.type is active_end.type:
            if _more_extreme(active, f):
                strokes[-1] = replace(
                    active,
                    end_fractal_id=f.id,
                    end_ticks=f.extreme_ticks,
                    structural_end_timestamp=f.center_timestamp,
                    confirmation_timestamp=f.confirmation_timestamp,
                    revision=active.revision + 1,
                    phase=StrokePhase.EXTENDING,
                )
            continue

        validation = validate_stroke_pair(active_end, f, mode=mode, processed_bars=processed_bars, raw_bars=raw_bars)
        if validation.valid and validation.direction is not None:
            strokes[-1] = replace(active, state=StructureState.FINALIZED, phase=StrokePhase.REVERSAL_FRACTAL_FORMING)
            strokes.append(_make_stroke(active_end, f, validation.direction, mode))

    return StrokeOutput(tuple(strokes), ValidationResult(True))
