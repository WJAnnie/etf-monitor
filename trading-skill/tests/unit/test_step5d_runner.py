from __future__ import annotations

import json
import sys

from scripts import analyze_step5d_execution_intent as runner


def _write(path, payload):
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _sizing_payload():
    return {
        "symbols": [
            {
                "market": 1,
                "code": "600000",
                "name": "浦发银行",
                "security_type": "STOCK",
                "permission": {
                    "state": "ELIGIBLE",
                    "entry_mode": "STANDARD",
                    "new_entry_allowed": True,
                    "selected_timeframe": "120m",
                    "signal_id": "sig-1",
                    "signal_type": "SECOND_BUY",
                },
                "structural_stop": {
                    "found": True,
                    "valid_for_new_entry": True,
                    "timeframe": "120m",
                    "signal_id": "sig-1",
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
        ]
    }


def test_runner_no_allocation_does_not_require_reservation_context(tmp_path, monkeypatch):
    allocation = tmp_path / "allocation.json"
    sizing = tmp_path / "sizing.json"
    output = tmp_path / "out.json"
    _write(
        allocation,
        {
            "plan": {
                "state": "NO_ELIGIBLE",
                "snapshot_id": None,
                "allocations": [
                    {"identity": "1:600000:STOCK", "state": "NOT_SIZED"}
                ],
            }
        },
    )
    _write(sizing, {"symbols": []})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step5d",
            "--allocation",
            str(allocation),
            "--sizing",
            str(sizing),
            "--output",
            str(output),
            "--strict",
        ],
    )

    assert runner.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["reservation_plan_state"] == "NO_ALLOCATION"
    assert payload["summary"]["durable_reservation_confirmed"] is False
    assert payload["summary"]["broker_order_created"] is False


def test_runner_matching_snapshot_is_ready_not_fake_reserved(tmp_path, monkeypatch):
    allocation = tmp_path / "allocation.json"
    sizing = tmp_path / "sizing.json"
    context = tmp_path / "reservation.json"
    output = tmp_path / "out.json"
    _write(
        allocation,
        {
            "plan": {
                "state": "ALLOCATED",
                "snapshot_id": "snap-1",
                "allocations": [
                    {
                        "identity": "1:600000:STOCK",
                        "code": "600000",
                        "state": "ALLOCATED_FULL",
                        "allocated_quantity": 1000,
                        "entry_price": "10",
                        "risk_per_unit": "0.8",
                        "allocated_value_cny": "10000",
                        "allocated_risk_cny": "800",
                    }
                ],
            }
        },
    )
    _write(sizing, _sizing_payload())
    _write(context, {"current_snapshot_id": "snap-1", "existing_reservations": {}})
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "step5d",
            "--allocation",
            str(allocation),
            "--sizing",
            str(sizing),
            "--reservation-context",
            str(context),
            "--output",
            str(output),
            "--strict",
        ],
    )

    assert runner.main() == 0
    payload = json.loads(output.read_text(encoding="utf-8"))
    assert payload["summary"]["reservation_plan_state"] == "READY_TO_RESERVE"
    assert payload["summary"]["execution_intents"] == 1
    assert payload["summary"]["reservation_key_created"] is True
    assert payload["summary"]["durable_reservation_confirmed"] is False
    assert payload["summary"]["broker_order_created"] is False
