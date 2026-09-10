from __future__ import annotations

from datetime import datetime, timedelta

import scripts.run_full_a_scan as base
from trading_skill.chan_policy_v2 import INTRADAY_CONFIRM_TYPES, validate_standard_second_buy
from trading_skill.domain.enums import ChanSignalType, Timeframe
from trading_skill.position_policy_v2 import staged_position_policy, take_profit_contract
from trading_skill.sizing import StopCandidate, StopLevel, StopType


# Freshness only answers whether a signal may still be considered current. It never
# grants lower timeframes fresh-position authority.
base.FRESHNESS = {
    Timeframe.WEEKLY: timedelta(days=60),
    Timeframe.DAILY: timedelta(days=30),
    Timeframe.M120: timedelta(days=15),
    Timeframe.M30: timedelta(days=5),
    Timeframe.M5: timedelta(hours=4),
}

DAILY_PRIMARY_PRIORITY = {
    ChanSignalType.SECOND_BUY: 4,
    ChanSignalType.FIRST_BUY: 1,
}

_MATURITY_RANK = {"WATCH": 0, "PREPARE": 1, "TRIGGERED": 2}
_HARD_CONTEXT_BLOCKERS = {
    "FUNDAMENTAL_VETO",
    "PARENT_CONTEXT_INVALID",
    "DATA_INCOMPLETE",
    "INDUSTRY_MAJOR_NEGATIVE_EVENT",
    "INDUSTRY_EVENT_CONTEXT_UNAVAILABLE",
    "INDUSTRY_CONTEXT_INCOMPLETE",
    "CORE_STRUCTURAL_STOP_UNDEFINED",
    "TECHNICAL_EXECUTION_PAUSED",
}


def _invalidated_by_later_sell(result, buy_signal, *, as_of) -> bool:
    for sell in base.fresh_signals(result, as_of=as_of, side="SELL"):
        if sell.confirmation_timestamp >= buy_signal.confirmation_timestamp:
            return True
    return False


def choose_primary_v2(results, *, as_of):
    """Only DAILY SECOND_BUY can authorize a fresh position; DAILY FIRST_BUY is watch-only."""
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
    _, _, signal = max(choices, key=lambda item: (item[0], item[1]))
    return Timeframe.DAILY, signal


def execution_maturity_v2(signal, current_price: float) -> str:
    """Price-distance chase guard only. It never creates structural permission."""
    signal_price = signal.structural_price_ticks * float(base.TICK_SIZE)
    if signal_price <= 0 or current_price <= 0:
        return "WATCH"
    kind = base.signal_type(signal)
    if kind is not ChanSignalType.SECOND_BUY:
        return "WATCH"
    distance = (current_price - signal_price) / signal_price
    if -0.01 <= distance <= 0.04:
        return "TRIGGERED"
    if 0.04 < distance <= 0.08:
        return "PREPARE"
    return "WATCH"


def _parse_ts(value: object, *, reference: datetime | None = None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if reference is not None and reference.tzinfo is not None and parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference.tzinfo)
    if reference is not None and reference.tzinfo is not None and parsed.tzinfo is not None:
        parsed = parsed.astimezone(reference.tzinfo)
    return parsed


def _fresh_raw_signals(tf_raw: dict, *, timeframe: Timeframe, as_of: datetime, side: str) -> list[dict]:
    cutoff = as_of - base.FRESHNESS[timeframe]
    signals = []
    for signal in tf_raw.get("signals") or []:
        if str(signal.get("side") or "") != side:
            continue
        confirmed = _parse_ts(signal.get("confirmation_timestamp"), reference=as_of)
        if confirmed is not None and cutoff <= confirmed <= as_of:
            signals.append(signal)
    return signals


def _raw_standard_types(signal: dict) -> set[ChanSignalType]:
    out: set[ChanSignalType] = set()
    for item in signal.get("types") or signal.get("standard_types") or []:
        try:
            out.add(ChanSignalType(str(item)))
        except ValueError:
            continue
    return out


def _eligible_raw_buys(
    tf_raw: dict,
    *,
    timeframe: Timeframe,
    as_of: datetime,
    authority_anchor: datetime,
) -> list[dict]:
    eligible = []
    for signal in _fresh_raw_signals(tf_raw, timeframe=timeframe, as_of=as_of, side="BUY"):
        confirmed = _parse_ts(signal.get("confirmation_timestamp"), reference=as_of)
        if confirmed is None or confirmed < authority_anchor:
            continue
        if _raw_standard_types(signal) & INTRADAY_CONFIRM_TYPES:
            eligible.append(signal)
    return eligible


