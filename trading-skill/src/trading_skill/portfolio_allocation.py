from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation, ROUND_FLOOR
from enum import StrEnum
from typing import Any, Mapping, Sequence


class PortfolioAllocationState(StrEnum):
    NO_ELIGIBLE = "NO_ELIGIBLE"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    ALLOCATED = "ALLOCATED"


class CandidateAllocationState(StrEnum):
    NOT_SIZED = "NOT_SIZED"
    NOT_SELECTED = "NOT_SELECTED"
    ALLOCATED_FULL = "ALLOCATED_FULL"
    ALLOCATED_PARTIAL = "ALLOCATED_PARTIAL"
    SKIPPED_CAPACITY = "SKIPPED_CAPACITY"


class PortfolioAllocationBlocker(StrEnum):
    ALLOCATION_CONTEXT_UNAVAILABLE = "ALLOCATION_CONTEXT_UNAVAILABLE"
    ALLOCATION_CONTEXT_INCOMPLETE = "ALLOCATION_CONTEXT_INCOMPLETE"
    ALLOCATION_CONTEXT_INVALID = "ALLOCATION_CONTEXT_INVALID"
    ENVELOPE_CONTRACT_INVALID = "ENVELOPE_CONTRACT_INVALID"


@dataclass(frozen=True, slots=True)
class SharedCapacitySnapshot:
    cash_cny: Decimal
    portfolio_risk_cny: Decimal
    industry_risk_cny: tuple[tuple[str, Decimal], ...]
    theme_risk_cny: tuple[tuple[str, Decimal], ...]
    industry_value_cny: tuple[tuple[str, Decimal], ...]
    theme_value_cny: tuple[tuple[str, Decimal], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "cash_cny": _decimal_text(self.cash_cny),
            "portfolio_risk_cny": _decimal_text(self.portfolio_risk_cny),
            "industry_risk_cny": _dimension_dict(self.industry_risk_cny),
            "theme_risk_cny": _dimension_dict(self.theme_risk_cny),
            "industry_value_cny": _dimension_dict(self.industry_value_cny),
            "theme_value_cny": _dimension_dict(self.theme_value_cny),
        }


@dataclass(frozen=True, slots=True)
class CandidateAllocation:
    identity: str
    code: str
    state: CandidateAllocationState
    envelope_quantity: int
    allocated_quantity: int
    lot_size: int | None
    entry_price: Decimal | None
    risk_per_unit: Decimal | None
    industry_key: str | None
    theme_keys: tuple[str, ...]
    allocated_value_cny: Decimal
    allocated_risk_cny: Decimal
    binding_limits: tuple[str, ...]
    reason: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        payload["theme_keys"] = list(self.theme_keys)
        payload["binding_limits"] = list(self.binding_limits)
        for key in ("entry_price", "risk_per_unit", "allocated_value_cny", "allocated_risk_cny"):
            value = payload[key]
            payload[key] = _decimal_text(value) if value is not None else None
        return payload


@dataclass(frozen=True, slots=True)
class PortfolioAllocationPlan:
    state: PortfolioAllocationState
    snapshot_id: str | None
    allocations: tuple[CandidateAllocation, ...]
    start_capacity: SharedCapacitySnapshot | None
    end_capacity: SharedCapacitySnapshot | None
    blockers: tuple[PortfolioAllocationBlocker, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "snapshot_id": self.snapshot_id,
            "allocations": [item.to_dict() for item in self.allocations],
            "start_capacity": self.start_capacity.to_dict() if self.start_capacity is not None else None,
            "end_capacity": self.end_capacity.to_dict() if self.end_capacity is not None else None,
            "blockers": [item.value for item in self.blockers],
            "reasons": list(self.reasons),
        }


def security_identity(row: Mapping[str, Any]) -> str:
    market = str(row.get("market") if row.get("market") is not None else "")
    code = str(row.get("code") or "")
    security_type = str(row.get("security_type") or "")
    if not market or not code or not security_type:
        raise ValueError("证券身份必须包含market/code/security_type")
    return f"{market}:{code}:{security_type}"


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _dimension_items(values: Mapping[str, Decimal]) -> tuple[tuple[str, Decimal], ...]:
    return tuple(sorted(values.items()))


def _dimension_dict(values: tuple[tuple[str, Decimal], ...]) -> dict[str, str]:
    return {name: _decimal_text(value) for name, value in values}


