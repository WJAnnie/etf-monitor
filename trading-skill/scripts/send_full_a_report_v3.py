from __future__ import annotations

import argparse
import json
from pathlib import Path

from trading_skill.a_share_reporting import DeliveryStatus, evaluate_delivery_gate, translate_scan_for_user
from trading_skill.industry_financial_metrics import summarize_sector_metrics
from trading_skill.notifications import notify_feishu


def _money(value):
    if value in (None, "", 0):
        return "未配置总资金"
    try:
        return f"{float(value):,.0f}元"
    except (TypeError, ValueError):
        return str(value)


def _signal_cn(value: object) -> str:
    text = str(value or "暂无")
    return "标准二买" if text == "二买" else text


def _valuation(value: object, *, kind: str) -> str:
    if value in (None, "", "-"):
        return "暂无"
    try:
        number = float(value)
    except (TypeError, ValueError):
        return str(value)
    if kind == "PE" and number <= 0:
        return f"{number:.2f}（亏损期，不以PE为主）"
    if kind == "PB" and number <= 0:
        return f"{number:.2f}（不适用）"
    return f"{number:.2f}"


def _fmt_report(report: dict | None) -> str:
    if not report:
        return "暂无新披露定期报告"
    parts = [f"{report.get('notice_date')}披露{report.get('report_type')}（报告期{report.get('report_date')}）"]
    if report.get("revenue_growth") is not None:
        parts.append(f"营收同比{report['revenue_growth']}%")
    if report.get("profit_growth") is not None:
        parts.append(f"净利同比{report['profit_growth']}%")
    if report.get("roe") is not None:
        parts.append(f"ROE {report['roe']}%")
    return "，".join(parts)


def _sector_metrics_text(profile: dict, metrics: dict | None) -> str:
    profile_name = str(profile.get("profile") or "")
    items = summarize_sector_metrics(profile_name, metrics, limit=4)
    return "；".join(items) if items else "本期暂未取得足够的行业专属报表变化字段"


def _append_industry_intelligence(lines: list[str], universe: dict) -> None:
    selected = list(universe.get("selected_industries") or [])
    reports = universe.get("industry_recent_reports") or {}
    if not selected:
        return
    lines.extend(["", "【重点行业动态与分析口径】"])
    selected.sort(
        key=lambda x: (x.get("rotation_state") == "刚开始升温", bool(x.get("events")), x.get("rank_score", 0)),
        reverse=True,
    )
    for idx, item in enumerate(selected[:12], 1):
        profile = item.get("analysis_profile") or {}
        events = list(item.get("events") or [])
        theme = item.get("prospect_theme") or "市场新方向"
        lines.append(
            f"{idx}. {item.get('name')}｜{theme}｜{item.get('rotation_state','持续跟踪')}｜"
            f"当日{item.get('change_pct','?')}%｜60日{item.get('change_60d','?')}%"
        )
        lines.append(f"   估值重点：{'、'.join((profile.get('valuation_focus') or [])[:2]) or '按行业画像判断'}")
        ops = (profile.get("operating_focus") or [])[:4]
        bs = (profile.get("balance_sheet_focus") or [])[:3]
        lines.append(f"   经营/报表重点：{'、'.join(ops + bs)}")
        if events:
            for event in events[:2]:
                source = event.get("source") or "财经资讯"
                lines.append(
                    f"   {event.get('importance','重要')}{event.get('impact','中性')}（{source}）：{event.get('content','')}"
                )
        else:
            lines.append("   重大利好/利空：最近36小时未匹配到明确重大事件")
        new_reports = reports.get(item.get("name")) or []
        if new_reports:
            for report in new_reports[:2]:
                lines.append(f"   新财报：{report.get('name')}（{report.get('code')}）｜{_fmt_report(report)}")
                lines.append(
                    "   行业专属财报变化：" + _sector_metrics_text(profile, report.get("sector_metrics"))
                )

    paused = list(universe.get("paused_high_industries") or [])
    if paused:
        names = [f"{x.get('name')}（60日{x.get('change_60d','?')}%）" for x in paused[:10]]
        lines.extend([
            "",
            "【高位暂退行业】" + "、".join(names),
            "说明：暂退不是长期看空；位置和热度冷却后会自动重新进入重点行业池。",
        ])


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
    lines.extend(["", f"【近期买点观察】共{len(watch)}只，以下列出前10只："])
    for idx, item in enumerate(watch[:10], 1):
        variant = item.get("class2_label") or ""
        lines.append(
            f"{idx}. {item.get('name')}（{item.get('code')}）｜{item.get('timeframe')} {_signal_cn(item.get('signal'))}"
            f"{('｜'+variant) if variant and '无类二买' not in variant else ''}｜买点后{item.get('rise_since_signal_pct','暂无')}%｜"
            f"{item.get('risk','暂无')}｜{item.get('action','观察')}"
        )
        if item.get("execution_conflicts"):
            lines.append("   暂缓原因：" + "；".join(item.get("execution_conflicts") or []))


