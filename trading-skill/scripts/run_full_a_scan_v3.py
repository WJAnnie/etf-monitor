from __future__ import annotations

from datetime import datetime, timedelta

import scripts.run_full_a_scan_v2 as v2
from trading_skill.chan_extensions import annotate_second_buy_variants
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.history_policy import classify_history_counts, primary_history_gate
from trading_skill.production_chan_v3 import analyze_production_chan_v3
from trading_skill.strategy_policy import TIMEFRAME_POLICY, entry_permission, parent_timeframes


base = v2.base
_original_v2_analyze_symbol = base.analyze_symbol

# Legacy scan freshness is retained only for backwards-compatible reporting. The STEP4C
# lifecycle engine remains the canonical freshness/invalidation implementation.
LEGACY_FRESHNESS_DAYS: dict[Timeframe, float] = {
    Timeframe.WEEKLY: 60.0,
    Timeframe.DAILY: 30.0,
    Timeframe.M120: 15.0,
    Timeframe.M30: 5.0,
    Timeframe.M5: 4.0 / 24.0,
}
base.FRESHNESS = {timeframe: timedelta(days=days) for timeframe, days in LEGACY_FRESHNESS_DAYS.items()}


def _analyze_with_extensions(raw_bars, *, tick_size, as_of):
    return annotate_second_buy_variants(analyze_production_chan_v3(raw_bars, tick_size=tick_size, as_of=as_of))


base.analyze_production_chan = _analyze_with_extensions


def _class2_tie_break(signal) -> tuple[bool, bool]:
    extended = set(getattr(signal, "extended_types", ()) or ())
    return (
        ChanSignalType.STRONG_CLASS2_BUY in extended,
        ChanSignalType.CENTER_CLASS2_BUY in extended,
    )


def choose_primary_v3(results, *, as_of):
    """Legacy report selector aligned to the canonical new-entry contract.

    It may return a DAILY FIRST_BUY only so the report can say WAIT_2B. It never returns
    DAILY THIRD_BUY, 120m or 30m as a fresh-position primary. Therefore no compatibility
    path can recreate the retired standalone-entry policy.
    """
    daily = results.get(Timeframe.DAILY)
    if daily is None:
        return None
    fresh = []
    for signal in base.fresh_signals(daily, as_of=as_of, side="BUY"):
        kind = base.signal_type(signal)
        if kind not in {ChanSignalType.SECOND_BUY, ChanSignalType.FIRST_BUY}:
            continue
        if v2._invalidated_by_later_sell(daily, signal, as_of=as_of):
            continue
        rank = 2 if kind is ChanSignalType.SECOND_BUY else 1
        fresh.append((rank, _class2_tie_break(signal), signal.confirmation_timestamp, signal))
    if not fresh:
        return None
    _, _, _, signal = max(fresh, key=lambda item: (item[0], item[1], item[2]))
    return Timeframe.DAILY, signal


base.choose_primary = choose_primary_v3


def parent_valid_v3(results, timeframe, *, as_of):
    """Higher context uses the newest current formal state; stale sells do not block forever."""
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


def _parse_iso(value: object) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def _formal_side(signal: dict) -> str | None:
    side = str(signal.get("side") or "")
    labels = set(str(item) for item in (signal.get("types") or signal.get("standard_types") or []))
    if side == "BUY" and labels & {"FIRST_BUY", "SECOND_BUY", "THIRD_BUY"}:
        return "BUY"
    if side == "SELL" and labels & {"FIRST_SELL", "SECOND_SELL", "THIRD_SELL"}:
        return "SELL"
    return None


