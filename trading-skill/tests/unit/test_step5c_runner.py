from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _run(tmp_path: Path, sizing_payload: dict, context_payload: dict | None = None) -> tuple[subprocess.CompletedProcess[str], dict]:
    sizing = tmp_path / "step5b.json"
    output = tmp_path / "step5c.json"
    sizing.write_text(json.dumps(sizing_payload, ensure_ascii=False), encoding="utf-8")
    command = [
        sys.executable,
        "-m",
        "scripts.analyze_step5c_portfolio_allocation",
        "--sizing",
        str(sizing),
        "--output",
        str(output),
        "--strict",
    ]
    if context_payload is not None:
        context = tmp_path / "allocation_context.json"
        context.write_text(json.dumps(context_payload, ensure_ascii=False), encoding="utf-8")
        command.extend(["--allocation-context", str(context)])
    result = subprocess.run(command, text=True, capture_output=True, check=False)
    payload = json.loads(output.read_text(encoding="utf-8")) if output.exists() else {}
    return result, payload


def test_cli_no_sized_envelopes_needs_no_allocation_context(tmp_path: Path):
    symbols = [
        {
            "code": "600001",
            "name": "测试A",
            "market": 1,
            "security_type": "STOCK",
            "sizing": {"state": "NOT_ELIGIBLE", "quantity": 0},
        },
        {
            "code": "510300",
            "name": "测试ETF",
            "market": 1,
            "security_type": "ETF",
            "sizing": {"state": "NOT_ELIGIBLE", "quantity": 0},
        },
    ]
    result, payload = _run(tmp_path, {"symbols": symbols})

    assert result.returncode == 0, result.stderr + result.stdout
    assert payload["summary"]["plan_state"] == "NO_ELIGIBLE"
    assert payload["summary"]["allocated_symbols"] == 0
    assert payload["summary"]["allocated_value_cny"] == "0"
    assert payload["summary"]["allocated_risk_cny"] == "0"
    assert payload["summary"]["allocation_context_supplied"] is False
    assert payload["summary"]["durable_reservation_created"] is False
    assert payload["plan"]["start_capacity"] is None
    assert payload["plan"]["end_capacity"] is None
    assert [item["state"] for item in payload["plan"]["allocations"]] == ["NOT_SIZED", "NOT_SIZED"]


def test_cli_allocates_sized_envelopes_only_in_explicit_order(tmp_path: Path):
    a = {
        "code": "600001",
        "name": "测试A",
        "market": 1,
        "security_type": "STOCK",
        "sizing": {
            "state": "SIZED",
            "quantity": 1000,
            "lot_size": 100,
            "planned_entry_price": "10",
            "risk_per_unit": "1",
            "planned_value_cny": "10000",
            "planned_risk_cny": "1000",
        },
    }
    b = {
        "code": "600002",
        "name": "测试B",
        "market": 1,
        "security_type": "STOCK",
        "sizing": {
            "state": "SIZED",
            "quantity": 1000,
            "lot_size": 100,
            "planned_entry_price": "10",
            "risk_per_unit": "1",
            "planned_value_cny": "10000",
            "planned_risk_cny": "1000",
        },
    }
    context = {
        "snapshot_id": "snapshot-cli-1",
        "allocation_order": ["1:600001:STOCK", "1:600002:STOCK"],
        "cash_remaining_cny": "15000",
        "portfolio_risk_remaining_cny": "5000",
        "industry_risk_remaining_cny": {},
        "theme_risk_remaining_cny": {},
        "industry_value_remaining_cny": {},
        "theme_value_remaining_cny": {},
        "symbols": {
            "1:600001:STOCK": {"industry_key": None, "theme_keys": []},
            "1:600002:STOCK": {"industry_key": None, "theme_keys": []},
        },
    }
    result, payload = _run(tmp_path, {"symbols": [a, b]}, context)

    assert result.returncode == 0, result.stderr + result.stdout
    assert payload["summary"]["plan_state"] == "ALLOCATED"
    assert payload["summary"]["allocated_symbols"] == 2
    assert payload["summary"]["allocated_value_cny"] == "15000"
    allocations = {item["code"]: item for item in payload["plan"]["allocations"]}
    assert allocations["600001"]["allocated_quantity"] == 1000
    assert allocations["600001"]["state"] == "ALLOCATED_FULL"
    assert allocations["600002"]["allocated_quantity"] == 500
    assert allocations["600002"]["state"] == "ALLOCATED_PARTIAL"
    assert payload["plan"]["end_capacity"]["cash_cny"] == "0"
    assert payload["summary"]["durable_reservation_created"] is False
