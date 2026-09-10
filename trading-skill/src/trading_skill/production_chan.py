from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal
from typing import Iterable

from trading_skill.chan.center import (
    Center,
    CenterMotion,
    extend_center,
    motion_leaves_core,
    motion_overlaps_core,
    register_first_return,
    register_leave,
    seed_center,
)
from trading_skill.chan.divergence import (
    MacdLegEvidence,
    StructuralLeg,
    evaluate_trend_divergence,
)
from trading_skill.chan.fractal import detect_fractals
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.chan.segment import NormalizedSegment, build_segments
from trading_skill.chan.signals import (
    ChanSignal,
    LowerMove,
    ReversalAnchor,
    first_buy_or_sell,
    new_second_buy_tracker,
    new_second_sell_tracker,
    new_third_buy_tracker,
    new_third_sell_tracker,
    second_buy_step,
    second_sell_step,
    third_buy_step,
    third_sell_step,
)
from trading_skill.chan.stroke import build_strokes
from trading_skill.chan.trend import classify_trend, complete_trend, mark_completion_candidate
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import (
    CenterState,
    ChanSignalType,
    Direction,
    StrokeMode,
    Timeframe,
    TrendClassification,
)
from trading_skill.domain.models import stable_id
from trading_skill.indicators import TechnicalBundle, build_bundle, macd


@dataclass(frozen=True, slots=True)
class CenterLifecycleResult:
    centers: tuple[Center, ...]
    signals: tuple[ChanSignal, ...]
    reason_codes: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ProductionChanResult:
    status: str
    timeframe: Timeframe
    raw_bars: int
    completed_bars: int
    processed_bars: int
    fractals: int
    strokes: int
    segments: int
    finalized_segments: int
    centers: tuple[Center, ...]
    trend_classification: str | None
    trend_state: str | None
    divergence_type: str | None
    divergence_state: str | None
    signals: tuple[ChanSignal, ...]
    technical: TechnicalBundle | None
    latest_close: float | None
    issues: tuple[str, ...] = ()


def _lower_move(motion: CenterMotion, *, level_rank: int | None = None) -> LowerMove:
    return LowerMove(
        id=motion.id,
        direction=motion.direction,
        level_rank=motion.level_rank if level_rank is None else level_rank,
        low_ticks=motion.low_ticks,
        high_ticks=motion.high_ticks,
        structural_end_timestamp=motion.structural_end_timestamp,
        confirmation_timestamp=motion.confirmation_timestamp,
        completed=motion.completed,
        start_ticks=motion.low_ticks if motion.direction is Direction.UP else motion.high_ticks,
        end_ticks=motion.structural_end_ticks,
    )


def _replace_center(centers: list[Center], center: Center) -> None:
    for index in range(len(centers) - 1, -1, -1):
        if centers[index].id == center.id:
            centers[index] = center
            return
    centers.append(center)


