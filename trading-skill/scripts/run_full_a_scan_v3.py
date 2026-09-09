from __future__ import annotations

from datetime import timedelta

import scripts.run_full_a_scan_v2 as v2
from trading_skill.chan_extensions import annotate_second_buy_variants
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.production_chan_v3 import analyze_production_chan_v3
from trading_skill.strategy_policy import (
    TIMEFRAME_POLICY,
    entry_permission,
    entry_priority,
    parent_timeframes,
    primary_entry_timeframes,
)


base = v2.base
_original_v2_analyze_symbol = base.analyze_symbol

base.FRESHNESS = {
    timeframe: timedelta(days=policy.freshness_days)
    for timeframe, policy in TIMEFRAME_POLICY.items()
}


def _analyze_with_extensions(raw_bars, *, tick_size, as_of):
    return annotate_second_buy_variants(analyze_production_chan_v3(raw_bars, tick_size=tick_size, as_of=as_of))


base.analyze_production_chan = _analyze_with_extensions


def choose_primary_v3(results, *, as_of):
    """从日线/120分钟/30分钟正式买点中选一个主逻辑；周期和买点类别使用同一优先矩阵。"""
    choices = []
    for timeframe in primary_entry_timeframes():
        result = results.get(timeframe)
        if result is None:
            continue
        for signal in base.fresh_signals(result, as_of=as_of, side="BUY"):
            kind = base.signal_type(signal)
            if kind not in {ChanSignalType.FIRST_BUY, ChanSignalType.SECOND_BUY, ChanSignalType.THIRD_BUY}:
                continue
            if v2._invalidated_by_later_sell(result, signal, as_of=as_of):
                continue
            priority = entry_priority(timeframe, kind, signal.extended_types)
            if priority <= 0:
                continue
            choices.append((priority, signal.confirmation_timestamp, timeframe, signal))
    if not choices:
        return None
    choices.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, timeframe, signal = choices[0]
    return timeframe, signal


base.choose_primary = choose_primary_v3


def parent_valid_v3(results, timeframe, *, as_of):
    """上级结构只看当前最新正式状态，不让已被新买点覆盖的旧卖点永久阻断下级机会。"""
    for parent_tf in parent_timeframes(timeframe):
        parent = results.get(parent_tf)
        if parent is None or parent.status not in ("OK", "UNRESOLVED"):
            return False
        recent = base.fresh_signals(parent, as_of=as_of)
        if recent:
            latest = max(recent, key=lambda signal: signal.confirmation_timestamp)
            if latest.side == "SELL":
                return False
        if parent.trend_classification == "DOWNTREND" and parent.divergence_state not in ("FORMING", "CONFIRMED"):
            return False
    return True


base.parent_valid = parent_valid_v3


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


def _latest_child_signal_after(raw: dict, confirmation) -> dict | None:
    candidates = []
    for signal in raw.get("signals") or []:
        when = _parse_iso(signal.get("confirmation_timestamp"))
        if when is None or when <= confirmation:
            continue
        candidates.append((when, signal))
    if not candidates:
        return None
    candidates.sort(key=lambda pair: pair[0])
    return candidates[-1][1]


def _execution_structure_ok(analysis: dict, candidate: dict) -> tuple[bool, list[str], dict[str, str]]:
    confirmation = _parse_iso(candidate.get("signal_confirmation_time"))
    if confirmation is None:
        return True, [], {}
    conflicts: list[str] = []
    latest_states: dict[str, str] = {}
    for child in _child_execution_timeframes(str(candidate.get("timeframe") or "")):
        raw = (analysis.get("timeframes") or {}).get(child) or {}
        latest = _latest_child_signal_after(raw, confirmation)
        if latest is None:
            latest_states[child] = "无更新正式买卖点"
            continue
        side = str(latest.get("side") or "")
        labels = list(latest.get("types") or []) + list(latest.get("extended_types") or [])
        latest_states[child] = f"{side}:{'/'.join(labels) or '结构信号'}"
        if side == "SELL":
            conflicts.append(f"{TIMEFRAME_POLICY[Timeframe(child)].chinese_name}最新正式结构仍为卖点")
    return not conflicts, conflicts, latest_states


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


