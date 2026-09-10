from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from trading_skill.portfolio_allocation import (
    CandidateAllocationState,
    PortfolioAllocationState,
    allocate_new_entries,
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_context(path: Path | None) -> dict | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("allocation-context顶层必须是JSON对象")
    return payload


def _decimal(value: Any) -> Decimal:
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"不是有效Decimal:{value!r}") from exc
    if not result.is_finite():
        raise ValueError(f"不是有限Decimal:{value!r}")
    return result


def _sum_decimal(rows: list[dict], key: str) -> Decimal:
    total = Decimal("0")
    for row in rows:
        total += _decimal(row.get(key) or "0")
    return total


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _exact_identity(row: Mapping[str, Any]) -> str:
    market = str(row.get("market") if row.get("market") is not None else "")
    code = str(row.get("code") or "")
    security_type = str(row.get("security_type") or "")
    return f"{market}:{code}:{security_type}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--sizing",
        type=Path,
        default=Path("full-a-results/step5b_entry_sizing_latest.json"),
    )
    parser.add_argument("--allocation-context", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("full-a-results/step5c_portfolio_allocation_latest.json"),
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    sizing_payload = json.loads(args.sizing.read_text(encoding="utf-8"))
    symbols = list(sizing_payload.get("symbols") or [])
    context = _load_context(args.allocation_context)
    plan = allocate_new_entries(symbols, context)
    plan_dict = plan.to_dict()

    allocations = list(plan_dict.get("allocations") or [])
    allocation_counts = Counter(str(item.get("state") or "UNKNOWN") for item in allocations)
    allocated = [
        item
        for item in allocations
        if item.get("state")
        in {
            CandidateAllocationState.ALLOCATED_FULL.value,
            CandidateAllocationState.ALLOCATED_PARTIAL.value,
        }
    ]
    sized_input = [row for row in symbols if str((row.get("sizing") or {}).get("state") or "") == "SIZED"]
    allocation_order = list((context or {}).get("allocation_order") or [])
    total_value = _sum_decimal(allocated, "allocated_value_cny")
    total_risk = _sum_decimal(allocated, "allocated_risk_cny")

    output = {
        "mode": "STEP5C_SHARED_PORTFOLIO_ALLOCATION_PLAN",
        "source_sizing": str(args.sizing),
        "allocation_context_source": str(args.allocation_context) if args.allocation_context else None,
        "allocation_order": allocation_order,
        "design_contract": {
            "step5b_envelope_is_upper_bound_never_expanded": True,
            "allocation_order_is_explicit_external_input": True,
            "no_internal_score_grade_or_weighted_ranking": True,
            "shared_cash_and_risk_are_consumed_once_per_plan": True,
            "industry_and_theme_capacity_are_deducted_immediately": True,
            "lot_rounding_is_down_only": True,
            "unselected_sized_candidates_are_not_auto_inserted": True,
            "unselected_candidates_do_not_require_unused_dimension_context": True,
            "step5b_envelope_failure_is_distinct_from_allocation_context_failure": True,
            "snapshot_id_is_carried_for_later_compare_and_swap": True,
            "this_stage_creates_an_in_memory_plan_not_a_durable_reservation": True,
            "this_stage_does_not_place_orders_add_positions_or_sell": True,
        },
        "input_symbols": len(symbols),
        "sized_input_symbols": len(sized_input),
        "summary": {
            "plan_state": plan.state.value,
            "allocation_states": dict(allocation_counts),
            "allocated_symbols": len(allocated),
            "allocated_value_cny": _decimal_text(total_value),
            "allocated_risk_cny": _decimal_text(total_risk),
            "allocation_context_supplied": args.allocation_context is not None,
            "allocation_order_count": len(allocation_order),
            "durable_reservation_created": False,
        },
        "plan": plan_dict,
    }
    atomic_json(args.output, output)

    print("STEP5C组合分配状态:", plan.state.value)
    print("5B可分配包络:", len(sized_input), "显式顺序:", len(allocation_order))
    print("候选分配状态:", dict(allocation_counts))
    print(
        "实际分配:",
        len(allocated),
        "只，总市值:",
        _decimal_text(total_value),
        "总风险:",
        _decimal_text(total_risk),
    )

    if args.strict:
        problems: list[str] = []
        if not sized_input:
            if plan.state is not PortfolioAllocationState.NO_ELIGIBLE:
                problems.append(f"没有SIZED envelope时STEP5C必须NO_ELIGIBLE，实际:{plan.state.value}")
            if allocated:
                problems.append("没有SIZED envelope却产生组合分配")
        else:
            if args.allocation_context is None:
                problems.append("存在SIZED envelope但没有显式allocation-context")
            if plan.state is PortfolioAllocationState.CONTEXT_REQUIRED:
                problems.append("STEP5C共享组合上下文/上游envelope合同未满足")

        sized_by_identity = {_exact_identity(row): row for row in sized_input}
        for item in allocated:
            identity = str(item.get("identity") or "")
            source = sized_by_identity.get(identity)
            if source is None:
                problems.append(f"STEP5C分配了非SIZED身份:{identity}")
                continue
            envelope = dict(source.get("sizing") or {})
            allocated_quantity = item.get("allocated_quantity")
            envelope_quantity = envelope.get("quantity")
            lot_size = item.get("lot_size")
            if not isinstance(allocated_quantity, int) or allocated_quantity <= 0:
                problems.append(f"STEP5C非法分配数量:{identity}:{allocated_quantity}")
            if not isinstance(envelope_quantity, int) or allocated_quantity > envelope_quantity:
                problems.append(f"STEP5C突破5B envelope:{identity}:{allocated_quantity}>{envelope_quantity}")
            if not isinstance(lot_size, int) or lot_size <= 0 or allocated_quantity % lot_size != 0:
                problems.append(f"STEP5C交易单位违规:{identity}:{allocated_quantity}/{lot_size}")

        selected_identities = set(allocation_order)
        auto_inserted = [
            item.get("identity")
            for item in allocated
            if str(item.get("identity") or "") not in selected_identities
        ]
        if auto_inserted:
            problems.append(f"STEP5C自动插入未排序候选:{len(auto_inserted)}")

        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
