from __future__ import annotations

from datetime import timedelta

import scripts.run_full_a_scan as base
from trading_skill.domain.enums import ChanSignalType, Timeframe


# 买点不要求“刚刚这一根K线才出现”。只要结构尚未失效且上涨幅度不大，仍保留为可执行/准备候选。
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
    """近期买点只有在同级别之后没有更新的正式卖点时才继续有效。"""
    for sell in base.fresh_signals(result, as_of=as_of, side="SELL"):
        if sell.confirmation_timestamp > buy_signal.confirmation_timestamp:
            return True
    return False


def choose_primary_v2(results, *, as_of):
    choices = []
    for timeframe in (Timeframe.DAILY, Timeframe.M120, Timeframe.M30):
        result = results.get(timeframe)
        if result is None:
            continue
        for signal in base.fresh_signals(result, as_of=as_of, side="BUY"):
            kind = base.signal_type(signal)
            if kind not in base.BUY_TYPES:
                continue
            if _invalidated_by_later_sell(result, signal, as_of=as_of):
                continue
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


_original_analyze_symbol = base.analyze_symbol


def analyze_symbol_v2(symbol, industry_map, *, as_of, equity):
    analysis, candidate = _original_analyze_symbol(symbol, industry_map, as_of=as_of, equity=equity)
    if candidate:
        candidate["prospect_theme"] = symbol.get("prospect_theme")
        candidate["industry_selection_reason"] = symbol.get("industry_selection_reason")
        candidate["candidate_route"] = symbol.get("candidate_route")
        candidate["fundamental_grade"] = (symbol.get("fundamental_prefilter") or {}).get("grade")
        signal_price_text = str(candidate.get("buy_point") or "").split("～", 1)[0].replace("元", "")
        try:
            signal_price = float(signal_price_text)
        except ValueError:
            signal_price = 0.0
        current_price = float(candidate.get("current_price") or 0)
        candidate["rise_since_signal_pct"] = round((current_price / signal_price - 1) * 100, 2) if signal_price > 0 else None
        candidate["recent_signal_note"] = (
            "近期买点仍在可执行涨幅范围"
            if candidate.get("execution_maturity") in {"TRIGGERED", "PREPARE"}
            else "买点已出现，但当前距离买点偏远"
        )
        timeframe_key = str(candidate.get("timeframe") or "")
        signal_type = str(candidate.get("signal") or "")
        tf_raw = (analysis.get("timeframes") or {}).get(timeframe_key) or {}
        for signal in reversed(tf_raw.get("signals") or []):
            if signal_type in (signal.get("types") or []):
                candidate["signal_confirmation_time"] = signal.get("confirmation_timestamp")
                break
    return analysis, candidate


base.choose_primary = choose_primary_v2
base.execution_maturity = execution_maturity_v2
base.analyze_symbol = analyze_symbol_v2


if __name__ == "__main__":
    raise SystemExit(base.main())