HARD_ENTRY_BLOCKERS = {
    "FUNDAMENTAL_VETO",
    "STOP_UNDEFINED",
    "RISK_TOO_HIGH",
    "TECHNICAL_EXECUTION_PAUSED",
    "PARENT_CONTEXT_INVALID",
    "DATA_INCOMPLETE",
    "PORTFOLIO_RISK_FULL",
    "REENTRY_LOCKED",
}


def _enforce_first_buy_permission(candidate: dict) -> None:
    """一买权限只能降级风险，绝不能把本来不成熟/低评分/被阻断的交易重新“复活”。"""
    if str(candidate.get("signal") or "") != ChanSignalType.FIRST_BUY.value:
        return
    blockers = set(candidate.get("blockers") or [])
    hard_blocked = bool(blockers & HARD_ENTRY_BLOCKERS)
    timeframe = str(candidate.get("timeframe") or "")
    if hard_blocked:
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "一买结构存在，但存在基本面/风险/数据/上级结构等硬阻断，仅保留观察"
        return

    if timeframe == Timeframe.DAILY.value:
        candidate["action"] = "WAIT_2B"
        candidate["push"] = False
        candidate["recent_signal_note"] = "日线一买已成立，但按策略默认等待标准二买，不直接建立核心仓"
        return

    if timeframe == Timeframe.M30.value:
        candidate["action"] = "OBSERVE"
        candidate["push"] = False
        candidate["recent_signal_note"] = "30分钟一买：反转初期，仅观察；优先等待标准二买/三买和5分钟执行条件"
        return

    if timeframe == Timeframe.M120.value:
        # 120分钟一买只允许“准备/小试仓观察”，且必须保留原决策的成熟度与机会等级门槛。
        grade_ok = str(candidate.get("opportunity") or "") in {"S", "A", "B"}
        maturity = str(candidate.get("execution_maturity") or "NOT_READY")
        maturity_ok = maturity in {"TRIGGERED", "PREPARE"}
        unexpected_blockers = blockers - {"DAILY_FIRST_BUY_WAIT_2B"}
        if not grade_ok:
            candidate["action"] = "OBSERVE"
            candidate["push"] = False
            candidate["recent_signal_note"] = "120分钟一买结构存在，但机会等级仅C，不允许因一买权限绕过评分门槛"
            return
        if not maturity_ok:
            candidate["action"] = "OBSERVE"
            candidate["push"] = False
            candidate["recent_signal_note"] = "120分钟一买结构存在，但当前已离买点过远或执行尚未成熟，仅观察等待新的执行条件"
            return
        if unexpected_blockers:
            candidate["action"] = "OBSERVE"
            candidate["push"] = False
            candidate["recent_signal_note"] = "120分钟一买结构存在，但仍有交易阻断项，不能由周期权限重新激活"
            return
        candidate["action"] = "PREPARE_BUY"
        candidate["push"] = True
        candidate["recent_signal_note"] = "120分钟一买：满足机会等级和距离门槛后仅进入准备/小试仓观察，不等同标准二买或三买"


def _major_negative_events(events: list[dict] | tuple[dict, ...]) -> list[dict]:
    return [
        event for event in (events or [])
        if event.get("importance") == "重大" and event.get("impact") == "利空"
    ]


def _major_negative_industry_events(industry: dict) -> list[dict]:
    """向后兼容测试和旧调用。"""
    return _major_negative_events(list(industry.get("events") or []))


def _events_for_symbol(symbol: dict, industry_map: dict) -> list[dict]:
    # 跨行业补充路线必须优先使用已解析的真实行业事件，不能因industry_code=CROSS_MARKET绕过行业风险。
    if "industry_events" in symbol:
        return list(symbol.get("industry_events") or [])
    industry = industry_map.get(str(symbol.get("industry_code"))) or {}
    return list(industry.get("events") or [])


