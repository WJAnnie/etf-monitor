from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

from trading_skill.a_share_bars import CN_TZ
from trading_skill.chan_extensions import annotate_second_buy_variants, class2_buy_types
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.multi_timeframe_structure import (
    build_structure_book,
    evaluate_lower_context,
    evaluate_parent_context,
)
from trading_skill.production_chan import result_dict
from trading_skill.production_chan_v3 import analyze_production_chan_v3
from trading_skill.security_pricing import tick_size_for_security_type
from trading_skill.strategy_policy import TIMEFRAME_POLICY, primary_entry_timeframes


TF_ROWS = {
    Timeframe.WEEKLY: "weekly",
    Timeframe.DAILY: "daily",
    Timeframe.M120: "120m",
    Timeframe.M30: "30m",
    Timeframe.M5: "5m",
}


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def rows_to_raw(symbol: str, timeframe: Timeframe, rows: list[dict]) -> tuple[RawBar, ...]:
    bars: list[RawBar] = []
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
                source=str(row.get("_source") or "step4"),
                adjustment=str(row.get("_adjustment") or "unknown"),
            )
        )
    return tuple(bars)


def _fresh_buy(result, *, as_of: datetime):
    cutoff = as_of - timedelta(days=TIMEFRAME_POLICY[result.timeframe].freshness_days)
    candidates = [
        signal
        for signal in result.signals
        if signal.side == "BUY"
        and cutoff <= signal.confirmation_timestamp <= as_of
        and any(
            kind in signal.standard_types
            for kind in (ChanSignalType.FIRST_BUY, ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY)
        )
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda signal: (signal.confirmation_timestamp, signal.id))


