from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from enum import StrEnum
from typing import Any, Mapping


class EntrySizingState(StrEnum):
    NOT_ELIGIBLE = "NOT_ELIGIBLE"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    BLOCKED = "BLOCKED"
    SIZED = "SIZED"


class EntrySizingBlocker(StrEnum):
    SIZING_CONTEXT_UNAVAILABLE = "SIZING_CONTEXT_UNAVAILABLE"
    SIZING_CONTEXT_INCOMPLETE = "SIZING_CONTEXT_INCOMPLETE"
    SIZING_CONTEXT_INVALID = "SIZING_CONTEXT_INVALID"
    EXISTING_POSITION_REQUIRES_SCALE_IN = "EXISTING_POSITION_REQUIRES_SCALE_IN"
    STRUCTURAL_STOP_UNAVAILABLE = "STRUCTURAL_STOP_UNAVAILABLE"
    INVALID_ENTRY_PRICE = "INVALID_ENTRY_PRICE"
    INVALID_ENTRY_TICK = "INVALID_ENTRY_TICK"
    INVALID_STOP_DISTANCE = "INVALID_STOP_DISTANCE"
    NO_RISK_CAPACITY = "NO_RISK_CAPACITY"
    NO_VALUE_CAPACITY = "NO_VALUE_CAPACITY"
    NO_EXECUTION_MINIMUM_LOT = "NO_EXECUTION_MINIMUM_LOT"


_REQUIRED_CONTEXT_KEYS = (
    "planned_entry_price",
    "lot_size",
    "existing_position_quantity",
    "standard_trade_risk_limit_cny",
    "test_trade_risk_limit_cny",
    "portfolio_risk_remaining_cny",
    # 行业/主题约束必须显式出现。null表示该维度明确不适用；缺字段表示上下文不完整。
    "industry_risk_remaining_cny",
    "theme_risk_remaining_cny",
    "cash_available_cny",
    "security_value_remaining_cny",
    "industry_value_remaining_cny",
    "theme_value_remaining_cny",
)

_OPTIONAL_CAP_KEYS = {
    "industry_risk_remaining_cny",
    "theme_risk_remaining_cny",
    "industry_value_remaining_cny",
    "theme_value_remaining_cny",
}


@dataclass(frozen=True, slots=True)
class EntrySizingDecision:
    state: EntrySizingState
    permission_state: str
    entry_mode: str
    quantity: int
    lot_size: int | None
    planned_entry_price: Decimal | None
    structural_stop_price: Decimal | None
    risk_per_unit: Decimal | None
    effective_risk_budget_cny: Decimal | None
    quantity_before_lot_rounding: int | None
    planned_value_cny: Decimal | None
    planned_risk_cny: Decimal | None
    risk_binding_limits: tuple[str, ...]
    quantity_binding_limits: tuple[str, ...]
    blockers: tuple[EntrySizingBlocker, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["blockers"] = [item.value for item in self.blockers]
        payload["risk_binding_limits"] = list(self.risk_binding_limits)
        payload["quantity_binding_limits"] = list(self.quantity_binding_limits)
        payload["reasons"] = list(self.reasons)
        for key in (
            "planned_entry_price",
            "structural_stop_price",
            "risk_per_unit",
            "effective_risk_budget_cny",
            "planned_value_cny",
            "planned_risk_cny",
        ):
            value = payload[key]
            payload[key] = _decimal_text(value) if value is not None else None
        return payload


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal(value: Any, *, allow_none: bool, label: str) -> Decimal | None:
    if value is None:
        if allow_none:
            return None
        raise ValueError(f"{label}不能为null")
    if isinstance(value, bool):
        raise ValueError(f"{label}必须是数值，不能使用boolean")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}不是有效数值") from exc
    if not result.is_finite():
        raise ValueError(f"{label}必须是有限数值")
    if result < 0:
        raise ValueError(f"{label}不能为负数")
    return result


def _positive_decimal(value: Any, *, label: str) -> Decimal:
    result = _decimal(value, allow_none=False, label=label)
    assert result is not None
    if result <= 0:
        raise ValueError(f"{label}必须大于0")
    return result


