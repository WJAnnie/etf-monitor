from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_skill.a_share_reporting import DeliveryStatus, evaluate_delivery_gate, translate_scan_for_user
from trading_skill.notifications import notify_feishu


def _entry_plan_text(item: dict) -> str:
    labels = {
        "TEST": "首笔TEST试仓",
        "CONFIRMATION": "30分钟确认仓",
        "CORE": "120分钟确认后的核心仓升级",
        "TREND_ADD": "日线趋势加仓",
    }
    gates = {
        "INITIAL_EXECUTION_CHAIN": "日线二买授权+120m→30m→5m正式BUY链",
        "NEW_M30_STRUCTURE": "新的30分钟标准二买/三买",
        "NEW_M120_STRUCTURE": "新的120分钟标准二买/三买",
        "DAILY_TREND_CONTINUATION": "新的日线二买/三买趋势延续",
    }
    parts = []
    for rule in item.get("staged_entry_plan") or []:
        role = str(rule.get("role") or "")
        gate = str(rule.get("add_gate") or "")
        basis = str(rule.get("capacity_basis") or "")
        fraction = rule.get("capacity_fraction")
        if basis == "EXPLICIT_TEST_RISK":
            size_text = "按显式TEST风险预算+5分钟执行止损计算，不使用固定仓位比例"
        elif fraction is not None:
            try:
                size_text = f"最多使用当时剩余风险容量的{float(fraction):.0%}"
            except (TypeError, ValueError):
                size_text = "按当时剩余风险容量计算"
        else:
            size_text = "按当时剩余风险容量计算"
        parts.append(f"{labels.get(role, role)}（{gates.get(gate, gate)}；{size_text}）")
    return " → ".join(parts) if parts else "当前仅观察，不生成分批建仓计划"


def _append_watch_candidates(lines: list[str], candidates: list[dict], structural_codes: set[str]) -> None:
    watch = [item for item in candidates if item.get("code") not in structural_codes and item.get("recent_signal_note")]
    if not watch:
        return
    watch.sort(
        key=lambda item: (
            item.get("signal") == "二买",
            item.get("timeframe") == "日线",
            item.get("structural_execution_maturity") == "准备",
            -(abs(float(item.get("rise_since_signal_pct") or 999))),
        ),
        reverse=True,
    )
    lines.extend(["", f"【近期结构观察】共{len(watch)}只，以下列出最值得继续跟踪的前8只："])
    for idx, item in enumerate(watch[:8], 1):
        label = item.get("signal_label") or item.get("signal")
        lines.append(
            f"{idx}. {item.get('name')}（{item.get('code')}）｜{item.get('timeframe')} {label}｜"
            f"执行链:{item.get('structural_execution_maturity', '观察')}｜买点后{item.get('rise_since_signal_pct', '暂无')}%｜"
            f"{item.get('action', '观察')}｜{item.get('recent_signal_note', '')}"
        )


def _append_industry_intelligence(lines: list[str], universe: dict) -> None:
    intelligence = list(universe.get("industry_intelligence") or [])
    if not intelligence:
        return
    ranked = sorted(
        intelligence,
        key=lambda item: (
            abs(int(item.get("net_event_score") or 0)),
            int(item.get("positive_score") or 0) + int(item.get("negative_score") or 0),
        ),
        reverse=True,
    )
    meaningful = [item for item in ranked if item.get("major_positive") or item.get("major_negative") or item.get("report_events")]
    if not meaningful:
        return
    lines.extend(["", "【行业重大事件 / 财报动态】"])
    for item in meaningful[:8]:
        positives = list(item.get("major_positive") or [])
        negatives = list(item.get("major_negative") or [])
        reports = list(item.get("report_events") or [])
        summary = []
        if positives:
            summary.append("利好：" + "；".join(str(event.get("title") or "") for event in positives[:2]))
        if negatives:
            summary.append("利空：" + "；".join(str(event.get("title") or "") for event in negatives[:2]))
        if reports:
            summary.append("财报：" + "；".join(str(event.get("title") or "") for event in reports[:2]))
        lines.append(f"- {item.get('industry_name')}｜事件净分{item.get('net_event_score', 0):+}｜" + "｜".join(summary))


def _universe_by_code(universe: dict) -> dict[str, dict]:
    return {str(item.get("code") or ""): item for item in (universe.get("leader_candidates") or [])}


