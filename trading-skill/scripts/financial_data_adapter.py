from __future__ import annotations

from typing import Mapping

import requests

from trading_skill.industry_financial_metrics import compare_snapshots, statement_snapshot


F10_URLS = (
    "https://datacenter.eastmoney.com/securities/api/data/v1/get",
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
)
LEGACY_DATACENTER_URLS = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
    "https://datacenter.eastmoney.com/securities/api/data/v1/get",
)
TIMEOUT = 8.0

STATEMENT_REPORTS = {
    "general": (
        "RPT_F10_FINANCE_GBALANCE",
        "RPT_F10_FINANCE_GINCOME",
        "RPT_F10_FINANCE_GCASHFLOW",
    ),
    "bank": (
        "RPT_F10_FINANCE_BBALANCE",
        "RPT_F10_FINANCE_BINCOME",
        "RPT_F10_FINANCE_BCASHFLOW",
    ),
    "insurance": (
        "RPT_F10_FINANCE_IBALANCE",
        "RPT_F10_FINANCE_IINCOME",
        "RPT_F10_FINANCE_ICASHFLOW",
    ),
    "security": (
        "RPT_F10_FINANCE_SBALANCE",
        "RPT_F10_FINANCE_SINCOME",
        "RPT_F10_FINANCE_SCASHFLOW",
    ),
}


def _headers(*, f10: bool = False) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://emweb.securities.eastmoney.com/" if f10 else "https://quote.eastmoney.com/",
        "Origin": "https://emweb.securities.eastmoney.com" if f10 else "https://quote.eastmoney.com",
        "Connection": "close",
    }


def _request_rows(urls: tuple[str, ...], params: dict[str, object], *, f10: bool = False) -> tuple[list[dict], str]:
    errors: list[str] = []
    empty_sources: list[str] = []
    for url in urls:
        try:
            response = requests.get(url, params=params, headers=_headers(f10=f10), timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") if isinstance(payload, dict) else None
            if not isinstance(result, Mapping):
                errors.append(f"{url}:empty-result")
                continue
            data = result.get("data") or []
            if not isinstance(data, list):
                errors.append(f"{url}:invalid-data")
                continue
            rows = [row for row in data if isinstance(row, dict)]
            if rows:
                return rows, url
            empty_sources.append(url)
        except Exception as exc:
            errors.append(f"{url}:{exc}")
    if empty_sources and not errors:
        return [], empty_sources[0]
    raise RuntimeError("；".join(errors + [f"{url}:empty-data" for url in empty_sources]))


def secucode(code: str, board: str = "") -> str:
    """Resolve A-share F10 SECUCODE without collapsing Beijing shares into SZ."""
    value = str(code).strip()
    board_text = str(board or "").upper()
    if board_text.startswith("BJ") or value.startswith(("4", "8")):
        suffix = "BJ"
    elif board_text.startswith("SH") or value.startswith(("6", "9")):
        suffix = "SH"
    else:
        suffix = "SZ"
    return f"{value}.{suffix}"


def _profile_company_type(profile: str | None) -> str:
    text = str(profile or "")
    if text == "银行" or "银行" in text:
        return "bank"
    if text == "保险" or "保险" in text:
        return "insurance"
    if text in {"券商/资产管理", "证券"} or "券商" in text or "证券" in text:
        return "security"
    return "general"


def fetch_main_financial_data(code: str, board: str, *, page_size: int = 12) -> tuple[list[dict], str]:
    """F10 main financial data: universal ratios plus financial-sector specific fields."""
    security = secucode(code, board)
    params = {
        "reportName": "RPT_F10_FINANCE_MAINFINADATA",
        "columns": "ALL",
        "quoteColumns": "",
        "filter": f'(SECUCODE="{security}")',
        "pageNumber": 1,
        "pageSize": page_size,
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "HSF10",
        "client": "PC",
    }
    return _request_rows(F10_URLS, params, f10=True)


def _fetch_f10_statement_rows(
    code: str,
    board: str,
    report_name: str,
    *,
    page_size: int = 12,
) -> tuple[list[dict], str]:
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "filter": f'(SECUCODE="{secucode(code, board)}")',
        "pageNumber": 1,
        "pageSize": page_size,
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "HSF10",
        "client": "PC",
    }
    rows, source = _request_rows(F10_URLS, params, f10=True)
    return rows, f"F10:{report_name}@{source}"


