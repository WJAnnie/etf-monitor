from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Mapping, Sequence


class ReservationPlanState(StrEnum):
    NO_ALLOCATION = "NO_ALLOCATION"
    CONTEXT_REQUIRED = "CONTEXT_REQUIRED"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    READY_TO_RESERVE = "READY_TO_RESERVE"
    RESERVED = "RESERVED"
    CONTRACT_INVALID = "CONTRACT_INVALID"


class ReservationBlocker(StrEnum):
    RESERVATION_CONTEXT_UNAVAILABLE = "RESERVATION_CONTEXT_UNAVAILABLE"
    RESERVATION_CONTEXT_INVALID = "RESERVATION_CONTEXT_INVALID"
    ALLOCATION_CONTRACT_INVALID = "ALLOCATION_CONTRACT_INVALID"
    SIZING_CONTRACT_INVALID = "SIZING_CONTRACT_INVALID"
    SNAPSHOT_STALE = "SNAPSHOT_STALE"
    IDEMPOTENCY_CONFLICT = "IDEMPOTENCY_CONFLICT"


class IntentState(StrEnum):
    READY_TO_RESERVE = "READY_TO_RESERVE"
    RESERVED = "RESERVED"


@dataclass(frozen=True, slots=True)
class ExecutionIntent:
    identity: str
    market: int
    code: str
    security_type: str
    signal_id: str
    timeframe: str
    signal_type: str
    entry_mode: str
    quantity: int
    lot_size: int
    planned_entry_price: Decimal
    structural_stop_price: Decimal
    risk_per_unit: Decimal
    allocated_value_cny: Decimal
    allocated_risk_cny: Decimal
    source_snapshot_id: str
    reservation_key: str
    state: IntentState
    reservation_id: str | None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["state"] = self.state.value
        for key in (
            "planned_entry_price",
            "structural_stop_price",
            "risk_per_unit",
            "allocated_value_cny",
            "allocated_risk_cny",
        ):
            payload[key] = _decimal_text(payload[key])
        return payload


@dataclass(frozen=True, slots=True)
class ReservationPlan:
    state: ReservationPlanState
    source_snapshot_id: str | None
    current_snapshot_id: str | None
    reservation_key: str | None
    reservation_id: str | None
    intents: tuple[ExecutionIntent, ...]
    allocated_value_cny: Decimal
    allocated_risk_cny: Decimal
    blockers: tuple[ReservationBlocker, ...]
    reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state.value,
            "source_snapshot_id": self.source_snapshot_id,
            "current_snapshot_id": self.current_snapshot_id,
            "reservation_key": self.reservation_key,
            "reservation_id": self.reservation_id,
            "intents": [intent.to_dict() for intent in self.intents],
            "allocated_value_cny": _decimal_text(self.allocated_value_cny),
            "allocated_risk_cny": _decimal_text(self.allocated_risk_cny),
            "blockers": [item.value for item in self.blockers],
            "reasons": list(self.reasons),
        }


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal(value: Any, *, label: str, positive: bool = False) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{label}必须是数值")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label}不是有效Decimal") from exc
    if not result.is_finite() or result < 0 or (positive and result <= 0):
        qualifier = "正数" if positive else "非负数"
        raise ValueError(f"{label}必须是有限{qualifier}")
    return result


