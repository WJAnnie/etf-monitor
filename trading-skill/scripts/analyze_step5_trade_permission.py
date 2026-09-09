from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Mapping

from trading_skill.structural_stop import resolve_structural_stop
from trading_skill.trade_permission import (
    EventEntryState,
    evaluate_event_entry_state,
    evaluate_trade_permission,
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
        state = evaluate_event_entry_state(events, data_complete=feed_complete)
        return state, events, f"股票行业事件上下文:{industry}"

    category = str((quality_row or {}).get("fund_category") or "")
    family = str((quality_row or {}).get("fund_family") or "").strip()
    if category == "EQUITY_SECTOR":
        # 只有能够精确对应本轮已建立事件上下文的行业时才使用行业事件；不靠字符串猜测映射ETF。
        if family and family in selected_names:
            events = list(event_map.get(family) or [])
            state = evaluate_event_entry_state(events, data_complete=feed_complete)
            return state, events, f"行业ETF精确匹配事件上下文:{family}"
        return EventEntryState.UNKNOWN, [], "行业ETF缺少可验证的精确行业事件映射；不得静默视为CLEAR"

    # 当前事件源只能证明“已映射行业的36小时事件”是否清晰。宽基/策略/跨境/商品/债券
    # 需要各自的市场、海外、商品或利率事件源；在这些适配器接入前不能把“行业门不适用”写成全局CLEAR。
    return (
        EventEntryState.UNKNOWN,
        [],
        f"{category or 'FUND'}缺少对应的宏观/跨境/商品/利率事件适配器；当前行业事件源不足以证明事件风险CLEAR",
    )


def _proposal(technical_row: Mapping[str, Any]) -> dict | None:
    executable = technical_row.get("best_executable_candidate")
    if isinstance(executable, Mapping) and executable:
        return dict(executable)
    dominant = technical_row.get("dominant_current_buy")
    if isinstance(dominant, Mapping) and str(dominant.get("state") or "") == "PREPARE_FIRST_BUY":
        return dict(dominant)
    return None


def _load_risk_context(path: Path | None) -> dict:
    if path is None:
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _context_for_symbol(context: Mapping[str, Any], item: Mapping[str, Any]) -> dict[str, bool]:
    defaults = {
        # STEP1只证明“本策略允许扫描/研究这个证券”，不等价于真实券商账户权限已核验。
        "strategy_security_permission_known": True,
        "strategy_security_allowed": True,
        "account_context_known": False,
        "account_allows_security": False,
        # 没有真实持仓/风险账本时绝不假设还有组合风险空间。
        "portfolio_context_known": False,
        "portfolio_allows_new_risk": False,
    }
    for key in ("account_context_known", "account_allows_security", "portfolio_context_known", "portfolio_allows_new_risk"):
        if key in context:
            defaults[key] = bool(context.get(key))

    symbol_map = dict(context.get("symbols") or {})
    code = str(item.get("code") or "")
    market = str(item.get("market") if item.get("market") is not None else "")
    security_type = str(item.get("security_type") or "")
    candidates = (f"{market}:{code}:{security_type}", f"{market}:{code}", code)
    override = next((symbol_map.get(key) for key in candidates if isinstance(symbol_map.get(key), Mapping)), None)
    if isinstance(override, Mapping):
        for key in ("account_context_known", "account_allows_security", "portfolio_context_known", "portfolio_allows_new_risk"):
            if key in override:
                defaults[key] = bool(override.get(key))
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
    stop = resolve_structural_stop(structure_row or {}, proposal)
    context = _context_for_symbol(risk_context, item)

    permission = evaluate_trade_permission(
        item,
        quality_status=quality_status,
        quality_deep_analysis_eligible=deep_eligible,
        event_state=event_state,
        structural_stop_defined=stop.valid_for_new_entry,
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
        "event": {
            "state": event_state.value,
            "events": events,
            "note": event_note,
        },
        "structural_stop": stop.to_dict(),
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
            quality_row = _find_row(item, quality_exact, quality_by_code)
            structure_row = _find_row(item, structure_exact, structure_by_code)
            analyzed.append(
                analyze_symbol(
                    item,
                    quality_row=quality_row,
                    candidate_payload=candidate_payload,
                    structure_row=structure_row,
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
        blocker
        for row in analyzed
        for blocker in ((row.get("permission") or {}).get("blockers") or [])
    )
    proposed = [
        row for row in analyzed
        if (row.get("best_executable_candidate") or str(((row.get("dominant_current_buy") or {}).get("state") or "")) == "PREPARE_FIRST_BUY")
    ]
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
            "step3_pass_is_required_for_automatic_new_entry": True,
            "step3_watch_or_unknown_requires_review_not_risk_discount": True,
            "only_explicit_step3_reject_is_quality_hard_veto": True,
            "major_negative_event_blocks_new_entry": True,
            "missing_event_context_is_not_clear": True,
            "fund_event_scope_must_match_product_exposure_before_clear": True,
            "unmapped_stock_industry_event_scope_is_unknown": True,
            "structural_stop_must_come_from_matching_step4b_signal": True,
            "fixed_percent_cost_basis_and_atr_are_not_stop_substitutes": True,
            "step1_strategy_security_permission_is_distinct_from_real_account_permission": True,
            "unknown_account_permission_never_defaults_to_allowed": True,
            "unknown_portfolio_capacity_never_defaults_to_available": True,
            "120m_first_buy_can_only_request_test_entry_permission": True,
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
            "standard_entry_allowed": sum(1 for row in allowed if (row.get("permission") or {}).get("entry_mode") == "STANDARD"),
            "test_entry_allowed": sum(1 for row in allowed if (row.get("permission") or {}).get("entry_mode") == "TEST"),
            "risk_context_supplied": bool(args.risk_context),
        },
        "symbols": analyzed,
    }
    atomic_json(args.output, output)

    print("STEP5A交易许可:", len(analyzed), "/", len(symbols), "异常:", len(errors))
    print("许可状态:", output["summary"]["permission_states"])
    print("阻断/待补上下文:", output["summary"]["blockers"])
    print("提出新开仓许可复核:", len(proposed), "最终允许:", len(allowed))

    if args.strict:
        problems: list[str] = []
        if symbols and len(analyzed) / len(symbols) < 0.99:
            problems.append(f"STEP5A分析成功率过低:{len(analyzed)}/{len(symbols)}")

        illegal_allowed: list[tuple] = []
        score_leaks: list[str] = []
        for row in analyzed:
            permission = dict(row.get("permission") or {})
            if any("score" in str(key).lower() for key in permission):
                score_leaks.append(str(row.get("code")))
            if not permission.get("new_entry_allowed"):
                continue
            quality = dict(row.get("quality") or {})
            event = dict(row.get("event") or {})
            stop = dict(row.get("structural_stop") or {})
            context = dict(row.get("risk_context") or {})
            if quality.get("status") != "PASS":
                illegal_allowed.append((row.get("code"), "QUALITY", quality.get("status")))
            if event.get("state") in {EventEntryState.UNKNOWN.value, EventEntryState.BLOCK_NEW_ENTRY.value}:
                illegal_allowed.append((row.get("code"), "EVENT", event.get("state")))
            if not stop.get("valid_for_new_entry"):
                illegal_allowed.append((row.get("code"), "STOP", False))
            if not context.get("account_context_known") or not context.get("account_allows_security"):
                illegal_allowed.append((row.get("code"), "ACCOUNT", context))
            if not context.get("portfolio_context_known") or not context.get("portfolio_allows_new_risk"):
                illegal_allowed.append((row.get("code"), "PORTFOLIO", context))

        if illegal_allowed:
            problems.append(f"STEP5A出现违反独立许可门的自动新开仓:{len(illegal_allowed)}")
        if score_leaks:
            problems.append(f"STEP5A重新引入综合score字段:{len(score_leaks)}")
        if not args.risk_context and allowed:
            problems.append("未提供真实账户/组合风险上下文时STEP5A错误地产生了自动新开仓许可")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
