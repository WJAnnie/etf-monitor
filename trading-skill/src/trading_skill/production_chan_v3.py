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
from trading_skill.domain.enums import CenterRelationType, ChanSignalType, Direction, StrokeMode
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


def _ordered_centers(centers: tuple[Center, ...]) -> tuple[Center, ...]:
    return tuple(sorted(centers, key=lambda c: (c.structural_start_timestamp, c.id)))


def directional_center_runs(centers: tuple[Center, ...]) -> tuple[tuple[Center, ...], ...]:
    """拆出全部“连续同向、同级别、独立中枢”趋势窗口。

    一个窗口至少包含两个中枢。方向切换时，转折处中枢既是上一趋势窗口的终点，
    也允许作为下一趋势窗口的起点；扩展、同中枢延伸、无法判定都会中断趋势窗口。
    这只是给经典趋势定义划定时间边界，不降低“两个同级别独立中枢”的要求。
    """
    ordered = _ordered_centers(centers)
    if len(ordered) < 2:
        return ()

    relations = tuple(classify_center_relation(a, b).type for a, b in zip(ordered, ordered[1:]))
    runs: list[tuple[Center, ...]] = []
    active_relation: CenterRelationType | None = None
    active_start: int | None = None

    for index, relation in enumerate(relations):
        if relation not in _DIRECTIONAL_RELATIONS:
            if active_relation is not None and active_start is not None:
                runs.append(ordered[active_start : index + 1])
            active_relation = None
            active_start = None
            continue

        if active_relation is None:
            active_relation = relation
            active_start = index
            continue

        if relation is active_relation:
            continue

        # 趋势方向切换：上一窗口结束于当前关系左侧中枢；该中枢同时成为新窗口起点。
        if active_start is not None:
            runs.append(ordered[active_start : index + 1])
        active_relation = relation
        active_start = index

    if active_relation is not None and active_start is not None:
        runs.append(ordered[active_start:])

    return tuple(run for run in runs if len(run) >= 2)


def latest_directional_center_run(centers: tuple[Center, ...]) -> tuple[Center, ...]:
    """返回当前仍延伸到最新中枢的趋势窗口；末端若处于扩展/未决则返回空。"""
    ordered = _ordered_centers(centers)
    if len(ordered) < 2:
        return ()
    latest_relation = classify_center_relation(ordered[-2], ordered[-1]).type
    if latest_relation not in _DIRECTIONAL_RELATIONS:
        return ()
    runs = directional_center_runs(ordered)
    if not runs:
        return ()
    latest = runs[-1]
    return latest if latest[-1].id == ordered[-1].id else ()


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


def _run_relation(run: tuple[Center, ...]) -> CenterRelationType | None:
    if len(run) < 2:
        return None
    relation = classify_center_relation(run[-2], run[-1]).type
    return relation if relation in _DIRECTIONAL_RELATIONS else None


def _segments_through_first_opposite_turn(
    segments: tuple[NormalizedSegment, ...], run: tuple[Center, ...]
) -> tuple[NormalizedSegment, ...]:
    """历史趋势窗口只看到趋势末端后的第一次反向完成段，避免把更晚行情并进同一个c段。"""
    relation = _run_relation(run)
    if relation is None:
        return segments
    direction = (
        Direction.UP
        if relation is CenterRelationType.INDEPENDENT_UP_NEW_CENTER
        else Direction.DOWN
    )
    opposite = Direction.DOWN if direction is Direction.UP else Direction.UP
    last_center = run[-1]
    saw_c = False
    cutoff = None

    for segment in segments:
        if segment.structural_start_timestamp < last_center.structural_end_timestamp:
            continue
        if not saw_c:
            if segment.direction is direction:
                saw_c = True
            continue
        if segment.direction is opposite:
            cutoff = segment.confirmation_timestamp
            break

    if cutoff is None:
        return segments
    return tuple(segment for segment in segments if segment.confirmation_timestamp <= cutoff)


def _trend_window_signals(
    *,
    raw_bars: tuple[RawBar, ...],
    complete_raw: tuple[RawBar, ...],
    segments: tuple[NormalizedSegment, ...],
    run: tuple[Center, ...],
    as_of: datetime,
):
    scoped_segments = _segments_through_first_opposite_turn(segments, run)
    trend, divergence, first_signal = _trend_divergence_and_first_signal(
        symbol=raw_bars[0].symbol,
        timeframe=raw_bars[0].timeframe,
        centers=run,
        segments=scoped_segments,
        raw_bars=complete_raw,
        as_of=min(as_of, scoped_segments[-1].confirmation_timestamp) if scoped_segments else as_of,
    )
    second_signal = _second_signal_from_first(first_signal, segments)
    return trend, divergence, first_signal, second_signal


def analyze_production_chan_v3(
    raw_bars: tuple[RawBar, ...], *, tick_size: Decimal, as_of: datetime
) -> ProductionChanResult:
    """V3生产覆盖层：当前趋势与近期历史买点分开管理。

    - 冻结核心继续负责笔/线段/中枢生命周期、三买三卖和技术指标；
    - 当前最后一段连续同向独立中枢决定“现在是什么趋势”；
    - 所有历史连续趋势窗口都可生成一买/一卖及其后续二买/二卖，后续由各周期freshness与
      “后续同级卖点失效”规则决定是否仍值得观察；这样不会把刚出现不久的一买/二买因趋势切换而删除。
    """
    base = analyze_production_chan(raw_bars, tick_size=tick_size, as_of=as_of)
    if base.status != "OK" or len(base.centers) < 2:
        return base

    segments = _segments_for(raw_bars, tick_size=tick_size, as_of=as_of)
    if not segments:
        return base
    complete_raw = tuple(bar for bar in raw_bars if bar.is_complete and bar.timestamp <= as_of)
    runs = directional_center_runs(base.centers)
    if not runs:
        return base

    # 三买/三卖仍完全保留核心结果；一买/二买及镜像卖点由分段趋势窗口重新生成。
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

    current_run = latest_directional_center_run(base.centers)
    current_trend = None
    current_divergence = None

    for run in runs:
        trend, divergence, first_signal, second_signal = _trend_window_signals(
            raw_bars=raw_bars,
            complete_raw=complete_raw,
            segments=segments,
            run=run,
            as_of=as_of,
        )
        if first_signal is not None:
            retained.append(first_signal)
        if second_signal is not None:
            retained.append(second_signal)
        if current_run and tuple(c.id for c in run) == tuple(c.id for c in current_run):
            current_trend = trend
            current_divergence = divergence

    unique = {signal.id: signal for signal in retained}
    signals = tuple(sorted(unique.values(), key=lambda s: (s.confirmation_timestamp, s.id)))

    if current_run and current_trend is not None:
        trend_classification = current_trend.current_classification.value
        trend_state = current_trend.state.value
        divergence_type = current_divergence.type.value if current_divergence is not None else None
        divergence_state = current_divergence.state.value if current_divergence is not None else None
    else:
        # 当前末端若处于扩展/未决，保留核心的“当前无法形成同级趋势”结论；历史有效信号仍可保留。
        trend_classification = base.trend_classification
        trend_state = base.trend_state
        divergence_type = base.divergence_type
        divergence_state = base.divergence_state

    return replace(
        base,
        trend_classification=trend_classification,
        trend_state=trend_state,
        divergence_type=divergence_type,
        divergence_state=divergence_state,
        signals=signals,
        issues=tuple(dict.fromkeys(base.issues + ("V3_DIRECTIONAL_CENTER_WINDOWS",))),
    )