def build_center_lifecycle(
    segments: tuple[NormalizedSegment, ...], *, symbol: str, timeframe: Timeframe
) -> CenterLifecycleResult:
    motions = tuple(CenterMotion.from_segment(seg, source_timeframe=timeframe, level_rank=0) for seg in segments)
    centers: list[Center] = []
    signals: list[ChanSignal] = []
    reasons: list[str] = []
    active: Center | None = None
    index = 0

    while index < len(motions):
        if active is None:
            if index + 2 >= len(motions):
                break
            seeded = seed_center(
                motions[index : index + 3],
                symbol=symbol,
                target_level_rank=1,
                base_engine_center=True,
            )
            if not seeded.result.valid or seeded.center.state is not CenterState.CONFIRMED:
                index += 1
                continue
            active = seeded.center
            centers.append(active)
            index += 3
            continue

        motion = motions[index]
        # 一个运动可以“包络与中枢相交”同时“结构终点已经离开”。
        # 生命周期必须先处理结构离开，不能被几何 overlap 抢先吞成 extension。
        if not motion_leaves_core(active, motion):
            if motion_overlaps_core(active, motion):
                updated = extend_center(active, motion)
                if updated.result.valid:
                    active = updated.center
                    _replace_center(centers, active)
                else:
                    reasons.extend(updated.result.reason_codes)
                index += 1
                continue
            reasons.append("MOTION_NEITHER_OVERLAP_NOR_VALID_LEAVE")
            active = None
            index += 1
            continue

        before_leave = active
        leaving = register_leave(active, motion)
        if not leaving.result.valid:
            reasons.extend(leaving.result.reason_codes)
            active = None
            index += 1
            continue
        active = leaving.center
        _replace_center(centers, active)

        if index + 1 >= len(motions):
            break
        return_motion = motions[index + 1]
        returned = register_first_return(active, return_motion)
        if not returned.result.valid:
            reasons.extend(returned.result.reason_codes)
            index += 1
            continue

        # 三买/三卖严格来自“完成离开 + 第一次完成回试”；离开看结构终点，回试看是否重返中枢核心。
        if active.state is CenterState.LEAVING_UP:
            tracker = new_third_buy_tracker(before_leave)
            tracker, _ = third_buy_step(tracker, before_leave, _lower_move(motion))
            tracker, signal = third_buy_step(tracker, before_leave, _lower_move(return_motion))
            if signal is not None:
                signals.append(signal)
        elif active.state is CenterState.LEAVING_DOWN:
            tracker = new_third_sell_tracker(before_leave)
            tracker, _ = third_sell_step(tracker, before_leave, _lower_move(motion))
            tracker, signal = third_sell_step(tracker, before_leave, _lower_move(return_motion))
            if signal is not None:
                signals.append(signal)

        active = returned.center
        _replace_center(centers, active)
        if active.state is CenterState.DESTROYED:
            # 第一次回试仍可以成为新中枢的第一段，因此只前进到回试段索引。
            active = None
            index += 1
            continue

        # 回到原中枢核心区，按延伸处理；ZD/ZG保持种子时固定，不重算。
        # RETURNING 状态允许一段回试穿过中枢后继续到另一侧，只要其包络真实经过核心区。
        extended = extend_center(active, return_motion)
        if extended.result.valid:
            active = extended.center
            _replace_center(centers, active)
        else:
            reasons.extend(extended.result.reason_codes)
        index += 2

    # 去重，保留每个中枢的最新revision。
    latest: dict[str, Center] = {}
    order: list[str] = []
    for center in centers:
        if center.id not in latest:
            order.append(center.id)
        latest[center.id] = center
    unique = tuple(latest[cid] for cid in order)
    unique_signals = {signal.id: signal for signal in signals}
    return CenterLifecycleResult(unique, tuple(unique_signals.values()), tuple(dict.fromkeys(reasons)))


def _directional_leg(
    motions: tuple[CenterMotion, ...], *, direction: Direction, level_rank: int, prefix: str
) -> StructuralLeg | None:
    """只接受一个明确、完成、同方向的同级运动作为 b/c 段。

    旧实现会把时间窗口内所有同方向运动合并，容易把“跌-反弹-再跌”拼成一条假 c 段。
    有多个同方向运动时宁可不确认标准背驰，等待上层形成明确可比结构。
    """
    selected = tuple(m for m in motions if m.direction is direction and m.completed)
    if len(selected) != 1:
        return None
    motion = selected[0]
    return StructuralLeg(
        id=stable_id(prefix, motion.id),
        direction=direction,
        level_rank=level_rank,
        low_ticks=motion.low_ticks,
        high_ticks=motion.high_ticks,
        structural_start_timestamp=motion.structural_start_timestamp,
        structural_end_timestamp=motion.structural_end_timestamp,
        confirmation_timestamp=motion.confirmation_timestamp,
        completed=True,
    )


