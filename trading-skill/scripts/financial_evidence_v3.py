from __future__ import annotations

import time
from typing import Iterable

import requests

from scripts.collect_full_a_universe import fetch_financial_reports

F10_DATACENTER = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
F10_TIMEOUT = 8
F10_RETRIES = 2

REPORTS = {
    "general": ("RPT_F10_FINANCE_GBALANCE", "RPT_F10_FINANCE_GINCOME", "RPT_F10_FINANCE_GCASHFLOW"),
    "bank": ("RPT_F10_FINANCE_BBALANCE", "RPT_F10_FINANCE_BINCOME", "RPT_F10_FINANCE_BCASHFLOW"),
    "insurance": ("RPT_F10_FINANCE_IBALANCE", "RPT_F10_FINANCE_IINCOME", "RPT_F10_FINANCE_ICASHFLOW"),
    "security": ("RPT_F10_FINANCE_SBALANCE", "RPT_F10_FINANCE_SINCOME", "RPT_F10_FINANCE_SCASHFLOW"),
}
MAIN_REPORT = "RPT_F10_FINANCE_MAINFINADATA"


def company_type_for_industry(industry_name: str | None) -> str:
    text = str(industry_name or "")
    if "银行" in text:
        return "bank"
    if "保险" in text:
        return "insurance"
    if "证券" in text or "券商" in text:
        return "security"
    return "general"


def secucode_for_a_share(code: str) -> str:
    code = str(code).strip()
    if len(code) != 6 or not code.isdigit():
        raise ValueError(f"INVALID_A_SHARE_CODE:{code}")
    if code[0] in {"6", "9"}:
        suffix = "SH"
    elif code[0] in {"4", "8"}:
        suffix = "BJ"
    else:
        suffix = "SZ"
    return f"{code}.{suffix}"


def _headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": "https://data.eastmoney.com/",
        "Connection": "close",
    }


def _query(report_name: str, secucode: str, *, page_size: int = 16) -> list[dict]:
    params = {
        "reportName": report_name,
        "columns": "ALL",
        "filter": f'(SECUCODE="{secucode}")',
        "pageNumber": 1,
        "pageSize": page_size,
        "sortColumns": "REPORT_DATE",
        "sortTypes": "-1",
        "source": "HSF10",
        "client": "PC",
    }
    last: Exception | None = None
    for attempt in range(F10_RETRIES):
        try:
            response = requests.get(F10_DATACENTER, params=params, headers=_headers(), timeout=F10_TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("result") or {}
            return [row for row in (result.get("data") or []) if isinstance(row, dict)]
        except Exception as exc:
            last = exc
            if attempt + 1 < F10_RETRIES:
                time.sleep(0.35 * (attempt + 1))
    raise RuntimeError(f"F10_QUERY_FAILED:{report_name}:{last}")


def _report_date(row: dict) -> str:
    return str(row.get("REPORT_DATE") or row.get("REPORTDATE") or "")[:10]


def _rows_by_date(rows: Iterable[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in rows:
        stamp = _report_date(row)
        if stamp and stamp not in out:
            out[stamp] = row
    return out


def _merge_detail(base_rows: list[dict], detail_sets: list[list[dict]], *, company_type: str, errors: list[str]) -> list[dict]:
    detail_maps = [_rows_by_date(rows) for rows in detail_sets]
    merged: list[dict] = []
    for base in base_rows:
        row = dict(base)
        stamp = _report_date(row)
        matched = 0
        for mapping in detail_maps:
            detail = mapping.get(stamp)
            if detail:
                matched += 1
                for key, value in detail.items():
                    if value not in (None, "") and row.get(key) in (None, ""):
                        row[key] = value
        row["FINANCIAL_COMPANY_TYPE"] = company_type
        row["FINANCIAL_DETAIL_STATUS"] = (
            "COMPLETE" if matched == len(detail_maps) and not errors
            else "PARTIAL" if matched
            else "BASE_ONLY"
        )
        if errors:
            row["FINANCIAL_DETAIL_ERRORS"] = tuple(errors)
        merged.append(row)
    return merged


def fetch_financial_evidence(code: str, industry_name: str | None) -> list[dict]:
    """Merge CPD summary with PIT-visible F10 statement evidence.

    Supplemental failures are non-fatal. They are surfaced through FINANCIAL_DETAIL_STATUS
    so the decision layer can fail closed for direct entry while preserving observation.
    """
    base_rows = fetch_financial_reports(code)
    if not base_rows:
        return []
    company_type = company_type_for_industry(industry_name)
    secucode = secucode_for_a_share(code)
    balance_report, income_report, cashflow_report = REPORTS[company_type]
    reports = (MAIN_REPORT, balance_report, income_report, cashflow_report)
    detail_sets: list[list[dict]] = []
    errors: list[str] = []
    for report in reports:
        try:
            detail_sets.append(_query(report, secucode))
        except Exception as exc:
            detail_sets.append([])
            errors.append(str(exc))
    return _merge_detail(base_rows, detail_sets, company_type=company_type, errors=errors)
