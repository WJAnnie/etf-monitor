from __future__ import annotations

from datetime import timedelta

import scripts.run_full_a_scan as base
from trading_skill.domain.enums import ChanSignalType, Timeframe


# V2 keeps a setup alive long enough for lower-level confirmation, but freshness alone
# never makes an old higher-timeframe signal executable.
base.FRESHNESS = {
    Timeframe.WEEKLY: timedelta(days=60),
    Timeframe.DAILY: timedelta(days=30),
    Timeframe.M120: timedelta(days=15),
    Timeframe.M30: timedelta(days=5),
    Timeframe.M5: timedelta(hours=4),
}

SIGNAL_PRIORITY = {
    ChanSignalType.SECOND_BUY: 4,
    ChanSignalType.THIRD_BUY: 3,
    ChanSignalType.FIRST_BUY: 2,
}
TIMEFRAME_PRIORITY = {
    Timeframe.M120: 4,
    Timeframe.DAILY: 3,
    Timeframe.M30: 2,
}


def _invalidated_by_later_sell(result, buy_signal, *, as_of) -> bool:
    """A buy remains active only while no later same-timeframe formal sell has appeared."""
    for sell in base.fresh_signals(result, as_of=as_of, side="SELL"):
        if sell.confirmation_timestamp > buy_signal.confirmation_timestamp:
            return True
    return False


def _valid_fresh_buys(result, *, as_of, not_before=None):
    if result is None or result.status not in {"OK", "UNRESOLVED"}:
        return []
    out = []
    for signal in base.fresh_signals(result, as_of=as_of, side="BUY"):
        kind = base.signal_type(signal)
        if kind not in base.BUY_TYPES:
            continue
        if not_before is not None and signal.confirmation_timestamp < not_before:
            continue
        if _invalidated_by_later_sell(result, signal, as_of=as_of):
            continue
        out.append(signal)
    out.sort(
        key=lambda signal: (
            SIGNAL_PRIORITY.get(base.signal_type(signal), 0),
            signal.confirmation_timestamp,
        ),
        reverse=True,
    )
    return out


def _latest_valid_buy(result, *, as_of, not_before=None):
    signals = _valid_fresh_buys(result, as_of=as_of, not_before=not_before)
    return signals[0] if signals else None


