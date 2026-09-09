from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from trading_skill.chan.center import Center, classify_center_relation
from trading_skill.chan.fractal import detect_fractals
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.chan.segment import NormalizedSegment, build_segments
from trading_skill.chan.stroke import build_strokes
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import CenterRelationType, ChanSignalType, StrokeMode
from trading_skill.production_chan import (
    ProductionChanResult,
    _second_signal_from_first,
    _trend_divergence_and_first_signal,
    analyze_production_chan,
)


_DIRECTIONAL_RELATIONS = {
    CenterRelationType.INDEPENDENT_UP_NEW_CENTER,
    CenterRelationType.INDEPENDENT_DOWN_NEW_CENTER,
}


def latest_directional_center_run(centers: tuple[Center, ...]) -> tuple[Center, ...]:
    """返回最新一段连续同向、同级别、独立中枢序列。

    关键边界：
    - 趋势仍严格要求至少两个同级别、互不重叠的独立中枢；这里不降低定义。
    - 只是不再把全部历史中枢强行归为一个永不重置的趋势。
    - 最新相邻关系若属于扩展、同中枢延伸或无法判定，则当前没有可用于一买/一卖的完整趋势段。
    """
    if len(centers) < 2:
        return ()
    ordered = tuple(sorted(centers, key=lambda c: (c.structural_start_timestamp, c.id)))
    relations = [classify_center_relation(a, b).type for a, b in zip(ordered, ordered[1:])]
    latest_relation = relations[-1]
    if latest_relation not in _DIRECTIONAL_RELATIONS:
        return ()

    start = len(ordered) - 2
    for index in range(len(relations) - 2, -1, -1):
        if relations[index] is not latest_relation:
            break
        start = index
    return ordered[start:]


def _segments_for(raw_bars: tuple[RawBar, ...], *, tick_size: Decimal, as_of: datetime) -> tuple[NormalizedSegment, ...]:
    complete_raw = tuple(bar for bar in raw_bars if bar.is_complete and bar.timestamp <= as_of)
    if not complete_raw:
        return ()
    validated = validate_raw_bars(complete_raw, tick_size)
    if not validated.result.valid:
        return ()
    inclusion = process_inclusions(validated.bars)
    if not inclusion.result.valid:
        return ()
    fractals = detect_fractals(inclusion.bars)
    strokes = build_strokes(
        fractals.fractals,
        mode=StrokeMode.RELAXED_LATE,
        processed_bars=inclusion.bars,
        raw_bars=validated.bars,
    )
    return build_segments(strokes.strokes).normalized_segments


def analyze_production_chan_v3(
    raw_bars: tuple[RawBar, ...], *, tick_size: Decimal, as_of: datetime
) -> ProductionChanResult:
    """V3生产覆盖层：保留冻结核心，只修正“当前趋势段”的分析窗口。

    基础中枢生命周期、三买/三卖、技术指标仍完全来自冻结核心。
    若全部历史中枢本来就是同一连续趋势，直接返回核心结果；只有历史包含方向切换时，
    才用最近连续同向独立中枢段重算当前趋势背驰及其一买/一卖、二买/二卖。
    """
    base = analyze_production_chan(raw_bars, tick_size=tick_size, as_of=as_of)
    if base.status != "OK" or len(base.centers) < 2:
        return base

    current_centers = latest_directional_center_run(base.centers)
    if len(current_centers) < 2:
        return base

    ordered_all = tuple(sorted(base.centers, key=lambda c: (c.structural_start_timestamp, c.id)))
    if tuple(c.id for c in current_centers) == tuple(c.id for c in ordered_all):
        return base

    segments = _segments_for(raw_bars, tick_size=tick_size, as_of=as_of)
    if not segments:
        return base
    complete_raw = tuple(bar for bar in raw_bars if bar.is_complete and bar.timestamp <= as_of)

    trend, divergence, first_signal = _trend_divergence_and_first_signal(
        symbol=raw_bars[0].symbol,
        timeframe=raw_bars[0].timeframe,
        centers=current_centers,
        segments=segments,
        raw_bars=complete_raw,
        as_of=as_of,
    )
    second_signal = _second_signal_from_first(first_signal, segments)

    # 三买/三卖属于中枢生命周期直接产生，完整保留；旧窗口算出的1/2买卖则替换为当前趋势段结果。
    retained = [
        signal
        for signal in base.signals
        if not any(
            kind in signal.standard_types
            for kind in (
                ChanSignalType.FIRST_BUY,
                ChanSignalType.SECOND_BUY,
                ChanSignalType.FIRST_SELL,
                ChanSignalType.SECOND_SELL,
            )
        )
    ]
    if first_signal is not None:
        retained.append(first_signal)
    if second_signal is not None:
        retained.append(second_signal)
    unique = {signal.id: signal for signal in retained}
    signals = tuple(sorted(unique.values(), key=lambda s: (s.confirmation_timestamp, s.id)))

    return replace(
        base,
        trend_classification=trend.current_classification.value if trend is not None else None,
        trend_state=trend.state.value if trend is not None else None,
        divergence_type=divergence.type.value if divergence is not None else None,
        divergence_state=divergence.state.value if divergence is not None else None,
        signals=signals,
        issues=tuple(dict.fromkeys(base.issues + ("V3_LATEST_DIRECTIONAL_CENTER_RUN",))),
    )
