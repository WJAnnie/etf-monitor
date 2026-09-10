from __future__ import annotations

from copy import deepcopy

from trading_skill.execution_intent import (
    ReservationBlocker,
    ReservationPlanState,
    build_execution_reservation_plan,
)


def _sizing_row(*, signal_id: str = "sig-120-2b") -> dict:
    return {
        "market": 1,
        "code": "600000",
        "name": "浦发银行",
        "security_type": "STOCK",
        "permission": {
            "state": "ELIGIBLE",
            "entry_mode": "STANDARD",
            "new_entry_allowed": True,
            "selected_timeframe": "120m",
            "signal_id": signal_id,
            "signal_type": "SECOND_BUY",
        },
        "structural_stop": {
            "found": True,
            "valid_for_new_entry": True,
            "timeframe": "120m",
            "signal_id": signal_id,
            "stop_price": 9.2,
        },
        "sizing": {
            "state": "SIZED",
            "quantity": 1000,
            "lot_size": 100,
            "planned_entry_price": "10",
            "structural_stop_price": "9.2",
            "risk_per_unit": "0.8",
            "planned_value_cny": "10000",
            "planned_risk_cny": "800",
        },
    }


def _allocation_payload(*, quantity: int = 1000) -> dict:
    return {
        "mode": "STEP5C_SHARED_PORTFOLIO_ALLOCATION_PLAN",
        "plan": {
            "state": "ALLOCATED",
            "snapshot_id": "snapshot-001",
            "allocations": [
                {
                    "identity": "1:600000:STOCK",
                    "code": "600000",
                    "state": "ALLOCATED_FULL" if quantity == 1000 else "ALLOCATED_PARTIAL",
                    "envelope_quantity": 1000,
                    "allocated_quantity": quantity,
                    "lot_size": 100,
                    "entry_price": "10",
                    "risk_per_unit": "0.8",
                    "industry_key": "银行",
                    "theme_keys": [],
                    "allocated_value_cny": str(quantity * 10),
                    "allocated_risk_cny": str(quantity * 0.8),
                    "binding_limits": [],
                    "reason": "test",
                }
            ],
        },
    }


def _reservation_context() -> dict:
    return {"current_snapshot_id": "snapshot-001", "existing_reservations": {}}


def test_no_allocation_needs_no_reservation_context():
    allocation = {
        "plan": {
            "state": "NO_ELIGIBLE",
            "snapshot_id": None,
            "allocations": [
                {
                    "identity": "1:600000:STOCK",
                    "state": "NOT_SIZED",
                }
            ],
        }
    }
    plan = build_execution_reservation_plan(allocation, [], None)
    assert plan.state is ReservationPlanState.NO_ALLOCATION
    assert plan.intents == ()
    assert plan.reservation_key is None


def test_allocated_plan_without_durable_context_is_context_required():
    plan = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], None)
    assert plan.state is ReservationPlanState.CONTEXT_REQUIRED
    assert plan.blockers == (ReservationBlocker.RESERVATION_CONTEXT_UNAVAILABLE,)


def test_snapshot_mismatch_never_reuses_old_allocation():
    context = _reservation_context()
    context["current_snapshot_id"] = "snapshot-002"
    plan = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], context)
    assert plan.state is ReservationPlanState.STALE_SNAPSHOT
    assert plan.blockers == (ReservationBlocker.SNAPSHOT_STALE,)
    assert plan.intents == ()


def test_matching_snapshot_builds_immutable_ready_to_reserve_bundle():
    plan = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], _reservation_context())
    assert plan.state is ReservationPlanState.READY_TO_RESERVE
    assert plan.reservation_key.startswith("entry-plan:")
    assert plan.allocated_value_cny == 10000
    assert plan.allocated_risk_cny == 800
    assert len(plan.intents) == 1
    intent = plan.intents[0]
    assert intent.identity == "1:600000:STOCK"
    assert intent.timeframe == "120m"
    assert intent.signal_id == "sig-120-2b"
    assert intent.quantity == 1000
    assert intent.planned_entry_price == 10
    assert intent.structural_stop_price == 9.2
    assert intent.reservation_key == plan.reservation_key
    assert intent.reservation_id is None


def test_same_plan_has_same_idempotency_key_but_signal_change_changes_key():
    first = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], _reservation_context())
    second = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], _reservation_context())
    changed = _sizing_row(signal_id="sig-new")
    changed_plan = build_execution_reservation_plan(_allocation_payload(), [changed], _reservation_context())

    assert first.reservation_key == second.reservation_key
    assert changed_plan.state is ReservationPlanState.READY_TO_RESERVE
    assert changed_plan.reservation_key != first.reservation_key


def test_exact_existing_receipt_is_idempotently_reported_reserved():
    ready = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], _reservation_context())
    assert ready.reservation_key is not None
    context = _reservation_context()
    context["existing_reservations"] = {
        ready.reservation_key: {
            "reservation_id": "reservation-001",
            "status": "RESERVED",
            "reservation_key": ready.reservation_key,
            "snapshot_id": "snapshot-001",
            "identities": ["1:600000:STOCK"],
            "allocated_value_cny": "10000",
            "allocated_risk_cny": "800",
        }
    }
    reserved = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], context)
    assert reserved.state is ReservationPlanState.RESERVED
    assert reserved.reservation_id == "reservation-001"
    assert reserved.intents[0].reservation_id == "reservation-001"
    assert reserved.intents[0].state.value == "RESERVED"


def test_same_idempotency_key_with_different_receipt_is_hard_conflict():
    ready = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], _reservation_context())
    assert ready.reservation_key is not None
    context = _reservation_context()
    context["existing_reservations"] = {
        ready.reservation_key: {
            "reservation_id": "reservation-001",
            "status": "RESERVED",
            "reservation_key": ready.reservation_key,
            "snapshot_id": "snapshot-001",
            "identities": ["1:600000:STOCK"],
            "allocated_value_cny": "9999",
            "allocated_risk_cny": "800",
        }
    }
    conflict = build_execution_reservation_plan(_allocation_payload(), [_sizing_row()], context)
    assert conflict.state is ReservationPlanState.CONTRACT_INVALID
    assert conflict.blockers == (ReservationBlocker.IDEMPOTENCY_CONFLICT,)


def test_step5c_allocation_cannot_drift_from_step5b_envelope():
    allocation = _allocation_payload()
    allocation["plan"]["allocations"][0]["allocated_value_cny"] = "9999"
    plan = build_execution_reservation_plan(allocation, [_sizing_row()], _reservation_context())
    assert plan.state is ReservationPlanState.CONTRACT_INVALID
    assert plan.blockers == (ReservationBlocker.SIZING_CONTRACT_INVALID,)


def test_stop_must_bind_same_signal_and_timeframe():
    sizing = deepcopy(_sizing_row())
    sizing["structural_stop"]["signal_id"] = "other-signal"
    plan = build_execution_reservation_plan(_allocation_payload(), [sizing], _reservation_context())
    assert plan.state is ReservationPlanState.CONTRACT_INVALID
    assert plan.blockers == (ReservationBlocker.SIZING_CONTRACT_INVALID,)
