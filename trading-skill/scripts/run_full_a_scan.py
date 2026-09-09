from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_skill.chan.signals import ChanSignal
from trading_skill.decision import (
    Action,
    OpportunityEvidence,
    OpportunityGrade,
    RiskEvidence,
    RiskState,
    blockers_for,
    decide,
    risk_state,
    score_opportunity,
)
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.indicators import BollState, KdjState, MacdState, TechnicalConfirmation, VolumeState
from trading_skill.notifications import build_stock_scan_report, notify_feishu
from trading_skill.production_chan import ProductionChanResult, analyze_production_chan, result_dict
from trading_skill.sizing import StopCandidate, StopLevel, StopType, TrancheRole, build_position_plan, tranche_fraction

CN_TZ = ZoneInfo("Asia/Shanghai")
TICK_SIZE = Decimal("0.01")

TF_MAP = {
    "weekly": Timeframe.WEEKLY,
    "daily": Timeframe.DAILY,
    "120m": Timeframe.M120,
    "30m": Timeframe.M30,
    "5m": Timeframe.M5,
}
BUY_TYPES = {ChanSignalType.FIRST_BUY, ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY}
SELL_TYPES = {ChanSignalType.FIRST_SELL, ChanSignalType.SECOND_SELL, ChanSignalType.THIRD_SELL}
BUY_PRIORITY = {ChanSignalType.SECOND_BUY: 3, ChanSignalType.THIRD_BUY: 2, ChanSignalType.FIRST_BUY: 1}
FRESHNESS = {
    Timeframe.WEEKLY: timedelta(days=30),
    Timeframe.DAILY: timedelta(days=10),
    Timeframe.M120: timedelta(days=3),
    Timeframe.M30: timedelta(days=1),
    Timeframe.M5: timedelta(hours=2),
}


def rows_to_raw(symbol: str, timeframe: Timeframe, rows: list[dict]) -> tuple[RawBar, ...]:
    bars = []
    for row in rows:
        raw_time = str(row["time"])
        if len(raw_time) == 10:
            dt = datetime.fromisoformat(raw_time).replace(tzinfo=CN_TZ, hour=15)
        else:
            dt = datetime.fromisoformat(raw_time)
            dt = dt.replace(tzinfo=CN_TZ) if dt.tzinfo is None else dt.astimezone(CN_TZ)
        bars.append(
            RawBar.make(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=dt,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume") or 0,
                amount=row.get("amount") or 0,
                is_complete=bool(row.get("_complete", True)),
                source=str(row.get("_source") or "full_a"),
            )
        )
    return tuple(bars)


def signal_type(signal: ChanSignal) -> ChanSignalType | None:
    return signal.standard_types[0] if signal.standard_types else None


def fresh_signals(result: ProductionChanResult, *, as_of: datetime, side: str | None = None) -> list[ChanSignal]:
    cutoff = as_of - FRESHNESS[result.timeframe]
    signals = [signal for signal in result.signals if cutoff <= signal.confirmation_timestamp <= as_of]
    if side is not None:
        signals = [signal for signal in signals if signal.side == side]
    return signals


def choose_primary(results: dict[Timeframe, ProductionChanResult], *, as_of: datetime) -> tuple[Timeframe, ChanSignal] | None:
    choices = []
    for timeframe in (Timeframe.DAILY, Timeframe.M120, Timeframe.M30):
        result = results.get(timeframe)
        if result is None:
            continue
        for signal in fresh_signals(result, as_of=as_of, side="BUY"):
            kind = signal_type(signal)
            if kind not in BUY_TYPES:
                continue
            choices.append((signal.confirmation_timestamp, BUY_PRIORITY.get(kind, 0), timeframe, signal))
    if not choices:
        return None
    choices.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, timeframe, signal = choices[0]
    return timeframe, signal


def parent_timeframes(timeframe: Timeframe) -> tuple[Timeframe, ...]:
    if timeframe is Timeframe.DAILY:
        return (Timeframe.WEEKLY,)
    if timeframe is Timeframe.M120:
        return (Timeframe.WEEKLY, Timeframe.DAILY)
    if timeframe is Timeframe.M30:
        return (Timeframe.DAILY, Timeframe.M120)
    return ()


