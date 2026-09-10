from __future__ import annotations

import inspect

from trading_skill import portfolio_allocation
from trading_skill.portfolio_allocation import (
    CandidateAllocationState,
    PortfolioAllocationBlocker,
    PortfolioAllocationState,
    allocate_new_entries,
)


def _identity(code: str, *, market: int = 1, security_type: str = "STOCK") -> str:
    return f"{market}:{code}:{security_type}"


def _sized_row(
    code: str,
    *,
    market: int = 1,
    security_type: str = "STOCK",
    quantity: int = 1000,
    lot_size: int = 100,
    entry: str = "10",
    risk_per_unit: str = "1",
) -> dict:
    return {
        "code": code,
        "name": f"证券{code}",
        "market": market,
        "security_type": security_type,
        "sizing": {
            "state": "SIZED",
            "quantity": quantity,
            "lot_size": lot_size,
            "planned_entry_price": entry,
            "risk_per_unit": risk_per_unit,
            "planned_value_cny": str(quantity * float(entry)).rstrip("0").rstrip("."),
            "planned_risk_cny": str(quantity * float(risk_per_unit)).rstrip("0").rstrip("."),
        },
    }


def _not_sized_row(code: str) -> dict:
    return {
        "code": code,
        "name": f"证券{code}",
        "market": 1,
        "security_type": "STOCK",
        "sizing": {"state": "NOT_ELIGIBLE", "quantity": 0},
    }


def _context(
    order: list[str],
    *,
    cash: str = "50000",
    portfolio_risk: str = "5000",
    industry_risk: dict | None = None,
    theme_risk: dict | None = None,
    industry_value: dict | None = None,
    theme_value: dict | None = None,
    symbols: dict | None = None,
) -> dict:
    return {
        "snapshot_id": "snapshot-1",
        "allocation_order": order,
        "cash_remaining_cny": cash,
        "portfolio_risk_remaining_cny": portfolio_risk,
        "industry_risk_remaining_cny": industry_risk or {},
        "theme_risk_remaining_cny": theme_risk or {},
        "industry_value_remaining_cny": industry_value or {},
        "theme_value_remaining_cny": theme_value or {},
        "symbols": symbols or {},
    }


def _symbol(industry: str | None = None, themes: list[str] | None = None) -> dict:
    return {"industry_key": industry, "theme_keys": list(themes or [])}


def _by_code(plan) -> dict[str, object]:
    return {item.code: item for item in plan.allocations}


def test_no_sized_envelope_requires_no_allocation_context():
    plan = allocate_new_entries([_not_sized_row("600001")], None)
    assert plan.state is PortfolioAllocationState.NO_ELIGIBLE
    assert plan.blockers == ()
    assert plan.allocations[0].state is CandidateAllocationState.NOT_SIZED
    assert plan.start_capacity is None
    assert plan.end_capacity is None


def test_sized_envelope_without_context_requires_context():
    plan = allocate_new_entries([_sized_row("600001")], None)
    assert plan.state is PortfolioAllocationState.CONTEXT_REQUIRED
    assert plan.blockers == (PortfolioAllocationBlocker.ALLOCATION_CONTEXT_UNAVAILABLE,)


def test_allocation_order_is_explicit_and_shared_cash_is_consumed_once():
    a = _sized_row("600001")
    b = _sized_row("600002")
    ia = _identity("600001")
    ib = _identity("600002")
    context = _context(
        [ia, ib],
        cash="15000",
        symbols={ia: _symbol(), ib: _symbol()},
    )
    plan = allocate_new_entries([a, b], context)
    rows = _by_code(plan)

    assert plan.state is PortfolioAllocationState.ALLOCATED
    assert rows["600001"].allocated_quantity == 1000
    assert rows["600001"].state is CandidateAllocationState.ALLOCATED_FULL
    assert rows["600002"].allocated_quantity == 500
    assert rows["600002"].state is CandidateAllocationState.ALLOCATED_PARTIAL
    assert "CASH" in rows["600002"].binding_limits
    assert plan.start_capacity.to_dict()["cash_cny"] == "15000"
    assert plan.end_capacity.to_dict()["cash_cny"] == "0"


def test_reversing_explicit_order_reverses_who_gets_full_envelope():
    a = _sized_row("600001")
    b = _sized_row("600002")
    ia = _identity("600001")
    ib = _identity("600002")
    context = _context(
        [ib, ia],
        cash="15000",
        symbols={ia: _symbol(), ib: _symbol()},
    )
    plan = allocate_new_entries([a, b], context)
    rows = _by_code(plan)

    assert rows["600002"].allocated_quantity == 1000
    assert rows["600001"].allocated_quantity == 500


def test_shared_industry_risk_is_deducted_for_later_candidates():
    a = _sized_row("600001")
    b = _sized_row("600002")
    ia = _identity("600001")
    ib = _identity("600002")
    context = _context(
        [ia, ib],
        industry_risk={"银行": "1500"},
        industry_value={"银行": "50000"},
        symbols={ia: _symbol("银行"), ib: _symbol("银行")},
    )
    plan = allocate_new_entries([a, b], context)
    rows = _by_code(plan)

    assert rows["600001"].allocated_quantity == 1000
    assert rows["600002"].allocated_quantity == 500
    assert "INDUSTRY_RISK:银行" in rows["600002"].binding_limits
    assert plan.end_capacity.to_dict()["industry_risk_cny"]["银行"] == "0"


