from __future__ import annotations

import inspect

from trading_skill import entry_sizing
from trading_skill.entry_sizing import EntrySizingBlocker, EntrySizingState, size_new_entry


def _row(*, mode: str = "STANDARD", allowed: bool = True, stop_ticks: int = 920, tick_size=0.01):
    state = "ELIGIBLE" if mode == "STANDARD" else "TEST_ENTRY_ELIGIBLE"
    if not allowed:
        state = "WAIT_TECHNICAL"
        mode = "NONE"
    return {
        "permission": {
            "state": state,
            "entry_mode": mode,
            "new_entry_allowed": allowed,
        },
        "structural_stop": {
            "valid_for_new_entry": True,
            "price_ticks": stop_ticks,
            "tick_size": tick_size,
        },
    }


def _context(**overrides):
    payload = {
        "planned_entry_price": "10.00",
        "lot_size": 100,
        "existing_position_quantity": 0,
        "standard_trade_risk_limit_cny": "1000",
        "test_trade_risk_limit_cny": "300",
        "portfolio_risk_remaining_cny": "5000",
        "industry_risk_remaining_cny": None,
        "theme_risk_remaining_cny": None,
        "cash_available_cny": "10000",
        "security_value_remaining_cny": "15000",
        "industry_value_remaining_cny": None,
        "theme_value_remaining_cny": None,
    }
    payload.update(overrides)
    return payload


def test_noneligible_permission_never_requires_sizing_context():
    decision = size_new_entry(_row(allowed=False), None)
    assert decision.state is EntrySizingState.NOT_ELIGIBLE
    assert decision.quantity == 0
    assert decision.blockers == ()


def test_standard_entry_is_sized_by_risk_and_value_caps_then_lot_rounding():
    decision = size_new_entry(_row(), _context())
    assert decision.state is EntrySizingState.SIZED
    assert decision.quantity == 1000
    assert decision.to_dict()["structural_stop_price"] == "9.2"
    assert decision.to_dict()["risk_per_unit"] == "0.8"
    assert decision.to_dict()["effective_risk_budget_cny"] == "1000"
    assert decision.to_dict()["planned_value_cny"] == "10000"
    assert decision.to_dict()["planned_risk_cny"] == "800"
    assert decision.risk_binding_limits == ("TRADE_RISK",)
    assert decision.quantity_binding_limits == ("CASH",)


def test_industry_risk_cap_can_be_the_binding_risk_without_score_multiplier():
    decision = size_new_entry(_row(), _context(industry_risk_remaining_cny="600"))
    assert decision.state is EntrySizingState.SIZED
    assert decision.effective_risk_budget_cny is not None
    assert str(decision.effective_risk_budget_cny) == "600"
    assert decision.quantity_before_lot_rounding == 750
    assert decision.quantity == 700
    assert decision.risk_binding_limits == ("INDUSTRY_RISK",)
    assert "LOT_ROUNDING" in decision.quantity_binding_limits


def test_test_entry_uses_explicit_test_risk_limit_not_fraction_of_standard():
    decision = size_new_entry(_row(mode="TEST"), _context())
    assert decision.state is EntrySizingState.SIZED
    assert decision.to_dict()["effective_risk_budget_cny"] == "300"
    assert decision.quantity_before_lot_rounding == 375
    assert decision.quantity == 300


def test_test_risk_limit_cannot_exceed_standard_risk_limit():
    decision = size_new_entry(
        _row(mode="TEST"),
        _context(standard_trade_risk_limit_cny="500", test_trade_risk_limit_cny="700"),
    )
    assert decision.state is EntrySizingState.CONTEXT_REQUIRED
    assert decision.blockers == (EntrySizingBlocker.SIZING_CONTEXT_INVALID,)


def test_optional_industry_theme_caps_must_be_explicit_null_not_missing():
    context = _context()
    del context["theme_risk_remaining_cny"]
    decision = size_new_entry(_row(), context)
    assert decision.state is EntrySizingState.CONTEXT_REQUIRED
    assert decision.blockers == (EntrySizingBlocker.SIZING_CONTEXT_INCOMPLETE,)
    assert "theme_risk_remaining_cny" in decision.reasons[0]


def test_existing_position_must_leave_new_entry_route():
    decision = size_new_entry(_row(), _context(existing_position_quantity=100))
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.EXISTING_POSITION_REQUIRES_SCALE_IN,)
    assert decision.quantity == 0


def test_entry_price_must_match_tick_without_silent_rounding():
    decision = size_new_entry(_row(), _context(planned_entry_price="10.005"))
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.INVALID_ENTRY_TICK,)


def test_stop_must_be_strictly_below_planned_entry():
    decision = size_new_entry(_row(stop_ticks=1020), _context())
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.INVALID_STOP_DISTANCE,)


def test_zero_portfolio_risk_capacity_blocks_even_with_cash_available():
    decision = size_new_entry(_row(), _context(portfolio_risk_remaining_cny="0"))
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.NO_RISK_CAPACITY,)
    assert "PORTFOLIO_RISK" in decision.risk_binding_limits


def test_quantity_is_never_rounded_up_to_minimum_lot():
    decision = size_new_entry(
        _row(),
        _context(standard_trade_risk_limit_cny="10", test_trade_risk_limit_cny="5"),
    )
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.NO_EXECUTION_MINIMUM_LOT,)
    assert decision.quantity_before_lot_rounding == 12
    assert decision.quantity == 0


def test_cash_and_security_caps_are_hard_upper_bounds_after_rounding():
    decision = size_new_entry(
        _row(),
        _context(cash_available_cny="7500", security_value_remaining_cny="6800"),
    )
    assert decision.state is EntrySizingState.SIZED
    assert decision.quantity_before_lot_rounding == 680
    assert decision.quantity == 600
    assert decision.to_dict()["planned_value_cny"] == "6000"
    assert decision.planned_risk_cny is not None
    assert decision.planned_risk_cny <= decision.effective_risk_budget_cny
    assert "SECURITY_VALUE" in decision.quantity_binding_limits
    assert "LOT_ROUNDING" in decision.quantity_binding_limits


def test_decimal_price_math_does_not_leak_binary_float_artifacts():
    decision = size_new_entry(
        _row(stop_ticks=920, tick_size=0.01),
        _context(planned_entry_price="10.20", lot_size=1, cash_available_cny="100000"),
    )
    assert decision.state is EntrySizingState.SIZED
    payload = decision.to_dict()
    assert payload["planned_entry_price"] == "10.2"
    assert payload["structural_stop_price"] == "9.2"
    assert payload["risk_per_unit"] == "1"


def test_step5b_source_has_no_legacy_grade_or_risk_state_dependency():
    source = inspect.getsource(entry_sizing)
    assert "OpportunityGrade" not in source
    assert "RiskState" not in source
    assert "BASE_RISK" not in source
    assert "RISK_MULT" not in source
    assert "from trading_skill.sizing" not in source
    assert "from trading_skill.decision" not in source