def _macd_evidence(raw_bars: tuple[RawBar, ...], leg: StructuralLeg) -> MacdLegEvidence:
    closes = [float(bar.close) for bar in raw_bars]
    points = macd(closes)
    scoped = [
        point
        for bar, point in zip(raw_bars, points)
        if leg.structural_start_timestamp <= bar.timestamp <= leg.structural_end_timestamp
    ]
    if not scoped:
        return MacdLegEvidence(leg.id, 0.0, 0.0, 0.0, 0.0)
    if leg.direction is Direction.DOWN:
        directional = [abs(min(point.hist, 0.0)) for point in scoped]
        dif_extreme = abs(min(point.dif for point in scoped))
        dea_extreme = abs(min(point.dea for point in scoped))
    else:
        directional = [max(point.hist, 0.0) for point in scoped]
        dif_extreme = abs(max(point.dif for point in scoped))
        dea_extreme = abs(max(point.dea for point in scoped))
    return MacdLegEvidence(
        leg_id=leg.id,
        directional_hist_area=sum(directional),
        hist_peak_abs=max(directional) if directional else 0.0,
        dif_extreme_abs=dif_extreme,
        dea_extreme_abs=dea_extreme,
    )


def _trend_divergence_and_first_signal(
    *,
    symbol: str,
    timeframe: Timeframe,
    centers: tuple[Center, ...],
    segments: tuple[NormalizedSegment, ...],
    raw_bars: tuple[RawBar, ...],
    as_of: datetime,
) -> tuple[object | None, object | None, ChanSignal | None]:
    if len(centers) < 2:
        return None, None, None
    trend_update = classify_trend(
        centers,
        symbol=symbol,
        lower_motion_ids=tuple(segment.id for segment in segments),
    )
    trend = trend_update.trend
    if trend.current_classification not in (TrendClassification.UPTREND, TrendClassification.DOWNTREND):
        return trend, None, None

    direction = Direction.UP if trend.current_classification is TrendClassification.UPTREND else Direction.DOWN
    motions = tuple(CenterMotion.from_segment(seg, source_timeframe=timeframe, level_rank=0) for seg in segments)
    previous_center = centers[-2]
    last_center = centers[-1]
    between = tuple(
        m
        for m in motions
        if m.structural_start_timestamp >= previous_center.structural_end_timestamp
        and m.structural_end_timestamp <= last_center.structural_start_timestamp
    )
    after_last = tuple(m for m in motions if m.structural_start_timestamp >= last_center.structural_end_timestamp)
    b_leg = _directional_leg(between, direction=direction, level_rank=trend.level_rank, prefix="trend_b")
    c_leg = _directional_leg(after_last, direction=direction, level_rank=trend.level_rank, prefix="trend_c")
    if b_leg is None or c_leg is None:
        return trend, None, None

    opposite = Direction.DOWN if direction is Direction.UP else Direction.UP
    opposite_after_c = tuple(
        m for m in after_last if m.direction is opposite and m.confirmation_timestamp > c_leg.confirmation_timestamp
    )
    if opposite_after_c:
        # 第一次完成反向运动就是完成确认；更晚行情不能回写更迟的确认时间。
        first_opposite = min(opposite_after_c, key=lambda m: (m.confirmation_timestamp, m.id))
        completion_time = first_opposite.confirmation_timestamp
        trend = mark_completion_candidate(trend, reason="LOWER_LEVEL_OPPOSITE_TURN", confirmation_timestamp=completion_time)
        trend = complete_trend(
            trend,
            lower_level_opposite_turn_completed=True,
            direct_extension_exists=False,
            confirmation_timestamp=completion_time,
        )

    b_macd = _macd_evidence(raw_bars, b_leg)
    c_macd = _macd_evidence(raw_bars, c_leg)
    divergence, _ = evaluate_trend_divergence(
        trend,
        b_leg,
        c_leg,
        b_macd,
        c_macd,
        lower_level_turn_completed=bool(opposite_after_c),
        as_of=as_of,
    )
    signal, _ = first_buy_or_sell(
        symbol=symbol,
        trend=trend,
        divergence=divergence,
        structural_price_ticks=c_leg.low_ticks if direction is Direction.DOWN else c_leg.high_ticks,
        structural_timestamp=c_leg.structural_end_timestamp,
    )
    return trend, divergence, signal


