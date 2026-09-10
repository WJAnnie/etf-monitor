from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path

from trading_skill.execution_intent import ReservationPlanState, build_execution_reservation_plan


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_optional_object(path: Path | None, *, label: str) -> dict | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{label}顶层必须是JSON对象")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--allocation",
        type=Path,
        default=Path("full-a-results/step5c_portfolio_allocation_latest.json"),
    )
    parser.add_argument(
        "--sizing",
        type=Path,
        default=Path("full-a-results/step5b_entry_sizing_latest.json"),
    )
    parser.add_argument("--reservation-context", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("full-a-results/step5d_execution_intent_latest.json"),
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    allocation_payload = json.loads(args.allocation.read_text(encoding="utf-8"))
    sizing_payload = json.loads(args.sizing.read_text(encoding="utf-8"))
    if not isinstance(allocation_payload, dict) or not isinstance(sizing_payload, dict):
        raise ValueError("STEP5C/STEP5B输入顶层必须是JSON对象")
    sizing_rows = list(sizing_payload.get("symbols") or [])
    reservation_context = _load_optional_object(args.reservation_context, label="reservation-context")

    plan = build_execution_reservation_plan(allocation_payload, sizing_rows, reservation_context)
    plan_dict = plan.to_dict()
    intents = list(plan_dict.get("intents") or [])
    state_counts = Counter(str(item.get("state") or "UNKNOWN") for item in intents)
    allocated_input = [
        item
        for item in list(((allocation_payload.get("plan") or {}).get("allocations") or []))
        if str(item.get("state") or "") in {"ALLOCATED_FULL", "ALLOCATED_PARTIAL"}
    ]

    output = {
        "mode": "STEP5D_EXECUTION_INTENT_RESERVATION_BOUNDARY",
        "source_allocation": str(args.allocation),
        "source_sizing": str(args.sizing),
        "reservation_context_source": str(args.reservation_context) if args.reservation_context else None,
        "design_contract": {
            "step5c_allocation_is_never_expanded": True,
            "step5b_signal_stop_price_and_lot_are_revalidated": True,
            "reservation_is_one_atomic_bundle_not_per_symbol_partial_lock": True,
            "explicit_step5c_snapshot_id_is_part_of_plan_identity": True,
            "current_external_snapshot_must_match_before_reservation_request": True,
            "reservation_key_is_deterministic_and_idempotent": True,
            "signal_or_quantity_or_price_or_stop_change_changes_reservation_identity": True,
            "ready_to_reserve_is_not_durable_reserved": True,
            "reserved_requires_exact_external_durable_receipt": True,
            "stale_snapshot_requires_recomputing_step5b_and_step5c": True,
            "this_stage_does_not_write_a_fake_local_ledger": True,
            "this_stage_does_not_place_modify_or_cancel_broker_orders": True,
            "this_stage_does_not_manage_scale_in_or_sell_positions": True,
        },
        "input_allocated_symbols": len(allocated_input),
        "summary": {
            "reservation_plan_state": plan.state.value,
            "execution_intents": len(intents),
            "intent_states": dict(state_counts),
            "reservation_context_supplied": args.reservation_context is not None,
            "reservation_key_created": plan.reservation_key is not None,
            "durable_reservation_confirmed": plan.state is ReservationPlanState.RESERVED,
            "broker_order_created": False,
        },
        "plan": plan_dict,
    }
    atomic_json(args.output, output)

    print("STEP5D预留/执行意图状态:", plan.state.value)
    print("STEP5C实际分配:", len(allocated_input), "执行意图:", len(intents))
    print("意图状态:", dict(state_counts))
    print(
        "持久化预留确认:",
        plan.state is ReservationPlanState.RESERVED,
        "reservation_key:",
        plan.reservation_key,
    )

    if args.strict:
        problems: list[str] = []
        if not allocated_input:
            if plan.state is not ReservationPlanState.NO_ALLOCATION:
                problems.append(f"没有STEP5C实际分配时STEP5D必须NO_ALLOCATION，实际:{plan.state.value}")
            if intents:
                problems.append("没有STEP5C实际分配却生成执行意图")
        else:
            if args.reservation_context is None:
                problems.append("存在STEP5C实际分配但没有reservation-context；不得跳过外部snapshot/CAS边界")
            if plan.state in {
                ReservationPlanState.CONTEXT_REQUIRED,
                ReservationPlanState.STALE_SNAPSHOT,
                ReservationPlanState.CONTRACT_INVALID,
                ReservationPlanState.NO_ALLOCATION,
            }:
                problems.append(f"STEP5D预留合同未满足:{plan.state.value}")
            if len(intents) != len(allocated_input):
                problems.append(f"STEP5D执行意图数量与STEP5C实际分配不一致:{len(intents)}!={len(allocated_input)}")

        if plan.state is ReservationPlanState.READY_TO_RESERVE and output["summary"]["durable_reservation_confirmed"]:
            problems.append("READY_TO_RESERVE不得伪装成持久化RESERVED")
        if plan.state is ReservationPlanState.RESERVED and not plan.reservation_id:
            problems.append("RESERVED状态缺少reservation_id")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
