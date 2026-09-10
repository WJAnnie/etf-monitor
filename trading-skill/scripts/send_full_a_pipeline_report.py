from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping

from trading_skill.notifications import notify_feishu


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}顶层必须是JSON对象")
    return payload


def _identity(row: Mapping[str, Any]) -> str:
    market = str(row.get("market") if row.get("market") is not None else "")
    code = str(row.get("code") or "")
    security_type = str(row.get("security_type") or "")
    return f"{market}:{code}:{security_type}"


def _index(rows: list[dict]) -> dict[str, dict]:
    return {_identity(row): row for row in rows if _identity(row) != "::"}


def _quality_rows(payload: Mapping[str, Any]) -> list[dict]:
    return list(payload.get("stock_assessments") or []) + list(payload.get("fund_product_assessments") or [])


def _quality_status(row: Mapping[str, Any] | None) -> tuple[str, str, list[str]]:
    if not row:
        return "UNKNOWN", "UNKNOWN", ["STEP3质量记录缺失"]
    if str(row.get("security_type") or "") == "STOCK":
        final = dict(row.get("final_decision") or {})
        phase_a = dict(row.get("phase_a_assessment") or {})
        return (
            str(final.get("status") or "UNKNOWN"),
            str(phase_a.get("profile") or "UNKNOWN"),
            list(final.get("reasons") or []),
        )
    assessment = dict(row.get("assessment") or {})
    return (
        str(assessment.get("status") or "UNKNOWN"),
        str(row.get("fund_category") or "FUND"),
        list(assessment.get("reasons") or assessment.get("warnings") or []),
    )


def _specialized_text(row: Mapping[str, Any] | None) -> str:
    if not row or str(row.get("security_type") or "") != "STOCK":
        return ""
    evidence = row.get("specialized_evidence")
    if not isinstance(evidence, Mapping) or not evidence:
        return "行业专属证据：通用画像/本轮未要求3B"
    positives = list(evidence.get("positive_evidence") or [])
    warnings = list(evidence.get("warnings") or [])
    missing = list(evidence.get("missing_evidence") or [])
    parts = [
        f"3B={evidence.get('family','?')}/{evidence.get('quality','?')}/{evidence.get('coverage','?')}"
    ]
    if positives:
        parts.append("正向:" + "；".join(str(x) for x in positives[:2]))
    if warnings:
        parts.append("风险:" + "；".join(str(x) for x in warnings[:2]))
    if missing:
        parts.append("待补:" + "；".join(str(x) for x in missing[:2]))
    return "｜".join(parts)


def _stop_text(stop: Mapping[str, Any] | None) -> str:
    if not stop:
        return "暂无"
    if not stop.get("valid_for_new_entry"):
        return "未定义/不可用于新仓"
    price = stop.get("price") or stop.get("price_cny") or stop.get("price_ticks")
    return f"{stop.get('timeframe','?')} {price}｜signal={stop.get('signal_id') or '暂无'}"


