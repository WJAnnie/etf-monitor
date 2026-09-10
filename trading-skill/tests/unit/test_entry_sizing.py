from __future__ import annotations

import inspect

from trading_skill import entry_sizing
from trading_skill.entry_sizing import EntrySizingBlocker, EntrySizingState, size_new_entry


def _row(
    *,
    allowed: bool = True,
    authority_stop_ticks: int = 920,
    execution_stop_ticks: int = 950,
    tick_size=0.01,
    signal_id: str = "sig-daily",
    execution_signal_id: str = "sig-5m",
):
    return {
        "permission": {
            "state": "ELIGIBLE" if allowed else "WAIT_TECHNICAL",
            "entry_mode": "STANDARD" if allowed else "NONE",
            "new_entry_allowed": allowed,
            "signal_id": signal_id if allowed else None,
            "selected_timeframe": "daily" if allowed else None,
            "signal_type": "SECOND_BUY" if allowed else None,
        },
        "authority_stop": {
            "valid_for_new_entry": True,
            "signal_id": signal_id,
            "timeframe": "daily",
            "price_ticks": authority_stop_ticks,
            "tick_size": tick_size,
            "stop_price": authority_stop_ticks * tick_size,
        },
        "execution_stop": {
            "valid_for_new_entry": True,
            "signal_id": execution_signal_id,
            "authority_signal_id": signal_id,
            "timeframe": "5m",
            "price_ticks": execution_stop_ticks,
            "tick_size": tick_size,
            "stop_price": execution_stop_ticks * tick_size,
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


def test_daily_standard_authority_starts_as_test_tranche_sized_from_5m_execution_stop():
    decision = size_new_entry(_row(), _context())
    payload = decision.to_dict()
    assert decision.state is EntrySizingState.SIZED
    assert decision.quantity == 600
    assert payload["structural_stop_price"] == "9.5"  # legacy field = execution stop
    assert payload["authority_stop_price"] == "9.2"
    assert payload["risk_per_unit"] == "0.5"
    assert payload["effective_risk_budget_cny"] == "300"
    assert payload["planned_value_cny"] == "6000"
    assert payload["planned_risk_cny"] == "300"
    assert decision.risk_binding_limits == ("TEST_TRADE_RISK",)
    assert payload["authority_signal_id"] == "sig-daily"
    assert payload["execution_stop_signal_id"] == "sig-5m"
    assert payload["execution_stop_timeframe"] == "5m"


def test_industry_risk_cap_can_bind_initial_test_tranche_without_score_multiplier():
    decision = size_new_entry(_row(), _context(industry_risk_remaining_cny="225"))
    assert decision.state is EntrySizingState.SIZED
    assert decision.to_dict()["effective_risk_budget_cny"] == "225"
    assert decision.quantity_before_lot_rounding == 450
    assert decision.quantity == 400
    assert decision.risk_binding_limits == ("INDUSTRY_RISK",)
    assert "LOT_ROUNDING" in decision.quantity_binding_limits


def test_legacy_test_entry_mode_is_no_longer_a_valid_new_position_permission():
    row = _row()
    row["permission"]["entry_mode"] = "TEST"
    row["permission"]["state"] = "TEST_ENTRY_ELIGIBLE"
    decision = size_new_entry(row, _context())
    assert decision.state is EntrySizingState.CONTEXT_REQUIRED
    assert decision.blockers == (EntrySizingBlocker.SIZING_CONTEXT_INVALID,)


def test_authority_stop_must_match_daily_permission_signal():
    row = _row()
    row["authority_stop"]["signal_id"] = "other-signal"
    decision = size_new_entry(row, _context())
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.STRUCTURAL_STOP_UNAVAILABLE,)


def test_execution_stop_must_be_5m_and_bound_to_same_daily_authority():
    row = _row()
    row["execution_stop"]["authority_signal_id"] = "other-daily"
    decision = size_new_entry(row, _context())
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.EXECUTION_STOP_UNAVAILABLE,)

    row = _row()
    row["execution_stop"]["timeframe"] = "30m"
    decision = size_new_entry(row, _context())
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.EXECUTION_STOP_UNAVAILABLE,)


def test_test_risk_limit_cannot_exceed_standard_risk_limit():
    decision = size_new_entry(
        _row(),
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


def test_execution_stop_must_be_strictly_below_planned_entry():
    decision = size_new_entry(_row(execution_stop_ticks=1020), _context())
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.INVALID_STOP_DISTANCE,)


def test_zero_portfolio_risk_capacity_blocks_even_with_cash_available():
    decision = size_new_entry(_row(), _context(portfolio_risk_remaining_cny="0"))
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.NO_RISK_CAPACITY,)
    assert "PORTFOLIO_RISK" in decision.risk_binding_limits


def test_positive_test_risk_budget_that_cannot_cover_one_unit_reports_risk_not_value():
    decision = size_new_entry(
        _row(),
        _context(planned_entry_price="1000.00", standard_trade_risk_limit_cny="10", test_trade_risk_limit_cny="5"),
    )
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.NO_RISK_CAPACITY,)
    assert decision.quantity_binding_limits == ("RISK_BUDGET",)


def test_quantity_is_never_rounded_up_to_minimum_lot():
    decision = size_new_entry(
        _row(),
        _context(standard_trade_risk_limit_cny="25", test_trade_risk_limit_cny="25"),
    )
    assert decision.state is EntrySizingState.BLOCKED
    assert decision.blockers == (EntrySizingBlocker.NO_EXECUTION_MINIMUM_LOT,)
    assert decision.quantity_before_lot_rounding == 50
    assert decision.quantity == 0


def test_cash_and_security_caps_are_hard_upper_bounds_after_rounding():
    decision = size_new_entry(
        _row(),
        _context(
            standard_trade_risk_limit_cny="1000",
            test_trade_risk_limit_cny="1000",
            cash_available_cny="7500",
            security_value_remaining_cny="6800",
        ),
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
        _row(authority_stop_ticks=900, execution_stop_ticks=920, tick_size=0.01),
        _context(
            planned_entry_price="10.20",
            lot_size=1,
            cash_available_cny="100000",
            security_value_remaining_cny="100000",
        ),
    )
    assert decision.state is EntrySizingState.SIZED
    payload = decision.to_dict()
    assert payload["planned_entry_price"] == "10.2"
    assert payload["structural_stop_price"] == "9.2"
    assert payload["authority_stop_price"] == "9"
    assert payload["risk_per_unit"] == "1"


def test_step5b_source_has_no_legacy_grade_or_risk_state_dependency():
    source = inspect.getsource(entry_sizing)
    assert "OpportunityGrade" not in source
    assert "RiskState" not in source
    assert "BASE_RISK" not in source
    assert "RISK_MULT" not in source
    assert "from trading_skill.sizing" not in source
    assert "from trading_skill.decision" not in source