def parent_valid(results: dict[Timeframe, ProductionChanResult], timeframe: Timeframe, *, as_of: datetime) -> bool:
    for parent_tf in parent_timeframes(timeframe):
        parent = results.get(parent_tf)
        if parent is None or parent.status not in ("OK", "UNRESOLVED"):
            return False
        if fresh_signals(parent, as_of=as_of, side="SELL"):
            return False
        if parent.trend_classification == "DOWNTREND" and parent.divergence_state not in ("FORMING", "CONFIRMED"):
            return False
    return True


def _volume_points(state: VolumeState) -> int:
    return {
        VolumeState.SHRINK: 14,
        VolumeState.NORMAL: 15,
        VolumeState.MILD_EXPAND: 18,
        VolumeState.SIGNIFICANT: 17,
        VolumeState.EXTREME: 8,
    }[state]


def _macd_points(state: MacdState) -> int:
    return {
        MacdState.GOLDEN_CROSS: 15,
        MacdState.BULLISH: 15,
        MacdState.GREEN_SHRINKING: 12,
        MacdState.RED_SHRINKING: 9,
        MacdState.TURNING_UP: 12,
        MacdState.TURNING_DOWN: 5,
        MacdState.GOLDEN_CROSS_FAILED: 4,
        MacdState.DEATH_CROSS: 2,
        MacdState.BEARISH: 4,
    }.get(state, 7)


def _boll_points(state: BollState) -> int:
    return {
        BollState.ABOVE_MID: 8,
        BollState.RETEST: 9,
        BollState.BREAKOUT: 9,
        BollState.UPPER_RAIL_WALK: 7,
        BollState.BELOW_MID: 5,
        BollState.FAILED_RECLAIM: 2,
    }.get(state, 5)


def _kdj_points(state: KdjState) -> int:
    return {
        KdjState.GOLDEN_CROSS: 10,
        KdjState.MID_SECOND_CROSS: 9,
        KdjState.LOW_ZONE: 8,
        KdjState.NEUTRAL: 6,
        KdjState.HIGH_SATURATION: 3,
        KdjState.DEATH_CROSS: 2,
    }.get(state, 5)


def _chan_points(signal: ChanSignal) -> int:
    kind = signal_type(signal)
    if kind in (ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY):
        return 35
    if kind is ChanSignalType.FIRST_BUY:
        return 31
    return 0


def _stop_for(result: ProductionChanResult, signal: ChanSignal) -> StopCandidate | None:
    kind = signal_type(signal)
    stop_ticks = signal.structural_price_ticks - 1
    source = signal.id
    stop_type = StopType.BUY_POINT_INVALIDATION
    if kind is ChanSignalType.THIRD_BUY and result.centers:
        eligible_centers = [c for c in result.centers if c.confirmation_timestamp <= signal.confirmation_timestamp]
        if eligible_centers:
            center = eligible_centers[-1]
            stop_ticks = center.zg_ticks - 1
            source = center.id
            stop_type = StopType.CENTER_INVALIDATION
    if stop_ticks <= 0:
        return None
    level = {
        Timeframe.M5: StopLevel.L5,
        Timeframe.M30: StopLevel.L30,
        Timeframe.M120: StopLevel.L120,
        Timeframe.DAILY: StopLevel.LD,
        Timeframe.WEEKLY: StopLevel.LW,
    }[result.timeframe]
    return StopCandidate(
        id=f"stop-{signal.id}",
        level=level,
        stop_type=stop_type,
        price_ticks=stop_ticks,
        source_structure_id=source,
        source_signal_id=signal.id,
        is_structural=True,
        noise_risk=False,
    )


def execution_maturity(signal: ChanSignal, current_price: float) -> str:
    signal_price = signal.structural_price_ticks * float(TICK_SIZE)
    if signal_price <= 0 or current_price <= 0:
        return "NOT_READY"
    distance = (current_price - signal_price) / signal_price
    if -0.005 <= distance <= 0.025:
        return "TRIGGERED"
    if 0.025 < distance <= 0.05:
        return "PREPARE"
    return "WATCH"


