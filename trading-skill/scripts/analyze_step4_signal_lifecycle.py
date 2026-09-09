from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from trading_skill.a_share_bars import CN_TZ
from trading_skill.domain.enums import Timeframe
from trading_skill.history_policy import primary_history_gate
from trading_skill.multi_timeframe_structure import evaluate_lower_context
from trading_skill.signal_lifecycle import (
    SignalLifecycleStage,
    build_signal_lifecycle_book,
)
from trading_skill.strategy_policy import primary_entry_timeframes


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _parse_time(value: object, *, as_of: datetime) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=as_of.tzinfo)
    return dt.astimezone(as_of.tzinfo) if as_of.tzinfo else dt


def _latest_completed_bar_map(item: dict, *, as_of: datetime) -> dict[Timeframe, datetime | None]:
    evidence = item.get("bar_evidence") or {}
    out: dict[Timeframe, datetime | None] = {}
    for timeframe in Timeframe:
        raw = (evidence.get(timeframe.value) or {}).get("latest_completed_bar")
        out[timeframe] = _parse_time(raw, as_of=as_of)
    return out


def analyze_symbol(item: dict, *, as_of: datetime) -> dict:
    chan = dict(item.get("chan") or {})
    latest_completed = _latest_completed_bar_map(item, as_of=as_of)
    lifecycle_book = build_signal_lifecycle_book(
        chan,
        as_of=as_of,
        latest_completed_bar_by_timeframe=latest_completed,
    )
    lifecycle_dict = {tf.value: lifecycle.to_dict() for tf, lifecycle in lifecycle_book.items()}

    history_quality = dict(item.get("history_quality") or {})
    current_buy_contexts: dict[str, dict] = {}
    for timeframe in primary_entry_timeframes():
        lifecycle = lifecycle_book.get(timeframe)
        if lifecycle is None:
            continue
        current_buy = lifecycle.current_buy()
        if current_buy is None or current_buy.confirmation_timestamp is None:
            continue
        history_ok, history_reason = primary_history_gate(timeframe.value, history_quality)
        lower = evaluate_lower_context(
            chan,
            primary_timeframe=timeframe,
            primary_confirmation=current_buy.confirmation_timestamp,
            as_of=as_of,
        )
        current_buy_contexts[timeframe.value] = {
            "signal": current_buy.to_dict(),
            "class2_types": [
                value
                for value in current_buy.extended_types
                if value in {"STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY"}
            ],
            "history_eligible": history_ok,
            "history_reason": history_reason,
            "parent_context": (item.get("parent_contexts") or {}).get(timeframe.value),
            "lower_context": lower.to_dict(),
        }

    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "market": item.get("market"),
        "security_type": item.get("security_type"),
        "tick_size": item.get("tick_size"),
        "step3_status": item.get("step3_status"),
        "step3_tier": item.get("step3_tier"),
        "research_priority": item.get("research_priority"),
        "history_quality": history_quality,
        "structures": item.get("structures") or {},
        "parent_contexts": item.get("parent_contexts") or {},
        "signal_lifecycle": lifecycle_dict,
        "current_buy_contexts": current_buy_contexts,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structure", type=Path, default=Path("full-a-results/step4_structure_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/step4_signal_lifecycle_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.structure.read_text(encoding="utf-8"))
    as_of = datetime.now(CN_TZ)
    symbols = list(payload.get("symbols") or [])
    analyzed: list[dict] = []
    errors: list[dict] = []

    stage_counts: dict[str, Counter] = defaultdict(Counter)
    current_buy_counts: dict[str, Counter] = defaultdict(Counter)
    current_sell_counts: dict[str, Counter] = defaultdict(Counter)
    lower_counts: dict[str, Counter] = defaultdict(Counter)
    history_gate_counts: dict[str, Counter] = defaultdict(Counter)
    class2_counts = Counter()

    for item in symbols:
        try:
            row = analyze_symbol(item, as_of=as_of)
            analyzed.append(row)
            for timeframe, lifecycle in (row.get("signal_lifecycle") or {}).items():
                for record in lifecycle.get("records") or []:
                    stage_counts[timeframe][str(record.get("stage") or "UNKNOWN")] += 1
                current_buy = lifecycle.get("current_buy")
                if current_buy:
                    for kind in current_buy.get("standard_types") or []:
                        current_buy_counts[timeframe][str(kind)] += 1
                current_sell = lifecycle.get("current_sell")
                if current_sell:
                    for kind in current_sell.get("standard_types") or []:
                        current_sell_counts[timeframe][str(kind)] += 1
            for timeframe, context in (row.get("current_buy_contexts") or {}).items():
                history_gate_counts[timeframe]["PASS" if context.get("history_eligible") else "BLOCKED"] += 1
                lower_state = str((context.get("lower_context") or {}).get("state") or "UNRESOLVED")
                lower_counts[timeframe][lower_state] += 1
                for kind in context.get("class2_types") or []:
                    class2_counts[f"{timeframe}:{kind}"] += 1
        except Exception as exc:
            errors.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "security_type": item.get("security_type"),
                "error": str(exc)[:1000],
            })

    output = {
        "mode": "STEP4C_CHAN_SIGNAL_LIFECYCLE",
        "generated_at": as_of.isoformat(),
        "source_structure": str(args.structure),
        "design_contract": {
            "canonical_signal_definition_stays_in_step4a": True,
            "step4b_only_describes_multi_timeframe_structure": True,
            "expiry_is_not_invalidation": True,
            "same_timeframe_same_level_opposite_signal_invalidates": True,
            "different_level_opposite_signal_does_not_invalidate": True,
            "newer_same_side_signal_matures_older_signal": True,
            "newest_signal_wins_before_signal_type_priority": True,
            "signal_type_priority_only_breaks_same_confirmation_time_ties": True,
            "class2_is_metadata_not_independent_lifecycle_signal": True,
            "m5_has_lifecycle_but_never_becomes_primary_trade_cycle": True,
            "history_gate_is_context_not_signal_redefinition": True,
            "this_stage_does_not_size_positions_or_emit_final_trade_action": True,
        },
        "input_symbols": len(symbols),
        "analyzed_symbols": len(analyzed),
        "errors": errors,
        "summary": {
            "lifecycle_stage_by_timeframe": {key: dict(value) for key, value in stage_counts.items()},
            "current_buy_by_timeframe": {key: dict(value) for key, value in current_buy_counts.items()},
            "current_sell_by_timeframe": {key: dict(value) for key, value in current_sell_counts.items()},
            "current_buy_history_gate": {key: dict(value) for key, value in history_gate_counts.items()},
            "current_buy_lower_context": {key: dict(value) for key, value in lower_counts.items()},
            "current_class2_buys": dict(class2_counts),
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP4C生命周期:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("生命周期阶段:", output["summary"]["lifecycle_stage_by_timeframe"])
    print("当前买点:", output["summary"]["current_buy_by_timeframe"])
    print("当前卖点:", output["summary"]["current_sell_by_timeframe"])
    print("当前买点历史门:", output["summary"]["current_buy_history_gate"])
    print("当前买点低级别关系:", output["summary"]["current_buy_lower_context"])
    print("当前类二买:", output["summary"]["current_class2_buys"])

    if args.strict:
        problems: list[str] = []
        if symbols and len(analyzed) / len(symbols) < 0.95:
            problems.append(f"STEP4C分析成功率过低:{len(analyzed)}/{len(symbols)}")
        illegal_current = []
        for row in analyzed:
            for lifecycle in (row.get("signal_lifecycle") or {}).values():
                for key in ("current_buy", "current_sell"):
                    record = lifecycle.get(key)
                    if record and record.get("stage") not in {
                        SignalLifecycleStage.CONFIRMED.value,
                        SignalLifecycleStage.ACTIVE.value,
                    }:
                        illegal_current.append((row.get("code"), key, record.get("stage")))
        if illegal_current:
            problems.append(f"非当前生命周期错误成为current信号:{len(illegal_current)}")
        leaked_m5_primary = [row for row in analyzed if "5m" in (row.get("current_buy_contexts") or {})]
        if leaked_m5_primary:
            problems.append(f"5分钟错误成为主交易买点周期:{len(leaked_m5_primary)}")
        class2_as_standard = []
        for row in analyzed:
            for lifecycle in (row.get("signal_lifecycle") or {}).values():
                for record in lifecycle.get("records") or []:
                    if set(record.get("standard_types") or []) & {"STRONG_CLASS2_BUY", "CENTER_CLASS2_BUY"}:
                        class2_as_standard.append((row.get("code"), record.get("signal_id")))
        if class2_as_standard:
            problems.append(f"类二买错误成为standard signal:{len(class2_as_standard)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