def analyze_symbol_v3(symbol, industry_map, *, as_of, equity):
    analysis, candidate = _original_v2_analyze_symbol(symbol, industry_map, as_of=as_of, equity=equity)
    if not candidate:
        return analysis, candidate

    raw_signal = _find_primary_signal_raw(analysis, candidate)
    if raw_signal:
        extended = list(raw_signal.get("extended_types") or [])
        candidate["extended_signal_types"] = extended
        labels = []
        if "STRONG_CLASS2_BUY" in extended:
            labels.append("强势类二买（二买/三买合一结构）")
        if "CENTER_CLASS2_BUY" in extended:
            labels.append("中枢类二买（二买回抽仍站在中枢上沿ZG及以上）")
        candidate["class2_label"] = "；".join(labels) if labels else "无类二买扩展标签"

    try:
        tf = Timeframe(str(candidate.get("timeframe")))
        kind = ChanSignalType(str(candidate.get("signal")))
        candidate["timeframe_role"] = TIMEFRAME_POLICY[tf].role
        candidate["entry_permission"] = entry_permission(tf, kind)
        candidate["stop_level"] = TIMEFRAME_POLICY[tf].stop_level.value
    except (ValueError, KeyError):
        pass

    _enforce_first_buy_permission(candidate)

    execution_ok, conflicts, latest_states = _execution_structure_ok(analysis, candidate)
    candidate["execution_structure_ok"] = execution_ok
    candidate["execution_conflicts"] = conflicts
    candidate["execution_latest_states"] = latest_states
    if not execution_ok:
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "高周期买点仍有效，但低级别最新正式结构为卖点，等待新的执行买点/确认"
        blockers = list(candidate.get("blockers") or [])
        if "EXECUTION_STRUCTURE_CONFLICT" not in blockers:
            blockers.append("EXECUTION_STRUCTURE_CONFLICT")
        candidate["blockers"] = blockers

    industry_events = _events_for_symbol(symbol, industry_map)
    candidate["industry_major_events"] = industry_events
    major_negative = _major_negative_events(industry_events)
    if major_negative:
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "缠论结构仍保留，但真实所属行业出现36小时内重大利空，暂停新开仓并等待事件影响重新定价"
        blockers = list(candidate.get("blockers") or [])
        if "INDUSTRY_MAJOR_NEGATIVE_EVENT" not in blockers:
            blockers.append("INDUSTRY_MAJOR_NEGATIVE_EVENT")
        candidate["blockers"] = blockers

    if symbol.get("candidate_route") == "跨行业结构补充" and symbol.get("industry_context_complete") is False:
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "缠论结构保留观察，但跨行业候选真实细分行业未成功解析，行业风险上下文不完整，禁止新开仓"
        blockers = list(candidate.get("blockers") or [])
        if "INDUSTRY_CONTEXT_INCOMPLETE" not in blockers:
            blockers.append("INDUSTRY_CONTEXT_INCOMPLETE")
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
    candidate["candidate_route"] = symbol.get("candidate_route")
    candidate["recent_report"] = symbol.get("recent_report")
    candidate["sector_financial_metrics"] = symbol.get("sector_financial_metrics")
    candidate["sector_observation_override"] = symbol.get("sector_observation_override", False)
    candidate["sector_observation_reason"] = symbol.get("sector_observation_reason")
    candidate["industry_context_complete"] = symbol.get("industry_context_complete", True)
    candidate["industry_context_note"] = symbol.get("industry_context_note")
    candidate["pe"] = symbol.get("pe")
    candidate["pb"] = symbol.get("pb")
    candidate["stop_logic"] = f"止损跟随{candidate.get('timeframe','主结构')}买点/中枢失效；单根5分钟影线或短线卖点不能直接否定更高周期核心结构。"
    candidate["add_plan"] = "首笔后只有出现新的同级或更高级确认买点/结构升级，且保护位能够抬高或保持，才允许第二笔/趋势加仓；禁止因为价格下跌而机械补仓。"
    candidate["take_profit_plan"] = "不设固定盈利百分比止盈；5分钟/30分钟卖点先处理试仓和战术仓，120分钟卖点逐级降低确认仓，日线二卖开始分批减核心仓，日线三卖或周线战略结构失效退出。"
    return analysis, candidate


base.analyze_symbol = analyze_symbol_v3


if __name__ == "__main__":
    raise SystemExit(base.main())