def _fetch_legacy_statement_rows(code: str, report_name: str, *, page_size: int = 8) -> tuple[list[dict], str]:
    params = {
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "pageSize": page_size,
        "pageNumber": 1,
        "reportName": report_name,
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    rows, source = _request_rows(LEGACY_DATACENTER_URLS, params, f10=False)
    return rows, f"LEGACY:{report_name}@{source}"


def _fetch_statement_with_fallback(
    code: str,
    board: str,
    *,
    f10_report: str,
    legacy_report: str,
) -> tuple[list[dict], str, tuple[str, ...]]:
    warnings: list[str] = []
    try:
        rows, source = _fetch_f10_statement_rows(code, board, f10_report)
        if rows:
            return rows, source, ()
        warnings.append(f"{f10_report}:empty")
    except Exception as exc:
        warnings.append(f"{f10_report}:{exc}")
    try:
        rows, source = _fetch_legacy_statement_rows(code, legacy_report)
        if rows:
            return rows, source, tuple(warnings)
        warnings.append(f"{legacy_report}:empty")
    except Exception as exc:
        warnings.append(f"{legacy_report}:{exc}")
    raise RuntimeError("；".join(warnings))


def _row_date(row: Mapping[str, object]) -> str:
    return str(row.get("REPORT_DATE") or row.get("REPORTDATE") or "")[:10]


def _choose_current_previous(rows: list[dict], target_report_date: str | None) -> tuple[dict | None, dict | None]:
    if not rows:
        return None, None
    ordered = sorted(rows, key=_row_date, reverse=True)
    current = None
    if target_report_date:
        current = next((row for row in ordered if _row_date(row) == target_report_date), None)
    current = current or ordered[0]
    current_date = _row_date(current)

    previous = None
    if current_date:
        try:
            year = int(current_date[:4])
            prior_same_period = f"{year - 1}{current_date[4:]}"
            previous = next((row for row in ordered if _row_date(row) == prior_same_period), None)
        except (TypeError, ValueError):
            previous = None
    if previous is None:
        previous = next((row for row in ordered if _row_date(row) < current_date), None)
    return current, previous


def fetch_detailed_statement_metrics(
    code: str,
    *,
    board: str = "",
    target_report_date: str | None = None,
    profile: str | None = None,
) -> dict:
    """Fetch balance + income + cashflow evidence, preferring official F10 report routing.

    The old DMSK reports stay as a compatibility fallback because they have already been
    exercised in production. F10 is primary so real inventory/CIP/contract liabilities,
    R&D expense and capex evidence are aligned by report date and company type.
    """
    company_type = _profile_company_type(profile)
    balance_report, income_report, cashflow_report = STATEMENT_REPORTS[company_type]

    balance_rows, balance_source, balance_warnings = _fetch_statement_with_fallback(
        code,
        board,
        f10_report=balance_report,
        legacy_report="RPT_DMSK_FN_BALANCE",
    )
    income_rows, income_source, income_warnings = _fetch_statement_with_fallback(
        code,
        board,
        f10_report=income_report,
        legacy_report="RPT_DMSK_FN_INCOME",
    )
    cash_rows, cash_source, cash_warnings = _fetch_statement_with_fallback(
        code,
        board,
        f10_report=cashflow_report,
        legacy_report="RPT_DMSK_FN_CASHFLOW",
    )

    cur_balance, prev_balance = _choose_current_previous(balance_rows, target_report_date)
    cur_income, prev_income = _choose_current_previous(income_rows, target_report_date)
    cur_cash, prev_cash = _choose_current_previous(cash_rows, target_report_date)
    current = statement_snapshot(cur_balance, cur_cash, cur_income)
    previous = statement_snapshot(prev_balance, prev_cash, prev_income) if (prev_balance or prev_cash or prev_income) else None
    warnings = tuple(dict.fromkeys(balance_warnings + income_warnings + cash_warnings))
    return {
        "current": current,
        "previous": previous,
        "changes": compare_snapshots(current, previous),
        "sources": {
            "balance": balance_source,
            "income": income_source,
            "cashflow": cash_source,
        },
        "warnings": warnings,
        "company_type": company_type,
        "data_note": (
            "合同负债、在建工程、资本开支、库存、应收、研发费用和现金流只是行业专属证据的一部分；"
            "不得把会计代理指标冒充真实订单、商品价格、研发管线、客户验证或动销。"
        ),
    }
