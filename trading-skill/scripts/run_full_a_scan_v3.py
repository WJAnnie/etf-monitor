from __future__ import annotations

from datetime import timedelta

import scripts.run_full_a_scan_v2 as v2
from trading_skill.chan_extensions import annotate_second_buy_variants
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.strategy_policy import (
    STANDARD_BUY_PRIORITY,
    TIMEFRAME_ENTRY_PRIORITY,
    TIMEFRAME_POLICY,
    entry_permission,
    primary_entry_timeframes,
    signal_label,
)


base = v2.base
_raw_analyze_production_chan = base.analyze_production_chan
_original_v2_analyze_symbol = base.analyze_symbol

# 统一近期信号窗口；窗口只是“仍值得观察的最大期限”，不是自动买入期限。
base.FRESHNESS = {
    timeframe: timedelta(days=policy.freshness_days)
    for timeframe, policy in TIMEFRAME_POLICY.items()
}


def _analyze_with_extensions(raw_bars, *, tick_size, as_of):
    return annotate_second_buy_variants(_raw_analyze_production_chan(raw_bars, tick_size=tick_size, as_of=as_of))


base.analyze_production_chan = _analyze_with_extensions


def choose_primary_v3(results, *, as_of):
    choices = []
    for timeframe in primary_entry_timeframes():
        result = results.get(timeframe)
        if result is None:
            continue
        for signal in base.fresh_signals(result, as_of=as_of, side="BUY"):
            kind = base.signal_type(signal)
            if kind not in STANDARD_BUY_PRIORITY:
                continue
            if v2._invalidated_by_later_sell(result, signal, as_of=as_of):
                continue
            # 标准二买优先，其次三买、一买；同类信号再看周期与确认时间。
            choices.append(
                (
                    STANDARD_BUY_PRIORITY[kind],
                    TIMEFRAME_ENTRY_PRIORITY.get(timeframe, 0),
                    signal.confirmation_timestamp,
                    timeframe,
                    signal,
                )
            )
    if not choices:
        return None
    choices.sort(key=lambda item: (item[0], item[1], item[2]), reverse=True)
    _, _, _, timeframe, signal = choices[0]
    return timeframe, signal


base.choose_primary = choose_primary_v3


def _parse_iso(value: str | None):
    if not value:
        return None
    from datetime import datetime
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _child_execution_timeframes(primary: str) -> tuple[str, ...]:
    if primary == "daily":
        return ("120m", "30m", "5m")
    if primary == "120m":
        return ("30m", "5m")
    if primary == "30m":
        return ("5m",)
    return ()


def _execution_structure_ok(analysis: dict, candidate: dict) -> tuple[bool, list[str]]:
    confirmation = _parse_iso(candidate.get("signal_confirmation_time"))
    if confirmation is None:
        return True, []
    conflicts = []
    for child in _child_execution_timeframes(str(candidate.get("timeframe") or "")):
        raw = (analysis.get("timeframes") or {}).get(child) or {}
        later_sells = []
        for signal in raw.get("signals") or []:
            if signal.get("side") != "SELL":
                continue
            when = _parse_iso(signal.get("confirmation_timestamp"))
            if when is not None and when > confirmation:
                later_sells.append(signal)
        if later_sells:
            conflicts.append(f"{TIMEFRAME_POLICY[Timeframe(child)].chinese_name}出现更新的正式卖点")
    return not conflicts, conflicts


def _find_primary_signal_raw(analysis: dict, candidate: dict) -> dict | None:
    tf = str(candidate.get("timeframe") or "")
    expected = str(candidate.get("signal") or "")
    confirm = str(candidate.get("signal_confirmation_time") or "")
    raw = (analysis.get("timeframes") or {}).get(tf) or {}
    for signal in reversed(raw.get("signals") or []):
        if expected in (signal.get("types") or []) and (not confirm or signal.get("confirmation_timestamp") == confirm):
            return signal
    return None