def test_overlapping_theme_risk_is_charged_in_full_to_each_theme():
    a = _sized_row("600001")
    b = _sized_row("600002")
    ia = _identity("600001")
    ib = _identity("600002")
    context = _context(
        [ia, ib],
        theme_risk={"AI": "1200", "算力": "5000"},
        theme_value={"AI": "50000", "算力": "50000"},
        symbols={ia: _symbol(None, ["AI", "算力"]), ib: _symbol(None, ["AI"])},
    )
    plan = allocate_new_entries([a, b], context)
    rows = _by_code(plan)

    assert rows["600001"].allocated_quantity == 1000
    assert rows["600002"].allocated_quantity == 200
    assert rows["600001"].theme_keys == ("AI", "算力")
    assert "THEME_RISK:AI" in rows["600002"].binding_limits
    end = plan.end_capacity.to_dict()
    assert end["theme_risk_cny"]["AI"] == "0"
    assert end["theme_risk_cny"]["算力"] == "4000"


def test_lot_rounding_only_reduces_and_never_rounds_up():
    row = _sized_row("600001")
    identity = _identity("600001")
    plan = allocate_new_entries(
        [row],
        _context([identity], cash="5550", symbols={identity: _symbol()}),
    )
    item = plan.allocations[0]

    assert item.allocated_quantity == 500
    assert item.allocated_quantity < item.envelope_quantity
    assert "CASH" in item.binding_limits
    assert "LOT_ROUNDING" in item.binding_limits


def test_capacity_below_one_lot_skips_candidate_without_spending_shared_capacity():
    row = _sized_row("600001")
    identity = _identity("600001")
    plan = allocate_new_entries(
        [row],
        _context([identity], cash="990", symbols={identity: _symbol()}),
    )
    item = plan.allocations[0]

    assert item.state is CandidateAllocationState.SKIPPED_CAPACITY
    assert item.allocated_quantity == 0
    assert plan.start_capacity.to_dict() == plan.end_capacity.to_dict()


def test_sized_candidate_not_in_order_is_not_selected_and_needs_no_dimension_context():
    a = _sized_row("600001")
    b = _sized_row("600002")
    ia = _identity("600001")
    plan = allocate_new_entries(
        [a, b],
        _context([ia], symbols={ia: _symbol()}),
    )
    rows = _by_code(plan)

    assert plan.state is PortfolioAllocationState.ALLOCATED
    assert rows["600001"].state is CandidateAllocationState.ALLOCATED_FULL
    assert rows["600002"].state is CandidateAllocationState.NOT_SELECTED
    assert rows["600002"].allocated_quantity == 0


def test_allocation_order_must_use_exact_sized_identity_not_code_guessing():
    row = _sized_row("600001")
    plan = allocate_new_entries([row], _context(["600001"], symbols={}))
    assert plan.state is PortfolioAllocationState.CONTEXT_REQUIRED
    assert plan.blockers == (PortfolioAllocationBlocker.ALLOCATION_CONTEXT_INVALID,)
    assert "未知身份" in plan.reasons[0]


def test_selected_dimension_requires_explicit_shared_capacity():
    row = _sized_row("600001")
    identity = _identity("600001")
    plan = allocate_new_entries(
        [row],
        _context([identity], symbols={identity: _symbol("银行")}),
    )
    assert plan.state is PortfolioAllocationState.CONTEXT_REQUIRED
    assert plan.blockers == (PortfolioAllocationBlocker.ALLOCATION_CONTEXT_INVALID,)
    assert "银行" in plan.reasons[0]


def test_tampered_step5b_envelope_is_reported_as_envelope_contract_failure():
    row = _sized_row("600001")
    row["sizing"]["planned_risk_cny"] = "999"
    identity = _identity("600001")
    plan = allocate_new_entries([row], _context([identity], symbols={identity: _symbol()}))
    assert plan.state is PortfolioAllocationState.CONTEXT_REQUIRED
    assert plan.blockers == (PortfolioAllocationBlocker.ENVELOPE_CONTRACT_INVALID,)


def test_step5c_never_increases_step5b_envelope_even_with_huge_capacity():
    row = _sized_row("600001", quantity=300)
    identity = _identity("600001")
    plan = allocate_new_entries(
        [row],
        _context([identity], cash="999999", portfolio_risk="999999", symbols={identity: _symbol()}),
    )
    item = plan.allocations[0]
    assert item.state is CandidateAllocationState.ALLOCATED_FULL
    assert item.allocated_quantity == 300
    assert item.envelope_quantity == 300


def test_empty_explicit_order_does_not_invent_a_ranking():
    row = _sized_row("600001")
    plan = allocate_new_entries([row], _context([], symbols={}))
    assert plan.state is PortfolioAllocationState.ALLOCATED
    assert plan.allocations[0].state is CandidateAllocationState.NOT_SELECTED
    assert plan.allocations[0].allocated_quantity == 0


def test_step5c_source_contains_no_hidden_score_or_grade_ranking():
    source = inspect.getsource(portfolio_allocation)
    assert "opportunity_grade" not in source
    assert "OpportunityGrade" not in source
    assert "weighted" not in source.lower()
    assert "score" not in source.lower()