def _latest_formal_signal_since(raw: dict, anchor: datetime) -> dict | None:
    candidates: list[tuple[datetime, str, dict]] = []
    for signal in raw.get("signals") or []:
        side = _formal_side(signal)
        if side is None:
            continue
        when = _parse_iso(signal.get("confirmation_timestamp"))
        if when is None:
            continue
        if anchor.tzinfo is not None and when.tzinfo is None:
            when = when.replace(tzinfo=anchor.tzinfo)
        if anchor.tzinfo is not None and when.tzinfo is not None:
            when = when.astimezone(anchor.tzinfo)
        if when < anchor:
            continue
        candidates.append((when, str(signal.get("id") or ""), signal))
    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], item[1]))[2]


def _execution_structure_ok(analysis: dict, candidate: dict) -> tuple[bool, list[str], dict[str, str]]:
    """Strict legacy execution bridge: DAILY 2B -> formal 120m BUY -> formal 30m BUY -> formal 5m BUY.

    Child signals are anchored to the DAILY authority structural timestamp (confirmation
    time is only a fallback). Their confirmation order relative to each other is irrelevant.
    A newer SELL controls that child level. Indicators may PAUSE but can never substitute
    for a missing formal BUY. Price-distance maturity is an independent chase guard.
    """
    if str(candidate.get("timeframe") or "") != Timeframe.DAILY.value:
        return False, ["只有日线授权候选可以进入新开仓执行链"], {"5m_execution": "NOT_DAILY_AUTHORITY"}
    if str(candidate.get("signal") or "") != ChanSignalType.SECOND_BUY.value:
        state = "WAIT_DAILY_SECOND_BUY" if str(candidate.get("signal") or "") == ChanSignalType.FIRST_BUY.value else "NO_FRESH_ENTRY"
        return False, ["新开仓执行链要求日线标准二买/类二买授权"], {"5m_execution": state}

    anchor = _parse_iso(candidate.get("signal_structural_time")) or _parse_iso(candidate.get("signal_confirmation_time"))
    if anchor is None:
        return False, ["日线授权信号缺少结构/确认时间，无法归属低周期执行证据"], {"5m_execution": "UNVERIFIABLE"}

    timeframes = analysis.get("timeframes") or {}
    conflicts: list[str] = []
    states: dict[str, str] = {}
    missing = False
    for child in ("120m", "30m", "5m"):
        raw = timeframes.get(child) or {}
        latest = _latest_formal_signal_since(raw, anchor)
        if latest is None:
            states[child] = "WAITING_FORMAL_BUY"
            conflicts.append(f"{TIMEFRAME_POLICY[Timeframe(child)].chinese_name}当前日线结构内没有正式BUY；趋势或指标不能替代")
            missing = True
            continue
        side = _formal_side(latest)
        labels = list(latest.get("types") or latest.get("standard_types") or [])
        states[child] = f"{side}:{'/'.join(labels) or '结构信号'}"
        if side == "SELL":
            conflicts.append(f"{TIMEFRAME_POLICY[Timeframe(child)].chinese_name}当前日线结构内最新正式结构仍为卖点")

    five = timeframes.get("5m") or {}
    five_status = str(five.get("status") or "")
    five_technical = str(((five.get("technical") or {}).get("confirmation") or ""))
    states["5m_technical"] = five_technical or "NONE"
    if five_status not in {"OK", "UNRESOLVED"}:
        conflicts.append("5分钟分析结果不可验证")
        states["5m_execution"] = "UNVERIFIABLE"
        return False, conflicts, states
    if five_technical == "PAUSE":
        conflicts.append("5分钟已有结构证据但技术确认层为PAUSE；指标只能暂停，不能创造或升级买点")

    if any("最新正式结构仍为卖点" in item for item in conflicts) or five_technical == "PAUSE":
        states["5m_execution"] = "CONFLICT"
        return False, conflicts, states
    if missing:
        states["5m_execution"] = "WAITING_FORMAL_CHAIN"
        return False, conflicts, states

    maturity = str(candidate.get("execution_maturity") or "NOT_READY")
    if maturity not in {"TRIGGERED", "PREPARE"}:
        conflicts.append(f"当前价格执行成熟度为{maturity}，不在触发/准备区")
        states["5m_execution"] = "WAITING_PRICE"
        return False, conflicts, states

    states["5m_execution"] = "CONFIRMED:FORMAL_120M_30M_5M_BUY"
    return True, [], states