def _permission_text(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "STEP5A记录缺失"
    permission = dict(row.get("permission") or {})
    state = str(permission.get("state") or "UNKNOWN")
    blockers = list(permission.get("blockers") or [])
    cautions = list(permission.get("cautions") or [])
    text = state
    if blockers:
        text += "｜阻断:" + "、".join(str(x) for x in blockers[:4])
    if cautions:
        text += "｜谨慎:" + "；".join(str(x) for x in cautions[:2])
    return text


def _sizing_text(row: Mapping[str, Any] | None) -> str:
    if not row:
        return "STEP5B记录缺失"
    sizing = dict(row.get("sizing") or {})
    state = str(sizing.get("state") or "UNKNOWN")
    if state != "SIZED":
        blockers = list(sizing.get("blockers") or [])
        return state + (("｜" + "、".join(str(x) for x in blockers[:3])) if blockers else "")
    return (
        f"SIZED｜TEST数量={sizing.get('quantity')}｜计划价={sizing.get('planned_entry_price')}｜"
        f"5m单位风险={sizing.get('unit_risk_cny')}｜计划风险={sizing.get('planned_risk_cny')}"
    )


def _allocation_index(payload: Mapping[str, Any]) -> dict[str, dict]:
    plan = dict(payload.get("plan") or {})
    out: dict[str, dict] = {}
    for row in list(plan.get("allocations") or []):
        identity = str(row.get("identity") or "")
        if identity:
            out[identity] = row
    return out


def _intent_index(payload: Mapping[str, Any]) -> dict[str, dict]:
    plan = dict(payload.get("plan") or {})
    out: dict[str, dict] = {}
    for row in list(plan.get("intents") or []):
        identity = str(row.get("identity") or "")
        if identity:
            out[identity] = row
    return out


def _fmt_pct(value: object) -> str:
    if value in (None, "", "-"):
        return "?"
    try:
        return f"{float(value):+.1f}%"
    except (TypeError, ValueError):
        return str(value)


def _industry_section(lines: list[str], candidates: Mapping[str, Any]) -> None:
    selected = list(candidates.get("selected_industries") or [])
    events = dict(candidates.get("industry_events") or {})
    if selected:
        lines.extend(["", "【今日重点行业】"])
        for idx, item in enumerate(selected[:12], 1):
            name = str(item.get("name") or "未知行业")
            lines.append(
                f"{idx}. {name}｜{item.get('pool','?')}/{item.get('state','?')}｜"
                f"质量{item.get('quality_score','?')}｜时点{item.get('timing_score','?')}｜"
                f"60日{_fmt_pct(item.get('change_60d'))}｜YTD{_fmt_pct(item.get('change_ytd'))}"
            )
            reason = str(item.get("selection_reason") or "")
            if reason:
                lines.append("   逻辑：" + reason)
            rows = list(events.get(name) or [])
            for event in rows[:2]:
                lines.append(
                    f"   {event.get('importance','重要')}{event.get('impact','中性')}："
                    f"{event.get('content') or event.get('title') or '事件内容缺失'}"
                )
    deferred = list(candidates.get("deferred_industries") or [])
    overheated = [row for row in deferred if str(row.get("state") or "") == "OVERHEATED"]
    if overheated:
        lines.extend([
            "",
            "【高位暂缓】" + "、".join(
                f"{row.get('name')}（60日{_fmt_pct(row.get('change_60d'))}）" for row in overheated[:10]
            ),
            "这些方向不是永久剔除；热度/位置冷却后重新参与每日行业排序。",
        ])


def _candidate_lines(
    item: Mapping[str, Any],
    *,
    quality: Mapping[str, Any] | None,
    permission: Mapping[str, Any] | None,
    sizing: Mapping[str, Any] | None,
    allocation: Mapping[str, Any] | None,
    intent: Mapping[str, Any] | None,
) -> list[str]:
    dominant = dict(item.get("dominant_current_buy") or {})
    executable = dict(item.get("best_executable_candidate") or {})
    signal = executable or dominant
    quality_state, profile, quality_reasons = _quality_status(quality)
    class2 = list(signal.get("class2_types") or [])
    reasons = list(signal.get("reasons") or [])
    out = [
        f"{item.get('name','未知')}（{item.get('code','')}｜{item.get('security_type','?')}）",
        f"结构：{signal.get('timeframe','暂无')} {signal.get('signal_type','暂无')}｜{signal.get('state','暂无')}"
        + (("｜类二买:" + "、".join(str(x) for x in class2)) if class2 else ""),
        f"结构说明：{'；'.join(str(x) for x in reasons[:2]) or '暂无'}",
        f"STEP3：{quality_state}｜画像={profile}｜{'；'.join(str(x) for x in quality_reasons[:2]) or '无额外说明'}",
    ]
    specialized = _specialized_text(quality)
    if specialized:
        out.append(specialized)
    if permission:
        event = dict(permission.get("event") or {})
        out.extend([
            f"事件：{event.get('state','UNKNOWN')}｜{event.get('note','暂无')}",
            f"双止损：日线authority={_stop_text(permission.get('authority_stop'))}｜5m execution={_stop_text(permission.get('execution_stop'))}",
            "STEP5A：" + _permission_text(permission),
        ])
    out.append("STEP5B：" + _sizing_text(sizing))
    if allocation:
        out.append(
            f"STEP5C：{allocation.get('state','UNKNOWN')}｜数量={allocation.get('allocated_quantity','-')}｜"
            f"金额={allocation.get('allocated_value_cny','-')}"
        )
    if intent:
        out.append(f"STEP5D：{intent.get('state','UNKNOWN')}｜仅执行意图/预留边界，不代表券商订单")
    return out


def build_report(
    candidates: dict,
    quality: dict,
    technical: dict,
    permission: dict,
    sizing: dict,
    allocation: dict,
    execution: dict,
    *,
    stage: str,
) -> tuple[str, str]:
    quality_idx = _index(_quality_rows(quality))
    permission_idx = _index(list(permission.get("symbols") or []))
    sizing_idx = _index(list(sizing.get("symbols") or []))
    allocation_idx = _allocation_index(allocation)
    intent_idx = _intent_index(execution)

    technical_rows = list(technical.get("symbols") or [])
    ready = [row for row in technical_rows if row.get("best_executable_candidate")]
    watch = [row for row in technical_rows if row.get("dominant_current_buy") and not row.get("best_executable_candidate")]
    permission_summary = dict(permission.get("summary") or {})
    sizing_summary = dict(sizing.get("summary") or {})
    allocation_summary = dict(allocation.get("summary") or {})
    execution_summary = dict(execution.get("summary") or {})
    candidate_summary = dict(candidates.get("summary") or {})
    quality_summary = dict(quality.get("summary") or {})

    title = f"🎯 全A策略扫描｜{stage}" if ready else f"📊 全A策略扫描｜{stage}"
    lines = [
        f"【扫描时点】{stage}",
        "",
        "【唯一交易合同】",
        "周线只做战略环境；日线标准二买/类二买是唯一新仓授权；日线一买WAIT_2B；日线三买只服务已有仓位趋势延续。",
        "120分钟→30分钟→5分钟必须是当前这一次日线结构内的正式BUY确认链；MACD/BOLL/KDJ/量价只能辅助确认或PAUSE，不能制造买点。",
        "首笔永远是TEST，数量只能由显式TEST风险预算和5分钟execution stop计算；日线authority stop负责核心逻辑，二者不得合并。",
        "本报告区分‘结构READY’与‘交易许可/仓位READY’。没有账户、组合、现金、risk-context时不会输出伪精确买入金额。",
        "",
        "【本轮总览】",
        f"可交易证券：{candidate_summary.get('tradeable_by_security_type',{})}",
        f"候选类型：{candidate_summary.get('candidate_by_security_type',{})}｜候选路线：{candidate_summary.get('candidate_by_route',{})}",
        f"STEP3股票最终质量：{quality_summary.get('final_stock_status',{})}｜基金产品质量：{quality_summary.get('fund_product_status',{})}",
        f"STEP4当前有买点结构：{(technical.get('summary') or {}).get('symbols_with_current_buy',0)}只｜日线二买执行链READY：{len(ready)}只",
        f"STEP5A许可：{permission_summary.get('permission_states',{})}｜允许新仓：{permission_summary.get('new_entry_allowed_symbols',0)}只",
        f"STEP5B：{sizing_summary.get('sizing_states',{})}｜已算出TEST数量：{sizing_summary.get('sized_symbols',0)}只",
        f"STEP5C：{allocation_summary.get('plan_state','UNKNOWN')}｜实际分配：{allocation_summary.get('allocated_symbols',0)}只",
        f"STEP5D：{execution_summary.get('reservation_plan_state','UNKNOWN')}｜券商订单创建：否",
    ]

    _industry_section(lines, candidates)

    if ready:
        lines.extend(["", f"【日线二买结构READY】共{len(ready)}只"])
        for idx, row in enumerate(ready[:12], 1):
            identity = _identity(row)
            lines.append("")
            lines.append(f"{idx}. " + _candidate_lines(
                row,
                quality=quality_idx.get(identity),
                permission=permission_idx.get(identity),
                sizing=sizing_idx.get(identity),
                allocation=allocation_idx.get(identity),
                intent=intent_idx.get(identity),
            )[0])
            lines.extend("   " + text for text in _candidate_lines(
                row,
                quality=quality_idx.get(identity),
                permission=permission_idx.get(identity),
                sizing=sizing_idx.get(identity),
                allocation=allocation_idx.get(identity),
                intent=intent_idx.get(identity),
            )[1:])
    else:
        lines.extend(["", "【结论】本轮没有完成日线二买授权 + 120m→30m→5m正式BUY链的结构READY候选；不为了凑数量降低标准。"])

    if watch:
        lines.extend(["", f"【结构观察】共{len(watch)}只，列出前10只"])
        state_rank = {
            "WAIT_LOWER_CONFIRMATION": 5,
            "WAIT_PULLBACK": 4,
            "WAIT_STANDARD_SECOND_BUY": 3,
            "READY_WITH_CAUTION": 2,
            "HISTORY_LIMITED": 1,
        }
        watch.sort(
            key=lambda row: state_rank.get(str((row.get("dominant_current_buy") or {}).get("state") or ""), 0),
            reverse=True,
        )
        for idx, row in enumerate(watch[:10], 1):
            signal = dict(row.get("dominant_current_buy") or {})
            identity = _identity(row)
            qstate, profile, _ = _quality_status(quality_idx.get(identity))
            lines.append(
                f"{idx}. {row.get('name')}（{row.get('code')}）｜{signal.get('timeframe')} {signal.get('signal_type')}｜"
                f"{signal.get('state')}｜STEP3 {qstate}/{profile}｜"
                f"{'；'.join(str(x) for x in list(signal.get('reasons') or [])[:1])}"
            )

    lines.extend([
        "",
        "【边界说明】结构READY只是技术结构成立；只有STEP5A允许、STEP5B完成TEST定仓、STEP5C完成共享组合分配并通过STEP5D外部预留边界后，才具备进一步执行条件。本流程不会自动创建券商订单。",
    ])
    return title, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidates", type=Path, default=Path("full-a-results/candidate_universe_latest.json"))
    parser.add_argument("--quality", type=Path, default=Path("full-a-results/fundamental_quality_latest.json"))
    parser.add_argument("--technical", type=Path, default=Path("full-a-results/step4_technical_opportunity_latest.json"))
    parser.add_argument("--permission", type=Path, default=Path("full-a-results/step5_trade_permission_latest.json"))
    parser.add_argument("--sizing", type=Path, default=Path("full-a-results/step5b_entry_sizing_latest.json"))
    parser.add_argument("--allocation", type=Path, default=Path("full-a-results/step5c_portfolio_allocation_latest.json"))
    parser.add_argument("--execution", type=Path, default=Path("full-a-results/step5d_execution_intent_latest.json"))
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()

    candidates = _load(args.candidates)
    quality = _load(args.quality)
    technical = _load(args.technical)
    permission = _load(args.permission)
    sizing = _load(args.sizing)
    allocation = _load(args.allocation)
    execution = _load(args.execution)
    title, body = build_report(candidates, quality, technical, permission, sizing, allocation, execution, stage=args.stage)
    channel = notify_feishu(title, body)
    print(f"[OK] 新版STEP1-5D全A报告已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
