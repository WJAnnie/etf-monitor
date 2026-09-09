from __future__ import annotations

from typing import Mapping

import requests

from trading_skill.industry_financial_metrics import compare_snapshots, statement_snapshot


F10_URLS = (
    "https://datacenter.eastmoney.com/securities/api/data/v1/get",
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
)
DATACENTER_URLS = (
    "https://datacenter-web.eastmoney.com/api/data/v1/get",
    "https://datacenter.eastmoney.com/securities/api/data/v1/get",
)
TIMEOUT = 8.0


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
            return [row for row in data if isinstance(row, dict)], url
        except Exception as exc:
            errors.append(f"{url}:{exc}")
    raise RuntimeError("；".join(errors))


def secucode(code: str, board: str) -> str:
    suffix = "SH" if str(board) == "SH_MAIN" or str(code).startswith("6") else "SZ"
    return f"{code}.{suffix}"


def fetch_main_financial_data(code: str, board: str, *, page_size: int = 12) -> tuple[list[dict], str]:
    """F10 主财务数据：金融行业专属指标和通用财务指标共用这一源。"""
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


def _fetch_statement_rows(code: str, report_name: str, *, page_size: int = 8) -> tuple[list[dict], str]:
    params = {
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "pageSize": page_size,
        "pageNumber": 1,
        "reportName": report_name,
        "columns": "ALL",
        "filter": f'(SECURITY_CODE="{code}")',
    }
    return _request_rows(DATACENTER_URLS, params, f10=False)


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


def fetch_detailed_statement_metrics(code: str, *, target_report_date: str | None = None) -> dict:
    """统一抓资产负债表+现金流，再交给纯逻辑层做行业证据判断。"""
    balance_rows, balance_source = _fetch_statement_rows(code, "RPT_DMSK_FN_BALANCE")
    cash_rows, cash_source = _fetch_statement_rows(code, "RPT_DMSK_FN_CASHFLOW")

    cur_balance, prev_balance = _choose_current_previous(balance_rows, target_report_date)
    cur_cash, prev_cash = _choose_current_previous(cash_rows, target_report_date)
    current = statement_snapshot(cur_balance, cur_cash)
    previous = statement_snapshot(prev_balance, prev_cash) if (prev_balance or prev_cash) else None
    return {
        "current": current,
        "previous": previous,
        "changes": compare_snapshots(current, previous),
        "sources": {"balance": balance_source, "cashflow": cash_source},
        "data_note": "合同负债、在建工程、资本开支、库存、应收和现金流只是行业专属证据的一部分；不得把会计代理指标冒充真实订单、商品价格、管线或动销。",
    }