def _find_primary_signal_raw(analysis: dict, candidate: dict) -> dict | None:
    tf = str(candidate.get("timeframe") or "")
    expected = str(candidate.get("signal") or "")
    confirm = str(candidate.get("signal_confirmation_time") or "")
    raw = (analysis.get("timeframes") or {}).get(tf) or {}
    for signal in reversed(raw.get("signals") or []):
        labels = signal.get("types") or signal.get("standard_types") or []
        if expected in labels and (not confirm or signal.get("confirmation_timestamp") == confirm):
            return signal
    return None


def _entry_zones(kind: str, signal_price: float) -> tuple[str, str]:
    if kind == ChanSignalType.SECOND_BUY.value:
        trigger, prepare = 0.04, 0.08
    else:
        trigger, prepare = 0.03, 0.06
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
    "HISTORY_CONTEXT_INCOMPLETE",
}


def _history_for_symbol(symbol: dict) -> dict | None:
    explicit = symbol.get("history_quality")
    if isinstance(explicit, dict) and explicit:
        return dict(explicit)
    if not any(key in symbol for key in ("daily", "weekly", "120m", "30m", "5m")):
        return None
    return classify_history_counts(
        daily=len(symbol.get("daily") or []),
        weekly=len(symbol.get("weekly") or []),
        m120=len(symbol.get("120m") or []),
        m30=len(symbol.get("30m") or []),
        m5=len(symbol.get("5m") or []),
    ).to_dict()


def _enforce_history_policy(candidate: dict, symbol: dict) -> None:
    quality = _history_for_symbol(symbol)
    if quality is None:
        return
    timeframe = str(candidate.get("timeframe") or "")
    ok, reason = primary_history_gate(timeframe, quality)
    candidate["history_quality"] = quality
    candidate["history_note"] = reason + "；" + str(quality.get("note") or "")
    if ok:
        return
    candidate["push"] = False
    candidate["action"] = "OBSERVE"
    candidate["recent_signal_note"] = f"缠论结构保留观察，但{reason}；有限历史不能冒充完整结构证据"
    blockers = list(candidate.get("blockers") or [])
    if "HISTORY_CONTEXT_INCOMPLETE" not in blockers:
        blockers.append("HISTORY_CONTEXT_INCOMPLETE")
    candidate["blockers"] = blockers


def _enforce_first_buy_permission(candidate: dict) -> None:
    """FIRST_BUY is descriptive only; it never creates fresh-position permission.

    DAILY FIRST_BUY has one specific strategy meaning: WAIT_2B. Lower-timeframe first
    buys remain useful structure observations, but because 120m/30m are no longer fresh
    entry authorities they must never be labelled PREPARE_BUY. Existing hard blockers
    remain the dominant explanation rather than being overwritten by WAIT_2B.
    """
    if str(candidate.get("signal") or "") != ChanSignalType.FIRST_BUY.value:
        return

    candidate["push"] = False
    candidate["buy_amount"] = None
    candidate["buy_quantity"] = None
    blockers = set(candidate.get("blockers") or [])
    if blockers & HARD_ENTRY_BLOCKERS:
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "一买结构存在，但仍有基本面/风险/数据/上级结构/历史等硬阻断，仅保留观察；一买不能绕过任何硬门槛"
        return

    timeframe = str(candidate.get("timeframe") or "")
    if timeframe == Timeframe.DAILY.value:
        candidate["action"] = "WAIT_2B"
        candidate["recent_signal_note"] = "日线一买仅记录反转事实，等待标准二买/类二买；120分钟、30分钟或5分钟信号不得提前创造新开仓资格"
        return

    candidate["action"] = "OBSERVE"
    candidate["recent_signal_note"] = f"{timeframe or '低周期'}一买只作为结构观察/执行背景；该周期不是新开仓授权周期，不能单独准备或建立新仓"


