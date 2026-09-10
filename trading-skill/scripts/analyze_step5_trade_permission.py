from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from trading_skill.structural_stop import resolve_authority_stop, resolve_execution_stop
from trading_skill.trade_permission import EventEntryState, evaluate_event_entry_state, evaluate_trade_permission


_CONTEXT_BOOL_KEYS = (
    "account_context_known",
    "account_allows_security",
    "portfolio_context_known",
    "portfolio_allows_new_risk",
)


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _identity(item: Mapping[str, Any]) -> tuple[str, str, str]:
    return (
        str(item.get("code") or ""),
        str(item.get("market") if item.get("market") is not None else ""),
        str(item.get("security_type") or ""),
    )


def _index_rows(rows: list[dict]) -> tuple[dict[tuple[str, str, str], dict], dict[str, list[dict]]]:
    exact: dict[tuple[str, str, str], dict] = {}
    by_code: dict[str, list[dict]] = {}
    for row in rows:
        exact[_identity(row)] = row
        by_code.setdefault(str(row.get("code") or ""), []).append(row)
    return exact, by_code


def _find_row(
    item: Mapping[str, Any],
    exact: Mapping[tuple[str, str, str], dict],
    by_code: Mapping[str, list[dict]],
) -> dict | None:
    key = _identity(item)
    found = exact.get(key)
    if found is not None:
        return found
    matches = list(by_code.get(key[0], ()))
    return matches[0] if len(matches) == 1 else None


def _quality_rows(payload: Mapping[str, Any]) -> list[dict]:
    return list(payload.get("stock_assessments") or []) + list(payload.get("fund_product_assessments") or [])


def _quality_facts(row: Mapping[str, Any] | None) -> tuple[str, bool, str | None, str | None]:
    if row is None:
        return "UNKNOWN", False, None, None
    if str(row.get("security_type") or "") == "STOCK":
        decision = dict(row.get("final_decision") or {})
        return (
            str(decision.get("status") or "UNKNOWN"),
            bool(decision.get("deep_analysis_eligible")),
            str(row.get("industry_name") or "") or None,
            None,
        )
    assessment = dict(row.get("assessment") or {})
    return (
        str(assessment.get("status") or "UNKNOWN"),
        bool(assessment.get("deep_analysis_eligible")),
        None,
        str(row.get("fund_category") or "") or None,
    )


def _event_feed_complete(candidate_payload: Mapping[str, Any]) -> bool:
    sources = dict(candidate_payload.get("sources") or {})
    try:
        rows = int(sources.get("industry_news_rows") or 0)
    except (TypeError, ValueError):
        rows = 0
    errors = list(sources.get("industry_event_errors") or [])
    return rows > 0 and not errors


def _selected_industry_names(candidate_payload: Mapping[str, Any]) -> set[str]:
    return {
        str(item.get("name") or "").strip()
        for item in (candidate_payload.get("selected_industries") or [])
        if str(item.get("name") or "").strip()
    }


def _event_facts(
    quality_row: Mapping[str, Any] | None,
    candidate_payload: Mapping[str, Any],
) -> tuple[EventEntryState, list[dict], str]:
    feed_complete = _event_feed_complete(candidate_payload)
    event_map = dict(candidate_payload.get("industry_events") or {})
    selected_names = _selected_industry_names(candidate_payload)
    security_type = str((quality_row or {}).get("security_type") or "")

    if security_type == "STOCK":
        industry = str((quality_row or {}).get("industry_name") or "").strip()
        if not industry:
            return EventEntryState.UNKNOWN, [], "股票真实行业缺失，无法建立行业事件上下文"
        if industry not in selected_names:
            return EventEntryState.UNKNOWN, [], f"真实行业{industry}不在本轮已建立事件上下文的行业集合中，不能因event_map无记录而判CLEAR"
        events = list(event_map.get(industry) or [])
        return evaluate_event_entry_state(events, data_complete=feed_complete), events, f"股票行业事件上下文:{industry}"

    category = str((quality_row or {}).get("fund_category") or "")
    family = str((quality_row or {}).get("fund_family") or "").strip()
    if category == "EQUITY_SECTOR":
        if family and family in selected_names:
            events = list(event_map.get(family) or [])
            return evaluate_event_entry_state(events, data_complete=feed_complete), events, f"行业ETF精确匹配事件上下文:{family}"
        return EventEntryState.UNKNOWN, [], "行业ETF缺少可验证的精确行业事件映射；不得静默视为CLEAR"
    return (
        EventEntryState.UNKNOWN,
        [],
        f"{category or 'FUND'}缺少对应的宏观/跨境/商品/利率事件适配器；当前行业事件源不足以证明事件风险CLEAR",
    )


