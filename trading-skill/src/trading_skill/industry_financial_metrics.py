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


PROFILE_METRIC_PRIORITY: dict[str, tuple[str, ...]] = {
    "创新药/生物医药": (
        "monetary_funds_change_pct", "operating_cash_flow_change_pct", "accounts_receivable_change_pct",
    ),
    "造船与海工": (
        "contract_liabilities_change_pct", "construction_in_progress_change_pct", "fixed_asset_change_pct",
        "inventory_change_pct", "construct_long_asset_cash_change_pct",
    ),
    "半导体设备与材料": (
        "construction_in_progress_change_pct", "construct_long_asset_cash_change_pct", "inventory_change_pct",
        "contract_liabilities_change_pct", "accounts_receivable_change_pct",
    ),
    "AI基础设施/通信硬件": (
        "contract_liabilities_change_pct", "inventory_change_pct", "construction_in_progress_change_pct",
        "accounts_receivable_change_pct", "operating_cash_flow_change_pct",
    ),
    "机器人与高端自动化": (
        "contract_liabilities_change_pct", "construction_in_progress_change_pct", "fixed_asset_change_pct",
        "inventory_change_pct", "accounts_receivable_change_pct",
    ),
    "电网设备与储能": (
        "contract_liabilities_change_pct", "accounts_receivable_change_pct", "operating_cash_flow_change_pct",
        "construction_in_progress_change_pct",
    ),
    "商业航天与军工电子": (
        "contract_liabilities_change_pct", "inventory_change_pct", "accounts_receivable_change_pct",
        "construction_in_progress_change_pct",
    ),
    "智能驾驶与汽车电子": (
        "inventory_change_pct", "accounts_receivable_change_pct", "construction_in_progress_change_pct",
        "operating_cash_flow_change_pct",
    ),
    "医疗器械": (
        "accounts_receivable_change_pct", "inventory_change_pct", "operating_cash_flow_change_pct",
        "construction_in_progress_change_pct",
    ),
    "先进能源装备": (
        "contract_liabilities_change_pct", "construction_in_progress_change_pct", "fixed_asset_change_pct",
        "construct_long_asset_cash_change_pct", "operating_cash_flow_change_pct",
    ),
    "光伏与新能源制造": (
        "inventory_change_pct", "construction_in_progress_change_pct", "fixed_asset_change_pct",
        "construct_long_asset_cash_change_pct", "operating_cash_flow_change_pct",
    ),
    "新材料/周期制造": (
        "construction_in_progress_change_pct", "fixed_asset_change_pct", "inventory_change_pct",
        "construct_long_asset_cash_change_pct", "operating_cash_flow_change_pct",
    ),
    "化工/橡胶": (
        "inventory_change_pct", "construction_in_progress_change_pct", "fixed_asset_change_pct",
        "construct_long_asset_cash_change_pct", "operating_cash_flow_change_pct",
    ),
    "工业软件/网络安全": (
        "contract_liabilities_change_pct", "accounts_receivable_change_pct", "operating_cash_flow_change_pct",
        "monetary_funds_change_pct",
    ),
    "影视院线/传媒": (
        "operating_cash_flow_change_pct", "accounts_receivable_change_pct", "monetary_funds_change_pct",
    ),
    "零售/专业连锁": (
        "inventory_change_pct", "operating_cash_flow_change_pct", "accounts_receivable_change_pct",
        "monetary_funds_change_pct",
    ),
    "食品饮料/白酒": (
        "contract_liabilities_change_pct", "inventory_change_pct", "operating_cash_flow_change_pct",
        "monetary_funds_change_pct",
    ),
    "家电/消费电子": (
        "inventory_change_pct", "accounts_receivable_change_pct", "operating_cash_flow_change_pct",
        "construct_long_asset_cash_change_pct",
    ),
    "机械/工程机械": (
        "accounts_receivable_change_pct", "inventory_change_pct", "contract_liabilities_change_pct",
        "operating_cash_flow_change_pct", "construction_in_progress_change_pct",
    ),
    "公用事业/电力": (
        "fixed_asset_change_pct", "construction_in_progress_change_pct", "construct_long_asset_cash_change_pct",
        "operating_cash_flow_change_pct",
    ),
    "港口/航运": (
        "fixed_asset_change_pct", "construct_long_asset_cash_change_pct", "operating_cash_flow_change_pct",
        "construction_in_progress_change_pct",
    ),
    "农业/养殖": (
        "inventory_change_pct", "operating_cash_flow_change_pct", "fixed_asset_change_pct",
        "construct_long_asset_cash_change_pct",
    ),
    "煤炭/油气/资源品": (
        "fixed_asset_change_pct", "construction_in_progress_change_pct", "construct_long_asset_cash_change_pct",
        "inventory_change_pct", "operating_cash_flow_change_pct",
    ),
    "地产/建筑重资产": (
        "contract_liabilities_change_pct", "accounts_receivable_change_pct", "inventory_change_pct",
        "operating_cash_flow_change_pct",
    ),
}

METRIC_CN = {
    "fixed_asset_change_pct": "固定资产同比",
    "construction_in_progress_change_pct": "在建工程同比",
    "contract_liabilities_change_pct": "合同负债同比",
    "inventory_change_pct": "存货同比",
    "accounts_receivable_change_pct": "应收账款同比",
    "monetary_funds_change_pct": "货币资金同比",
    "construct_long_asset_cash_change_pct": "资本开支现金同比",
    "operating_cash_flow_change_pct": "经营现金流同比",
}

SPECIALIZED_ONLY_PROFILES = {
    "银行": "银行应重点使用PB、ROE、净息差、不良率、拨备覆盖率和资本充足率；通用制造业资产负债表指标不能替代这些数据。",
    "券商/资产管理": "券商应重点使用PB、ROE、日均成交额、两融余额、投行业务、资管规模和自营收益；普通制造业订单/在建工程不适合作为核心指标。",
    "保险": "保险应重点使用P/EV、NBV、保费增速、投资收益率和偿付能力；普通PE和固定资产不能作为主判断。",
}


def summarize_sector_metrics(profile_name: str, metrics: Mapping[str, object] | None, *, limit: int = 4) -> list[str]:
    """按行业画像挑出真正有意义的财报字段，避免所有行业打印同一套制造业指标。"""
    if profile_name in SPECIALIZED_ONLY_PROFILES:
        return [SPECIALIZED_ONLY_PROFILES[profile_name]]
    if not metrics:
        return []
    changes = metrics.get("changes") if isinstance(metrics, Mapping) else None
    if not isinstance(changes, Mapping):
        return []
    priority = PROFILE_METRIC_PRIORITY.get(profile_name, (
        "operating_cash_flow_change_pct", "accounts_receivable_change_pct", "inventory_change_pct",
    ))
    out: list[str] = []
    for key in priority:
        value = changes.get(key)
        if value is None:
            continue
        try:
            number = float(value)
        except (TypeError, ValueError):
            continue
        direction = "增加" if number > 0 else "下降" if number < 0 else "持平"
        out.append(f"{METRIC_CN.get(key, key)}{number:+.2f}%（{direction}）")
        if len(out) >= limit:
            break
    return out
