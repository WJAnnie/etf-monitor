from __future__ import annotations

from datetime import datetime, timedelta

import scripts.run_full_a_scan as base
from trading_skill.chan_policy_v2 import INTRADAY_CONFIRM_TYPES, validate_standard_second_buy
from trading_skill.decision import OpportunityGrade
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.position_policy_v2 import staged_position_policy, take_profit_contract


# 买点不要求“刚刚这一根K线才出现”。只要结构尚未失效且上涨幅度不大，仍保留为可执行/准备候选。
# 注意：freshness 只决定结构是否仍值得跟踪，不赋予任何低级别独立买入权。
base.FRESHNESS = {
    Timeframe.WEEKLY: timedelta(days=60),
    Timeframe.DAILY: timedelta(days=30),
    Timeframe.M120: timedelta(days=15),
    Timeframe.M30: timedelta(days=5),
    Timeframe.M5: timedelta(hours=4),
}

# 新开核心仓的 primary 只能来自日线。日线二买是正式准入；日线一买只用于 WAIT_2B。
# 120m / 30m / 5m 永远不能替代日线创建核心入场许可。
DAILY_PRIMARY_PRIORITY = {
    ChanSignalType.SECOND_BUY: 4,
    ChanSignalType.FIRST_BUY: 1,
}

_MATURITY_RANK = {"WATCH": 0, "PREPARE": 1, "TRIGGERED": 2}


def _invalidated_by_later_sell(result, buy_signal, *, as_of) -> bool:
    """近期买点只有在同级别之后没有更新的正式卖点时才继续有效。"""
    for sell in base.fresh_signals(result, as_of=as_of, side="SELL"):
        if sell.confirmation_timestamp > buy_signal.confirmation_timestamp:
            return True
    return False


def choose_primary_v2(results, *, as_of):
    """Choose only daily authority signals for a new-position scan.

    - DAILY 2B: executable authority after strict 2B boundary validation.
    - DAILY 1B: watch-only, so the decision engine can emit WAIT_2B.
    - DAILY 3B: not a fresh-core-entry substitute; it belongs to continuation/add management.
    - 120m/30m/5m: confirmation/execution only and are therefore excluded here.
    """
    daily = results.get(Timeframe.DAILY)
    if daily is None:
        return None

    choices = []
    for signal in base.fresh_signals(daily, as_of=as_of, side="BUY"):
        kind = base.signal_type(signal)
        if kind not in DAILY_PRIMARY_PRIORITY:
            continue
        if _invalidated_by_later_sell(daily, signal, as_of=as_of):
            continue
        if kind is ChanSignalType.SECOND_BUY:
            validation = validate_standard_second_buy(signal, daily.signals)
            if not validation.valid:
                continue
        choices.append((DAILY_PRIMARY_PRIORITY[kind], signal.confirmation_timestamp, signal))

    if not choices:
        return None
    choices.sort(key=lambda item: (item[0], item[1]), reverse=True)
    _, _, signal = choices[0]
    return Timeframe.DAILY, signal


def execution_maturity_v2(signal, current_price: float) -> str:
    """Price-distance chase guard only; it must never create entry authority.

    The final execution maturity is the minimum of this guard and the structural
    120m -> 30m -> 5m nesting state calculated after the five-timeframe analysis.
    """
    signal_price = signal.structural_price_ticks * float(base.TICK_SIZE)
    if signal_price <= 0 or current_price <= 0:
        return "WATCH"
    kind = base.signal_type(signal)
    if kind is ChanSignalType.FIRST_BUY:
        return "WATCH"
    if kind is not ChanSignalType.SECOND_BUY:
        return "WATCH"

    distance = (current_price - signal_price) / signal_price
    if -0.01 <= distance <= 0.04:
        return "TRIGGERED"
    if 0.04 < distance <= 0.08:
        return "PREPARE"
    return "WATCH"