def _proposal(technical_row: Mapping[str, Any]) -> dict | None:
    executable = technical_row.get("best_executable_candidate")
    return dict(executable) if isinstance(executable, Mapping) and executable else None


def _validate_context_booleans(mapping: Mapping[str, Any], *, label: str) -> None:
    for key in _CONTEXT_BOOL_KEYS:
        if key in mapping and not isinstance(mapping.get(key), bool):
            raise ValueError(f"{label}.{key}必须是JSON boolean，不能使用字符串/数字代替")


def _load_risk_context(path: Path | None) -> dict:
    if path is None:
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("risk-context顶层必须是JSON对象")
    _validate_context_booleans(payload, label="risk_context")
    symbols = payload.get("symbols") or {}
    if not isinstance(symbols, dict):
        raise ValueError("risk_context.symbols必须是JSON对象")
    for symbol_key, override in symbols.items():
        if not isinstance(override, dict):
            raise ValueError(f"risk_context.symbols[{symbol_key}]必须是JSON对象")
        _validate_context_booleans(override, label=f"risk_context.symbols[{symbol_key}]")
    return payload


def _context_for_symbol(context: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, bool]:
    defaults = {
        "strategy_security_permission_known": True,
        "strategy_security_allowed": True,
        "account_context_known": False,
        "account_allows_security": False,
        "portfolio_context_known": False,
        "portfolio_allows_new_risk": False,
    }
    for key in _CONTEXT_BOOL_KEYS:
        if key in context:
            defaults[key] = context[key]

    symbol_map = dict(context.get("symbols") or {})
    code = str(item.get("code") or "")
    market = str(item.get("market") if item.get("market") is not None else "")
    security_type = str(item.get("security_type") or "")
    candidates = (f"{market}:{code}:{security_type}", f"{market}:{code}", code)
    override = next((symbol_map.get(key) for key in candidates if isinstance(symbol_map.get(key), Mapping)), None)
    if isinstance(override, Mapping):
        for key in _CONTEXT_BOOL_KEYS:
            if key in override:
                defaults[key] = override[key]
    return defaults