def _entry_zones(kind: str, signal_price: float) -> tuple[str, str]:
    if kind == ChanSignalType.SECOND_BUY.value:
        trigger, prepare = 0.04, 0.08
    elif kind == ChanSignalType.FIRST_BUY.value:
        trigger, prepare = 0.03, 0.06
    else:
        trigger, prepare = 0.03, 0.05
    return f"{signal_price:.2f}～{signal_price*(1+trigger):.2f}元", f"不高于约{signal_price*(1+prepare):.2f}元"


def analyze_symbol_v3(symbol, industry_map, *, as_of, equity):
    analysis, candidate = _original_v2_analyze_symbol(symbol, industry_map, as_of=as_of, equity=equity)
    if not candidate:
        return analysis, candidate

    raw_signal = _find_primary_signal_raw(analysis, candidate)
    if raw_signal:
        # 构造一个轻量展示对象，避免报告层猜测类二买。
        extended = list(raw_signal.get("extended_types") or [])
        candidate["extended_signal_types"] = extended
        if "STRONG_CLASS2_BUY" in extended:
            candidate["class2_label"] = "强势类二买（二买/三买合一结构）"
        elif "CENTER_CLASS2_BUY" in extended:
            candidate["class2_label"] = "中枢类二买（标准二买的中枢环境变体）"
        else:
            candidate["class2_label"] = "无类二买扩展标签"

    try:
        tf = Timeframe(str(candidate.get("timeframe")))
        kind = ChanSignalType(str(candidate.get("signal")))
        candidate["timeframe_role"] = TIMEFRAME_POLICY[tf].role
        candidate["entry_permission"] = entry_permission(tf, kind)
        candidate["stop_level"] = TIMEFRAME_POLICY[tf].stop_level.value
    except (ValueError, KeyError):
        pass

    execution_ok, conflicts = _execution_structure_ok(analysis, candidate)
    candidate["execution_structure_ok"] = execution_ok
    candidate["execution_conflicts"] = conflicts
    if not execution_ok:
        # 高周期买点仍保留为观察，不因5/30分钟反向噪声删除；但禁止当场开新仓。
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "高周期买点仍有效，但低级别出现更新卖点，等待新的执行确认"
        blockers = list(candidate.get("blockers") or [])
        if "EXECUTION_STRUCTURE_CONFLICT" not in blockers:
            blockers.append("EXECUTION_STRUCTURE_CONFLICT")
        candidate["blockers"] = blockers

    signal_price_text = str(candidate.get("buy_point") or "").split("～", 1)[0].replace("元", "")
    try:
        signal_price = float(signal_price_text)
    except ValueError:
        signal_price = 0.0
    if signal_price > 0:
        trigger_zone, max_watch = _entry_zones(str(candidate.get("signal") or ""), signal_price)
        candidate["buy_point"] = trigger_zone
        candidate["max_watch_price"] = max_watch

    candidate["industry_rotation_state"] = symbol.get("industry_rotation_state")
    candidate["industry_analysis_profile"] = symbol.get("industry_analysis_profile")
    candidate["recent_report"] = symbol.get("recent_report")
    candidate["stop_logic"] = (
        f"止损跟随{candidate.get('timeframe','主结构')}买点/中枢失效；5分钟只负责暂停执行，不能把高周期止损下移。"
    )
    candidate["add_plan"] = (
        "首笔后只有出现新的同级或更高级确认买点/结构升级，且保护位能够抬高或保持，才允许第二笔/趋势加仓；"
        "禁止因为价格下跌而机械补仓。"
    )
    candidate["take_profit_plan"] = (
        "不设固定盈利百分比止盈；5分钟/30分钟卖点先处理试仓和战术仓，120分钟卖点逐级降低确认仓，"
        "日线二卖开始分批减核心仓，日线三卖或战略结构失效退出。"
    )
    return analysis, candidate


base.analyze_symbol = analyze_symbol_v3


if __name__ == "__main__":
    raise SystemExit(base.main())
