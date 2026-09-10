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

# 场内基金备用规模证据。f38=最新份额，f441=IOPV实时估值，f20=总市值。
# 优先用“最新份额×IOPV”，其次“最新份额×市价”，最后才用交易所行情总市值。
EASTMONEY_FUND_FS = (
    "b:MK0021,b:MK0022,b:MK0023,b:MK0024,b:MK0827,"
    "b:MK0404,b:MK0405,b:MK0406,b:MK0407"
)
EASTMONEY_FUND_QUOTE_HOSTS = (
    "https://push2delay.eastmoney.com/api/qt/clist/get",
    "https://88.push2.eastmoney.com/api/qt/clist/get",
    "https://push2.eastmoney.com/api/qt/clist/get",
)
EASTMONEY_FUND_FIELDS = "f2,f12,f13,f14,f20,f38,f124,f297,f441"
EASTMONEY_UT = "bd1d9ddb04089700cf9c27f6f7426281"


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
        "fund_size_basis": "recent_shares_x_nav" if size_cny is not None else None,
        "fund_size_estimated": False if size_cny is not None else None,
    }
    return code, reference


def _eastmoney_as_of(row: dict) -> str | None:
    data_date = row.get("f297")
    if data_date not in (None, "", "-"):
        text = str(data_date)
        if len(text) == 8 and text.isdigit():
            return f"{text[:4]}-{text[4:6]}-{text[6:]}"
        return text
    update_ts = _num(row.get("f124"))
    if update_ts is not None and update_ts > 0:
        return str(int(update_ts))
    return None


def _reference_from_eastmoney_row(row: dict) -> tuple[str, dict] | None:
    code = str(row.get("f12") or "").strip()
    if not code:
        return None

    shares = _num(row.get("f38"))
    iopv = _num(row.get("f441"))
    market_price = _num(row.get("f2"))
    market_cap = _num(row.get("f20"))

    size_cny: float | None = None
    basis: str | None = None
    reference_price: float | None = None
    if shares is not None and shares > 0 and iopv is not None and iopv > 0:
        reference_price = iopv
        size_cny = shares * iopv
        basis = "latest_shares_x_iopv"
    elif shares is not None and shares > 0 and market_price is not None and market_price > 0:
        reference_price = market_price
        size_cny = shares * market_price
        basis = "latest_shares_x_market_price"
    elif market_cap is not None and market_cap > 0:
        size_cny = market_cap
        basis = "exchange_total_market_cap"

    if size_cny is None:
        return None

    return code, {
        "fund_size_cny": size_cny,
        "fund_nav_reference": iopv if iopv is not None and iopv > 0 else None,
        "fund_market_price_reference": market_price if market_price is not None and market_price > 0 else None,
        "fund_recent_shares": shares if shares is not None and shares > 0 else None,
        "fund_size_as_of": _eastmoney_as_of(row),
        "fund_size_source": "东方财富场内基金份额/市值批量接口",
        "fund_size_basis": basis,
        "fund_size_estimated": True,
        "fund_size_reference_price": reference_price,
    }


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


def _fetch_eastmoney_page(host: str, page: int, *, timeout: float = 12.0) -> dict:
    params = {
        "pn": str(page),
        "pz": "500",
        "po": "1",
        "np": "1",
        "ut": EASTMONEY_UT,
        "fltt": "2",
        "invt": "2",
        "fid": "f12",
        "fs": EASTMONEY_FUND_FS,
        "fields": EASTMONEY_FUND_FIELDS,
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://quote.eastmoney.com/center/",
        "Connection": "close",
    }
    response = requests.get(host, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("data") is None:
        raise RuntimeError("东方财富场内基金批量行情无data")
    return payload


def _fetch_eastmoney_fund_rows(*, timeout: float = 12.0) -> list[dict]:
    errors: list[str] = []
    for host in EASTMONEY_FUND_QUOTE_HOSTS:
        try:
            first_payload = _fetch_eastmoney_page(host, 1, timeout=timeout)
            data = first_payload.get("data") or {}
            first = [row for row in (data.get("diff") or []) if isinstance(row, dict)]
            total = int(data.get("total") or len(first))
            if not first:
                raise RuntimeError("第一页为空")
            rows = list(first)
            pages = max(1, (total + 499) // 500)
            for page in range(2, pages + 1):
                payload = _fetch_eastmoney_page(host, page, timeout=timeout)
                rows.extend(row for row in ((payload.get("data") or {}).get("diff") or []) if isinstance(row, dict))
            if rows:
                return rows
        except Exception as exc:
            errors.append(f"{host}:{exc}")
    raise RuntimeError("；".join(errors) or "东方财富场内基金批量行情失败")


def _fetch_eastmoney_references(wanted: set[str]) -> tuple[dict[str, dict], list[str]]:
    if not wanted:
        return {}, []
    try:
        rows = _fetch_eastmoney_fund_rows()
    except Exception as exc:
        return {}, [f"EASTMONEY:{exc}"]

    references: dict[str, dict] = {}
    for row in rows:
        parsed = _reference_from_eastmoney_row(row)
        if parsed is None:
            continue
        code, reference = parsed
        if code in wanted:
            references[code] = reference
    return references, []


def fetch_fund_scale_references(codes: Iterable[str]) -> tuple[dict[str, dict], list[str]]:
    """Fetch slow fund-size evidence in batches, with exchange-fund fallback.

    Sina remains the preferred NAV×shares source. If one or more Sina fund classes
    time out, only still-missing exchange-traded codes are backfilled from Eastmoney's
    batch quote list. Missing evidence stays missing; no initial fundraising amount is
    used as current size.
    """
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

    missing = wanted - set(references)
    fallback_references, fallback_errors = _fetch_eastmoney_references(missing)
    references.update(fallback_references)
    errors.extend(fallback_errors)
    return references, errors