def _second_signal_from_first(
    first_signal: ChanSignal | None,
    segments: tuple[NormalizedSegment, ...],
) -> ChanSignal | None:
    if first_signal is None:
        return None
    motions = tuple(
        CenterMotion.from_segment(seg, source_timeframe=Timeframe(first_signal.timeframe), level_rank=0)
        for seg in segments
        if seg.confirmation_timestamp > first_signal.confirmation_timestamp
    )
    if not motions:
        return None
    first_type = first_signal.standard_types[0] if first_signal.standard_types else None
    if first_type is ChanSignalType.FIRST_BUY:
        anchor = ReversalAnchor(
            id=stable_id("anchor", first_signal.id),
            symbol=first_signal.symbol,
            direction=Direction.UP,
            level_rank=first_signal.level_rank,
            price_ticks=first_signal.structural_price_ticks,
            confirmation_timestamp=first_signal.confirmation_timestamp,
            timeframe=first_signal.timeframe,
        )
        tracker = new_second_buy_tracker(anchor)
        for motion in motions:
            tracker, signal = second_buy_step(tracker, _lower_move(motion, level_rank=first_signal.level_rank))
            if signal is not None:
                return signal
    elif first_type is ChanSignalType.FIRST_SELL:
        anchor = ReversalAnchor(
            id=stable_id("anchor", first_signal.id),
            symbol=first_signal.symbol,
            direction=Direction.DOWN,
            level_rank=first_signal.level_rank,
            price_ticks=first_signal.structural_price_ticks,
            confirmation_timestamp=first_signal.confirmation_timestamp,
            timeframe=first_signal.timeframe,
        )
        tracker = new_second_sell_tracker(anchor)
        for motion in motions:
            tracker, signal = second_sell_step(tracker, _lower_move(motion, level_rank=first_signal.level_rank))
            if signal is not None:
                return signal
    return None


def analyze_production_chan(
    raw_bars: tuple[RawBar, ...], *, tick_size: Decimal, as_of: datetime
) -> ProductionChanResult:
    if not raw_bars:
        return ProductionChanResult(
            "DATA_INCOMPLETE", Timeframe.DAILY, 0, 0, 0, 0, 0, 0, 0, (), None, None, None, None, (), None, None, ("EMPTY_INPUT",)
        )
    timeframe = raw_bars[0].timeframe
    complete_raw = tuple(bar for bar in raw_bars if bar.is_complete and bar.timestamp <= as_of)
    if not complete_raw:
        return ProductionChanResult(
            "DATA_INCOMPLETE", timeframe, len(raw_bars), 0, 0, 0, 0, 0, 0, (), None, None, None, None, (), None, None, ("NO_COMPLETED_BARS",)
        )
    adjustments = {str(bar.adjustment or "unknown") for bar in complete_raw}
    if "mixed" in adjustments or len(adjustments) != 1:
        return ProductionChanResult(
            "DATA_INCOMPLETE", timeframe, len(raw_bars), len(complete_raw), 0, 0, 0, 0, 0, (), None, None, None, None, (), None, None,
            ("MIXED_PRICE_BASIS",),
        )
    validated = validate_raw_bars(complete_raw, tick_size)
    if not validated.result.valid:
        return ProductionChanResult(
            "DATA_INCOMPLETE", timeframe, len(raw_bars), len(complete_raw), 0, 0, 0, 0, 0, (), None, None, None, None, (), None, None, tuple(validated.result.reason_codes)
        )
    inclusion = process_inclusions(validated.bars)
    if not inclusion.result.valid:
        return ProductionChanResult(
            "UNRESOLVED", timeframe, len(raw_bars), len(complete_raw), len(inclusion.bars), 0, 0, 0, 0, (), None, None, None, None, (), None, None, tuple(inclusion.result.reason_codes)
        )
    fractals = detect_fractals(inclusion.bars)
    strokes = build_strokes(
        fractals.fractals,
        mode=StrokeMode.RELAXED_LATE,
        processed_bars=inclusion.bars,
        raw_bars=validated.bars,
    )
    segment_output = build_segments(strokes.strokes)
    segments = segment_output.normalized_segments
    lifecycle = build_center_lifecycle(segments, symbol=raw_bars[0].symbol, timeframe=timeframe)

    trend, divergence, first_signal = _trend_divergence_and_first_signal(
        symbol=raw_bars[0].symbol,
        timeframe=timeframe,
        centers=lifecycle.centers,
        segments=segments,
        raw_bars=complete_raw,
        as_of=as_of,
    )
    second_signal = _second_signal_from_first(first_signal, segments)
    signals = list(lifecycle.signals)
    if first_signal is not None:
        signals.append(first_signal)
    if second_signal is not None:
        signals.append(second_signal)
    unique_signals = {signal.id: signal for signal in signals}
    ordered_signals = tuple(sorted(unique_signals.values(), key=lambda s: (s.confirmation_timestamp, s.id)))

    tech = build_bundle(
        volumes=[float(bar.volume) for bar in complete_raw],
        closes=[float(bar.close) for bar in complete_raw],
        highs=[float(bar.high) for bar in complete_raw],
        lows=[float(bar.low) for bar in complete_raw],
        bar_complete=True,
    )
    return ProductionChanResult(
        status="OK",
        timeframe=timeframe,
        raw_bars=len(raw_bars),
        completed_bars=len(complete_raw),
        processed_bars=len(inclusion.bars),
        fractals=len(fractals.fractals),
        strokes=len(strokes.strokes),
        segments=len(segment_output.segments),
        finalized_segments=len(segments),
        centers=lifecycle.centers,
        trend_classification=trend.current_classification.value if trend is not None else None,
        trend_state=trend.state.value if trend is not None else None,
        divergence_type=divergence.type.value if divergence is not None else None,
        divergence_state=divergence.state.value if divergence is not None else None,
        signals=ordered_signals,
        technical=tech,
        latest_close=float(complete_raw[-1].close),
        issues=lifecycle.reason_codes,
    )