def opportunity_and_risk(
    *,
    primary: ProductionChanResult,
    signal: ChanSignal,
    execution: ProductionChanResult,
    parents_ok: bool,
    industry: dict,
    stock: dict,
) -> tuple[object, RiskState]:
    tech = primary.technical
    exec_tech = execution.technical
    if tech is None:
        evidence = OpportunityEvidence(_chan_points(signal), 0, 0, 0, 0, 0)
    else:
        mtf_points = 10 if parents_ok and exec_tech and exec_tech.confirmation in (TechnicalConfirmation.SUPPORT, TechnicalConfirmation.NEUTRAL) else 6 if parents_ok else 2
        evidence = OpportunityEvidence(
            _chan_points(signal),
            _volume_points(tech.volume_state),
            _macd_points(tech.macd_state),
            _boll_points(tech.boll_state),
            _kdj_points(tech.kdj_state),
            mtf_points,
        )

    position_risk = 0
    if str(industry.get("heat_state")) == "过热" or float(stock.get("change_60d") or 0) > 30:
        position_risk = 1
    if float(stock.get("change_60d") or 0) > 50:
        position_risk = 2
    volume_risk = 0
    momentum_risk = 0
    if exec_tech is not None:
        if exec_tech.confirmation is TechnicalConfirmation.PAUSE:
            volume_risk = 2
            momentum_risk = 1
        elif exec_tech.confirmation is TechnicalConfirmation.CAUTION:
            momentum_risk = 1
    structure_risk = 0 if parents_ok else 2
    risk = risk_state(
        RiskEvidence(structure=structure_risk, volume=volume_risk, momentum=momentum_risk, position=position_risk)
    )
    stop_defined = _stop_for(primary, signal) is not None
    opportunity = score_opportunity(evidence, chan_buy_eligible=True, stop_defined=stop_defined, risk=risk)
    return opportunity, risk


