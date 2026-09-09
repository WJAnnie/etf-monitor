from __future__ import annotations

from dataclasses import replace

from trading_skill.domain.enums import ChanSignalType
from trading_skill.production_chan import ProductionChanResult


def annotate_second_buy_variants(result: ProductionChanResult) -> ProductionChanResult:
    """给标准二买增加“类二买”扩展标签，但绝不凭扩展标签创造新的标准买点。

    统一定义：
    1. 强势类二买：同级别标准二买与三买落在同一结构结束点，属于二/三买合一的强势结构。
    2. 中枢类二买：标准二买发生在已经确认的中枢之后，且二买回抽低点仍站在最近中枢上沿ZG及以上。
       这表示回抽没有重新进入原中枢核心区，是“二买 + 中枢上方承接”的扩展标签。

    注意：
    - 类二买是工程扩展标签，不改变经典一/二/三买的 canonical 定义。
    - 中枢类二买不等同于三买；三买仍要求“向上离开中枢后的第一次完成回试，回试低点>=ZG”。
    - 所有类二买仍必须通过上级结构、风险、财务和5分钟执行门槛。
    """
    if not result.signals:
        return result

    third_buys = [
        signal
        for signal in result.signals
        if ChanSignalType.THIRD_BUY in signal.standard_types and signal.side == "BUY"
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
            prior_centers = [
                center
                for center in centers
                if center.confirmation_timestamp <= signal.confirmation_timestamp
            ]
            if prior_centers:
                center = prior_centers[-1]
                # 必须站在中枢上沿ZG及以上；仅站在ZD之上仍可能处于中枢内部，不应标注类二买。
                if signal.structural_price_ticks >= center.zg_ticks:
                    if ChanSignalType.CENTER_CLASS2_BUY not in extended:
                        extended.append(ChanSignalType.CENTER_CLASS2_BUY)

        out.append(replace(signal, extended_types=tuple(extended)))

    return replace(result, signals=tuple(out))