def analyze_symbol(item: dict, *, as_of: datetime) -> dict:
    code = str(item.get("code") or "")
    security_type = str(item.get("security_type") or "")
    market = item.get("market")
    if market not in {0, 1}:
        raise ValueError("STEP4_STRUCTURE_MARKET_REQUIRED")
    tick_size = tick_size_for_security_type(security_type)

    results = {}
    serialized = {}
    for timeframe, key in TF_ROWS.items():
        rows = list(item.get(key) or [])
        raw = rows_to_raw(code, timeframe, rows)
        result = annotate_second_buy_variants(
            analyze_production_chan_v3(raw, tick_size=tick_size, as_of=as_of)
        )
        results[timeframe] = result
        serialized[key] = result_dict(result)

    structure_book = build_structure_book(results, as_of=as_of)
    parent_contexts = {}
    fresh_buy_contexts = {}
    for timeframe in primary_entry_timeframes():
        parent = evaluate_parent_context(results, primary_timeframe=timeframe, as_of=as_of)
        parent_contexts[timeframe.value] = parent.to_dict()
        signal = _fresh_buy(results[timeframe], as_of=as_of)
        if signal is None:
            continue
        lower = evaluate_lower_context(
            results,
            primary_timeframe=timeframe,
            primary_confirmation=signal.confirmation_timestamp,
            as_of=as_of,
        )
        fresh_buy_contexts[timeframe.value] = {
            "signal_id": signal.id,
            "standard_types": [kind.value for kind in signal.standard_types],
            "class2_types": [kind.value for kind in class2_buy_types(signal)],
            "structural_timestamp": signal.structural_timestamp.isoformat(),
            "confirmation_timestamp": signal.confirmation_timestamp.isoformat(),
            "parent_context": parent.to_dict(),
            "lower_context": lower.to_dict(),
        }

    return {
        "code": code,
        "name": item.get("name"),
        "market": market,
        "security_type": security_type,
        "tick_size": str(tick_size),
        "step3_status": item.get("step3_status"),
        "step3_tier": item.get("step3_tier"),
        "research_priority": item.get("research_priority"),
        "structures": structure_book.to_dict(),
        "parent_contexts": parent_contexts,
        "fresh_buy_contexts": fresh_buy_contexts,
        "chan": serialized,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=Path, default=Path("full-a-results/step4_bars_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/step4_structure_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.bars.read_text(encoding="utf-8"))
    as_of = datetime.now(CN_TZ)
    symbols = list(payload.get("symbols") or [])
    analyzed: list[dict] = []
    errors: list[dict] = []

    phase_counts: dict[str, Counter] = defaultdict(Counter)
    parent_counts: dict[str, Counter] = defaultdict(Counter)
    lower_counts: dict[str, Counter] = defaultdict(Counter)
    signal_counts = Counter()
    class2_counts = Counter()

    for item in symbols:
        try:
            row = analyze_symbol(item, as_of=as_of)
            analyzed.append(row)
            for timeframe, snapshot in (row.get("structures") or {}).items():
                phase_counts[timeframe][str(snapshot.get("phase"))] += 1
            for timeframe, context in (row.get("parent_contexts") or {}).items():
                parent_counts[timeframe][str(context.get("state"))] += 1
            for timeframe, context in (row.get("fresh_buy_contexts") or {}).items():
                standard = list(context.get("standard_types") or [])
                for kind in standard:
                    signal_counts[f"{timeframe}:{kind}"] += 1
                for kind in context.get("class2_types") or []:
                    class2_counts[f"{timeframe}:{kind}"] += 1
                lower_state = str((context.get("lower_context") or {}).get("state") or "")
                lower_counts[timeframe][lower_state] += 1
        except Exception as exc:
            errors.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "security_type": item.get("security_type"),
                "error": str(exc)[:1000],
            })

    output = {
        "mode": "STEP4B_HIERARCHICAL_MULTI_TIMEFRAME_STRUCTURE",
        "generated_at": as_of.isoformat(),
        "source_bars": str(args.bars),
        "design_contract": {
            "timeframes_are_hierarchical_not_voting": True,
            "weekly_is_strategic_only": True,
            "daily_is_core_structure": True,
            "m120_is_primary_trading_structure": True,
            "m30_is_tactical_structure": True,
            "m5_is_execution_only": True,
            "lower_sell_does_not_invalidate_higher_buy": True,
            "higher_caution_preserves_opportunity_but_can_pause_entry": True,
            "class2_buy_is_extension_not_standard_second_buy": True,
            "security_specific_tick_size": True,
            "this_stage_does_not_size_positions": True,
        },
        "input_symbols": len(symbols),
        "analyzed_symbols": len(analyzed),
        "errors": errors,
        "summary": {
            "phase_by_timeframe": {key: dict(value) for key, value in phase_counts.items()},
            "parent_context_by_primary_timeframe": {key: dict(value) for key, value in parent_counts.items()},
            "lower_context_for_fresh_buys": {key: dict(value) for key, value in lower_counts.items()},
            "fresh_standard_buys": dict(signal_counts),
            "fresh_class2_buys": dict(class2_counts),
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP4B分析:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("各周期阶段:", output["summary"]["phase_by_timeframe"])
    print("上级环境:", output["summary"]["parent_context_by_primary_timeframe"])
    print("近期正式买点:", output["summary"]["fresh_standard_buys"])
    print("近期类二买:", output["summary"]["fresh_class2_buys"])
    print("低级别执行关系:", output["summary"]["lower_context_for_fresh_buys"])

    if args.strict:
        problems: list[str] = []
        if symbols and len(analyzed) / len(symbols) < 0.95:
            problems.append(f"STEP4B分析成功率过低:{len(analyzed)}/{len(symbols)}")
        wrong_ticks = [
            row for row in analyzed
            if (row.get("security_type") == "STOCK" and row.get("tick_size") != "0.01")
            or (row.get("security_type") in {"ETF", "LOF", "FUND"} and row.get("tick_size") != "0.001")
        ]
        if wrong_ticks:
            problems.append(f"证券价格最小变动单位错误:{len(wrong_ticks)}")
        standalone_m5 = [
            row for row in analyzed if "5m" in (row.get("fresh_buy_contexts") or {})
        ]
        if standalone_m5:
            problems.append(f"5分钟错误成为主买点周期:{len(standalone_m5)}")
        missing_snapshots = [
            row for row in analyzed
            if set((row.get("structures") or {}).keys()) != {"weekly", "daily", "120m", "30m", "5m"}
        ]
        if missing_snapshots:
            problems.append(f"五周期结构快照不完整:{len(missing_snapshots)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