def _parse_ts(value: object) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def _fresh_raw_signals(tf_raw: dict, *, timeframe: Timeframe, as_of: datetime, side: str) -> list[dict]:
    cutoff = as_of - base.FRESHNESS[timeframe]
    signals = []
    for signal in tf_raw.get("signals") or []:
        if signal.get("side") != side:
            continue
        confirmed = _parse_ts(signal.get("confirmation_timestamp"))
        if confirmed is None:
            continue
        if confirmed.tzinfo is None and as_of.tzinfo is not None:
            confirmed = confirmed.replace(tzinfo=as_of.tzinfo)
        if cutoff <= confirmed <= as_of:
            signals.append(signal)
    return signals


def _raw_has_active_buy(tf_raw: dict, *, timeframe: Timeframe, as_of: datetime) -> bool:
    buys = _fresh_raw_signals(tf_raw, timeframe=timeframe, as_of=as_of, side="BUY")
    eligible_buys = [
        signal
        for signal in buys
        if any(ChanSignalType(item) in INTRADAY_CONFIRM_TYPES for item in signal.get("types") or [])
    ]
    if not eligible_buys:
        return False
    latest_buy = max(_parse_ts(item.get("confirmation_timestamp")) for item in eligible_buys)
    sells = _fresh_raw_signals(tf_raw, timeframe=timeframe, as_of=as_of, side="SELL")
    if not sells:
        return True
    latest_sell = max(_parse_ts(item.get("confirmation_timestamp")) for item in sells)
    return latest_sell <= latest_buy


def _structural_execution_maturity(analysis: dict, *, as_of: datetime, signal_type: str) -> tuple[str, list[str]]:
    if signal_type == ChanSignalType.FIRST_BUY.value:
        return "WATCH", ["DAILY_FIRST_BUY_WAIT_2B"]
    if signal_type != ChanSignalType.SECOND_BUY.value:
        return "WATCH", ["DAILY_2B_PERMISSION_MISSING"]

    timeframes = analysis.get("timeframes") or {}
    m120 = _raw_has_active_buy(timeframes.get("120m") or {}, timeframe=Timeframe.M120, as_of=as_of)
    m30 = _raw_has_active_buy(timeframes.get("30m") or {}, timeframe=Timeframe.M30, as_of=as_of)
    m5 = _raw_has_active_buy(timeframes.get("5m") or {}, timeframe=Timeframe.M5, as_of=as_of)

    if not m120:
        return "WATCH", ["M120_CONFIRMATION_MISSING"]
    if not m30:
        return "WATCH", ["M30_EXECUTION_SETUP_MISSING"]
    if not m5:
        return "PREPARE", ["M5_EXECUTION_TRIGGER_MISSING"]
    return "TRIGGERED", []


def _lower_maturity(left: str, right: str) -> str:
    return min((left, right), key=lambda item: _MATURITY_RANK.get(item, -1))


def _raw_class2_annotations(
    analysis: dict, *, signal_type: str, signal_price_ticks: int | None = None
) -> list[str]:
    """Class-2 labels remain extended labels; standard signal stays SECOND_BUY."""
    if signal_type != ChanSignalType.SECOND_BUY.value:
        return []
    daily = (analysis.get("timeframes") or {}).get("daily") or {}
    second_candidates = [s for s in daily.get("signals") or [] if ChanSignalType.SECOND_BUY.value in (s.get("types") or [])]
    if signal_price_ticks is not None:
        matched = [s for s in second_candidates if int(s.get("structural_price_ticks") or 0) == signal_price_ticks]
        if matched:
            second_candidates = matched
    if not second_candidates:
        return []
    second = max(second_candidates, key=lambda item: _parse_ts(item.get("confirmation_timestamp")) or datetime.min)
    second_evidence = {item for item in second.get("evidence_ids") or [] if item}
    level = int(second.get("level_rank") or 0)
    extended: list[str] = []

    for signal in daily.get("signals") or []:
        if ChanSignalType.THIRD_BUY.value not in (signal.get("types") or []):
            continue
        if int(signal.get("level_rank") or 0) != level:
            continue
        third_evidence = {item for item in signal.get("evidence_ids") or [] if item}
        if second_evidence & third_evidence:
            extended.append(ChanSignalType.STRONG_CLASS2_BUY.value)
            break

    second_ts = _parse_ts(second.get("confirmation_timestamp"))
    center_candidates = []
    for center in daily.get("centers") or []:
        if int(center.get("level_rank") or 0) != level:
            continue
        center_ts = _parse_ts(center.get("confirmation"))
        if second_ts is not None and center_ts is not None and center_ts <= second_ts:
            center_candidates.append((center_ts, center))
    if center_candidates:
        _, center = max(center_candidates, key=lambda item: item[0])
        if int(second.get("structural_price_ticks") or 0) >= int(center.get("zg_ticks") or 0):
            extended.append(ChanSignalType.CENTER_CLASS2_BUY.value)
    return list(dict.fromkeys(extended))


