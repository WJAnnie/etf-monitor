from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping

from trading_skill.entry_sizing import EntrySizingState, size_new_entry


_SIZING_KEYS = {
    "planned_entry_price",
    "lot_size",
    "existing_position_quantity",
    "standard_trade_risk_limit_cny",
    "test_trade_risk_limit_cny",
    "portfolio_risk_remaining_cny",
    "industry_risk_remaining_cny",
    "theme_risk_remaining_cny",
    "cash_available_cny",
    "security_value_remaining_cny",
    "industry_value_remaining_cny",
    "theme_value_remaining_cny",
}


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _load_context(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("risk-context顶层必须是JSON对象")
    symbols = payload.get("symbols") or {}
    if not isinstance(symbols, dict):
        raise ValueError("risk_context.symbols必须是JSON对象")
    for key, value in symbols.items():
        if not isinstance(value, dict):
            raise ValueError(f"risk_context.symbols[{key}]必须是JSON对象")
        # STEP5B涉及真实金额与仓位，单证券覆盖必须使用完整市场身份；
        # 不接受仅code/market:code的模糊覆盖，避免跨市场/证券类型串用额度。
        parts = str(key).split(":")
        if len(parts) != 3 or not all(parts):
            raise ValueError(
                f"risk_context.symbols[{key}]必须使用market:code:security_type完整身份，例如1:600000:STOCK"
            )
    return payload


def _symbol_override(context: Mapping[str, Any], item: Mapping[str, Any]) -> Mapping[str, Any] | None:
    symbols = context.get("symbols") or {}
    if not isinstance(symbols, Mapping):
        return None
    code = str(item.get("code") or "")
    market = str(item.get("market") if item.get("market") is not None else "")
    security_type = str(item.get("security_type") or "")
    if not code or not market or not security_type:
        return None
    value = symbols.get(f"{market}:{code}:{security_type}")
    return value if isinstance(value, Mapping) else None


def _sizing_context_for_symbol(context: Mapping[str, Any], item: Mapping[str, Any]) -> dict | None:
    if not context:
        return None
    resolved = {key: context[key] for key in _SIZING_KEYS if key in context}
    override = _symbol_override(context, item)
    if override is not None:
        for key in _SIZING_KEYS:
            if key in override:
                # 显式null也是覆盖值，不能因为是null又回退到全局额度。
                resolved[key] = override[key]
    return resolved or None


def _decimal_le(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) <= Decimal(str(right))
    except (InvalidOperation, TypeError, ValueError):
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--permission",
        type=Path,
        default=Path("full-a-results/step5_trade_permission_latest.json"),
    )
    parser.add_argument("--risk-context", type=Path, default=None)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("full-a-results/step5b_entry_sizing_latest.json"),
    )
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    permission_payload = json.loads(args.permission.read_text(encoding="utf-8"))
    context = _load_context(args.risk_context)
    symbols = list(permission_payload.get("symbols") or [])
    analyzed: list[dict] = []
    errors: list[dict] = []

    for item in symbols:
        try:
            sizing_context = _sizing_context_for_symbol(context, item)
            sizing = size_new_entry(item, sizing_context)
            analyzed.append(
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "market": item.get("market"),
                    "security_type": item.get("security_type"),
                    "permission": item.get("permission"),
                    "structural_stop": item.get("structural_stop"),
                    "sizing_context_supplied": sizing_context is not None,
                    "sizing": sizing.to_dict(),
                }
            )
        except Exception as exc:
            errors.append(
                {
                    "code": item.get("code"),
                    "name": item.get("name"),
                    "security_type": item.get("security_type"),
                    "error": str(exc)[:1000],
                }
            )

    state_counts = Counter(str((row.get("sizing") or {}).get("state") or "UNKNOWN") for row in analyzed)
    risk_binding_counts = Counter(
        item
        for row in analyzed
        for item in ((row.get("sizing") or {}).get("risk_binding_limits") or [])
    )
    quantity_binding_counts = Counter(
        item
        for row in analyzed
        for item in ((row.get("sizing") or {}).get("quantity_binding_limits") or [])
    )
    blocker_counts = Counter(
        item
        for row in analyzed
        for item in ((row.get("sizing") or {}).get("blockers") or [])
    )
    sized = [row for row in analyzed if (row.get("sizing") or {}).get("state") == EntrySizingState.SIZED.value]

    output = {
        "mode": "STEP5B_NEW_ENTRY_SIZING_ENVELOPE",
        "source_permission": str(args.permission),
        "risk_context_source": str(args.risk_context) if args.risk_context else None,
        "design_contract": {
            "step5a_permission_is_never_bypassed": True,
            "sizing_uses_explicit_money_limits_not_opportunity_grade": True,
            "sizing_uses_structural_stop_distance_only": True,
            "planned_entry_price_is_explicit_not_latest_close": True,
            "lot_size_is_explicit_not_hardcoded": True,
            "existing_position_must_use_scale_in_route": True,
            "industry_and_theme_caps_require_explicit_number_or_null": True,
            "test_entry_has_explicit_risk_limit_not_percentage_multiplier": True,
            "quantity_rounds_down_never_up": True,
            "decimal_math_is_used_for_price_and_money": True,
            "symbol_override_requires_exact_market_code_security_type_identity": True,
            "explicit_null_symbol_override_does_not_fall_back_to_global_value": True,
            "each_result_is_single_candidate_envelope_not_reserved_portfolio_allocation": True,
            "shared_capacity_is_not_summed_across_candidate_envelopes": True,
            "this_stage_does_not_choose_candidates_place_orders_add_positions_or_sell": True,
        },
        "input_symbols": len(symbols),
        "analyzed_symbols": len(analyzed),
        "errors": errors,
        "summary": {
            "sizing_states": dict(state_counts),
            "sized_symbols": len(sized),
            "blockers": dict(blocker_counts),
            "risk_binding_limits": dict(risk_binding_counts),
            "quantity_binding_limits": dict(quantity_binding_counts),
            "risk_context_supplied": bool(args.risk_context),
            # 故意不输出planned_value/planned_risk总和：多个envelope共用同一组合容量，相加会重复计算。
            "aggregate_envelope_value_is_intentionally_omitted": True,
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP5B新开仓仓位包络:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("仓位状态:", output["summary"]["sizing_states"])
    print("已算出数量:", len(sized), "阻断/上下文:", output["summary"]["blockers"])
    print("风险绑定:", output["summary"]["risk_binding_limits"])
    print("数量绑定:", output["summary"]["quantity_binding_limits"])

    if args.strict:
        problems: list[str] = []
        if symbols and len(analyzed) / len(symbols) < 0.99:
            problems.append(f"STEP5B分析成功率过低:{len(analyzed)}/{len(symbols)}")

        illegal: list[tuple] = []
        score_leaks: list[str] = []
        for row in analyzed:
            permission = dict(row.get("permission") or {})
            sizing = dict(row.get("sizing") or {})
            state = str(sizing.get("state") or "")
            allowed = permission.get("new_entry_allowed") is True
            if not allowed and state != EntrySizingState.NOT_ELIGIBLE.value:
                illegal.append((row.get("code"), "BYPASS_STEP5A", state))
            if state == EntrySizingState.SIZED.value:
                if not allowed:
                    illegal.append((row.get("code"), "SIZED_WITHOUT_PERMISSION", None))
                quantity = sizing.get("quantity")
                lot_size = sizing.get("lot_size")
                if not isinstance(quantity, int) or quantity <= 0:
                    illegal.append((row.get("code"), "INVALID_QUANTITY", quantity))
                if not isinstance(lot_size, int) or lot_size <= 0 or quantity % lot_size != 0:
                    illegal.append((row.get("code"), "LOT_VIOLATION", (quantity, lot_size)))
                if not _decimal_le(sizing.get("planned_risk_cny"), sizing.get("effective_risk_budget_cny")):
                    illegal.append((row.get("code"), "RISK_BUDGET_VIOLATION", None))
            if any("score" in str(key).lower() or "grade" in str(key).lower() for key in sizing.keys()):
                score_leaks.append(str(row.get("code")))

        if sized and not args.risk_context:
            problems.append("没有显式risk-context却生成了STEP5B仓位")
        if illegal:
            problems.append(f"STEP5B仓位合同违规:{len(illegal)}")
        if score_leaks:
            problems.append(f"STEP5B重新引入score/grade字段:{len(score_leaks)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