def analyze_symbol(symbol: dict, industry_map: dict[str, dict], *, as_of: datetime, equity: float) -> tuple[dict, dict]:
    code = str(symbol["code"])
    results: dict[Timeframe, ProductionChanResult] = {}
    raw_results = {}
    for key, timeframe in TF_MAP.items():
        raw = rows_to_raw(code, timeframe, list(symbol.get(key) or []))
        result = analyze_production_chan(raw, tick_size=TICK_SIZE, as_of=as_of)
        results[timeframe] = result
        raw_results[key] = result_dict(result)

    picked = choose_primary(results, as_of=as_of)
    if picked is None:
        return {"code": code, "name": symbol.get("name"), "timeframes": raw_results, "candidate": None}, {}
    primary_tf, signal = picked
    primary = results[primary_tf]
    execution = results[Timeframe.M5]
    parents_ok = parent_valid(results, primary_tf, as_of=as_of)
    industry = industry_map.get(str(symbol.get("industry_code"))) or {}
    opportunity, risk = opportunity_and_risk(
        primary=primary,
        signal=signal,
        execution=execution,
        parents_ok=parents_ok,
        industry=industry,
        stock=symbol,
    )
    stop = _stop_for(primary, signal)
    current_price = float(symbol.get("5m", [{}])[-1].get("close") if symbol.get("5m") else primary.latest_close or 0)
    maturity = execution_maturity(signal, current_price)
    execution_tech = execution.technical if execution.technical and execution.technical.confirmation is TechnicalConfirmation.PAUSE else primary.technical
    blockers = blockers_for(
        signal=signal,
        fundamental_eligible=bool((symbol.get("fundamental_prefilter") or {}).get("eligible")),
        stop_defined=stop is not None,
        risk=risk,
        technical=execution_tech,
        parent_valid=parents_ok,
        data_complete=primary.status == "OK" and execution.status == "OK",
        portfolio_permission=True,
        allow_daily_first_buy=primary_tf is not Timeframe.DAILY,
    )
    decision = decide(
        opportunity=opportunity,
        risk=risk,
        signal=signal,
        blockers=blockers,
        has_position=False,
        execution_maturity=maturity,
    )

    kind = signal_type(signal)
    signal_price = signal.structural_price_ticks * float(TICK_SIZE)
    buy_low = signal_price
    buy_high = signal_price * 1.025
    amount = None
    quantity = None
    fraction = tranche_fraction(opportunity.grade)
    sizing_blocker = None
    if equity > 0 and stop is not None:
        plan = build_position_plan(
            equity=equity,
            grade=opportunity.grade,
            risk=risk,
            entry=max(current_price, signal_price),
            stop=stop,
            tick_size=float(TICK_SIZE),
            archetype="core_leader" if int(symbol.get("leader_rank") or 99) == 1 else "quality_name",
            role=TrancheRole.TEST,
        )
        amount = plan.rounded_value if plan.rounded_quantity else None
        quantity = plan.rounded_quantity or None
        sizing_blocker = plan.blocker

    push = decision.action in (Action.PREPARE_BUY, Action.BUY_TRANCHE_1) and opportunity.grade is not OpportunityGrade.C and risk < RiskState.L2
    candidate = {
        "name": symbol.get("name"),
        "code": code,
        "industry": symbol.get("industry_name"),
        "industry_state": industry.get("heat_state", "暂无"),
        "leader_rank": f"细分行业第{symbol.get('leader_rank')}候选",
        "signal": kind.value if kind else None,
        "timeframe": primary_tf.value,
        "parent_structure": "上级结构通过" if parents_ok else "上级结构暂不支持",
        "volume_price": f"成交量:{primary.technical.volume_state.value if primary.technical else '暂无'}",
        "technical": execution.technical.confirmation.value if execution.technical else None,
        "opportunity": opportunity.grade.value,
        "risk": f"L{int(risk)}",
        "action": decision.action.value,
        "buy_point": f"{buy_low:.2f}～{buy_high:.2f}元",
        "buy_amount": amount,
        "buy_quantity": quantity,
        "buy_fraction": f"计划仓位的{fraction:.0%}" if fraction > 0 else "0%",
        "add_plan": "只有形成新的30分钟/120分钟确认结构后再考虑加仓，不因下跌机械补仓",
        "stop": f"{stop.price_ticks * float(TICK_SIZE):.2f}元" if stop else "暂无",
        "reason": f"{symbol.get('industry_name')}行业筛选通过 + 龙头候选 + 财务预筛{(symbol.get('fundamental_prefilter') or {}).get('grade')}级 + {primary_tf.value}{kind.value if kind else ''}",
        "push": push,
        "current_price": current_price,
        "execution_maturity": maturity,
        "blockers": [item.value for item in blockers],
        "sizing_blocker": sizing_blocker,
    }
    return {"code": code, "name": symbol.get("name"), "timeframes": raw_results, "candidate": candidate}, candidate


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--bars", type=Path, default=Path("full-a-results/candidate_bars_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/scan_latest.json"))
    parser.add_argument("--stage", default="收盘确认")
    parser.add_argument("--equity", type=float, default=float(os.getenv("TRADING_EQUITY", "0") or 0))
    parser.add_argument("--notify-feishu", action="store_true")
    args = parser.parse_args()

    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    bars = json.loads(args.bars.read_text(encoding="utf-8"))
    as_of = datetime.fromisoformat(str(bars["generated_at"]))
    as_of = as_of.replace(tzinfo=CN_TZ) if as_of.tzinfo is None else as_of.astimezone(CN_TZ)
    industry_map = {str(item["code"]): item for item in universe.get("selected_industries", [])}

    analyses = []
    candidates = []
    for symbol in bars.get("symbols", []):
        analysis, candidate = analyze_symbol(symbol, industry_map, as_of=as_of, equity=args.equity)
        analyses.append(analysis)
        if candidate:
            candidates.append(candidate)

    scan = {
        "generated_at": f"{as_of.strftime('%Y-%m-%d %H:%M')}｜{args.stage}",
        "stage": args.stage,
        "total_stocks": universe.get("all_a_stocks_loaded", 0),
        "industries_screened": universe.get("industry_rows_loaded", 0),
        "selected_industries": len(universe.get("selected_industries", [])),
        "leader_candidates": len(universe.get("leader_candidates", [])),
        "fundamental_passed": universe.get("fundamental_eligible_candidates", 0),
        "deep_scanned": len(bars.get("symbols", [])),
        "chan_buy_candidates": len(candidates),
        "candidates": candidates,
        "analyses": analyses,
        "data_errors": bars.get("errors", []),
        "guardrails": {
            "5m_signal_alone_can_push": False,
            "chan_first": True,
            "technical_only_confirms_or_pauses": True,
            "daily_first_buy_waits_for_second_buy": True,
            "live_broker_execution": False,
        },
    }
    atomic_json(args.output, scan)
    confirmed = [item for item in candidates if item.get("push")]
    print(f"深度缠论={scan['deep_scanned']}，出现正式买点={len(candidates)}，达到推送条件={len(confirmed)}")
    for item in confirmed:
        print(item["code"], item["name"], item["signal"], item["timeframe"], item["opportunity"], item["risk"], item["action"], item["buy_point"])

    if args.notify_feishu:
        title, body = build_stock_scan_report(scan)
        title = f"{title}｜{args.stage}"
        channel = notify_feishu(title, body)
        print(f"[OK] 全A买点扫描已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
