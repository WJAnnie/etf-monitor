from __future__ import annotations

from dataclasses import dataclass

from trading_skill.chan.fractal import Fractal
from trading_skill.chan.stroke import StrokeOutput, build_strokes
from trading_skill.domain.bar import ProcessedBar, ValidatedBar
from trading_skill.domain.enums import StrokeMode


@dataclass(frozen=True, slots=True)
class StrokeModeAudit:
    relaxed: StrokeOutput
    strict: StrokeOutput

    @property
    def relaxed_count(self) -> int:
        return len(self.relaxed.strokes)

    @property
    def strict_count(self) -> int:
        return len(self.strict.strokes)

    @property
    def differs(self) -> bool:
        relaxed_shape = tuple((s.direction, s.start_ticks, s.end_ticks, s.state) for s in self.relaxed.strokes)
        strict_shape = tuple((s.direction, s.start_ticks, s.end_ticks, s.state) for s in self.strict.strokes)
        return relaxed_shape != strict_shape


def compare_stroke_modes(
    fractals: tuple[Fractal, ...],
    *,
    processed_bars: tuple[ProcessedBar, ...],
    raw_bars: tuple[ValidatedBar, ...],
) -> StrokeModeAudit:
    return StrokeModeAudit(
        relaxed=build_strokes(fractals, mode=StrokeMode.RELAXED_LATE, processed_bars=processed_bars, raw_bars=raw_bars),
        strict=build_strokes(fractals, mode=StrokeMode.STRICT_OLD, processed_bars=processed_bars, raw_bars=raw_bars),
    )
