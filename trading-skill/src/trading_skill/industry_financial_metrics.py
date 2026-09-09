from __future__ import annotations

from typing import Mapping


def _num(row: Mapping[str, object], *keys: str) -> float | None:
    for key in keys:
        value = row.get(key)
        if value in (None, "", "-"):
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _ratio(numerator: float | None, denominator: float | None) -> float | None:
    if numerator is None or denominator in (None, 0):
        return None
    return round(numerator / denominator * 100.0, 2)


def statement_snapshot(balance: Mapping[str, object] | None, cashflow: Mapping[str, object] | None) -> dict:
    balance = balance or {}
    cashflow = cashflow or {}
    total_assets = _num(balance, "TOTAL_ASSETS")
    total_liabilities = _num(balance, "TOTAL_LIABILITIES")
    fixed_asset = _num(balance, "FIXED_ASSET")
    cip = _num(balance, "CIP")
    contract_liab = _num(balance, "CONTRACT_LIAB", "ADVANCE_RECEIVABLES")
    inventory = _num(balance, "INVENTORY")
    receivables = _num(balance, "ACCOUNTS_RECE", "NOTE_ACCOUNTS_RECE")
    cash = _num(balance, "MONETARYFUNDS")
    short_loan = _num(balance, "SHORT_LOAN")
    long_loan = _num(balance, "LONG_LOAN")
    capex_cash = _num(cashflow, "CONSTRUCT_LONG_ASSET")
    operating_cash = _num(cashflow, "NETCASH_OPERATE")
    return {
        "report_date": str(balance.get("REPORT_DATE") or balance.get("REPORTDATE") or cashflow.get("REPORT_DATE") or cashflow.get("REPORTDATE") or "")[:10],
        "total_assets": total_assets,
        "total_liabilities": total_liabilities,
        "debt_asset_ratio_pct": _ratio(total_liabilities, total_assets),
        "fixed_asset": fixed_asset,
        "fixed_asset_to_assets_pct": _ratio(fixed_asset, total_assets),
        "construction_in_progress": cip,
        "cip_to_assets_pct": _ratio(cip, total_assets),
        "contract_liabilities": contract_liab,
        "contract_liabilities_to_assets_pct": _ratio(contract_liab, total_assets),
        "inventory": inventory,
        "inventory_to_assets_pct": _ratio(inventory, total_assets),
        "accounts_receivable": receivables,
        "receivables_to_assets_pct": _ratio(receivables, total_assets),
        "monetary_funds": cash,
        "cash_to_assets_pct": _ratio(cash, total_assets),
        "short_loan": short_loan,
        "long_loan": long_loan,
        "construct_long_asset_cash": capex_cash,
        "operating_cash_flow": operating_cash,
        "notes": {
            "contract_liabilities": "合同负债可辅助观察订单/预收变化，但不能等同于真实订单量。",
            "construct_long_asset_cash": "购建固定资产、无形资产和其他长期资产支付的现金，用于观察资本开支强度。",
        },
    }


def compare_snapshots(current: Mapping[str, object], previous: Mapping[str, object] | None) -> dict:
    if not previous:
        return {}
    fields = (
        "fixed_asset", "construction_in_progress", "contract_liabilities", "inventory",
        "accounts_receivable", "monetary_funds", "construct_long_asset_cash", "operating_cash_flow",
    )
    out = {}
    for field in fields:
        cur = current.get(field)
        prev = previous.get(field)
        if cur is None or prev in (None, 0):
            continue
        try:
            out[field + "_change_pct"] = round((float(cur) / float(prev) - 1.0) * 100.0, 2)
        except (TypeError, ValueError, ZeroDivisionError):
            continue
    return out
