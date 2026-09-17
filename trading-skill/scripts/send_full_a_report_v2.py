from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_skill.a_share_reporting import DeliveryStatus, evaluate_delivery_gate, translate_scan_for_user
from trading_skill.notifications import notify_feishu


SIGNAL_CN = {
    "FIRST_BUY": "一买",
    "SECOND_BUY": "二买",
    "THIRD_BUY": "三买",
    "FIRST_SELL": "一卖",
    "SECOND_SELL": "二卖",
    "THIRD_SELL": "三卖",
}
TIMEFRAME_CN = {"weekly": "周线", "daily": "日线", "120m": "120分钟", "30m": "30分钟", "5m": "5分钟"}
MATURITY_CN = {"TRIGGERED": "已触发", "PREPARE": "准备中", "WATCH": "观察", "NOT_READY": "未就绪"}


def _money(value):
    if value in (None, "", 0):
        return "未配置总资金"
    try:
        return f"{float(value):,.0f}元"
    except (TypeError, ValueError):
        return str(value)


def _signal_list(values) -> str:
    items = [SIGNAL_CN.get(str(value), str(value)) for value in (values or [])]
    return "+".join(items) if items else "暂无"


def _tf(value) -> str:
    return TIMEFRAME_CN.get(str(value or ""), str(value or "暂无"))


def _append_watch_candidates(lines: list[str], candidates: list[dict], confirmed_codes: set[str]) -> None:
    watch = [item for item in candidates if item.get("code") not in confirmed_codes and item.get("recent_signal_note")]
    if not watch:
        return
    watch.sort(
        key=lambda item: (
            item.get("signal") == "二买",
            item.get("timeframe") == "120分钟",
            -(abs(float(item.get("rise_since_signal_pct") or 999))),
        ),
        reverse=True,
    )
    lines.extend(["", f"【近期买点观察】共{len(watch)}只，以下列出最值得继续跟踪的前8只："])
    for idx, item in enumerate(watch[:8], 1):
        maturity = MATURITY_CN.get(str(item.get("execution_maturity") or ""), item.get("execution_maturity") or "观察")
        exec_text = _signal_list(item.get("execution_signal_types"))
        lines.append(
            f"{idx}. {item.get('name')}（{item.get('code')}）｜母级{item.get('timeframe')} {item.get('signal')}｜"
            f"5分钟执行:{exec_text}｜{maturity}｜买点后{item.get('rise_since_signal_pct', '暂无')}%｜{item.get('risk', '暂无')}"
        )