def _latest_active_raw_buy(
    tf_raw: dict,
    *,
    timeframe: Timeframe,
    as_of: datetime,
    authority_anchor: datetime,
) -> dict | None:
    """Return a current formal BUY belonging to the current DAILY structural leg.

    Lower-level confirmations may occur before DAILY confirmation, therefore membership is
    anchored to DAILY structural_timestamp rather than daily confirmation_timestamp.
    """
    buys = _eligible_raw_buys(
        tf_raw,
        timeframe=timeframe,
        as_of=as_of,
        authority_anchor=authority_anchor,
    )
    if not buys:
        return None
    latest_buy = max(
        buys,
        key=lambda item: (
            _parse_ts(item.get("confirmation_timestamp"), reference=as_of) or authority_anchor,
            str(item.get("id") or ""),
        ),
    )
    buy_ts = _parse_ts(latest_buy.get("confirmation_timestamp"), reference=as_of)
    if buy_ts is None:
        return None
    for sell in _fresh_raw_signals(tf_raw, timeframe=timeframe, as_of=as_of, side="SELL"):
        sell_ts = _parse_ts(sell.get("confirmation_timestamp"), reference=as_of)
        if sell_ts is None or sell_ts < authority_anchor:
            continue
        if not (_raw_standard_types(sell) & {
            ChanSignalType.FIRST_SELL,
            ChanSignalType.SECOND_SELL,
            ChanSignalType.THIRD_SELL,
        }):
            continue
        if sell_ts >= buy_ts:
            return None
    return latest_buy


def _authority_raw_signal(
    analysis: dict,
    *,
    signal_type: str,
    signal_price_ticks: int | None,
) -> dict | None:
    daily = (analysis.get("timeframes") or {}).get("daily") or {}
    candidates = []
    for signal in daily.get("signals") or []:
        if signal_type not in {kind.value for kind in _raw_standard_types(signal)}:
            continue
        if signal_price_ticks is not None and int(signal.get("structural_price_ticks") or 0) != signal_price_ticks:
            continue
        candidates.append(signal)
    if not candidates:
        return None
    return max(candidates, key=lambda item: str(item.get("confirmation_timestamp") or ""))


def _authority_anchor(authority_signal: dict | None, *, as_of: datetime) -> datetime | None:
    if not authority_signal:
        return None
    return (
        _parse_ts(authority_signal.get("structural_timestamp"), reference=as_of)
        or _parse_ts(authority_signal.get("confirmation_timestamp"), reference=as_of)
    )


def _structural_execution_maturity(
    analysis: dict,
    *,
    as_of: datetime,
    signal_type: str,
    authority_anchor: datetime | None,
) -> tuple[str, list[str], dict[str, dict]]:
    if signal_type == ChanSignalType.FIRST_BUY.value:
        return "WATCH", ["DAILY_FIRST_BUY_WAIT_2B"], {}
    if signal_type != ChanSignalType.SECOND_BUY.value:
        return "WATCH", ["DAILY_2B_PERMISSION_MISSING"], {}
    if authority_anchor is None:
        return "WATCH", ["DAILY_AUTHORITY_ANCHOR_MISSING"], {}

    timeframes = analysis.get("timeframes") or {}
    evidence: dict[str, dict] = {}
    for key, timeframe, reason in (
        ("120m", Timeframe.M120, "M120_CONFIRMATION_MISSING"),
        ("30m", Timeframe.M30, "M30_EXECUTION_SETUP_MISSING"),
        ("5m", Timeframe.M5, "M5_EXECUTION_TRIGGER_MISSING"),
    ):
        signal = _latest_active_raw_buy(
            timeframes.get(key) or {},
            timeframe=timeframe,
            as_of=as_of,
            authority_anchor=authority_anchor,
        )
        if signal is None:
            maturity = "PREPARE" if key == "5m" and "120m" in evidence and "30m" in evidence else "WATCH"
            return maturity, [reason], evidence
        evidence[key] = {
            "signal_id": signal.get("id"),
            "confirmation_timestamp": signal.get("confirmation_timestamp"),
            "types": list(signal.get("types") or signal.get("standard_types") or []),
        }

    five = timeframes.get("5m") or {}
    technical_confirmation = str(((five.get("technical") or {}).get("confirmation") or ""))
    if technical_confirmation == "PAUSE":
        return "WATCH", ["TECHNICAL_EXECUTION_PAUSED"], evidence
    return "TRIGGERED", [], evidence


