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


def build_report(scan: dict, universe: dict, *, stage: str) -> tuple[str, str]:
    candidates = list(scan.get("candidates") or [])
    confirmed = [item for item in candidates if item.get("push") is not False]
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
        f"前五/新龙头去重候选：{universe.get('deduped_leader_candidates', scan.get('leader_candidates', 0))}只",
        f"允许长历史深扫：{universe.get('deep_scan_eligible_candidates', scan.get('fundamental_passed', 0))}只",
        f"实际五周期深扫：{scan.get('deep_scanned', 0)}只",
        f"近期出现正式缠论买点：{scan.get('chan_buy_candidates', 0)}只",
        f"当前达到执行/准备标准：{len(confirmed)}只",
        "",
        "【行业逻辑】长期前景决定主要扫描池；当日热度只用于判断节奏，不再作为行业准入门槛。",
        "【买点逻辑】30分钟/120分钟近期已经出现的一买、二买、三买，只要结构未失效且距离买点涨幅不大，仍保留为机会。",
    ]

    if prospect_industries:
        names = []
        for item in prospect_industries[:12]:
            theme = item.get("prospect_theme") or item.get("name")
            names.append(f"{item.get('name')}（{theme}）")
        lines.extend(["", "【重点前景方向】" + "、".join(names)])

    if not confirmed:
        near = [item for item in candidates if item.get("recent_signal_note")]
        lines.extend(["", "【结论】本轮没有达到正式执行标准的股票，不为了凑数量而降低缠论定义。"])
        if near:
            lines.append(f"但已有{len(near)}只股票出现过近期买点，系统会继续观察其涨幅、上级结构和风险状态。")
        return title, "\n".join(lines)

    lines.extend(["", "【可执行/准备候选】"])
    for idx, item in enumerate(confirmed[:15], 1):
        lines.extend([
            "",
            f"{idx}. {item.get('name', '未知')}（{item.get('code', '')}）",
            f"前景主题：{item.get('prospect_theme') or '市场结构补充'}",
            f"细分行业：{item.get('industry', '暂无')}｜行业节奏：{item.get('industry_state', '暂无')}",
            f"行业位置：{item.get('leader_rank', '暂无')}｜基本面：{item.get('fundamental_grade', '暂无')}级",
            f"缠论买点：{item.get('signal', '暂无')}｜级别：{item.get('timeframe', '暂无')}",
            f"买点确认时间：{item.get('signal_confirmation_time', '暂无')}",
            f"买点后涨幅：{item.get('rise_since_signal_pct', '暂无')}%｜状态：{item.get('recent_signal_note', '暂无')}",
            f"上级结构：{item.get('parent_structure', '暂无')}",
            f"量价：{item.get('volume_price', '暂无')}｜技术确认：{item.get('technical', '暂无')}",
            f"机会等级：{item.get('opportunity', '暂无')}｜风险：{item.get('risk', '暂无')}",
            f"操作：{item.get('action', '暂无')}",
            f"建议区间：{item.get('buy_point', '暂无')}｜结构止损：{item.get('stop', '暂无')}",
            f"建议首笔资金：{_money(item.get('buy_amount'))}｜建议股数：{item.get('buy_quantity') or '待总资金配置'}",
            f"后续加仓：{item.get('add_plan', '只有形成新的确认结构后再考虑加仓')}",
        ])
    lines.extend([
        "",
        "说明：不因当天没有候选而放宽一买/二买/三买定义；扩大的是历史、覆盖范围和近期买点有效观察窗口。",
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