def _positive_int(value: Any, *, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{label}必须是正JSON整数")
    return value


def _identity(row: Mapping[str, Any]) -> str:
    market = row.get("market")
    code = str(row.get("code") or "").strip()
    security_type = str(row.get("security_type") or "").strip()
    if isinstance(market, bool) or not isinstance(market, int) or not code or not security_type:
        raise ValueError("STEP5B证券身份必须包含JSON整数market、code、security_type")
    return f"{market}:{code}:{security_type}"


def _context_problem(
    state: ReservationPlanState,
    blocker: ReservationBlocker,
    reason: str,
    *,
    source_snapshot_id: str | None = None,
    current_snapshot_id: str | None = None,
) -> ReservationPlan:
    return ReservationPlan(
        state=state,
        source_snapshot_id=source_snapshot_id,
        current_snapshot_id=current_snapshot_id,
        reservation_key=None,
        reservation_id=None,
        intents=(),
        allocated_value_cny=Decimal("0"),
        allocated_risk_cny=Decimal("0"),
        blockers=(blocker,),
        reasons=(reason,),
    )


def _allocated_rows(allocation_payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], list[Mapping[str, Any]]]:
    plan = allocation_payload.get("plan")
    if not isinstance(plan, Mapping):
        raise ValueError("STEP5C输出缺少plan对象")
    allocations = plan.get("allocations")
    if not isinstance(allocations, list):
        raise ValueError("STEP5C plan.allocations必须是JSON数组")
    allocated = [
        row
        for row in allocations
        if isinstance(row, Mapping)
        and str(row.get("state") or "") in {"ALLOCATED_FULL", "ALLOCATED_PARTIAL"}
    ]
    return plan, allocated