def _lower_maturity(left: str, right: str) -> str:
    return min((left, right), key=lambda item: _MATURITY_RANK.get(item, -1))


def _raw_class2_annotations(
    analysis: dict,
    *,
    signal_type: str,
    signal_price_ticks: int | None = None,
) -> list[str]:
    """Compatibility serialization of class-2 labels; standard signal remains SECOND_BUY."""
    if signal_type != ChanSignalType.SECOND_BUY.value:
        return []
    daily = (analysis.get("timeframes") or {}).get("daily") or {}
    second_candidates = [
        signal for signal in daily.get("signals") or []
        if ChanSignalType.SECOND_BUY in _raw_standard_types(signal)
    ]
    if signal_price_ticks is not None:
        matched = [
            signal for signal in second_candidates
            if int(signal.get("structural_price_ticks") or 0) == signal_price_ticks
        ]
        if matched:
            second_candidates = matched
    if not second_candidates:
        return []
    second = max(second_candidates, key=lambda item: str(item.get("confirmation_timestamp") or ""))
    second_evidence = {item for item in second.get("evidence_ids") or [] if item}
    level = int(second.get("level_rank") or 0)
    extended: list[str] = []

    for signal in daily.get("signals") or []:
        if ChanSignalType.THIRD_BUY not in _raw_standard_types(signal):
            continue
        if int(signal.get("level_rank") or 0) != level:
            continue
        third_evidence = {item for item in signal.get("evidence_ids") or [] if item}
        if second_evidence & third_evidence:
            extended.append(ChanSignalType.STRONG_CLASS2_BUY.value)
            break

    second_ts = _parse_ts(second.get("confirmation_timestamp"))
    centers = []
    for center in daily.get("centers") or []:
        if int(center.get("level_rank") or 0) != level:
            continue
        center_ts = _parse_ts(center.get("confirmation"))
        if second_ts is not None and center_ts is not None and center_ts <= second_ts:
            centers.append((center_ts, center))
    if centers:
        _, center = max(centers, key=lambda item: item[0])
        if int(second.get("structural_price_ticks") or 0) >= int(center.get("zg_ticks") or 0):
            extended.append(ChanSignalType.CENTER_CLASS2_BUY.value)
    return list(dict.fromkeys(extended))


def _staged_entry_payload(*, signal_type: str) -> list[dict]:
    if signal_type != ChanSignalType.SECOND_BUY.value:
        return []
    return [
        {
            "role": rule.role.value,
            "capacity_fraction": rule.capacity_fraction,
            "capacity_basis": rule.capacity_basis.value,
            "add_gate": rule.add_gate.value,
            "management_stop_level": rule.management_stop_level.value,
            "requires_new_structure": rule.requires_new_structure,
            "may_average_down": rule.may_average_down,
        }
        for rule in staged_position_policy().rules
    ]


def _execution_stop_candidate(
    analysis: dict,
    *,
    as_of: datetime,
    authority_anchor: datetime | None,
) -> StopCandidate | None:
    if authority_anchor is None:
        return None
    m5 = (analysis.get("timeframes") or {}).get("5m") or {}
    signal = _latest_active_raw_buy(
        m5,
        timeframe=Timeframe.M5,
        as_of=as_of,
        authority_anchor=authority_anchor,
    )
    if signal is None:
        return None
    structural_ticks = int(signal.get("structural_price_ticks") or 0)
    stop_ticks = structural_ticks - 1
    if stop_ticks <= 0:
        return None
    signal_id = str(signal.get("id") or f"m5-{signal.get('confirmation_timestamp') or 'signal'}")
    return StopCandidate(
        id=f"stop-execution-{signal_id}",
        level=StopLevel.L5,
        stop_type=StopType.EXECUTION_STRUCTURE,
        price_ticks=stop_ticks,
        source_structure_id=signal_id,
        source_signal_id=signal_id,
        is_structural=True,
        noise_risk=False,
    )


