from __future__ import annotations

import argparse
import json
import os
from collections import Counter, defaultdict
from pathlib import Path

from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.technical_opportunity import (
    EXECUTABLE_STATES,
    best_executable_candidate,
    build_technical_opportunities,
    dominant_current_buy,
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def analyze_symbol(item: dict) -> dict:
    opportunities = build_technical_opportunities(item.get("current_buy_contexts") or {})
    dominant = dominant_current_buy(opportunities)
    executable = best_executable_candidate(opportunities)
    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "market": item.get("market"),
        "security_type": item.get("security_type"),
        "step3_status": item.get("step3_status"),
        "step3_tier": item.get("step3_tier"),
        "research_priority": item.get("research_priority"),
        "opportunities": [opportunity.to_dict() for opportunity in opportunities],
        "dominant_current_buy": dominant.to_dict() if dominant else None,
        "best_executable_candidate": executable.to_dict() if executable else None,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--lifecycle",
        type=Path,
        default=Path("full-a-results/step4_signal_lifecycle_latest.json"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("full-a-results/step4_technical_opportunity_latest.json"),
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.lifecycle.read_text(encoding="utf-8"))
    symbols = list(payload.get("symbols") or [])
    analyzed: list[dict] = []
    errors: list[dict] = []
    state_counts: dict[str, Counter] = defaultdict(Counter)
    dominant_counts = Counter()
    executable_counts = Counter()

    for item in symbols:
        try:
            row = analyze_symbol(item)
            analyzed.append(row)
            for opportunity in row.get("opportunities") or []:
                timeframe = str(opportunity.get("timeframe") or "UNKNOWN")
                state = str(opportunity.get("state") or "UNKNOWN")
                state_counts[timeframe][state] += 1
            dominant = row.get("dominant_current_buy")
            if dominant:
                dominant_counts[f"{dominant.get('timeframe')}:{dominant.get('signal_type')}:{dominant.get('state')}"] += 1
            executable = row.get("best_executable_candidate")
            if executable:
                executable_counts[f"{executable.get('timeframe')}:{executable.get('signal_type')}:{executable.get('state')}"] += 1
        except Exception as exc:
            errors.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "security_type": item.get("security_type"),
                "error": str(exc)[:1000],
            })

    output = {
        "mode": "STEP4D_TECHNICAL_OPPORTUNITY_ELIGIBILITY",
        "source_lifecycle": str(args.lifecycle),
        "design_contract": {
            "no_weighted_or_composite_score": True,
            "dominant_current_buy_is_structural_significance_not_execution_permission": True,
            "best_executable_candidate_is_daily_second_buy_only": True,
            "daily_first_buy_waits_for_second_buy": True,
            "daily_third_buy_is_continuation_not_fresh_entry": True,
            "m120_m30_m5_never_become_standalone_new_entry": True,
            "class2_is_metadata_on_standard_second_buy_not_independent_permission": True,
            "execution_chain_requires_formal_m120_m30_m5_buy": True,
            "technical_indicators_may_pause_but_never_create_buy_point": True,
            "history_parent_structure_parent_signal_and_execution_are_separate_facts": True,
            "this_stage_does_not_size_positions_or_emit_final_order": True,
        },
        "input_symbols": len(symbols),
        "analyzed_symbols": len(analyzed),
        "errors": errors,
        "summary": {
            "opportunity_state_by_timeframe": {key: dict(value) for key, value in state_counts.items()},
            "dominant_current_buy": dict(dominant_counts),
            "best_executable_candidate": dict(executable_counts),
            "symbols_with_current_buy": sum(1 for row in analyzed if row.get("dominant_current_buy")),
            "symbols_with_executable_candidate": sum(1 for row in analyzed if row.get("best_executable_candidate")),
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP4D技术机会:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("技术机会状态:", output["summary"]["opportunity_state_by_timeframe"])
    print("主结构买点:", output["summary"]["dominant_current_buy"])
    print("当前可执行技术候选:", output["summary"]["best_executable_candidate"])

    if args.strict:
        problems: list[str] = []
        if symbols and len(analyzed) / len(symbols) < 0.99:
            problems.append(f"STEP4D分析成功率过低:{len(analyzed)}/{len(symbols)}")

        illegal_executable = []
        score_leaks = []
        for row in analyzed:
            for opportunity in row.get("opportunities") or []:
                if opportunity.get("executable_candidate"):
                    if opportunity.get("state") not in {state.value for state in EXECUTABLE_STATES}:
                        illegal_executable.append((row.get("code"), "STATE", opportunity.get("state")))
                    if opportunity.get("timeframe") != Timeframe.DAILY.value:
                        illegal_executable.append((row.get("code"), "TIMEFRAME", opportunity.get("timeframe")))
                    if opportunity.get("signal_type") != ChanSignalType.SECOND_BUY.value:
                        illegal_executable.append((row.get("code"), "SIGNAL", opportunity.get("signal_type")))
                    if not opportunity.get("history_eligible"):
                        illegal_executable.append((row.get("code"), "HISTORY", False))
                    if opportunity.get("parent_current_sell_conflict"):
                        illegal_executable.append((row.get("code"), "PARENT_SELL", True))
                if any("score" in str(key).lower() for key in opportunity.keys()):
                    score_leaks.append(row.get("code"))

            executable = row.get("best_executable_candidate")
            if executable:
                if not executable.get("executable_candidate"):
                    illegal_executable.append((row.get("code"), "BEST_NOT_EXECUTABLE", executable.get("state")))
                if executable.get("timeframe") != Timeframe.DAILY.value or executable.get("signal_type") != ChanSignalType.SECOND_BUY.value:
                    illegal_executable.append((row.get("code"), "BEST_IDENTITY", executable))

        if illegal_executable:
            problems.append(f"STEP4D可执行候选违反日线二买授权合同:{len(illegal_executable)}")
        if score_leaks:
            problems.append(f"STEP4D重新引入综合score字段:{len(score_leaks)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
