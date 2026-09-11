from datetime import datetime, timedelta
import inspect
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from scripts.collect_candidate_bars_v2 import aggregate_m30_to_m120, normalize_complete_m30
import scripts.run_full_a_scan_v2 as scan_v2
from scripts.run_full_a_scan_v2 import (
    _staged_entry_payload,
    _structural_execution_maturity,
    execution_maturity_v2,
)
from trading_skill.domain.enums import ChanSignalType


CN = ZoneInfo("Asia/Shanghai")


def m30(time, open_, close, high, low):
    return {"time": time, "open": open_, "close": close, "high": high, "low": low, "volume": 1, "amount": 1}


def test_long_history_m30_builds_two_m120_bars_per_full_day():
    rows = [
        m30("2026-09-08 10:00:00", 10, 10.1, 10.2, 9.9),
        m30("2026-09-08 10:30:00", 10.1, 10.2, 10.3, 10.0),
        m30("2026-09-08 11:00:00", 10.2, 10.3, 10.4, 10.1),
        m30("2026-09-08 11:30:00", 10.3, 10.4, 10.5, 10.2),
        m30("2026-09-08 13:30:00", 10.4, 10.5, 10.6, 10.3),
        m30("2026-09-08 14:00:00", 10.5, 10.6, 10.7, 10.4),
        m30("2026-09-08 14:30:00", 10.6, 10.7, 10.8, 10.5),
        m30("2026-09-08 15:00:00", 10.7, 10.8, 10.9, 10.6),
    ]
    normalized = normalize_complete_m30(rows, now=datetime(2026, 9, 8, 16, 0, tzinfo=CN), source="测试")
    m120 = aggregate_m30_to_m120(normalized)
    assert len(m120) == 2
    assert m120[0]["time"].endswith("11:30:00")
    assert m120[1]["time"].endswith("15:00:00")


def fake_signal(kind):
    return SimpleNamespace(structural_price_ticks=1000, standard_types=(kind,))


def test_second_buy_price_distance_is_only_a_chase_guard():
    signal = fake_signal(ChanSignalType.SECOND_BUY)
    assert execution_maturity_v2(signal, 10.30) == "TRIGGERED"
    assert execution_maturity_v2(signal, 10.70) == "PREPARE"
    assert execution_maturity_v2(signal, 10.90) == "WATCH"


def test_daily_first_buy_is_never_promoted_by_price_distance():
    signal = fake_signal(ChanSignalType.FIRST_BUY)
    assert execution_maturity_v2(signal, 10.00) == "WATCH"
    assert execution_maturity_v2(signal, 10.30) == "WATCH"
    assert execution_maturity_v2(signal, 10.50) == "WATCH"


def _formal(side: str, kind: str, when: datetime, signal_id: str):
    return {
        "id": signal_id,
        "side": side,
        "types": [kind],
        "confirmation_timestamp": when.isoformat(),
        "structural_price_ticks": 1000,
    }


def _analysis(anchor: datetime, *, m120_time=None, m30_time=None, m5_time=None, m5_support=False, m5_sell_time=None):
    if m120_time is None:
        m120_time = anchor + timedelta(minutes=10)
    if m30_time is None:
        m30_time = anchor + timedelta(minutes=20)
    if m5_time is None:
        m5_time = anchor + timedelta(minutes=30)
    m5_signals = [] if m5_time is False else [_formal("BUY", "SECOND_BUY", m5_time, "m5-buy")]
    if m5_sell_time is not None:
        m5_signals.append(_formal("SELL", "FIRST_SELL", m5_sell_time, "m5-sell"))
    return {
        "timeframes": {
            "120m": {"status": "OK", "signals": [_formal("BUY", "SECOND_BUY", m120_time, "m120-buy")]},
            "30m": {"status": "OK", "signals": [_formal("BUY", "SECOND_BUY", m30_time, "m30-buy")]},
            "5m": {
                "status": "OK",
                "signals": m5_signals,
                "technical": {"confirmation": "SUPPORT" if m5_support else "NEUTRAL"},
            },
        }
    }