def analyze_symbol(
    item: dict,
    *,
    quality_row: dict | None,
    candidate_payload: Mapping[str, Any],
    structure_row: dict | None,
    risk_context: Mapping[str, Any],
) -> dict:
    quality_status, deep_eligible, industry_name, fund_category = _quality_facts(quality_row)
    event_state, events, event_note = _event_facts(quality_row, candidate_payload)
    proposal = _proposal(item)
    structure = structure_row or {}
    authority_stop = resolve_authority_stop(structure, proposal)
    execution_stop = resolve_execution_stop(structure, proposal)
    context = _context_for_symbol(risk_context, item)

    permission = evaluate_trade_permission(
        item,
        quality_status=quality_status,
        quality_deep_analysis_eligible=deep_eligible,
        event_state=event_state,
        authority_stop_defined=authority_stop.valid_for_new_entry,
        execution_stop_defined=execution_stop.valid_for_new_entry,
        account_context_known=context["account_context_known"],
        account_allows_security=context["account_allows_security"],
        portfolio_context_known=context["portfolio_context_known"],
        portfolio_allows_new_risk=context["portfolio_allows_new_risk"],
    )
    return {
        "code": item.get("code"),
        "name": item.get("name"),
        "market": item.get("market"),
        "security_type": item.get("security_type"),
        "quality": {
            "status": quality_status,
            "deep_analysis_eligible": deep_eligible,
            "industry_name": industry_name,
            "fund_category": fund_category,
        },
        "event": {"state": event_state.value, "events": events, "note": event_note},
        "authority_stop": authority_stop.to_dict(),
        "execution_stop": execution_stop.to_dict(),
        # Compatibility alias for consumers that display the core thesis stop.
        "structural_stop": authority_stop.to_dict(),
        "risk_context": context,
        "permission": permission.to_dict(),
        "dominant_current_buy": item.get("dominant_current_buy"),
        "best_executable_candidate": item.get("best_executable_candidate"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--technical", type=Path, default=Path("full-a-results/step4_technical_opportunity_latest.json"))
    parser.add_argument("--quality", type=Path, default=Path("full-a-results/fundamental_quality_latest.json"))
    parser.add_argument("--candidates", type=Path, default=Path("full-a-results/candidate_universe_latest.json"))
    parser.add_argument("--structure", type=Path, default=Path("full-a-results/step4_structure_latest.json"))
    parser.add_argument("--risk-context", type=Path, default=None)
    parser.add_argument("--output", type=Path, default=Path("full-a-results/step5_trade_permission_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    technical_payload = json.loads(args.technical.read_text(encoding="utf-8"))
    quality_payload = json.loads(args.quality.read_text(encoding="utf-8"))
    candidate_payload = json.loads(args.candidates.read_text(encoding="utf-8"))
    structure_payload = json.loads(args.structure.read_text(encoding="utf-8"))
    risk_context = _load_risk_context(args.risk_context)

    quality_exact, quality_by_code = _index_rows(_quality_rows(quality_payload))
    structure_exact, structure_by_code = _index_rows(list(structure_payload.get("symbols") or []))
    symbols = list(technical_payload.get("symbols") or [])
    analyzed: list[dict] = []
    errors: list[dict] = []

    for item in symbols:
        try:
            analyzed.append(
                analyze_symbol(
                    item,
                    quality_row=_find_row(item, quality_exact, quality_by_code),
                    candidate_payload=candidate_payload,
                    structure_row=_find_row(item, structure_exact, structure_by_code),
                    risk_context=risk_context,
                )
            )
        except Exception as exc:
            errors.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "security_type": item.get("security_type"),
                "error": str(exc)[:1000],
            })

    permission_counts = Counter(str((row.get("permission") or {}).get("state") or "UNKNOWN") for row in analyzed)
    blocker_counts = Counter(
        blocker for row in analyzed for blocker in ((row.get("permission") or {}).get("blockers") or [])
    )
    proposed = [row for row in analyzed if row.get("best_executable_candidate")]
    allowed = [row for row in analyzed if bool((row.get("permission") or {}).get("new_entry_allowed"))]

    output = {
        "mode": "STEP5A_NEW_ENTRY_PERMISSION",
        "source_technical": str(args.technical),
        "source_quality": str(args.quality),
        "source_candidates": str(args.candidates),
        "source_structure": str(args.structure),
        "risk_context_source": str(args.risk_context) if args.risk_context else None,
        "design_contract": {
            "facts_are_gates_not_weighted_score": True,
            "step4d_is_not_recomputed": True,
            "new_entry_requires_daily_second_buy_authority": True,
            "daily_first_buy_and_daily_third_buy_do_not_create_fresh_entry": True,
            "m120_m30_m5_never_create_standalone_new_entry": True,
            "step3_pass_is_required_for_automatic_new_entry": True,
            "major_negative_event_blocks_new_entry": True,
            "missing_event_context_is_not_clear": True,
            "authority_stop_is_daily_core_invalidation": True,
            "execution_stop_is_5m_test_sizing_stop": True,
            "authority_and_execution_stops_have_distinct_signal_identities": True,
            "fixed_percent_cost_basis_and_atr_are_not_stop_substitutes": True,
            "unknown_account_permission_never_defaults_to_allowed": True,
            "unknown_portfolio_capacity_never_defaults_to_available": True,
            "this_stage_does_not_compute_risk_amount_position_value_or_quantity": True,
        },
        "input_symbols": len(symbols),
        "analyzed_symbols": len(analyzed),
        "errors": errors,
        "summary": {
            "permission_states": dict(permission_counts),
            "blockers": dict(blocker_counts),
            "proposed_entry_symbols": len(proposed),
            "new_entry_allowed_symbols": len(allowed),
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP5A交易许可:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("许可状态:", dict(permission_counts))
    print("阻断项:", dict(blocker_counts))
    print("提出新开仓候选:", len(proposed), "允许:", len(allowed))

    if args.strict:
        problems: list[str] = []
        if symbols and len(analyzed) / len(symbols) < 0.99:
            problems.append(f"STEP5A分析成功率过低:{len(analyzed)}/{len(symbols)}")
        illegal = []
        for row in analyzed:
            permission = row.get("permission") or {}
            if permission.get("new_entry_allowed"):
                if permission.get("selected_timeframe") != "daily" or permission.get("signal_type") != "SECOND_BUY":
                    illegal.append((row.get("code"), "ENTRY_IDENTITY"))
                authority = row.get("authority_stop") or {}
                execution = row.get("execution_stop") or {}
                if authority.get("timeframe") != "daily" or authority.get("signal_id") != permission.get("signal_id"):
                    illegal.append((row.get("code"), "AUTHORITY_STOP"))
                if execution.get("timeframe") != "5m" or execution.get("authority_signal_id") != permission.get("signal_id"):
                    illegal.append((row.get("code"), "EXECUTION_STOP"))
        if illegal:
            problems.append(f"STEP5A允许项违反日线授权/5分钟执行双止损合同:{len(illegal)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
