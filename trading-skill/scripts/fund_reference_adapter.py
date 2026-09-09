from __future__ import annotations

import ast
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Iterable

import requests


SINA_FUND_SCALE_URL = (
    "https://vip.stock.finance.sina.com.cn/fund_center/data/jsonp.php/"
    "IO.XSRV2.CallbackList['J2cW8KXheoWKdSHc']/NetValueReturn_Service.NetValueReturnOpen"
)
SINA_FUND_TYPES = {
    "STOCK": "2",
    "MIXED": "1",
    "BOND": "3",
    "CASH": "5",
    "QDII": "6",
}


def _num(value: object) -> float | None:
    if value in (None, "", "-", "--"):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _parse_jsonp(text: str) -> dict:
    """Decode Sina's JSONP without adding a heavy demjson dependency."""
    start = text.find("({")
    if start >= 0:
        start += 1
    else:
        start = text.find("(") + 1
    end = text.rfind(")")
    if start <= 0 or end <= start:
        raise ValueError("invalid Sina fund-scale JSONP")
    payload = text[start:end].strip().rstrip(";")
    try:
        return json.loads(payload)
    except json.JSONDecodeError:
        # Sina occasionally emits JavaScript literals accepted by demjson. Keep the
        # fallback data-only and non-executable.
        normalized = re.sub(r"\bnull\b", "None", payload, flags=re.IGNORECASE)
        normalized = re.sub(r"\btrue\b", "True", normalized, flags=re.IGNORECASE)
        normalized = re.sub(r"\bfalse\b", "False", normalized, flags=re.IGNORECASE)
        value = ast.literal_eval(normalized)
        if not isinstance(value, dict):
            raise ValueError("Sina fund-scale payload is not an object")
        return value


def _reference_from_row(row: dict) -> tuple[str, dict] | None:
    code = str(row.get("symbol") or "").strip()
    if not code:
        return None
    nav = _num(row.get("dwjz"))
    shares = _num(row.get("zjzfe"))
    size_cny = nav * shares if nav is not None and shares is not None and nav > 0 and shares > 0 else None
    reference = {
        "fund_size_cny": size_cny,
        "fund_nav_reference": nav,
        "fund_recent_shares": shares,
        "fund_size_as_of": row.get("jzrq") or None,
        "fund_size_source": "新浪基金规模批量接口",
    }
    return code, reference


def _fetch_type(type_code: str, *, timeout: float = 12.0) -> list[dict]:
    params = {
        "page": "1",
        "num": "10000",
        "sort": "zmjgm",
        "asc": "0",
        "ccode": "",
        "type2": type_code,
        "type3": "",
    }
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://vip.stock.finance.sina.com.cn/"}
    response = requests.get(SINA_FUND_SCALE_URL, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = _parse_jsonp(response.text)
    return list(payload.get("data") or [])


def fetch_fund_scale_references(codes: Iterable[str]) -> tuple[dict[str, dict], list[str]]:
    """Fetch slow fund-size evidence in five batched requests, not one request per fund."""
    wanted = {str(code).strip() for code in codes if str(code).strip()}
    if not wanted:
        return {}, []

    references: dict[str, dict] = {}
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=len(SINA_FUND_TYPES)) as pool:
        futures = {pool.submit(_fetch_type, type_code): name for name, type_code in SINA_FUND_TYPES.items()}
        for future in as_completed(futures):
            name = futures[future]
            try:
                rows = future.result()
            except Exception as exc:
                errors.append(f"{name}:{exc}")
                continue
            for row in rows:
                parsed = _reference_from_row(row)
                if parsed is None:
                    continue
                code, reference = parsed
                if code not in wanted:
                    continue
                previous = references.get(code)
                if previous is None or (reference.get("fund_size_as_of") or "") >= (previous.get("fund_size_as_of") or ""):
                    references[code] = reference
    return references, errors