def test_old_lower_buy_before_current_daily_structural_anchor_cannot_confirm_new_authority():
    anchor = datetime(2026, 9, 10, 10, 0, tzinfo=CN)
    as_of = anchor + timedelta(hours=2)
    analysis = _analysis(anchor, m120_time=anchor - timedelta(minutes=5))
    maturity, reasons, evidence = _structural_execution_maturity(
        analysis,
        as_of=as_of,
        signal_type="SECOND_BUY",
        authority_anchor=anchor,
    )
    assert maturity == "WATCH"
    assert reasons == ["M120_CONFIRMATION_MISSING"]
    assert "120m" not in evidence


def test_lower_buys_after_daily_structural_anchor_can_confirm_even_before_daily_final_confirmation():
    anchor = datetime(2026, 9, 10, 9, 30, tzinfo=CN)
    daily_confirmation = anchor + timedelta(hours=2)
    analysis = _analysis(anchor)
    maturity, reasons, evidence = _structural_execution_maturity(
        analysis,
        as_of=daily_confirmation + timedelta(minutes=5),
        signal_type="SECOND_BUY",
        authority_anchor=anchor,
    )
    assert maturity == "TRIGGERED"
    assert reasons == []
    assert set(evidence) == {"120m", "30m", "5m"}
    assert all(datetime.fromisoformat(item["confirmation_timestamp"]) < daily_confirmation for item in evidence.values())


def test_5m_support_cannot_substitute_for_missing_formal_5m_buy():
    anchor = datetime(2026, 9, 10, 10, 0, tzinfo=CN)
    analysis = _analysis(anchor, m5_time=False, m5_support=True)
    maturity, reasons, evidence = _structural_execution_maturity(
        analysis,
        as_of=anchor + timedelta(hours=2),
        signal_type="SECOND_BUY",
        authority_anchor=anchor,
    )
    assert maturity == "PREPARE"
    assert reasons == ["M5_EXECUTION_TRIGGER_MISSING"]
    assert set(evidence) == {"120m", "30m"}


def test_newer_5m_sell_invalidates_the_formal_execution_buy():
    anchor = datetime(2026, 9, 10, 10, 0, tzinfo=CN)
    analysis = _analysis(anchor, m5_sell_time=anchor + timedelta(minutes=40))
    maturity, reasons, evidence = _structural_execution_maturity(
        analysis,
        as_of=anchor + timedelta(hours=2),
        signal_type="SECOND_BUY",
        authority_anchor=anchor,
    )
    assert maturity == "PREPARE"
    assert reasons == ["M5_EXECUTION_TRIGGER_MISSING"]
    assert set(evidence) == {"120m", "30m"}


def test_indicator_pause_can_block_existing_formal_chain_but_indicator_support_never_creates_it():
    anchor = datetime(2026, 9, 10, 10, 0, tzinfo=CN)
    analysis = _analysis(anchor)
    analysis["timeframes"]["5m"]["technical"]["confirmation"] = "PAUSE"
    maturity, reasons, evidence = _structural_execution_maturity(
        analysis,
        as_of=anchor + timedelta(hours=2),
        signal_type="SECOND_BUY",
        authority_anchor=anchor,
    )
    assert maturity == "WATCH"
    assert reasons == ["TECHNICAL_EXECUTION_PAUSED"]
    assert set(evidence) == {"120m", "30m", "5m"}


def test_staged_entry_payload_has_no_static_grade_based_initial_fraction():
    plan = _staged_entry_payload(signal_type="SECOND_BUY")
    by_role = {item["role"]: item for item in plan}
    assert by_role["TEST"]["capacity_fraction"] is None
    assert by_role["TEST"]["capacity_basis"] == "EXPLICIT_TEST_RISK"
    assert by_role["CONFIRMATION"]["capacity_fraction"] == 0.30
    assert by_role["CORE"]["capacity_fraction"] == 0.50
    assert by_role["TREND_ADD"]["capacity_fraction"] == 0.25
    assert all("target_fraction" not in item for item in plan)


def test_legacy_scan_no_longer_imports_grade_based_position_sizing_path():
    source = inspect.getsource(scan_v2)
    assert "build_position_plan" not in source
    assert "tranche_fraction" not in source
    assert "OpportunityGrade" not in source
    assert "RiskState" not in source