def build_report(scan: dict, universe: dict, *, stage: str) -> tuple[str, str]:
    candidates = list(scan.get("candidates") or [])
    confirmed = [item for item in candidates if item.get("push") is not False]
    confirmed_codes = {item.get("code") for item in confirmed}
    selected = list(universe.get("selected_industries") or [])
    prospect_industries = [item for item in selected if item.get("prospect_theme")]
    supplement_industries = [item for item in selected if not item.get("prospect_theme")]
    fundamental_by_code = {
        str(item.get("code")): item.get("fundamental_prefilter") or {}
        for item in (universe.get("leader_candidates") or [])
    }
    title = "🎯 全A买点扫描" if confirmed else "📊 全A扫描完成"
    lines = [
        f"【扫描时点】{stage}",
        "",
        "【扫描范围】",
        f"全A股票：{scan.get('total_stocks', 0)}只",
        f"行业/细分板块：{scan.get('industries_screened', 0)}个",
        f"长期前景行业：{len(prospect_industries)}个｜市场结构补充：{len(supplement_industries)}个",
        f"行业前五+跨行业去重候选：{universe.get('deduped_leader_candidates', scan.get('leader_candidates', 0))}只",
        f"允许长历史深扫：{universe.get('deep_scan_eligible_candidates', scan.get('fundamental_passed', 0))}只",
        f"实际五周期深扫：{scan.get('deep_scanned', 0)}只",
        f"近期出现正式缠论买点：{scan.get('chan_buy_candidates', 0)}只",
        f"当前达到执行/准备标准：{len(confirmed)}只",
        "",
        "【行业逻辑】长期前景决定主要扫描池；过热行业只做动态停车、不删除长期主题身份，冷却后自动恢复；市场补充池优先寻找刚开始升温的新方向。",
        "【买点逻辑】周线只做战略过滤；日线/120分钟负责母级 setup；30分钟负责确认和收窄；第一笔买入必须由新鲜5分钟正式缠论买点触发。5分钟单独买点不能脱离母级结构下单。",
        "【指标逻辑】MACD/KDJ/布林带/量价只能确认、谨慎或暂停执行，不能创造一买/二买/三买。",
    ]

    if prospect_industries:
        names = []
        for item in prospect_industries[:12]:
            theme = item.get("prospect_theme") or item.get("name")
            names.append(f"{item.get('name')}（{theme}）")
        lines.extend(["", "【重点前景方向】" + "、".join(names)])

    if not confirmed:
        lines.extend(["", "【结论】本轮没有达到正式执行标准的股票，不为了凑数量而降低缠论定义。高周期旧买点可以继续观察，但没有5分钟结构触发就不执行首仓。"])
        _append_watch_candidates(lines, candidates, confirmed_codes)
        return title, "\n".join(lines)

    lines.extend(["", "【可执行/准备候选】"])
    for idx, item in enumerate(confirmed[:15], 1):
        fundamental = fundamental_by_code.get(str(item.get("code"))) or {}
        focus_metrics = "、".join(fundamental.get("focus_metrics") or []) or "暂无"
        missing_kpis = "、".join(fundamental.get("external_metrics_required") or []) or "无额外缺口"
        labels = "、".join(item.get("chan_labels") or []) or "无扩展标签"
        maturity = MATURITY_CN.get(str(item.get("execution_maturity") or ""), item.get("execution_maturity") or "暂无")
        lines.extend([
            "",
            f"{idx}. {item.get('name', '未知')}（{item.get('code', '')}）",
            f"前景主题：{item.get('prospect_theme') or '跨行业/市场结构补充'}",
            f"细分行业：{item.get('industry', '暂无')}｜行业节奏：{item.get('industry_state', '暂无')}",
            f"行业位置：{item.get('leader_rank', '暂无')}｜基本面：{item.get('fundamental_grade', '暂无')}级｜策略：{fundamental.get('policy_name', 'GENERIC_QUALITY')}",
            f"财务重点：{focus_metrics}",
            f"待补行业KPI：{missing_kpis}",
            f"母级 setup：{_tf(item.get('setup_timeframe'))} {_signal_list(item.get('setup_signal_types'))}",
            f"30分钟确认：{_tf(item.get('confirmation_timeframe'))} {_signal_list(item.get('confirmation_signal_types'))}",
            f"5分钟执行：{_tf(item.get('execution_timeframe'))} {_signal_list(item.get('execution_signal_types'))}｜执行状态：{maturity}",
            f"缠论扩展标注：{labels}",
            f"母级买点确认时间：{item.get('signal_confirmation_time', '暂无')}",
            f"母级买点后涨幅：{item.get('rise_since_signal_pct', '暂无')}%｜状态：{item.get('recent_signal_note', '暂无')}",
            f"上级结构：{item.get('parent_structure', '暂无')}",
            f"量价：{item.get('volume_price', '暂无')}｜5分钟技术确认：{item.get('technical', '暂无')}",
            f"机会等级：{item.get('opportunity', '暂无')}｜风险：{item.get('risk', '暂无')}",
            f"操作：{item.get('action', '暂无')}",
            f"建议区间：{item.get('buy_point', '暂无')}｜结构止损：{item.get('stop', '暂无')}（{item.get('stop_basis', '结构失效')}）",
            f"建议首笔资金：{_money(item.get('buy_amount'))}｜建议股数：{item.get('buy_quantity') or '待总资金配置'}",
            f"后续加仓：{item.get('add_plan', '只有形成新的确认结构后再考虑加仓')}",
        ])
    _append_watch_candidates(lines, candidates, confirmed_codes)
    lines.extend([
        "",
        "说明：扩大的是扫描历史和覆盖范围，不放宽买点定义；首仓看5分钟结构，后续加仓看新的30分钟/120分钟确认，任何级别出现对应正式卖点或结构失效都按该级别管理范围处理。",
    ])
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
    print(f"[OK] 全A扫描V2报告已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