def _independent_context_blockers(
    candidate: dict,
    symbol: dict,
    analysis: dict,
    *,
    parents_ok: bool,
    signal_type: str,
    authority_signal: dict | None,
) -> list[str]:
    blockers: list[str] = []
    prefilter = symbol.get("fundamental_prefilter") or {}
    if not bool(prefilter.get("eligible")):
        blockers.append("FUNDAMENTAL_VETO")
    if not parents_ok:
        blockers.append("PARENT_CONTEXT_INVALID")
    required = ("weekly", "daily", "120m", "30m", "5m")
    for key in required:
        status = str((((analysis.get("timeframes") or {}).get(key) or {}).get("status") or ""))
        if status not in {"OK", "UNRESOLVED"}:
            blockers.append("DATA_INCOMPLETE")
            break

    event_risk = str(symbol.get("industry_event_risk") or "UNKNOWN")
    event_context_complete = symbol.get("industry_event_context_complete") is True
    if event_risk == "HIGH":
        blockers.append("INDUSTRY_MAJOR_NEGATIVE_EVENT")
    elif event_risk == "UNKNOWN" or not event_context_complete:
        blockers.append("INDUSTRY_EVENT_CONTEXT_UNAVAILABLE")

    if symbol.get("industry_context_complete") is False:
        blockers.append("INDUSTRY_CONTEXT_INCOMPLETE")
    if signal_type == ChanSignalType.SECOND_BUY.value:
        if not authority_signal or int(authority_signal.get("structural_price_ticks") or 0) <= 0:
            blockers.append("CORE_STRUCTURAL_STOP_UNDEFINED")
    five_technical = str(
        (((((analysis.get("timeframes") or {}).get("5m") or {}).get("technical") or {}).get("confirmation") or ""))
    )
    if five_technical == "PAUSE":
        blockers.append("TECHNICAL_EXECUTION_PAUSED")
    return list(dict.fromkeys(blockers))


def _apply_execution_risk_context(
    candidate: dict,
    analysis: dict,
    *,
    as_of: datetime,
    authority_anchor: datetime | None,
    core_stop_ticks: int | None,
) -> None:
    """Expose both stops but deliberately do not invent position size in this legacy scan.

    Exact quantity belongs to audited STEP5B and requires explicit TEST risk, portfolio,
    industry/theme capacity, cash and lot-size context. A configured account equity alone
    is insufficient and must not be converted through legacy opportunity-grade multipliers.
    """
    candidate["buy_amount"] = None
    candidate["buy_quantity"] = None
    candidate["buy_fraction"] = "不使用固定S/A/B仓位比例"
    candidate["core_structural_stop"] = (
        f"{core_stop_ticks * float(base.TICK_SIZE):.2f}元" if core_stop_ticks else None
    )
    candidate["stop"] = candidate["core_structural_stop"] or "暂无"
    candidate["execution_stop"] = None
    candidate["execution_stop_signal_id"] = None
    execution_stop = _execution_stop_candidate(
        analysis,
        as_of=as_of,
        authority_anchor=authority_anchor,
    )
    if execution_stop is not None:
        candidate["execution_stop"] = f"{execution_stop.price_ticks * float(base.TICK_SIZE):.2f}元"
        candidate["execution_stop_signal_id"] = execution_stop.source_signal_id
        candidate["sizing_basis"] = "首笔TEST应由显式TEST风险预算 ÷（计划成交价-5分钟执行止损）计算"
    else:
        candidate["sizing_basis"] = "等待当前日线结构内正式5分钟BUY执行止损"
    candidate["sizing_blocker"] = "STEP5B_EXPLICIT_RISK_CONTEXT_REQUIRED"


_original_analyze_symbol = base.analyze_symbol