def choose_primary_v2(results, *, as_of):
    """Select a setup, not an execution trigger.

    Weekly is strategic context only. Daily/120m/30m can become setup layers.
    Signal quality outranks recency so a fresh 2B is not displaced by a newer 1B.
    """
    choices = []
    for timeframe in (Timeframe.DAILY, Timeframe.M120, Timeframe.M30):
        result = results.get(timeframe)
        if result is None:
            continue
        for signal in _valid_fresh_buys(result, as_of=as_of):
            kind = base.signal_type(signal)
            choices.append(
                (
                    SIGNAL_PRIORITY.get(kind, 0),
                    TIMEFRAME_PRIORITY.get(timeframe, 0),
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


def execution_maturity_v2(signal, current_price: float) -> str:
    """Price-extension gate for the parent setup only.

    This function deliberately does *not* create an executable trigger. The final
    TRIGGERED state is decided by resolve_execution_context_v2 using lower-level
    Chan structure.
    """
    signal_price = signal.structural_price_ticks * float(base.TICK_SIZE)
    if signal_price <= 0 or current_price <= 0:
        return "NOT_READY"
    distance = (current_price - signal_price) / signal_price
    kind = base.signal_type(signal)
    if kind is ChanSignalType.SECOND_BUY:
        trigger_high, prepare_high = 0.04, 0.08
    elif kind is ChanSignalType.FIRST_BUY:
        trigger_high, prepare_high = 0.03, 0.06
    else:
        trigger_high, prepare_high = 0.03, 0.05
    if -0.01 <= distance <= trigger_high:
        return "TRIGGERED"
    if trigger_high < distance <= prepare_high:
        return "PREPARE"
    return "WATCH"


def resolve_execution_context_v2(results, *, primary_tf, setup_signal, as_of, current_price):
    """Resolve the top-down structural execution chain.

    Contract:
    - weekly: strategic filter only, never an entry layer;
    - daily: setup; needs a fresh 120m child and then a fresh 30m child;
    - 120m: setup; needs a fresh 30m child;
    - 30m: execution setup itself;
    - 5m: final first-tranche trigger. A 5m signal alone can never create a trade.

    Lower-level signals must be no older than the parent structure they execute and
    must not have been invalidated by a later same-level formal sell.
    """
    price_state = execution_maturity_v2(setup_signal, current_price)
    reasons = [f"SETUP_PRICE_STATE_{price_state}"]
    if price_state in {"NOT_READY", "WATCH"}:
        return {
            "maturity": "WATCH" if price_state == "WATCH" else "NOT_READY",
            "confirmation_timeframe": None,
            "confirmation_signal": None,
            "execution_timeframe": None,
            "execution_signal": None,
            "reason_codes": tuple(reasons),
        }

    confirmation_tf = None
    confirmation_signal = None

    if primary_tf is Timeframe.DAILY:
        m120 = _latest_valid_buy(
            results.get(Timeframe.M120),
            as_of=as_of,
            not_before=setup_signal.structural_timestamp,
        )
        if m120 is None:
            reasons.append("WAIT_120M_CHILD_STRUCTURE")
            return {
                "maturity": "WATCH",
                "confirmation_timeframe": None,
                "confirmation_signal": None,
                "execution_timeframe": None,
                "execution_signal": None,
                "reason_codes": tuple(reasons),
            }
        reasons.append("120M_CHILD_ACTIVE")
        confirmation_tf = Timeframe.M30
        confirmation_signal = _latest_valid_buy(
            results.get(Timeframe.M30),
            as_of=as_of,
            not_before=m120.structural_timestamp,
        )
    elif primary_tf is Timeframe.M120:
        confirmation_tf = Timeframe.M30
        confirmation_signal = _latest_valid_buy(
            results.get(Timeframe.M30),
            as_of=as_of,
            not_before=setup_signal.structural_timestamp,
        )
    elif primary_tf is Timeframe.M30:
        confirmation_tf = Timeframe.M30
        confirmation_signal = setup_signal
    else:
        reasons.append("UNSUPPORTED_SETUP_TIMEFRAME")
        return {
            "maturity": "NOT_READY",
            "confirmation_timeframe": None,
            "confirmation_signal": None,
            "execution_timeframe": None,
            "execution_signal": None,
            "reason_codes": tuple(reasons),
        }

    if confirmation_signal is None:
        reasons.append("WAIT_30M_CONFIRMATION")
        return {
            "maturity": "PREPARE",
            "confirmation_timeframe": confirmation_tf,
            "confirmation_signal": None,
            "execution_timeframe": None,
            "execution_signal": None,
            "reason_codes": tuple(reasons),
        }

    reasons.append("30M_CONFIRMATION_ACTIVE")
    execution_signal = _latest_valid_buy(
        results.get(Timeframe.M5),
        as_of=as_of,
        not_before=confirmation_signal.structural_timestamp,
    )
    if execution_signal is None:
        reasons.append("WAIT_5M_EXECUTION_BUY")
        return {
            "maturity": "PREPARE",
            "confirmation_timeframe": confirmation_tf,
            "confirmation_signal": confirmation_signal,
            "execution_timeframe": Timeframe.M5,
            "execution_signal": None,
            "reason_codes": tuple(reasons),
        }

    reasons.append("5M_STRUCTURAL_BUY_CONFIRMED")
    # A lower-level signal cannot waive parent overextension. If the setup has moved
    # into the prepare-only window, keep PREPARE even with a fresh 5m buy.
    maturity = "TRIGGERED" if price_state == "TRIGGERED" else "PREPARE"
    if maturity != "TRIGGERED":
        reasons.append("PARENT_EXTENSION_BLOCKS_IMMEDIATE_ENTRY")
    return {
        "maturity": maturity,
        "confirmation_timeframe": confirmation_tf,
        "confirmation_signal": confirmation_signal,
        "execution_timeframe": Timeframe.M5,
        "execution_signal": execution_signal,
        "reason_codes": tuple(reasons),
    }


def _signal_values(signal):
    return [item.value for item in signal.standard_types] if signal is not None else []


def _nested_labels(setup_signal, confirmation_signal, execution_signal):
    labels = []
    setup_types = set(setup_signal.standard_types)
    confirmation_types = set(confirmation_signal.standard_types) if confirmation_signal else set()
    execution_types = set(execution_signal.standard_types) if execution_signal else set()
    if ChanSignalType.SECOND_BUY in setup_types:
        labels.append("标准二买")
    if ChanSignalType.THIRD_BUY in setup_types:
        labels.append("标准三买")
    if ChanSignalType.SECOND_BUY in setup_types and ChanSignalType.THIRD_BUY in setup_types:
        labels.append("二买+三买重合")
    if ChanSignalType.FIRST_BUY in execution_types and ChanSignalType.SECOND_BUY in (setup_types | confirmation_types):
        labels.append("二买级别嵌套：次级别一买执行")
    return labels


def analyze_symbol_v2(symbol, industry_map, *, as_of, equity):
    """V2 production analysis with structural 5m execution instead of price-only triggering."""
    code = str(symbol["code"])
    results = {}
    raw_results = {}
    for key, timeframe in base.TF_MAP.items():
        raw = base.rows_to_raw(code, timeframe, list(symbol.get(key) or []))
        result = base.analyze_production_chan(raw, tick_size=base.TICK_SIZE, as_of=as_of)
        results[timeframe] = result
        raw_results[key] = base.result_dict(result)

    picked = choose_primary_v2(results, as_of=as_of)
    if picked is None:
        return {"code": code, "name": symbol.get("name"), "timeframes": raw_results, "candidate": None}, {}

    primary_tf, setup_signal = picked
    primary = results[primary_tf]
    m5_result = results[Timeframe.M5]
    parents_ok = base.parent_valid(results, primary_tf, as_of=as_of)
    industry = industry_map.get(str(symbol.get("industry_code"))) or {}
    opportunity, risk = base.opportunity_and_risk(
        primary=primary,
        signal=setup_signal,
        execution=m5_result,
        parents_ok=parents_ok,
        industry=industry,
        stock=symbol,
    )

    current_price = float(symbol.get("5m", [{}])[-1].get("close") if symbol.get("5m") else primary.latest_close or 0)
    execution_context = resolve_execution_context_v2(
        results,
        primary_tf=primary_tf,
        setup_signal=setup_signal,
        as_of=as_of,
        current_price=current_price,
    )
    maturity = execution_context["maturity"]
    confirmation_signal = execution_context["confirmation_signal"]
    execution_signal = execution_context["execution_signal"]

    setup_stop = base._stop_for(primary, setup_signal)
    execution_stop = base._stop_for(m5_result, execution_signal) if execution_signal is not None else None
    stop = execution_stop or setup_stop

    required_tfs = {primary_tf, Timeframe.M5}
    if primary_tf is Timeframe.DAILY:
        required_tfs.update({Timeframe.M120, Timeframe.M30})
    elif primary_tf is Timeframe.M120:
        required_tfs.add(Timeframe.M30)
    data_complete = all(results[tf].status == "OK" for tf in required_tfs)

    # 5m indicators are execution veto/confirmation only. They can pause a valid
    # Chan trigger but can never create one.
    execution_tech = m5_result.technical
    blockers = base.blockers_for(
        signal=setup_signal,
        fundamental_eligible=bool((symbol.get("fundamental_prefilter") or {}).get("eligible")),
        stop_defined=stop is not None,
        risk=risk,
        technical=execution_tech,
        parent_valid=parents_ok,
        data_complete=data_complete,
        portfolio_permission=True,
        allow_daily_first_buy=primary_tf is not Timeframe.DAILY,
    )
    decision = base.decide(
        opportunity=opportunity,
        risk=risk,
        signal=setup_signal,
        blockers=blockers,
        has_position=False,
        execution_maturity=maturity,
    )

    setup_kind = base.signal_type(setup_signal)
    price_signal = execution_signal if execution_signal is not None else setup_signal
    signal_price = price_signal.structural_price_ticks * float(base.TICK_SIZE)
    buy_low = signal_price
    buy_high = signal_price * (1.015 if execution_signal is not None else 1.025)

    amount = None
    quantity = None
    fraction = base.tranche_fraction(opportunity.grade)
    sizing_blocker = None
    if equity > 0 and stop is not None:
        plan = base.build_position_plan(
            equity=equity,
            grade=opportunity.grade,
            risk=risk,
            entry=max(current_price, signal_price),
            stop=stop,
            tick_size=float(base.TICK_SIZE),
            archetype="core_leader" if int(symbol.get("leader_rank") or 99) == 1 else "quality_name",
            role=base.TrancheRole.TEST,
        )
        amount = plan.rounded_value if plan.rounded_quantity else None
        quantity = plan.rounded_quantity or None
        sizing_blocker = plan.blocker

    push = (
        decision.action in (base.Action.PREPARE_BUY, base.Action.BUY_TRANCHE_1)
        and opportunity.grade is not base.OpportunityGrade.C
        and risk < base.RiskState.L2
    )
    stop_basis = "5分钟执行结构失效" if execution_stop is not None else f"{primary_tf.value}母级买点失效"
    nested_labels = _nested_labels(setup_signal, confirmation_signal, execution_signal)

    candidate = {
        "name": symbol.get("name"),
        "code": code,
        "industry": symbol.get("industry_name"),
        "industry_state": industry.get("heat_state", "暂无"),
        "leader_rank": f"细分行业第{symbol.get('leader_rank')}候选",
        # Backward-compatible primary signal fields.
        "signal": setup_kind.value if setup_kind else None,
        "timeframe": primary_tf.value,
        # Explicit V2 multi-timeframe contract.
        "setup_signal_types": _signal_values(setup_signal),
        "setup_timeframe": primary_tf.value,
        "confirmation_signal_types": _signal_values(confirmation_signal),
        "confirmation_timeframe": execution_context["confirmation_timeframe"].value if execution_context["confirmation_timeframe"] else None,
        "execution_signal_types": _signal_values(execution_signal),
        "execution_timeframe": execution_context["execution_timeframe"].value if execution_context["execution_timeframe"] else None,
        "execution_reason_codes": list(execution_context["reason_codes"]),
        "chan_labels": nested_labels,
        "parent_structure": "上级结构通过" if parents_ok else "上级结构暂不支持",
        "volume_price": f"成交量:{primary.technical.volume_state.value if primary.technical else '暂无'}",
        "technical": m5_result.technical.confirmation.value if m5_result.technical else None,
        "opportunity": opportunity.grade.value,
        "risk": f"L{int(risk)}",
        "action": decision.action.value,
        "buy_point": f"{buy_low:.2f}～{buy_high:.2f}元",
        "buy_amount": amount,
        "buy_quantity": quantity,
        "buy_fraction": f"计划仓位的{fraction:.0%}" if fraction > 0 else "0%",
        "add_plan": "首仓必须由5分钟结构买点触发；只有形成新的30分钟/120分钟确认结构后再加仓，不因下跌机械补仓",
        "stop": f"{stop.price_ticks * float(base.TICK_SIZE):.2f}元" if stop else "暂无",
        "stop_basis": stop_basis,
        "reason": f"{symbol.get('industry_name')}行业筛选通过 + 龙头候选 + 财务预筛{(symbol.get('fundamental_prefilter') or {}).get('grade')}级 + {primary_tf.value}{setup_kind.value if setup_kind else ''}",
        "push": push,
        "current_price": current_price,
        "execution_maturity": maturity,
        "blockers": [item.value for item in blockers],
        "sizing_blocker": sizing_blocker,
        "prospect_theme": symbol.get("prospect_theme"),
        "industry_selection_reason": symbol.get("industry_selection_reason"),
        "candidate_route": symbol.get("candidate_route"),
        "fundamental_grade": (symbol.get("fundamental_prefilter") or {}).get("grade"),
    }

    setup_price = setup_signal.structural_price_ticks * float(base.TICK_SIZE)
    candidate["rise_since_signal_pct"] = round((current_price / setup_price - 1) * 100, 2) if setup_price > 0 else None
    if maturity == "TRIGGERED":
        candidate["recent_signal_note"] = "母级买点有效，30分钟确认与5分钟执行结构已就绪"
    elif maturity == "PREPARE":
        candidate["recent_signal_note"] = "母级买点仍有效，但5分钟执行触发尚未满足或母级涨幅已进入准备区"
    else:
        candidate["recent_signal_note"] = "仅保留结构观察，不具备当前首仓执行条件"
    candidate["signal_confirmation_time"] = setup_signal.confirmation_timestamp.isoformat()

    return {"code": code, "name": symbol.get("name"), "timeframes": raw_results, "candidate": candidate}, candidate


# Production base script calls these globals at runtime; patch only the V2 entrypoint.
base.choose_primary = choose_primary_v2
base.execution_maturity = execution_maturity_v2
base.analyze_symbol = analyze_symbol_v2


if __name__ == "__main__":
    raise SystemExit(base.main())