def _snapshot(
    *,
    cash: Decimal,
    portfolio_risk: Decimal,
    industry_risk: Mapping[str, Decimal],
    theme_risk: Mapping[str, Decimal],
    industry_value: Mapping[str, Decimal],
    theme_value: Mapping[str, Decimal],
) -> SharedCapacitySnapshot:
    return SharedCapacitySnapshot(
        cash_cny=cash,
        portfolio_risk_cny=portfolio_risk,
        industry_risk_cny=_dimension_items(industry_risk),
        theme_risk_cny=_dimension_items(theme_risk),
        industry_value_cny=_dimension_items(industry_value),
        theme_value_cny=_dimension_items(theme_value),
    )


def _nonnegative_decimal(value: Any, *, label: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label}必须是非负数值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{label}不是有效数值") from exc
    if not result.is_finite() or result < 0:
        raise ValueError(f"{label}必须是有限非负数值")
    return result


def _positive_decimal(value: Any, *, label: str) -> Decimal:
    result = _nonnegative_decimal(value, label=label)
    if result <= 0:
        raise ValueError(f"{label}必须大于0")
    return result


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label}必须是正JSON整数")
    return value


def _context_required(blocker: PortfolioAllocationBlocker, reason: str) -> PortfolioAllocationPlan:
    return PortfolioAllocationPlan(
        state=PortfolioAllocationState.CONTEXT_REQUIRED,
        snapshot_id=None,
        allocations=(),
        start_capacity=None,
        end_capacity=None,
        blockers=(blocker,),
        reasons=(reason,),
    )


