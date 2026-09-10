from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from trading_skill.a_share_bars import CN_TZ
from trading_skill.chan_extensions import annotate_second_buy_variants
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import Timeframe
from trading_skill.multi_timeframe_structure import build_structure_book, evaluate_parent_context
from trading_skill.production_chan import result_dict
from trading_skill.production_chan_v3 import analyze_production_chan_v3
from trading_skill.security_pricing import tick_size_for_security_type
from trading_skill.strategy_policy import primary_entry_timeframes


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


def _parse_bar_time(raw_time: str) -> datetime:
    if len(raw_time) == 10:
        return datetime.fromisoformat(raw_time).replace(tzinfo=CN_TZ, hour=15)
    dt = datetime.fromisoformat(raw_time)
    return dt.replace(tzinfo=CN_TZ) if dt.tzinfo is None else dt.astimezone(CN_TZ)


def rows_to_raw(symbol: str, timeframe: Timeframe, rows: list[dict]) -> tuple[RawBar, ...]:
    bars: list[RawBar] = []
    for row in rows:
        dt = _parse_bar_time(str(row["time"]))
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


def _latest_completed_bar(rows: list[dict]) -> datetime | None:
    completed = [row for row in rows if bool(row.get("_complete", True)) and row.get("time")]
    if not completed:
        return None
    return max(_parse_bar_time(str(row["time"])) for row in completed)


def _empty_result(timeframe: Timeframe, *, issue: str) -> dict:
    return {
        "status": "DATA_INCOMPLETE",
        "timeframe": timeframe.value,
        "raw_bars": 0,
        "completed_bars": 0,
        "processed_bars": 0,
        "fractals": 0,
        "strokes": 0,
        "segments": 0,
        "finalized_segments": 0,
        "centers": [],
        "trend": {"classification": None, "state": None},
        "divergence": {"type": None, "state": None},
        "signals": [],
        "technical": None,
        "latest_close": None,
        "issues": [issue],
    }


def analyze_symbol(item: dict, *, as_of: datetime) -> dict:
    code = str(item.get("code") or "")
    security_type = str(item.get("security_type") or "")
    market = item.get("market")
    if market not in {0, 1}:
        raise ValueError("STEP4_STRUCTURE_MARKET_REQUIRED")
    tick_size = tick_size_for_security_type(security_type)

    results = {}
    serialized = {}
    bar_evidence = {}
    for timeframe, key in TF_ROWS.items():
        rows = list(item.get(key) or [])
        latest_completed = _latest_completed_bar(rows)
        bar_evidence[key] = {
            "row_count": len(rows),
            "completed_row_count": sum(1 for row in rows if bool(row.get("_complete", True))),
            "latest_completed_bar": latest_completed.isoformat() if latest_completed else None,
        }
        if not rows:
            result = _empty_result(timeframe, issue=f"NO_{timeframe.value.upper()}_BARS")
            results[timeframe] = result
            serialized[key] = result
            continue
        raw = rows_to_raw(code, timeframe, rows)
        result = annotate_second_buy_variants(
            analyze_production_chan_v3(raw, tick_size=tick_size, as_of=as_of)
        )
        results[timeframe] = result
        serialized[key] = result_dict(result)

    structure_book = build_structure_book(results, as_of=as_of)
    parent_contexts = {}
    for timeframe in primary_entry_timeframes():
        parent = evaluate_parent_context(results, primary_timeframe=timeframe, as_of=as_of)
        parent_contexts[timeframe.value] = parent.to_dict()

    return {
        "code": code,
        "name": item.get("name"),
        "market": market,
        "security_type": security_type,
        "tick_size": str(tick_size),
        "step3_status": item.get("step3_status"),
        "step3_tier": item.get("step3_tier"),
        "research_priority": item.get("research_priority"),
        "history_quality": ((item.get("quality") or {}).get("history_quality") or {}),
        "bar_evidence": bar_evidence,
        "structures": structure_book.to_dict(),
        "parent_contexts": parent_contexts,
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

    for item in symbols:
        try:
            row = analyze_symbol(item, as_of=as_of)
            analyzed.append(row)
            for timeframe, snapshot in (row.get("structures") or {}).items():
                phase_counts[timeframe][str(snapshot.get("phase"))] += 1
            for timeframe, context in (row.get("parent_contexts") or {}).items():
                parent_counts[timeframe][str(context.get("state"))] += 1
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
            "weekly_is_strategic_environment_only": True,
            "daily_is_only_new_entry_authority_structure": True,
            "m120_is_daily_tail_confirmation_not_standalone_entry": True,
            "m30_is_execution_setup_not_standalone_entry": True,
            "m5_is_final_execution_trigger_only": True,
            "step4b_does_not_choose_current_buy_or_sell_signal": True,
            "signal_freshness_and_invalidation_belong_to_step4c": True,
            "higher_caution_is_structural_context_only": True,
            "class2_buy_is_extension_not_independent_standard_signal": True,
            "security_specific_tick_size": True,
            "short_history_preserves_timeframe_identity": True,
            "this_stage_does_not_size_positions_or_decide_entry_permission": True,
        },
        "input_symbols": len(symbols),
        "analyzed_symbols": len(analyzed),
        "errors": errors,
        "summary": {
            "phase_by_timeframe": {key: dict(value) for key, value in phase_counts.items()},
            "parent_context_by_primary_timeframe": {key: dict(value) for key, value in parent_counts.items()},
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP4B分析:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("各周期阶段:", output["summary"]["phase_by_timeframe"])
    print("上级环境:", output["summary"]["parent_context_by_primary_timeframe"])

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
        missing_snapshots = [
            row for row in analyzed
            if set((row.get("structures") or {}).keys()) != {"weekly", "daily", "120m", "30m", "5m"}
        ]
        if missing_snapshots:
            problems.append(f"五周期结构快照不完整:{len(missing_snapshots)}")
        missing_history = [row for row in analyzed if not isinstance(row.get("history_quality"), dict)]
        if missing_history:
            problems.append(f"4B缺少历史证据合同:{len(missing_history)}")
        wrong_parent_contexts = [
            row for row in analyzed if set((row.get("parent_contexts") or {}).keys()) - {Timeframe.DAILY.value}
        ]
        if wrong_parent_contexts:
            problems.append(f"4B错误为非日线新开仓周期生成primary parent context:{len(wrong_parent_contexts)}")
        if any("fresh_buy_contexts" in row for row in analyzed):
            problems.append("4B仍然泄漏当前买点选择职责")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
