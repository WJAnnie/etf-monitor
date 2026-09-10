from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_skill.a_share_reporting import DeliveryStatus, evaluate_delivery_gate, translate_scan_for_user
from trading_skill.notifications import notify_feishu


def _money(value):
    if value in (None, "", 0):
        return "未配置总资金"
    try:
        return f"{float(value):,.0f}元"
    except (TypeError, ValueError):
        return str(value)


def _append_watch_candidates(lines: list[str], candidates: list[dict], confirmed_codes: set[str]) -> None:
    watch = [item for item in candidates if item.get("code") not in confirmed_codes and item.get("recent_signal_note")]
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
            f"{item.get('risk', '暂无')}｜{item.get('action', '观察')}"
        )


def _append_industry_intelligence(lines: list[str], universe: dict) -> None:
    intelligence = list(universe.get("industry_intelligence") or [])
    if not intelligence:
        return
    ranked = sorted(
        intelligence,
        key=lambda item: (abs(int(item.get("net_event_score") or 0)), int(item.get("positive_score") or 0) + int(item.get("negative_score") or 0)),
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


def build_report(scan: dict, universe: dict, *, stage: str) -> tuple[str, str]:
    candidates = list(scan.get("candidates") or [])
    confirmed = [item for item in candidates if item.get("push") is not False]
    confirmed_codes = {item.get("code") for item in confirmed}
    selected = list(universe.get("selected_industries") or [])
    prospect_industries = [item for item in selected if item.get("prospect_theme")]
    supplement_industries = [item for item in selected if not item.get("prospect_theme")]
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
        f"近期出现正式缠论结构：{scan.get('chan_buy_candidates', 0)}只",
        f"当前达到执行/准备标准：{len(confirmed)}只",
        "",
        "【行业逻辑】长期前景池每日重排，并保留市场结构补充池发现新方向；位置过高、风险抬升的行业可暂时退出重点池，回落后重新进入。行业重大利好/利空、季报/中报/年报单独列示，不用新闻直接制造买点。",
        "【缠论层级】周线=战略环境；日线=核心入场授权（二买/类二买，一买只观察）；120分钟=日线尾部结构确认；30分钟=执行准备；5分钟=最终执行触发。低级别不得越级替代日线买点。",
        "【指标逻辑】MACD(6,13,4)、BOLL、KDJ、量价只做确认/暂停，不能定义一买二买三买；其中120分钟MACD重点观察。",
    ]

    if prospect_industries:
        names = []
        for item in prospect_industries[:12]:
            theme = item.get("prospect_theme") or item.get("name")
            names.append(f"{item.get('name')}（{theme}）")
        lines.extend(["", "【重点前景方向】" + "、".join(names)])

    _append_industry_intelligence(lines, universe)

    if not confirmed:
        lines.extend(["", "【结论】本轮没有达到正式执行标准的股票，不为了凑数量而降低日线二买/类二买与多周期确认要求。"])
        _append_watch_candidates(lines, candidates, confirmed_codes)
        return title, "\n".join(lines)

    lines.extend(["", "【可执行/准备候选】"])
    for idx, item in enumerate(confirmed[:15], 1):
        signal_label = item.get("signal_label") or item.get("signal", "暂无")
        metric_focus = item.get("industry_metric_focus") or []
        metric_text = "、".join(metric_focus[:6]) if isinstance(metric_focus, list) and metric_focus else "按行业口径"
        lines.extend([
            "",
            f"{idx}. {item.get('name', '未知')}（{item.get('code', '')}）",
            f"前景主题：{item.get('prospect_theme') or '跨行业/市场结构补充'}",
            f"细分行业：{item.get('industry', '暂无')}｜行业节奏：{item.get('industry_state', '暂无')}",
            f"行业位置：{item.get('leader_rank', '暂无')}｜基本面：{item.get('fundamental_grade', '暂无')}级｜重点财务口径：{metric_text}",
            f"缠论买点：{signal_label}｜授权级别：{item.get('timeframe', '暂无')}",
            f"买点确认时间：{item.get('signal_confirmation_time', '暂无')}",
            f"买点后涨幅：{item.get('rise_since_signal_pct', '暂无')}%｜状态：{item.get('recent_signal_note', '暂无')}",
            f"多周期执行：结构链={item.get('structural_execution_maturity', '暂无')}｜价格追高保护={item.get('price_distance_guard', '暂无')}",
            f"上级结构：{item.get('parent_structure', '暂无')}",
            f"量价：{item.get('volume_price', '暂无')}｜技术确认：{item.get('technical', '暂无')}",
            f"机会等级：{item.get('opportunity', '暂无')}｜风险：{item.get('risk', '暂无')}",
            f"操作：{item.get('action', '暂无')}",
            f"建议区间：{item.get('buy_point', '暂无')}｜结构止损：{item.get('stop', '暂无')}",
            f"建议首笔资金：{_money(item.get('buy_amount'))}｜建议股数：{item.get('buy_quantity') or '待总资金配置'}",
            f"后续加仓：{item.get('add_plan', '只有形成新的独立确认结构后再考虑加仓')}",
        ])
    _append_watch_candidates(lines, candidates, confirmed_codes)
    lines.extend([
        "",
        "说明：不因当天没有候选而放宽缠论定义；扩大的是历史覆盖、行业覆盖和有效观察窗口。日线一买只进入观察，核心新仓以日线二买/类二买为准。",
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
