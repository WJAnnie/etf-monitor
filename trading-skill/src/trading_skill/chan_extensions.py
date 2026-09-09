from __future__ import annotations

from dataclasses import replace

from trading_skill.domain.enums import ChanSignalType
from trading_skill.production_chan import ProductionChanResult


def annotate_second_buy_variants(result: ProductionChanResult) -> ProductionChanResult:
    """给标准二买增加“类二买”结构标签，但不凭标签创造新的标准买点。

    规则：
    1. 强势类二买：同级别二买与三买落在同一结构结束点，视为二/三买合一的强势变体。
    2. 中枢类二买：标准二买发生在已确认中枢之后，且回抽低点仍站在最近中枢下沿ZD之上；
       它只是二买的中枢环境标签，不等于三买，也不绕过上级结构/风险/5分钟执行门槛。
    """
    if not result.signals:
        return result
    third_buys = [
        s for s in result.signals
        if ChanSignalType.THIRD_BUY in s.standard_types and s.side == "BUY"
    ]
    centers = list(result.centers)
    out = []
    for signal in result.signals:
        if ChanSignalType.SECOND_BUY not in signal.standard_types:
            out.append(signal)
            continue
        extended = list(signal.extended_types)
        same_structure_third = any(
            third.level_rank == signal.level_rank
            and third.structural_timestamp == signal.structural_timestamp
            for third in third_buys
        )
        if same_structure_third:
            if ChanSignalType.STRONG_CLASS2_BUY not in extended:
                extended.append(ChanSignalType.STRONG_CLASS2_BUY)
        else:
            prior_centers = [c for c in centers if c.confirmation_timestamp <= signal.confirmation_timestamp]
            if prior_centers:
                center = prior_centers[-1]
                if signal.structural_price_ticks >= center.zd_ticks:
                    if ChanSignalType.CENTER_CLASS2_BUY not in extended:
                        extended.append(ChanSignalType.CENTER_CLASS2_BUY)
        out.append(replace(signal, extended_types=tuple(extended)))
    return replace(result, signals=tuple(out))