def _sized_rows(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    return [row for row in rows if str((row.get("sizing") or {}).get("state") or "") == "SIZED"]


def _validate_envelope(row: Mapping[str, Any]) -> tuple[str, int, int, Decimal, Decimal]:
    identity = security_identity(row)
    sizing = row.get("sizing")
    if not isinstance(sizing, Mapping):
        raise ValueError(f"{identity}:缺少STEP5B sizing")
    quantity = _positive_int(sizing.get("quantity"), label=f"{identity}.quantity")
    lot_size = _positive_int(sizing.get("lot_size"), label=f"{identity}.lot_size")
    if quantity % lot_size != 0:
        raise ValueError(f"{identity}:STEP5B数量不是lot_size整数倍")
    entry = _positive_decimal(sizing.get("planned_entry_price"), label=f"{identity}.planned_entry_price")
    risk_per_unit = _positive_decimal(sizing.get("risk_per_unit"), label=f"{identity}.risk_per_unit")
    expected_value = Decimal(quantity) * entry
    expected_risk = Decimal(quantity) * risk_per_unit
    try:
        planned_value = Decimal(str(sizing.get("planned_value_cny")))
        planned_risk = Decimal(str(sizing.get("planned_risk_cny")))
    except InvalidOperation as exc:
        raise ValueError(f"{identity}:STEP5B planned_value/planned_risk不是有效数值") from exc
    if not planned_value.is_finite() or planned_value != expected_value:
        raise ValueError(f"{identity}:STEP5B planned_value与quantity*entry不一致")
    if not planned_risk.is_finite() or planned_risk != expected_risk:
        raise ValueError(f"{identity}:STEP5B planned_risk与quantity*risk_per_unit不一致")
    return identity, quantity, lot_size, entry, risk_per_unit


def _read_dimension_map(context: Mapping[str, Any], key: str) -> dict[str, Decimal]:
    raw = context.get(key)
    if not isinstance(raw, Mapping):
        raise ValueError(f"{key}必须是JSON对象")
    result: dict[str, Decimal] = {}
    for name, value in raw.items():
        text = str(name).strip()
        if not text:
            raise ValueError(f"{key}不能包含空维度键")
        result[text] = _nonnegative_decimal(value, label=f"{key}.{text}")
    return result


def _symbol_dimensions(context: Mapping[str, Any], identity: str) -> tuple[str | None, tuple[str, ...]]:
    raw_symbols = context.get("symbols")
    if not isinstance(raw_symbols, Mapping):
        raise ValueError("symbols必须是JSON对象")
    raw = raw_symbols.get(identity)
    if not isinstance(raw, Mapping):
        raise ValueError(f"symbols缺少完整身份:{identity}")
    if "industry_key" not in raw or "theme_keys" not in raw:
        raise ValueError(f"symbols[{identity}]必须显式包含industry_key和theme_keys")
    industry_raw = raw.get("industry_key")
    industry = None if industry_raw is None else str(industry_raw).strip()
    if industry_raw is not None and not industry:
        raise ValueError(f"symbols[{identity}].industry_key不能是空字符串")
    themes_raw = raw.get("theme_keys")
    if not isinstance(themes_raw, list):
        raise ValueError(f"symbols[{identity}].theme_keys必须是JSON数组")
    themes = tuple(str(item).strip() for item in themes_raw)
    if any(not item for item in themes) or len(set(themes)) != len(themes):
        raise ValueError(f"symbols[{identity}].theme_keys不能包含空值或重复值")
    return industry, themes


def allocate_new_entries(
    rows: Sequence[Mapping[str, Any]],
    context: Mapping[str, Any] | None,
) -> PortfolioAllocationPlan:
    """STEP5C: consume shared capacities once within one deterministic allocation plan.

    STEP5B envelopes are per-symbol upper bounds. This layer never increases them. Candidate
    selection/ranking is deliberately external: `allocation_order` is explicit and exact-identity
    based. Shared cash/risk/industry/theme capacity is deducted immediately after each allocation,
    so later candidates cannot reuse the same capacity.

    This is an in-memory reservation plan, not a durable broker/account reservation. Cross-process
    atomicity requires a later persistent ledger/CAS boundary before order placement.
    """
    sized = _sized_rows(rows)
    if not sized:
        try:
            allocations = tuple(
                CandidateAllocation(
                    identity=security_identity(row),
                    code=str(row.get("code") or ""),
                    state=CandidateAllocationState.NOT_SIZED,
                    envelope_quantity=0,
                    allocated_quantity=0,
                    lot_size=None,
                    entry_price=None,
                    risk_per_unit=None,
                    industry_key=None,
                    theme_keys=(),
                    allocated_value_cny=Decimal("0"),
                    allocated_risk_cny=Decimal("0"),
                    binding_limits=(),
                    reason="STEP5B未产生SIZED envelope；STEP5C不要求无关的组合分配上下文",
                )
                for row in rows
            )
        except ValueError as exc:
            return _context_required(PortfolioAllocationBlocker.ENVELOPE_CONTRACT_INVALID, str(exc))
        return PortfolioAllocationPlan(
            state=PortfolioAllocationState.NO_ELIGIBLE,
            snapshot_id=None,
            allocations=allocations,
            start_capacity=None,
            end_capacity=None,
            blockers=(),
            reasons=("没有可进入共享容量分配的STEP5B仓位包络",),
        )

    envelope_by_identity: dict[str, tuple[Mapping[str, Any], int, int, Decimal, Decimal]] = {}
    try:
        for row in sized:
            identity, quantity, lot_size, entry, risk_per_unit = _validate_envelope(row)
            if identity in envelope_by_identity:
                raise ValueError(f"重复STEP5B证券身份:{identity}")
            envelope_by_identity[identity] = (row, quantity, lot_size, entry, risk_per_unit)
    except ValueError as exc:
        return _context_required(PortfolioAllocationBlocker.ENVELOPE_CONTRACT_INVALID, str(exc))

    if not isinstance(context, Mapping):
        return _context_required(
            PortfolioAllocationBlocker.ALLOCATION_CONTEXT_UNAVAILABLE,
            "存在SIZED envelope，但没有提供共享组合容量快照和显式allocation_order",
        )

    required = (
        "snapshot_id",
        "allocation_order",
        "cash_remaining_cny",
        "portfolio_risk_remaining_cny",
        "industry_risk_remaining_cny",
        "theme_risk_remaining_cny",
        "industry_value_remaining_cny",
        "theme_value_remaining_cny",
        "symbols",
    )
    missing = [key for key in required if key not in context]
    if missing:
        return _context_required(
            PortfolioAllocationBlocker.ALLOCATION_CONTEXT_INCOMPLETE,
            "STEP5C上下文字段缺失:" + ",".join(missing),
        )

    try:
        snapshot_id = str(context.get("snapshot_id") or "").strip()
        if not snapshot_id:
            raise ValueError("snapshot_id不能为空")
        order_raw = context.get("allocation_order")
        if not isinstance(order_raw, list):
            raise ValueError("allocation_order必须是JSON数组")
        order = tuple(str(item).strip() for item in order_raw)
        if any(not item for item in order) or len(set(order)) != len(order):
            raise ValueError("allocation_order不能包含空值或重复身份")

        cash_start = _nonnegative_decimal(context.get("cash_remaining_cny"), label="cash_remaining_cny")
        portfolio_risk_start = _nonnegative_decimal(
            context.get("portfolio_risk_remaining_cny"), label="portfolio_risk_remaining_cny"
        )
        industry_risk = _read_dimension_map(context, "industry_risk_remaining_cny")
        theme_risk = _read_dimension_map(context, "theme_risk_remaining_cny")
        industry_value = _read_dimension_map(context, "industry_value_remaining_cny")
        theme_value = _read_dimension_map(context, "theme_value_remaining_cny")

        unknown_order = [identity for identity in order if identity not in envelope_by_identity]
        if unknown_order:
            raise ValueError("allocation_order包含非SIZED或未知身份:" + ",".join(unknown_order))

        dimensions: dict[str, tuple[str | None, tuple[str, ...]]] = {}
        for identity in order:
            industry, themes = _symbol_dimensions(context, identity)
            if industry is not None and (industry not in industry_risk or industry not in industry_value):
                raise ValueError(f"{identity}:行业{industry}缺少共享risk/value容量")
            for theme in themes:
                if theme not in theme_risk or theme not in theme_value:
                    raise ValueError(f"{identity}:主题{theme}缺少共享risk/value容量")
            dimensions[identity] = (industry, themes)
    except (ValueError, InvalidOperation) as exc:
        return _context_required(PortfolioAllocationBlocker.ALLOCATION_CONTEXT_INVALID, str(exc))

    start_capacity = _snapshot(
        cash=cash_start,
        portfolio_risk=portfolio_risk_start,
        industry_risk=industry_risk,
        theme_risk=theme_risk,
        industry_value=industry_value,
        theme_value=theme_value,
    )
    cash = cash_start
    portfolio_risk = portfolio_risk_start
    allocations_by_identity: dict[str, CandidateAllocation] = {}

    for identity in order:
        row, envelope_quantity, lot_size, entry, risk_per_unit = envelope_by_identity[identity]
        industry, themes = dimensions[identity]
        caps: list[tuple[str, int]] = [
            ("STEP5B_ENVELOPE", envelope_quantity),
            ("CASH", int((cash / entry).to_integral_value(rounding=ROUND_FLOOR))),
            ("PORTFOLIO_RISK", int((portfolio_risk / risk_per_unit).to_integral_value(rounding=ROUND_FLOOR))),
        ]
        if industry is not None:
            caps.extend(
                [
                    (
                        f"INDUSTRY_RISK:{industry}",
                        int((industry_risk[industry] / risk_per_unit).to_integral_value(rounding=ROUND_FLOOR)),
                    ),
                    (
                        f"INDUSTRY_VALUE:{industry}",
                        int((industry_value[industry] / entry).to_integral_value(rounding=ROUND_FLOOR)),
                    ),
                ]
            )
        for theme in themes:
            caps.extend(
                [
                    (
                        f"THEME_RISK:{theme}",
                        int((theme_risk[theme] / risk_per_unit).to_integral_value(rounding=ROUND_FLOOR)),
                    ),
                    (
                        f"THEME_VALUE:{theme}",
                        int((theme_value[theme] / entry).to_integral_value(rounding=ROUND_FLOOR)),
                    ),
                ]
            )

        raw_quantity = min(value for _, value in caps)
        binding = tuple(name for name, value in caps if value == raw_quantity)
        allocated_quantity = (raw_quantity // lot_size) * lot_size if raw_quantity > 0 else 0
        if allocated_quantity <= 0:
            allocations_by_identity[identity] = CandidateAllocation(
                identity=identity,
                code=str(row.get("code") or ""),
                state=CandidateAllocationState.SKIPPED_CAPACITY,
                envelope_quantity=envelope_quantity,
                allocated_quantity=0,
                lot_size=lot_size,
                entry_price=entry,
                risk_per_unit=risk_per_unit,
                industry_key=industry,
                theme_keys=themes,
                allocated_value_cny=Decimal("0"),
                allocated_risk_cny=Decimal("0"),
                binding_limits=tuple(dict.fromkeys(binding + (("LOT_ROUNDING",) if raw_quantity > 0 else ()))),
                reason="共享容量不足以支持至少1个显式交易单位；不向上取整，也不挪用其他候选额度",
            )
            continue

        value = Decimal(allocated_quantity) * entry
        risk = Decimal(allocated_quantity) * risk_per_unit
        cash -= value
        portfolio_risk -= risk
        if industry is not None:
            industry_risk[industry] -= risk
            industry_value[industry] -= value
        for theme in themes:
            theme_risk[theme] -= risk
            theme_value[theme] -= value

        state = (
            CandidateAllocationState.ALLOCATED_FULL
            if allocated_quantity == envelope_quantity
            else CandidateAllocationState.ALLOCATED_PARTIAL
        )
        final_binding = binding
        if allocated_quantity < raw_quantity:
            final_binding = tuple(dict.fromkeys(binding + ("LOT_ROUNDING",)))
        allocations_by_identity[identity] = CandidateAllocation(
            identity=identity,
            code=str(row.get("code") or ""),
            state=state,
            envelope_quantity=envelope_quantity,
            allocated_quantity=allocated_quantity,
            lot_size=lot_size,
            entry_price=entry,
            risk_per_unit=risk_per_unit,
            industry_key=industry,
            theme_keys=themes,
            allocated_value_cny=value,
            allocated_risk_cny=risk,
            binding_limits=final_binding,
            reason="按显式allocation_order顺序使用当前剩余共享容量；本次分配后立即扣减供后续候选使用",
        )

    all_allocations: list[CandidateAllocation] = []
    for row in rows:
        try:
            identity = security_identity(row)
        except ValueError as exc:
            return _context_required(PortfolioAllocationBlocker.ENVELOPE_CONTRACT_INVALID, str(exc))
        if identity in allocations_by_identity:
            all_allocations.append(allocations_by_identity[identity])
            continue
        sizing_state = str((row.get("sizing") or {}).get("state") or "")
        if sizing_state == "SIZED":
            _, quantity, lot_size, entry, risk_per_unit = envelope_by_identity[identity]
            all_allocations.append(
                CandidateAllocation(
                    identity=identity,
                    code=str(row.get("code") or ""),
                    state=CandidateAllocationState.NOT_SELECTED,
                    envelope_quantity=quantity,
                    allocated_quantity=0,
                    lot_size=lot_size,
                    entry_price=entry,
                    risk_per_unit=risk_per_unit,
                    industry_key=None,
                    theme_keys=(),
                    allocated_value_cny=Decimal("0"),
                    allocated_risk_cny=Decimal("0"),
                    binding_limits=(),
                    reason="该SIZED候选未出现在显式allocation_order中；STEP5C不自行补排名或自动选择",
                )
            )
        else:
            all_allocations.append(
                CandidateAllocation(
                    identity=identity,
                    code=str(row.get("code") or ""),
                    state=CandidateAllocationState.NOT_SIZED,
                    envelope_quantity=0,
                    allocated_quantity=0,
                    lot_size=None,
                    entry_price=None,
                    risk_per_unit=None,
                    industry_key=None,
                    theme_keys=(),
                    allocated_value_cny=Decimal("0"),
                    allocated_risk_cny=Decimal("0"),
                    binding_limits=(),
                    reason="STEP5B未产生SIZED envelope",
                )
            )

    if (
        cash < 0
        or portfolio_risk < 0
        or any(value < 0 for value in industry_risk.values())
        or any(value < 0 for value in theme_risk.values())
        or any(value < 0 for value in industry_value.values())
        or any(value < 0 for value in theme_value.values())
    ):
        return _context_required(
            PortfolioAllocationBlocker.ENVELOPE_CONTRACT_INVALID,
            "STEP5C分配后出现负容量；拒绝生成可能超配的组合计划",
        )

    end_capacity = _snapshot(
        cash=cash,
        portfolio_risk=portfolio_risk,
        industry_risk=industry_risk,
        theme_risk=theme_risk,
        industry_value=industry_value,
        theme_value=theme_value,
    )
    return PortfolioAllocationPlan(
        state=PortfolioAllocationState.ALLOCATED,
        snapshot_id=snapshot_id,
        allocations=tuple(all_allocations),
        start_capacity=start_capacity,
        end_capacity=end_capacity,
        blockers=(),
        reasons=(
            "同一计划内共享现金/风险容量只消费一次；后续候选使用实时剩余额度",
            "STEP5B envelope只是单候选上限；STEP5C只能缩小或跳过，绝不放大",
            "这是基于snapshot_id的内存分配计划，不等价于持久化账户锁定；下单前仍需持久化/CAS校验快照未变化",
        ),
    )