def build_report(scan: dict, universe: dict, *, stage: str) -> tuple[str, str]:
    candidates = list(scan.get("candidates") or [])
    confirmed = [item for item in candidates if item.get("push") is not False]
    confirmed_codes = {item.get("code") for item in confirmed}
    title = "🎯 全A买点扫描" if confirmed else "📊 全A扫描完成"
    lines = [
        f"【扫描时点】{stage}",
        "",
        "【统一交易口径】",
        "周线＝战略环境，不单独下单；日线＝中期核心结构；120分钟＝主要中短线买点；30分钟＝战术买点；5分钟＝精细执行确认，不能独立形成选股买入理由。",
        "正式主买点只从日线、120分钟、30分钟产生；日线一买默认等待二买。标准二买与类二买分别标注，类二买不替代经典二买定义。",
        "止损跟随产生买入依据的主结构级别；止盈不设固定百分比，按5分钟→30分钟→120分钟→日线→周线卖点逐级处理对应仓位。",
        "",
        "【扫描范围】",
        f"全A股票：{scan.get('total_stocks',0)}只｜行业/细分板块：{scan.get('industries_screened',0)}个",
        f"当前重点行业：{len(universe.get('selected_industries') or [])}个｜高位暂退：{len(universe.get('paused_high_industries') or [])}个",
        f"行业前五+跨行业候选：{universe.get('deduped_leader_candidates',scan.get('leader_candidates',0))}只",
        f"允许长历史深扫：{universe.get('deep_scan_eligible_candidates',scan.get('fundamental_passed',0))}只｜实际五周期：{scan.get('deep_scanned',0)}只",
        f"近期正式缠论买点：{scan.get('chan_buy_candidates',0)}只｜当前达到执行/准备：{len(confirmed)}只",
    ]

    _append_industry_intelligence(lines, universe)

    if not confirmed:
        lines.extend(["", "【结论】本轮没有达到正式执行标准的股票；不为了凑数量降低缠论定义。"])
        _append_watch_candidates(lines, candidates, confirmed_codes)
        return title, "\n".join(lines)

    lines.extend(["", "【可执行/准备候选】"])
    for idx, item in enumerate(confirmed[:15], 1):
        profile = item.get("industry_analysis_profile") or {}
        variant = item.get("class2_label") or "无"
        valuation_focus = "、".join((profile.get("valuation_focus") or [])[:2]) or "按行业画像"
        lines.extend([
            "",
            f"{idx}. {item.get('name','未知')}（{item.get('code','')}）",
            f"行业：{item.get('industry','暂无')}｜前景主题：{item.get('prospect_theme') or '跨行业结构补充'}｜轮动状态：{item.get('industry_rotation_state','暂无')}",
            f"行业位置：{item.get('leader_rank','暂无')}｜基本面：{item.get('fundamental_grade','暂无')}级",
            f"当前估值：PE {_valuation(item.get('pe'), kind='PE')}｜PB {_valuation(item.get('pb'), kind='PB')}｜行业口径：{valuation_focus}",
            f"缠论主买点：{item.get('timeframe','暂无')} {_signal_cn(item.get('signal'))}｜类二买标注：{variant}",
            f"周期职责：{item.get('timeframe_role','暂无')}｜执行权限：{item.get('entry_permission','暂无')}",
            f"买点确认：{item.get('signal_confirmation_time','暂无')}｜买点后涨幅：{item.get('rise_since_signal_pct','暂无')}%",
            f"上级结构：{item.get('parent_structure','暂无')}｜低级别执行：{'通过' if item.get('execution_structure_ok',True) else '暂缓'}",
            f"量价：{item.get('volume_price','暂无')}｜技术确认：{item.get('technical','暂无')}",
            f"机会等级：{item.get('opportunity','暂无')}｜风险：{item.get('risk','暂无')}｜操作：{item.get('action','暂无')}",
            f"建议触发区间：{item.get('buy_point','暂无')}｜最大观察价：{item.get('max_watch_price','暂无')}｜结构止损：{item.get('stop','暂无')}",
            f"止损逻辑：{item.get('stop_logic','暂无')}",
            f"首笔：{_money(item.get('buy_amount'))}｜股数：{item.get('buy_quantity') or '待总资金配置'}｜{item.get('buy_fraction','')}",
            f"后续加仓：{item.get('add_plan','暂无')}",
            f"止盈/减仓：{item.get('take_profit_plan','暂无')}",
            f"新财报：{_fmt_report(item.get('recent_report'))}",
        ])
        if item.get("recent_report"):
            lines.append(
                "行业专属财报变化：" + _sector_metrics_text(profile, item.get("sector_financial_metrics"))
            )
        if item.get("execution_latest_states"):
            states = "；".join(f"{key}:{value}" for key, value in item.get("execution_latest_states", {}).items())
            lines.append("低级别最新结构：" + states)
        if item.get("execution_conflicts"):
            lines.append("执行冲突：" + "；".join(item.get("execution_conflicts") or []))
    _append_watch_candidates(lines, candidates, confirmed_codes)
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
    channel = notify_feishu(f"{title}｜{args.stage}", body)
    print(f"[OK] 全A扫描V3报告已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