def _sizing_map(sizing_rows: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    result: dict[str, Mapping[str, Any]] = {}
    for row in sizing_rows:
        if str((row.get("sizing") or {}).get("state") or "") != "SIZED":
            continue
        identity = _identity(row)
        if identity in result:
            raise ValueError(f"STEP5B存在重复SIZED身份:{identity}")
        result[identity] = row
    return result


def _validate_source_pair(
    allocation: Mapping[str, Any],
    sizing_row: Mapping[str, Any],
) -> dict[str, Any]:
    identity = _identity(sizing_row)
    if str(allocation.get("identity") or "") != identity:
        raise ValueError(f"{identity}:STEP5C identity与STEP5B不一致")

    sizing = sizing_row.get("sizing")
    permission = sizing_row.get("permission")
    stop = sizing_row.get("structural_stop")
    if not isinstance(sizing, Mapping) or not isinstance(permission, Mapping) or not isinstance(stop, Mapping):
        raise ValueError(f"{identity}:STEP5B缺少sizing/permission/structural_stop")
    if permission.get("new_entry_allowed") is not True:
        raise ValueError(f"{identity}:STEP5C分配来源未获STEP5A新开仓许可")

    quantity = _positive_int(allocation.get("allocated_quantity"), label=f"{identity}.allocated_quantity")
    envelope_quantity = _positive_int(sizing.get("quantity"), label=f"{identity}.sizing.quantity")
    lot_size = _positive_int(sizing.get("lot_size"), label=f"{identity}.lot_size")
    if quantity > envelope_quantity or quantity % lot_size != 0:
        raise ValueError(f"{identity}:STEP5C数量突破STEP5B envelope或lot_size")

    entry = _decimal(sizing.get("planned_entry_price"), label=f"{identity}.planned_entry_price", positive=True)
    stop_price = _decimal(sizing.get("structural_stop_price"), label=f"{identity}.structural_stop_price", positive=True)
    risk_per_unit = _decimal(sizing.get("risk_per_unit"), label=f"{identity}.risk_per_unit", positive=True)
    if entry - stop_price != risk_per_unit:
        raise ValueError(f"{identity}:entry-stop与risk_per_unit不一致")

    allocated_entry = _decimal(allocation.get("entry_price"), label=f"{identity}.allocation.entry_price", positive=True)
    allocated_unit_risk = _decimal(allocation.get("risk_per_unit"), label=f"{identity}.allocation.risk_per_unit", positive=True)
    allocated_value = _decimal(allocation.get("allocated_value_cny"), label=f"{identity}.allocated_value_cny", positive=True)
    allocated_risk = _decimal(allocation.get("allocated_risk_cny"), label=f"{identity}.allocated_risk_cny", positive=True)
    if allocated_entry != entry or allocated_unit_risk != risk_per_unit:
        raise ValueError(f"{identity}:STEP5C价格/单位风险与STEP5B不一致")
    if allocated_value != Decimal(quantity) * entry:
        raise ValueError(f"{identity}:STEP5C allocated_value与数量×计划价不一致")
    if allocated_risk != Decimal(quantity) * risk_per_unit:
        raise ValueError(f"{identity}:STEP5C allocated_risk与数量×结构风险不一致")

    timeframe = str(permission.get("selected_timeframe") or "").strip()
    signal_id = str(permission.get("signal_id") or "").strip()
    signal_type = str(permission.get("signal_type") or "").strip()
    entry_mode = str(permission.get("entry_mode") or "").strip()
    if not timeframe or not signal_id or not signal_type or entry_mode not in {"STANDARD", "TEST"}:
        raise ValueError(f"{identity}:STEP5A主信号身份不完整")
    if str(stop.get("timeframe") or "") != timeframe or str(stop.get("signal_id") or "") != signal_id:
        raise ValueError(f"{identity}:结构止损没有绑定同一signal_id/timeframe")
    if stop.get("valid_for_new_entry") is not True:
        raise ValueError(f"{identity}:结构止损未通过新开仓有效性验证")
    stop_evidence_price = _decimal(stop.get("stop_price"), label=f"{identity}.stop_evidence_price", positive=True)
    if stop_evidence_price != stop_price:
        raise ValueError(f"{identity}:STEP5B stop_price与结构止损证据不一致")

    market = sizing_row.get("market")
    assert isinstance(market, int) and not isinstance(market, bool)
    return {
        "identity": identity,
        "market": market,
        "code": str(sizing_row.get("code") or ""),
        "security_type": str(sizing_row.get("security_type") or ""),
        "signal_id": signal_id,
        "timeframe": timeframe,
        "signal_type": signal_type,
        "entry_mode": entry_mode,
        "quantity": quantity,
        "lot_size": lot_size,
        "planned_entry_price": _decimal_text(entry),
        "structural_stop_price": _decimal_text(stop_price),
        "risk_per_unit": _decimal_text(risk_per_unit),
        "allocated_value_cny": _decimal_text(allocated_value),
        "allocated_risk_cny": _decimal_text(allocated_risk),
    }


def _canonical_plan_payload(snapshot_id: str, items: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    return {
        "snapshot_id": snapshot_id,
        # Preserve STEP5C allocation order: order is an explicit external decision and part of plan identity.
        "intents": [dict(item) for item in items],
    }


def _reservation_key(snapshot_id: str, items: Sequence[Mapping[str, Any]]) -> str:
    encoded = json.dumps(
        _canonical_plan_payload(snapshot_id, items),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "entry-plan:" + hashlib.sha256(encoded).hexdigest()


def _validate_receipt(
    receipt: Mapping[str, Any],
    *,
    reservation_key: str,
    snapshot_id: str,
    items: Sequence[Mapping[str, Any]],
) -> str:
    reservation_id = str(receipt.get("reservation_id") or "").strip()
    if not reservation_id or str(receipt.get("status") or "") != "RESERVED":
        raise ValueError("已存在的reservation receipt缺少reservation_id或不是RESERVED")
    if str(receipt.get("reservation_key") or "") != reservation_key:
        raise ValueError("reservation receipt的幂等键不一致")
    if str(receipt.get("snapshot_id") or "") != snapshot_id:
        raise ValueError("reservation receipt的snapshot_id不一致")
    identities = [str(item["identity"]) for item in items]
    if list(receipt.get("identities") or []) != identities:
        raise ValueError("reservation receipt的证券顺序/集合与当前计划不一致")
    expected_value = sum((_decimal(item["allocated_value_cny"], label="intent.value") for item in items), Decimal("0"))
    expected_risk = sum((_decimal(item["allocated_risk_cny"], label="intent.risk") for item in items), Decimal("0"))
    if _decimal(receipt.get("allocated_value_cny"), label="receipt.allocated_value_cny") != expected_value:
        raise ValueError("reservation receipt的总市值与当前计划不一致")
    if _decimal(receipt.get("allocated_risk_cny"), label="receipt.allocated_risk_cny") != expected_risk:
        raise ValueError("reservation receipt的总风险与当前计划不一致")
    return reservation_id


def build_execution_reservation_plan(
    allocation_payload: Mapping[str, Any],
    sizing_rows: Sequence[Mapping[str, Any]],
    reservation_context: Mapping[str, Any] | None,
) -> ReservationPlan:
    """STEP5D: bind a STEP5C allocation to an immutable, CAS-ready execution intent bundle.

    This function deliberately does not write a ledger and does not place an order. A plan is
    `READY_TO_RESERVE` only after an external durable store reports the same current snapshot ID.
    It becomes `RESERVED` only when an exact durable receipt is supplied back and validated.
    """
    try:
        plan, allocated = _allocated_rows(allocation_payload)
    except ValueError as exc:
        return _context_problem(
            ReservationPlanState.CONTRACT_INVALID,
            ReservationBlocker.ALLOCATION_CONTRACT_INVALID,
            str(exc),
        )

    if not allocated:
        if str(plan.get("state") or "") not in {"NO_ELIGIBLE", "ALLOCATED"}:
            return _context_problem(
                ReservationPlanState.CONTRACT_INVALID,
                ReservationBlocker.ALLOCATION_CONTRACT_INVALID,
                "STEP5C没有实际分配，但plan.state不是可接受的NO_ELIGIBLE/ALLOCATED",
            )
        return ReservationPlan(
            state=ReservationPlanState.NO_ALLOCATION,
            source_snapshot_id=str(plan.get("snapshot_id") or "") or None,
            current_snapshot_id=None,
            reservation_key=None,
            reservation_id=None,
            intents=(),
            allocated_value_cny=Decimal("0"),
            allocated_risk_cny=Decimal("0"),
            blockers=(),
            reasons=("STEP5C没有实际分配；STEP5D不要求无关的持久化reservation上下文",),
        )

    snapshot_id = str(plan.get("snapshot_id") or "").strip()
    if str(plan.get("state") or "") != "ALLOCATED" or not snapshot_id:
        return _context_problem(
            ReservationPlanState.CONTRACT_INVALID,
            ReservationBlocker.ALLOCATION_CONTRACT_INVALID,
            "存在STEP5C实际分配时，plan.state必须ALLOCATED且snapshot_id不能为空",
        )

    try:
        sized = _sizing_map(sizing_rows)
        intent_specs: list[dict[str, Any]] = []
        for allocation in allocated:
            identity = str(allocation.get("identity") or "")
            sizing_row = sized.get(identity)
            if sizing_row is None:
                raise ValueError(f"{identity}:STEP5C实际分配找不到对应STEP5B SIZED来源")
            intent_specs.append(_validate_source_pair(allocation, sizing_row))
    except ValueError as exc:
        return _context_problem(
            ReservationPlanState.CONTRACT_INVALID,
            ReservationBlocker.SIZING_CONTRACT_INVALID,
            str(exc),
            source_snapshot_id=snapshot_id,
        )

    key = _reservation_key(snapshot_id, intent_specs)
    total_value = sum((_decimal(item["allocated_value_cny"], label="intent.value") for item in intent_specs), Decimal("0"))
    total_risk = sum((_decimal(item["allocated_risk_cny"], label="intent.risk") for item in intent_specs), Decimal("0"))

    if not isinstance(reservation_context, Mapping):
        return _context_problem(
            ReservationPlanState.CONTEXT_REQUIRED,
            ReservationBlocker.RESERVATION_CONTEXT_UNAVAILABLE,
            "存在STEP5C实际分配，但尚未提供外部持久化reservation store的当前snapshot上下文",
            source_snapshot_id=snapshot_id,
        )

    current_snapshot_id = str(reservation_context.get("current_snapshot_id") or "").strip()
    existing = reservation_context.get("existing_reservations")
    if not current_snapshot_id or not isinstance(existing, Mapping):
        return _context_problem(
            ReservationPlanState.CONTRACT_INVALID,
            ReservationBlocker.RESERVATION_CONTEXT_INVALID,
            "reservation-context必须包含非空current_snapshot_id和existing_reservations对象",
            source_snapshot_id=snapshot_id,
            current_snapshot_id=current_snapshot_id or None,
        )

    # Idempotency lookup comes before freshness rejection. A successful CAS reservation should
    # advance the durable snapshot. Retrying the exact same request after that advance must return
    # the existing receipt rather than incorrectly calling the already-completed plan stale.
    receipt = existing.get(key)
    reservation_id: str | None = None
    final_state = ReservationPlanState.READY_TO_RESERVE
    intent_state = IntentState.READY_TO_RESERVE
    if receipt is not None:
        if not isinstance(receipt, Mapping):
            return _context_problem(
                ReservationPlanState.CONTRACT_INVALID,
                ReservationBlocker.IDEMPOTENCY_CONFLICT,
                "existing_reservations中的同幂等键记录不是JSON对象",
                source_snapshot_id=snapshot_id,
                current_snapshot_id=current_snapshot_id,
            )
        try:
            reservation_id = _validate_receipt(
                receipt,
                reservation_key=key,
                snapshot_id=snapshot_id,
                items=intent_specs,
            )
        except ValueError as exc:
            return _context_problem(
                ReservationPlanState.CONTRACT_INVALID,
                ReservationBlocker.IDEMPOTENCY_CONFLICT,
                str(exc),
                source_snapshot_id=snapshot_id,
                current_snapshot_id=current_snapshot_id,
            )
        final_state = ReservationPlanState.RESERVED
        intent_state = IntentState.RESERVED
    elif current_snapshot_id != snapshot_id:
        return _context_problem(
            ReservationPlanState.STALE_SNAPSHOT,
            ReservationBlocker.SNAPSHOT_STALE,
            "外部账户/组合snapshot已经变化且不存在当前幂等键的已完成receipt；STEP5B/5C必须基于新snapshot重新计算",
            source_snapshot_id=snapshot_id,
            current_snapshot_id=current_snapshot_id,
        )

    intents = tuple(
        ExecutionIntent(
            identity=item["identity"],
            market=int(item["market"]),
            code=item["code"],
            security_type=item["security_type"],
            signal_id=item["signal_id"],
            timeframe=item["timeframe"],
            signal_type=item["signal_type"],
            entry_mode=item["entry_mode"],
            quantity=int(item["quantity"]),
            lot_size=int(item["lot_size"]),
            planned_entry_price=_decimal(item["planned_entry_price"], label="intent.entry", positive=True),
            structural_stop_price=_decimal(item["structural_stop_price"], label="intent.stop", positive=True),
            risk_per_unit=_decimal(item["risk_per_unit"], label="intent.risk_per_unit", positive=True),
            allocated_value_cny=_decimal(item["allocated_value_cny"], label="intent.value", positive=True),
            allocated_risk_cny=_decimal(item["allocated_risk_cny"], label="intent.risk", positive=True),
            source_snapshot_id=snapshot_id,
            reservation_key=key,
            state=intent_state,
            reservation_id=reservation_id,
        )
        for item in intent_specs
    )

    reason = (
        "外部持久化store已返回与当前计划完全一致的reservation receipt；幂等重跑复用同一reservation_id"
        if final_state is ReservationPlanState.RESERVED
        else "snapshot仍与STEP5C一致；已生成不可变reservation bundle，等待外部store用CAS原子预留共享现金/风险容量"
    )
    return ReservationPlan(
        state=final_state,
        source_snapshot_id=snapshot_id,
        current_snapshot_id=current_snapshot_id,
        reservation_key=key,
        reservation_id=reservation_id,
        intents=intents,
        allocated_value_cny=total_value,
        allocated_risk_cny=total_risk,
        blockers=(),
        reasons=(reason,),
    )