def signal_dict(signal: ChanSignal) -> dict:
    return {
        "id": signal.id,
        "types": [item.value for item in signal.standard_types],
        "extended_types": [item.value for item in signal.extended_types],
        "side": signal.side,
        "level_rank": signal.level_rank,
        "timeframe": signal.timeframe,
        "structural_price_ticks": signal.structural_price_ticks,
        "structural_timestamp": signal.structural_timestamp.isoformat(),
        "confirmation_timestamp": signal.confirmation_timestamp.isoformat(),
        "evidence_ids": list(signal.evidence_ids),
    }


def result_dict(result: ProductionChanResult) -> dict:
    return {
        "status": result.status,
        "timeframe": result.timeframe.value,
        "raw_bars": result.raw_bars,
        "completed_bars": result.completed_bars,
        "processed_bars": result.processed_bars,
        "fractals": result.fractals,
        "strokes": result.strokes,
        "segments": result.segments,
        "finalized_segments": result.finalized_segments,
        "centers": [
            {
                "id": center.id,
                "state": center.state.value,
                "level_rank": center.level_rank,
                "zd_ticks": center.zd_ticks,
                "zg_ticks": center.zg_ticks,
                "dd_ticks": center.dd_ticks,
                "gg_ticks": center.gg_ticks,
                "structural_start": center.structural_start_timestamp.isoformat(),
                "structural_end": center.structural_end_timestamp.isoformat(),
                "confirmation": center.confirmation_timestamp.isoformat(),
            }
            for center in result.centers
        ],
        "trend": {"classification": result.trend_classification, "state": result.trend_state},
        "divergence": {"type": result.divergence_type, "state": result.divergence_state},
        "signals": [signal_dict(signal) for signal in result.signals],
        "technical": None
        if result.technical is None
        else {
            "volume": result.technical.volume_state.value,
            "macd": result.technical.macd_state.value,
            "boll": result.technical.boll_state.value,
            "kdj": result.technical.kdj_state.value,
            "confirmation": result.technical.confirmation.value,
            "reasons": list(result.technical.reason_codes),
        },
        "latest_close": result.latest_close,
        "issues": list(result.issues),
    }