def _whole_number(value: Any, *, positive: bool, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{label}必须是JSON整数")
    if positive and value <= 0:
        raise ValueError(f"{label}必须大于0")
    if not positive and value < 0:
        raise ValueError(f"{label}不能为负数")
    return value


def _permission_facts(row: Mapping[str, Any]) -> tuple[str, str, bool]:
    permission = row.get("permission")
    if not isinstance(permission, Mapping):
        return "UNKNOWN", "NONE", False
    return (
        str(permission.get("state") or "UNKNOWN"),
        str(permission.get("entry_mode") or "NONE"),
        permission.get("new_entry_allowed") is True,
    )


def _not_eligible(permission_state: str, entry_mode: str) -> EntrySizingDecision:
    return EntrySizingDecision(
        state=EntrySizingState.NOT_ELIGIBLE,
        permission_state=permission_state,
        entry_mode=entry_mode,
        quantity=0,
        lot_size=None,
        planned_entry_price=None,
        structural_stop_price=None,
        risk_per_unit=None,
        effective_risk_budget_cny=None,
        quantity_before_lot_rounding=None,
        planned_value_cny=None,
        planned_risk_cny=None,
        risk_binding_limits=(),
        quantity_binding_limits=(),
        blockers=(),
        reasons=("STEP5A尚未允许新开仓；STEP5B不绕过许可层，也不要求无关的仓位上下文",),
    )


def _context_problem(
    permission_state: str,
    entry_mode: str,
    blocker: EntrySizingBlocker,
    reason: str,
) -> EntrySizingDecision:
    return EntrySizingDecision(
        state=EntrySizingState.CONTEXT_REQUIRED,
        permission_state=permission_state,
        entry_mode=entry_mode,
        quantity=0,
        lot_size=None,
        planned_entry_price=None,
        structural_stop_price=None,
        risk_per_unit=None,
        effective_risk_budget_cny=None,
        quantity_before_lot_rounding=None,
        planned_value_cny=None,
        planned_risk_cny=None,
        risk_binding_limits=(),
        quantity_binding_limits=(),
        blockers=(blocker,),
        reasons=(reason,),
    )


def _blocked(
    *,
    permission_state: str,
    entry_mode: str,
    blocker: EntrySizingBlocker,
    reason: str,
    lot_size: int | None = None,
    planned_entry_price: Decimal | None = None,
    structural_stop_price: Decimal | None = None,
    risk_per_unit: Decimal | None = None,
    effective_risk_budget_cny: Decimal | None = None,
    quantity_before_lot_rounding: int | None = None,
    risk_binding_limits: tuple[str, ...] = (),
    quantity_binding_limits: tuple[str, ...] = (),
) -> EntrySizingDecision:
    return EntrySizingDecision(
        state=EntrySizingState.BLOCKED,
        permission_state=permission_state,
        entry_mode=entry_mode,
        quantity=0,
        lot_size=lot_size,
        planned_entry_price=planned_entry_price,
        structural_stop_price=structural_stop_price,
        risk_per_unit=risk_per_unit,
        effective_risk_budget_cny=effective_risk_budget_cny,
        quantity_before_lot_rounding=quantity_before_lot_rounding,
        planned_value_cny=None,
        planned_risk_cny=None,
        risk_binding_limits=risk_binding_limits,
        quantity_binding_limits=quantity_binding_limits,
        blockers=(blocker,),
        reasons=(reason,),
    )


def size_new_entry(
    permission_row: Mapping[str, Any],
    sizing_context: Mapping[str, Any] | None,
) -> EntrySizingDecision:
    """STEP5B: calculate one candidate's maximum new-entry sizing envelope.

    This function deliberately does not choose among candidates and does not reserve shared
    portfolio/cash capacity. If several symbols are simultaneously eligible, a later portfolio
    allocation layer must select/reserve capacity atomically before orders are created.

    There is no opportunity grade, risk-state multiplier, fixed percentage stop, cost-basis stop,
    or implicit lot-size default here. All monetary caps are explicit inputs and all stop risk comes
    from STEP5A's matching structural stop evidence.
    """
    permission_state, entry_mode, allowed = _permission_facts(permission_row)
    if not allowed:
        return _not_eligible(permission_state, entry_mode)

    if entry_mode not in {"STANDARD", "TEST"}:
        return _context_problem(
            permission_state,
            entry_mode,
            EntrySizingBlocker.SIZING_CONTEXT_INVALID,
            "STEP5A允许新开仓但entry_mode不是STANDARD/TEST，许可合同不一致",
        )
    if not isinstance(sizing_context, Mapping):
        return _context_problem(
            permission_state,
            entry_mode,
            EntrySizingBlocker.SIZING_CONTEXT_UNAVAILABLE,
            "STEP5A已允许新开仓，但没有提供STEP5B所需的显式金额、计划价格与交易单位上下文",
        )

    missing = [key for key in _REQUIRED_CONTEXT_KEYS if key not in sizing_context]
    if missing:
        return _context_problem(
            permission_state,
            entry_mode,
            EntrySizingBlocker.SIZING_CONTEXT_INCOMPLETE,
            "STEP5B上下文字段缺失:" + ",".join(missing),
        )

    try:
        entry_price = _positive_decimal(sizing_context.get("planned_entry_price"), label="planned_entry_price")
        lot_size = _whole_number(sizing_context.get("lot_size"), positive=True, label="lot_size")
        existing_quantity = _whole_number(
            sizing_context.get("existing_position_quantity"), positive=False, label="existing_position_quantity"
        )
        standard_trade_risk = _decimal(
            sizing_context.get("standard_trade_risk_limit_cny"), allow_none=False, label="standard_trade_risk_limit_cny"
        )
        test_trade_risk = _decimal(
            sizing_context.get("test_trade_risk_limit_cny"), allow_none=False, label="test_trade_risk_limit_cny"
        )
        portfolio_risk = _decimal(
            sizing_context.get("portfolio_risk_remaining_cny"), allow_none=False, label="portfolio_risk_remaining_cny"
        )
        industry_risk = _decimal(
            sizing_context.get("industry_risk_remaining_cny"), allow_none=True, label="industry_risk_remaining_cny"
        )
        theme_risk = _decimal(
            sizing_context.get("theme_risk_remaining_cny"), allow_none=True, label="theme_risk_remaining_cny"
        )
        cash_available = _decimal(
            sizing_context.get("cash_available_cny"), allow_none=False, label="cash_available_cny"
        )
        security_value = _decimal(
            sizing_context.get("security_value_remaining_cny"), allow_none=False, label="security_value_remaining_cny"
        )
        industry_value = _decimal(
            sizing_context.get("industry_value_remaining_cny"), allow_none=True, label="industry_value_remaining_cny"
        )
        theme_value = _decimal(
            sizing_context.get("theme_value_remaining_cny"), allow_none=True, label="theme_value_remaining_cny"
        )
    except ValueError as exc:
        return _context_problem(
            permission_state,
            entry_mode,
            EntrySizingBlocker.SIZING_CONTEXT_INVALID,
            str(exc),
        )

    assert standard_trade_risk is not None
    assert test_trade_risk is not None
    assert portfolio_risk is not None
    assert cash_available is not None
    assert security_value is not None

    if test_trade_risk > standard_trade_risk:
        return _context_problem(
            permission_state,
            entry_mode,
            EntrySizingBlocker.SIZING_CONTEXT_INVALID,
            "test_trade_risk_limit_cny不得大于standard_trade_risk_limit_cny；试仓不能比标准新开仓承担更多风险",
        )
    if existing_quantity > 0:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.EXISTING_POSITION_REQUIRES_SCALE_IN,
            reason="账户已有该证券持仓；STEP5B只负责新开仓，必须转入后续结构加仓/持仓管理路径",
            lot_size=lot_size,
            planned_entry_price=entry_price,
        )

    stop = permission_row.get("structural_stop")
    if not isinstance(stop, Mapping) or stop.get("valid_for_new_entry") is not True:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.STRUCTURAL_STOP_UNAVAILABLE,
            reason="STEP5A没有可用于新开仓风险预算的真实结构止损",
            lot_size=lot_size,
            planned_entry_price=entry_price,
        )
    try:
        stop_ticks = _whole_number(stop.get("price_ticks"), positive=True, label="structural_stop.price_ticks")
        tick_size = _positive_decimal(stop.get("tick_size"), label="structural_stop.tick_size")
    except ValueError as exc:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.STRUCTURAL_STOP_UNAVAILABLE,
            reason=str(exc),
            lot_size=lot_size,
            planned_entry_price=entry_price,
        )

    entry_ticks = entry_price / tick_size
    if entry_ticks != entry_ticks.to_integral_value():
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.INVALID_ENTRY_TICK,
            reason="planned_entry_price不符合该证券最小价格变动单位；STEP5B不静默四舍五入计划成交价",
            lot_size=lot_size,
            planned_entry_price=entry_price,
        )

    stop_price = Decimal(stop_ticks) * tick_size
    if entry_price <= 0:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.INVALID_ENTRY_PRICE,
            reason="计划成交价必须大于0",
            lot_size=lot_size,
            planned_entry_price=entry_price,
            structural_stop_price=stop_price,
        )
    risk_per_unit = entry_price - stop_price
    if risk_per_unit <= 0:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.INVALID_STOP_DISTANCE,
            reason="结构止损必须严格低于计划新开仓价格；不能用零/负风险距离计算仓位",
            lot_size=lot_size,
            planned_entry_price=entry_price,
            structural_stop_price=stop_price,
            risk_per_unit=risk_per_unit,
        )

    selected_trade_risk = standard_trade_risk if entry_mode == "STANDARD" else test_trade_risk
    risk_caps: list[tuple[str, Decimal]] = [
        ("TRADE_RISK", selected_trade_risk),
        ("PORTFOLIO_RISK", portfolio_risk),
    ]
    if industry_risk is not None:
        risk_caps.append(("INDUSTRY_RISK", industry_risk))
    if theme_risk is not None:
        risk_caps.append(("THEME_RISK", theme_risk))

    risk_budget = min(value for _, value in risk_caps)
    risk_binding = tuple(name for name, value in risk_caps if value == risk_budget)
    if risk_budget <= 0:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.NO_RISK_CAPACITY,
            reason="至少一个适用的显式风险额度已经为0；技术机会不能突破账户/组合/行业/主题风险上限",
            lot_size=lot_size,
            planned_entry_price=entry_price,
            structural_stop_price=stop_price,
            risk_per_unit=risk_per_unit,
            effective_risk_budget_cny=risk_budget,
            risk_binding_limits=risk_binding,
        )

    risk_quantity = int((risk_budget / risk_per_unit).to_integral_value(rounding=ROUND_FLOOR))
    quantity_caps: list[tuple[str, int]] = [("RISK_BUDGET", risk_quantity)]
    value_caps: list[tuple[str, Decimal]] = [
        ("CASH", cash_available),
        ("SECURITY_VALUE", security_value),
    ]
    if industry_value is not None:
        value_caps.append(("INDUSTRY_VALUE", industry_value))
    if theme_value is not None:
        value_caps.append(("THEME_VALUE", theme_value))

    for name, value_cap in value_caps:
        quantity_caps.append(
            (name, int((value_cap / entry_price).to_integral_value(rounding=ROUND_FLOOR)))
        )

    quantity_before_lot = min(value for _, value in quantity_caps)
    quantity_binding = tuple(name for name, value in quantity_caps if value == quantity_before_lot)
    if quantity_before_lot <= 0:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.NO_VALUE_CAPACITY,
            reason="风险预算虽可能存在，但现金或单证券/行业/主题显式价值额度不足以买入1个单位",
            lot_size=lot_size,
            planned_entry_price=entry_price,
            structural_stop_price=stop_price,
            risk_per_unit=risk_per_unit,
            effective_risk_budget_cny=risk_budget,
            quantity_before_lot_rounding=quantity_before_lot,
            risk_binding_limits=risk_binding,
            quantity_binding_limits=quantity_binding,
        )

    rounded_quantity = (quantity_before_lot // lot_size) * lot_size
    if rounded_quantity <= 0:
        return _blocked(
            permission_state=permission_state,
            entry_mode=entry_mode,
            blocker=EntrySizingBlocker.NO_EXECUTION_MINIMUM_LOT,
            reason="按全部风险/价值上限计算后的数量不足1个显式交易单位；STEP5B不把lot_size默认为100，也不向上取整",
            lot_size=lot_size,
            planned_entry_price=entry_price,
            structural_stop_price=stop_price,
            risk_per_unit=risk_per_unit,
            effective_risk_budget_cny=risk_budget,
            quantity_before_lot_rounding=quantity_before_lot,
            risk_binding_limits=risk_binding,
            quantity_binding_limits=quantity_binding + (("LOT_ROUNDING",) if quantity_before_lot > 0 else ()),
        )

    planned_value = Decimal(rounded_quantity) * entry_price
    planned_risk = Decimal(rounded_quantity) * risk_per_unit
    final_binding = quantity_binding
    if rounded_quantity < quantity_before_lot:
        final_binding = tuple(dict.fromkeys(quantity_binding + ("LOT_ROUNDING",)))

    return EntrySizingDecision(
        state=EntrySizingState.SIZED,
        permission_state=permission_state,
        entry_mode=entry_mode,
        quantity=rounded_quantity,
        lot_size=lot_size,
        planned_entry_price=entry_price,
        structural_stop_price=stop_price,
        risk_per_unit=risk_per_unit,
        effective_risk_budget_cny=risk_budget,
        quantity_before_lot_rounding=quantity_before_lot,
        planned_value_cny=planned_value,
        planned_risk_cny=planned_risk,
        risk_binding_limits=risk_binding,
        quantity_binding_limits=final_binding,
        blockers=(),
        reasons=(
            "最大新开仓数量由显式风险预算、结构止损距离、现金/暴露上限共同取最小值后向下按lot_size取整",
            "该结果是单候选sizing envelope，不代表共享组合容量已经为该候选原子预留",
        ),
    )