def _staged_entry_payload(opportunity: object, *, signal_type: str) -> list[dict]:
    if signal_type != ChanSignalType.SECOND_BUY.value:
        return []
    try:
        grade = OpportunityGrade(str(opportunity))
    except ValueError:
        return []
    policy = staged_position_policy(grade)
    return [
        {
            "role": rule.role.value,
            "target_fraction": rule.target_fraction,
            "add_gate": rule.add_gate.value,
            "management_stop_level": rule.management_stop_level.value,
            "requires_new_structure": rule.requires_new_structure,
            "may_average_down": rule.may_average_down,
        }
        for rule in policy.rules
    ]


_original_analyze_symbol = base.analyze_symbol


def analyze_symbol_v2(symbol, industry_map, *, as_of, equity):
    analysis, candidate = _original_analyze_symbol(symbol, industry_map, as_of=as_of, equity=equity)
    if not candidate:
        return analysis, candidate

    prefilter = symbol.get("fundamental_prefilter") or {}
    candidate["prospect_theme"] = symbol.get("prospect_theme")
    candidate["industry_selection_reason"] = symbol.get("industry_selection_reason")
    candidate["candidate_route"] = symbol.get("candidate_route")
    candidate["fundamental_grade"] = prefilter.get("grade")
    candidate["industry_metric_policy"] = prefilter.get("industry_policy")
    candidate["industry_valuation_focus"] = list(prefilter.get("valuation_focus") or [])
    candidate["industry_metric_focus"] = list(prefilter.get("metric_focus") or [])
    candidate["industry_report_focus"] = list(prefilter.get("report_focus") or [])
    candidate["latest_financial_report"] = (prefilter.get("latest") or {}).get("report_date")
    candidate["industry_event_risk"] = symbol.get("industry_event_risk")
    candidate["industry_event_score"] = symbol.get("industry_event_score")

    signal_price_text = str(candidate.get("buy_point") or "").split("～", 1)[0].replace("元", "")
    try:
        signal_price = float(signal_price_text)
    except ValueError:
        signal_price = 0.0
    signal_price_ticks = round(signal_price / float(base.TICK_SIZE)) if signal_price > 0 else None
    current_price = float(candidate.get("current_price") or 0)
    candidate["rise_since_signal_pct"] = round((current_price / signal_price - 1) * 100, 2) if signal_price > 0 else None

    signal_type = str(candidate.get("signal") or "")
    structural_maturity, hierarchy_reasons = _structural_execution_maturity(
        analysis, as_of=as_of, signal_type=signal_type
    )
    price_guard = str(candidate.get("execution_maturity") or "WATCH")
    final_maturity = _lower_maturity(structural_maturity, price_guard)
    candidate["price_distance_guard"] = price_guard
    candidate["structural_execution_maturity"] = structural_maturity
    candidate["execution_maturity"] = final_maturity
    candidate["timeframe_contract"] = {
        "weekly": "战略环境/周线底分型关注，不直接下单",
        "daily": "核心入场授权：二买/类二买；一买只观察",
        "120m": "日线尾部结构确认，不能独立创造核心买点；MACD(6,13,4)重点观察",
        "30m": "执行准备与回踩结构细化",
        "5m": "最终执行触发，不改变日线交易方向",
    }

    extended = _raw_class2_annotations(
        analysis,
        signal_type=signal_type,
        signal_price_ticks=signal_price_ticks,
    )
    candidate["extended_signals"] = extended
    candidate["signal_label"] = " / ".join([signal_type] + extended) if signal_type else None
    candidate["staged_entry_plan"] = _staged_entry_payload(candidate.get("opportunity"), signal_type=signal_type)
    candidate["take_profit_contract"] = list(take_profit_contract())
    candidate["stop_contract"] = {
        "TEST": "5分钟执行结构只管理试仓；不得单独否定日线核心逻辑",
        "CONFIRMATION": "30分钟保护位管理确认仓，必须来自已确认新结构",
        "CORE": "日线二买/中枢失效是核心仓保护依据",
        "TREND_ADD": "120分钟保护位管理趋势加仓；盈利保护只能上移",
    }
    candidate["add_plan"] = (
        "首笔仅在日线二买/类二买 + 120分钟确认 + 30分钟执行准备 + 5分钟触发后执行；"
        "第二笔必须出现新的30分钟独立确认结构；核心仓必须出现新的120分钟确认结构；"
        "趋势加仓必须出现日线趋势延续结构。任何结构失效后禁止因下跌机械补仓。"
    )

    blockers = list(candidate.get("blockers") or [])
    for reason in hierarchy_reasons:
        if reason not in blockers:
            blockers.append(reason)
    candidate["blockers"] = blockers

    base_action = str(candidate.get("action") or "OBSERVE")
    if signal_type == ChanSignalType.FIRST_BUY.value:
        candidate["action"] = "WAIT_2B"
        candidate["push"] = False
        candidate["buy_amount"] = None
        candidate["buy_quantity"] = None
        candidate["recent_signal_note"] = "日线一买仅进入观察，等待日线二买/类二买"
    elif final_maturity == "WATCH":
        candidate["action"] = "OBSERVE"
        candidate["push"] = False
        candidate["recent_signal_note"] = "日线二买已出现，但120m→30m→5m执行链尚未完成或价格已偏离"
    elif final_maturity == "PREPARE":
        # 只能在基础风险/基本面/技术门槛原本允许时保持 PREPARE，绝不由低级别反向提升被阻断的候选。
        if base_action in {"PREPARE_BUY", "BUY_TRANCHE_1"}:
            candidate["action"] = "PREPARE_BUY"
            candidate["push"] = True
        else:
            candidate["action"] = base_action
            candidate["push"] = False
        candidate["recent_signal_note"] = "日线二买有效，120m与30m已确认，等待5m执行触发"
    else:
        # TRIGGERED 也只保留基础决策允许的动作；指标只能暂停/降级，不能创造买点。
        candidate["action"] = base_action
        candidate["push"] = bool(candidate.get("push")) and base_action in {"PREPARE_BUY", "BUY_TRANCHE_1"}
        candidate["recent_signal_note"] = "日线二买有效，120m→30m→5m结构链完成；仍受风险、基本面与追高保护约束"

    tf_raw = (analysis.get("timeframes") or {}).get("daily") or {}
    for signal in reversed(tf_raw.get("signals") or []):
        if signal_type in (signal.get("types") or []):
            if signal_price_ticks is not None and int(signal.get("structural_price_ticks") or 0) != signal_price_ticks:
                continue
            candidate["signal_confirmation_time"] = signal.get("confirmation_timestamp")
            break
    return analysis, candidate


base.choose_primary = choose_primary_v2
base.execution_maturity = execution_maturity_v2
base.analyze_symbol = analyze_symbol_v2


if __name__ == "__main__":
    raise SystemExit(base.main())