def analyze_symbol_v2(symbol, industry_map, *, as_of, equity):
    # base still supplies five-timeframe Chan analysis and auxiliary indicator/risk
    # observations. This wrapper replaces its authority, action and sizing semantics.
    analysis, candidate = _original_analyze_symbol(symbol, industry_map, as_of=as_of, equity=equity)
    if not candidate:
        return analysis, candidate

    prefilter = symbol.get("fundamental_prefilter") or {}
    candidate["prospect_theme"] = symbol.get("prospect_theme")
    candidate["industry_selection_reason"] = symbol.get("industry_selection_reason")
    candidate["candidate_route"] = symbol.get("candidate_route")
    candidate["daily_priority_score"] = symbol.get("daily_priority_score")
    candidate["actual_industry_name"] = symbol.get("actual_industry_name")
    candidate["industry_context_complete"] = symbol.get("industry_context_complete")
    candidate["industry_context_note"] = symbol.get("industry_context_note")
    candidate["fundamental_grade"] = prefilter.get("grade")
    candidate["industry_metric_policy"] = prefilter.get("industry_policy")
    candidate["industry_valuation_focus"] = list(prefilter.get("valuation_focus") or [])
    candidate["industry_metric_focus"] = list(prefilter.get("metric_focus") or [])
    candidate["industry_report_focus"] = list(prefilter.get("report_focus") or [])
    candidate["latest_financial_report"] = (prefilter.get("latest") or {}).get("report_date")
    candidate["industry_event_risk"] = symbol.get("industry_event_risk")
    candidate["industry_event_score"] = symbol.get("industry_event_score")
    candidate["industry_event_context_complete"] = symbol.get("industry_event_context_complete")

    # Keep legacy score/risk only as diagnostic observations. They do not define Chan,
    # new-entry authority or TEST size after this point.
    candidate["auxiliary_opportunity_grade"] = candidate.get("opportunity")
    candidate["auxiliary_risk_state"] = candidate.get("risk")
    candidate["opportunity_role"] = "辅助观察：不得定义缠论买点、不得替代执行链、不得直接换算仓位比例"

    signal_price_text = str(candidate.get("buy_point") or "").split("～", 1)[0].replace("元", "")
    try:
        signal_price = float(signal_price_text)
    except ValueError:
        signal_price = 0.0
    signal_price_ticks = round(signal_price / float(base.TICK_SIZE)) if signal_price > 0 else None
    current_price = float(candidate.get("current_price") or 0)
    candidate["rise_since_signal_pct"] = round((current_price / signal_price - 1) * 100, 2) if signal_price > 0 else None

    signal_type = str(candidate.get("signal") or "")
    authority_signal = _authority_raw_signal(
        analysis,
        signal_type=signal_type,
        signal_price_ticks=signal_price_ticks,
    )
    authority_anchor = _authority_anchor(authority_signal, as_of=as_of)
    core_stop_ticks = int((authority_signal or {}).get("structural_price_ticks") or 0) or None
    candidate["authority_signal_id"] = (authority_signal or {}).get("id")
    candidate["authority_structural_time"] = authority_anchor.isoformat() if authority_anchor else None

    structural_maturity, hierarchy_reasons, execution_evidence = _structural_execution_maturity(
        analysis,
        as_of=as_of,
        signal_type=signal_type,
        authority_anchor=authority_anchor,
    )
    price_guard = str(candidate.get("execution_maturity") or "WATCH")
    final_maturity = _lower_maturity(structural_maturity, price_guard)
    candidate["price_distance_guard"] = price_guard
    candidate["structural_execution_maturity"] = structural_maturity
    candidate["execution_maturity"] = final_maturity
    candidate["execution_chain_evidence"] = execution_evidence
    candidate["timeframe_contract"] = {
        "weekly": "战略环境与长期风险边界，不直接下单",
        "daily": "唯一新开仓结构授权：标准二买/类二买；一买只WAIT_2B，三买仅管理已有仓位的趋势延续",
        "120m": "当前日线结构的尾部确认；新独立二买/三买仅可用于已有仓位核心升级",
        "30m": "当前日线结构的执行准备；新独立二买/三买仅可用于已有仓位确认仓",
        "5m": "当前日线结构的最终正式BUY触发，并定义首笔TEST执行止损",
    }

    extended = _raw_class2_annotations(
        analysis,
        signal_type=signal_type,
        signal_price_ticks=signal_price_ticks,
    )
    candidate["extended_signals"] = extended
    candidate["signal_label"] = " / ".join([signal_type] + extended) if signal_type else None
    candidate["staged_entry_plan"] = _staged_entry_payload(signal_type=signal_type)
    candidate["take_profit_contract"] = list(take_profit_contract())
    candidate["stop_contract"] = {
        "TEST": "首笔TEST按当前日线结构内5分钟正式BUY执行止损定风险；5分钟失效只处理首笔/低周期仓层",
        "CONFIRMATION": "新的30分钟二买/三买确认后才可增加确认仓，保护位只允许上移或保持",
        "CORE": "日线二买结构失效位定义核心交易逻辑；新的120分钟二买/三买才可申请核心仓升级",
        "TREND_ADD": "新的日线二买/三买趋势延续最多增加一层，按120分钟结构保护",
    }
    candidate["add_plan"] = (
        "首笔必须先有日线标准二买/类二买授权，再完成120分钟→30分钟→5分钟正式BUY执行链；"
        "已有仓位后，新的30分钟标准二买/三买使用剩余风险容量申请确认仓，新的120分钟标准二买/三买申请核心仓升级，"
        "新的日线二买/三买趋势延续最多再加一层；禁止机械摊低成本。"
    )

    independent_blockers = _independent_context_blockers(
        candidate,
        symbol,
        analysis,
        parents_ok=str(candidate.get("parent_structure") or "") == "上级结构通过",
        signal_type=signal_type,
        authority_signal=authority_signal,
    )
    blockers = list(dict.fromkeys(independent_blockers + hierarchy_reasons))
    candidate["blockers"] = blockers
    hard_blocked = bool(set(blockers) & _HARD_CONTEXT_BLOCKERS)
    candidate["cautions"] = []
    if str(symbol.get("industry_event_risk") or "UNKNOWN") == "CAUTION":
        candidate["cautions"].append("行业近期存在重要负向事件，但尚未达到重大利空硬阻断；保留谨慎标签")

    if signal_type == ChanSignalType.FIRST_BUY.value:
        candidate["action"] = "WAIT_2B"
        candidate["push"] = False
        candidate["recent_signal_note"] = "日线一买只记录反转事实，等待标准二买/类二买；低周期信号不得提前创造新开仓资格"
        candidate["trade_permission_state"] = "WAIT_TECHNICAL"
    elif signal_type != ChanSignalType.SECOND_BUY.value:
        candidate["action"] = "OBSERVE"
        candidate["push"] = False
        candidate["recent_signal_note"] = "当前日线信号不是新开仓所需的标准二买，保留结构观察"
        candidate["trade_permission_state"] = "WAIT_TECHNICAL"
    elif hard_blocked:
        candidate["action"] = "OBSERVE"
        candidate["push"] = False
        candidate["recent_signal_note"] = "日线二买结构保留，但基本面/事件/事件数据完整性/周线/数据/技术暂停等独立门槛未通过，不允许新开仓"
        candidate["trade_permission_state"] = "BLOCKED_OR_CONTEXT_REQUIRED"
    elif final_maturity == "WATCH":
        candidate["action"] = "OBSERVE"
        candidate["push"] = False
        candidate["recent_signal_note"] = "日线二买有效，但当前日线结构内120m→30m→5m正式执行链尚未完成，或价格已偏离追高保护区"
        candidate["trade_permission_state"] = "WAIT_TECHNICAL"
    elif final_maturity == "PREPARE":
        candidate["action"] = "PREPARE_BUY"
        candidate["push"] = True
        candidate["recent_signal_note"] = "日线二买有效，120m与30m结构已确认；等待当前日线结构内新的5分钟正式BUY触发，或等待更合适价格"
        candidate["trade_permission_state"] = "WAIT_EXECUTION_OR_CONTEXT"
    else:
        candidate["action"] = "PREPARE_BUY"
        candidate["push"] = True
        candidate["recent_signal_note"] = "日线二买授权与120m→30m→5m正式结构链已完成；结构机会成立，但真实下单仍需STEP5A/B账户与风险上下文"
        candidate["trade_permission_state"] = "STEP5_CONTEXT_REQUIRED"

    candidate["new_entry_order_ready"] = False
    candidate["push_semantics"] = "结构机会报告，不等同券商下单许可"
    _apply_execution_risk_context(
        candidate,
        analysis,
        as_of=as_of,
        authority_anchor=authority_anchor,
        core_stop_ticks=core_stop_ticks,
    )

    if authority_signal is not None:
        candidate["signal_confirmation_time"] = authority_signal.get("confirmation_timestamp")
    return analysis, candidate


base.choose_primary = choose_primary_v2
base.execution_maturity = execution_maturity_v2
base.analyze_symbol = analyze_symbol_v2


if __name__ == "__main__":
    raise SystemExit(base.main())