def build_report(scan: dict, universe: dict, *, stage: str) -> tuple[str, str]:
    candidates = list(scan.get("candidates") or [])
    structural_candidates = [item for item in candidates if item.get("push") is not False]
    structural_codes = {item.get("code") for item in structural_candidates}
    selected = list(universe.get("selected_industries") or [])
    prospect_industries = [item for item in selected if item.get("prospect_theme")]
    supplement_industries = [item for item in selected if not item.get("prospect_theme")]
    quarantined = list(universe.get("quarantined_high_position_industries") or [])
    financial_enrichment = universe.get("financial_detail_enrichment") or {}
    financial_status = financial_enrichment.get("status_counts") or {}
    source_by_code = _universe_by_code(universe)

    title = "🎯 全A结构机会扫描" if structural_candidates else "📊 全A扫描完成"
    lines = [
        f"【扫描时点】{stage}",
        "",
        "【扫描范围】",
        f"全A股票：{scan.get('total_stocks', 0)}只",
        f"行业/细分板块：{scan.get('industries_screened', 0)}个",
        f"长期前景行业：{len(prospect_industries)}个｜动态/结构补充：{len(supplement_industries)}个｜高位暂时隔离：{len(quarantined)}个",
        f"行业前五+跨行业去重候选：{universe.get('deduped_leader_candidates', scan.get('leader_candidates', 0))}只",
        f"允许长历史深扫：{universe.get('deep_scan_eligible_candidates', scan.get('fundamental_passed', 0))}只",
        f"实际五周期深扫：{scan.get('deep_scanned', 0)}只",
        f"近期出现日线正式结构：{scan.get('chan_buy_candidates', 0)}只",
        f"当前达到结构准备/触发标准：{len(structural_candidates)}只",
        f"行业专属财务证据：{financial_status or '尚未增强/无统计'}",
        "",
        "【行业逻辑】重点行业每日动态轮换：长期前景、新升温、重大事件与结构补充共同参与；明显高位行业暂缓，回落后重新纳入。利好不能制造买点，重大利空可以暂停新开仓。",
        "【基本面逻辑】不同行业真正使用不同证据：银行看PB/ROE/不良率/核心一级资本，保险看偿付能力/投资收益，券商看PB/ROE/净资本，周期资源看PB/库存/资本开支，订单制造看合同负债/在建工程，科技医药看研发强度，消费看库存与收入匹配，公用事业/地产建筑看负债率、建设投入与合同负债。行业专属证据缺失时只保留观察，不能直接新开仓。",
        "【缠论层级】周线=战略环境；日线标准二买/类二买=唯一新开仓结构授权；日线一买只WAIT_2B，日线三买用于已有仓位趋势延续；120分钟=当前日线结构确认；30分钟=执行准备；5分钟正式BUY=最终执行触发。",
        "【结构归属】120分钟、30分钟、5分钟确认必须属于当前这一次日线二买结构；旧低周期买点不能确认新的日线买点。",
        "【指标逻辑】MACD(6,13,4)、BOLL、KDJ、量价只做辅助确认或PAUSE，不能定义买点，也不能替代缺失的5分钟正式BUY。",
        "【仓位逻辑】第一笔永远是TEST：按显式TEST风险预算和5分钟执行止损距离计算；新的30分钟结构最多用剩余风险容量30%，新的120分钟结构最多50%，新的日线趋势延续最多25%；禁止机械摊低成本。",
        "【双止损】日线二买结构失效位负责核心逻辑；当前日线结构内5分钟正式BUY失效位负责首笔TEST风险。两者必须保留为不同周期、不同signal。",
        "【卖出逻辑】不设固定百分比主止盈。5m/30m/120m/日线/周线的一卖、二卖、三卖逐级影响不同仓层；日线/周线三卖才完成相应全退出。",
        "【下单边界】结构准备/触发不等于券商下单许可；真实数量仍需STEP5A/5B核验账户权限、组合/行业/主题风险、现金、lot size与显式TEST风险预算。",
    ]

    if prospect_industries:
        names = [f"{item.get('name')}（{item.get('prospect_theme') or item.get('name')}）" for item in prospect_industries[:12]]
        lines.extend(["", "【重点前景方向】" + "、".join(names)])
    if quarantined:
        lines.extend(["", "【高位暂缓方向】" + "、".join(str(item.get("name") or "") for item in quarantined[:10])])
    _append_industry_intelligence(lines, universe)

    if not structural_candidates:
        lines.extend(["", "【结论】本轮没有达到结构准备/触发标准的股票，不为了凑数量而降低日线二买、行业财务证据或多周期正式BUY要求。"])
        _append_watch_candidates(lines, candidates, structural_codes)
        return title, "\n".join(lines)

    lines.extend(["", "【结构准备/触发候选】"])
    for idx, item in enumerate(structural_candidates[:15], 1):
        source = source_by_code.get(str(item.get("code") or ""), {})
        fp = source.get("fundamental_prefilter") or {}
        signal_label = item.get("signal_label") or item.get("signal", "暂无")
        metric_focus = fp.get("metric_focus") or item.get("industry_metric_focus") or []
        valuation_focus = fp.get("valuation_focus") or item.get("industry_valuation_focus") or []
        metric_text = "、".join(metric_focus[:6]) if isinstance(metric_focus, list) and metric_focus else "按行业口径"
        valuation_text = "、".join(valuation_focus[:4]) if isinstance(valuation_focus, list) and valuation_focus else "按行业口径"
        evidence_reasons = list(fp.get("industry_evidence_reasons") or [])
        evidence_text = "；".join(evidence_reasons[:4]) if evidence_reasons else "暂无额外行业专属加减分证据"
        execution_ids = item.get("execution_chain_evidence") or {}
        chain_ids = "、".join(f"{tf}:{(evidence or {}).get('signal_id', '暂无')}" for tf, evidence in execution_ids.items()) or "暂无"
        lines.extend([
            "",
            f"{idx}. {item.get('name', '未知')}（{item.get('code', '')}）",
            f"前景主题：{item.get('prospect_theme') or '跨行业/市场结构补充'}",
            f"细分行业：{source.get('industry_name') or item.get('industry', '暂无')}｜行业节奏：{item.get('industry_state', '暂无')}｜事件风险：{item.get('industry_event_risk') or 'UNKNOWN'}（净分{item.get('industry_event_score', 0) or 0:+}）",
            f"行业位置：{item.get('leader_rank', '暂无')}｜基本面：{fp.get('grade', item.get('fundamental_grade', '暂无'))}级｜最新财报：{(fp.get('latest') or {}).get('report_date') or item.get('latest_financial_report') or '暂无'}",
            f"行业财务证据：{fp.get('industry_evidence_status') or 'UNKNOWN'}｜直接新仓基本面许可：{'通过' if fp.get('eligible') else '未通过'}｜硬否决：{'是' if fp.get('hard_fail') else '否'}",
            f"行业专属判断：{evidence_text}",
            f"基本面原因：{'；'.join(list(fp.get('reasons') or [])[:4]) or '无额外否决'}",
            f"估值重点：{valuation_text}",
            f"行业财务重点：{metric_text}",
            f"日线授权：{signal_label}｜authority signal：{item.get('authority_signal_id') or '暂无'}",
            f"日线结构时间：{item.get('authority_structural_time') or '暂无'}｜买点确认时间：{item.get('signal_confirmation_time', '暂无')}",
            f"买点后涨幅：{item.get('rise_since_signal_pct', '暂无')}%｜状态：{item.get('recent_signal_note', '暂无')}",
            f"多周期执行：结构链={item.get('structural_execution_maturity', '暂无')}｜价格追高保护={item.get('price_distance_guard', '暂无')}｜证据={chain_ids}",
            f"上级结构：{item.get('parent_structure', '暂无')}｜技术确认：{item.get('technical', '暂无')}",
            f"辅助观察等级：{item.get('auxiliary_opportunity_grade', item.get('opportunity', '暂无'))}｜辅助风险状态：{item.get('auxiliary_risk_state', item.get('risk', '暂无'))}（不定义买点、不直接定仓）",
            f"结构动作：{item.get('action', '暂无')}｜交易许可：{item.get('trade_permission_state', '暂无')}｜真实下单就绪：{'是' if item.get('new_entry_order_ready') else '否'}",
            f"观察区间：{item.get('buy_point', '暂无')}",
            f"核心逻辑止损（日线）：{item.get('core_structural_stop') or item.get('stop') or '暂无'}｜首笔执行止损（5分钟）：{item.get('execution_stop') or '等待正式5分钟BUY'}",
            f"分批建仓：{_entry_plan_text(item)}",
            f"首笔定仓：{item.get('sizing_basis') or '需STEP5B显式风险上下文'}｜当前不输出伪精确资金/股数",
            f"后续加仓：{item.get('add_plan', '只有形成新的独立确认结构后再考虑加仓')}",
            "分批止盈/减仓：5m/30m先逐步处理TEST/确认风险；120m二卖/三卖开始轻度减核心；日线一卖/二卖减核心25%/50%；周线一卖/二卖保留核心50%/25%；日线/周线三卖才完成相应全退出。",
        ])
    _append_watch_candidates(lines, candidates, structural_codes)
    lines.extend(["", "说明：不因当天没有候选而放宽缠论或基本面证据标准。扩大的是历史覆盖、行业覆盖和有效观察窗口；行业专属财务证据不足、重大利空、事件上下文缺失或低周期链不完整时，结构仍可观察，但不能直接升级为新仓。"])
    return title, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scan", type=Path, default=Path("full-a-results/scan_latest.json"))
    parser.add_argument("--bars", type=Path, default=Path("full-a-results/candidate_bars_latest.json"))
    parser.add_argument("--universe", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--stage", required=True)
    args = parser.parse_args()

    scan = json.loads(args.scan.read_text(encoding="utf-8"))
    bars = json.loads(args.bars.read_text(encoding="utf-8"))
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    gate = evaluate_delivery_gate(bars, stage=args.stage)
    print(f"推送质量门：{gate.status.value}｜{gate.message}")

    if gate.status is DeliveryStatus.HOLIDAY_SKIP:
        print("当日无对应时点交易数据，本轮按休市处理，不发送旧行情。")
        return 0
    if gate.status is DeliveryStatus.DATA_INCOMPLETE:
        title = f"⚠️ 全A扫描数据异常｜{args.stage}"
        body = f"【结论】本轮数据未达到生产质量门槛，不生成买点结论。\n【原因】{gate.message}\n【覆盖】{gate.current_count}/{gate.total_count}"
        channel = notify_feishu(title, body)
        print(f"[WARN] 数据异常提醒已通过{channel}发送")
        return 2

    user_scan = translate_scan_for_user(scan)
    title, body = build_report(user_scan, universe, stage=args.stage)
    title = f"{title}｜{args.stage}"
    channel = notify_feishu(title, body)
    print(f"[OK] 全A扫描V3报告已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