def _major_negative_events(events: list[dict] | tuple[dict, ...]) -> list[dict]:
    return [event for event in (events or []) if event.get("importance") == "重大" and event.get("impact") == "利空"]


def _major_negative_industry_events(industry: dict) -> list[dict]:
    return _major_negative_events(list(industry.get("events") or []))


def _events_for_symbol(symbol: dict, industry_map: dict) -> list[dict]:
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
        candidate["signal_structural_time"] = raw_signal.get("structural_timestamp")
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

    _enforce_history_policy(candidate, symbol)
    _enforce_first_buy_permission(candidate)

    execution_ok, conflicts, latest_states = _execution_structure_ok(analysis, candidate)
    candidate["execution_structure_ok"] = execution_ok
    candidate["execution_conflicts"] = conflicts
    candidate["execution_latest_states"] = latest_states
    candidate["execution_confirmation_state"] = latest_states.get("5m_execution", "UNVERIFIABLE")
    if not execution_ok:
        candidate["push"] = False
        # Preserve WAIT_2B wording for daily first buy; otherwise execution waits as OBSERVE.
        if str(candidate.get("signal") or "") != ChanSignalType.FIRST_BUY.value:
            candidate["action"] = "OBSERVE"
            state = str(candidate.get("execution_confirmation_state") or "UNVERIFIABLE")
            candidate["recent_signal_note"] = (
                f"日线授权结构仍保留，但执行状态为{state}；"
                + ("；".join(conflicts) if conflicts else "等待120m→30m→5m正式结构链")
            )
        blockers = list(candidate.get("blockers") or [])
        state = str(candidate.get("execution_confirmation_state") or "UNVERIFIABLE")
        blocker = "EXECUTION_STRUCTURE_CONFLICT" if state == "CONFLICT" else "EXECUTION_CONFIRMATION_PENDING"
        if blocker not in blockers:
            blockers.append(blocker)
        candidate["blockers"] = blockers

    industry_events = _events_for_symbol(symbol, industry_map)
    candidate["industry_major_events"] = industry_events
    major_negative = _major_negative_events(industry_events)
    if major_negative:
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "缠论结构保留，但真实所属行业出现近期重大利空，暂停新开仓并等待事件影响重新定价"
        blockers = list(candidate.get("blockers") or [])
        if "INDUSTRY_MAJOR_NEGATIVE_EVENT" not in blockers:
            blockers.append("INDUSTRY_MAJOR_NEGATIVE_EVENT")
        candidate["blockers"] = blockers

    if symbol.get("candidate_route") == "跨行业结构补充" and symbol.get("industry_context_complete") is False:
        candidate["push"] = False
        candidate["action"] = "OBSERVE"
        candidate["recent_signal_note"] = "真实细分行业未成功解析，行业风险上下文不完整；结构可观察但禁止新开仓"
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
    candidate["stop_logic"] = (
        "双层保护：日线二买结构失效位定义核心交易逻辑；首笔TEST按当前日线结构内5分钟正式BUY执行止损定仓。"
        "低周期止损只处理对应低周期仓层，不能单独否定日线核心逻辑。"
    )
    candidate["add_plan"] = (
        "首笔后只有新的30分钟标准二买/三买才申请确认仓，新的120分钟标准二买/三买才申请核心仓升级，"
        "新的日线二买/三买趋势延续最多再加一层TREND_ADD；任何机械摊低成本补仓禁止。"
    )
    candidate["take_profit_plan"] = (
        "不设固定盈利百分比止盈；按5分钟、30分钟、120分钟、日线、周线的一卖/二卖/三卖分级减仓，"
        "保护位只能随新确认结构上移或保持。"
    )
    return analysis, candidate


base.analyze_symbol = analyze_symbol_v3


if __name__ == "__main__":
    raise SystemExit(base.main())
