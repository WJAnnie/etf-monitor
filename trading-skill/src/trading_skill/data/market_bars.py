from __future__ import annotations

import json
import random
import time
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta
from typing import Callable

import requests

from trading_skill.a_share_bars import aggregate_weekly, normalize_complete_m5, parse_cn_time


TIMEOUT = 10
RETRIES = 2
DAILY_LIMIT = 1200
M30_LIMIT = 1600
M5_LIMIT = 1200
MIN_DAILY = 120
MIN_M30 = 480
MIN_M5 = 500
MIN_M120 = 100
VALID_M30_ENDS = {
    dt_time(10, 0), dt_time(10, 30), dt_time(11, 0), dt_time(11, 30),
    dt_time(13, 30), dt_time(14, 0), dt_time(14, 30), dt_time(15, 0),
}


@dataclass(frozen=True, slots=True)
class SecurityIdentity:
    code: str
    name: str
    market: int
    security_type: str

    def __post_init__(self) -> None:
        if not self.code:
            raise ValueError("STEP4_IDENTITY_EMPTY_CODE")
        if self.market not in {0, 1}:
            raise ValueError("STEP4_IDENTITY_MARKET_REQUIRED")
        if not self.security_type:
            raise ValueError("STEP4_IDENTITY_SECURITY_TYPE_REQUIRED")


@dataclass(frozen=True, slots=True)
class BarCollection:
    identity: SecurityIdentity
    daily: tuple[dict, ...]
    weekly: tuple[dict, ...]
    m120: tuple[dict, ...]
    m30: tuple[dict, ...]
    m5: tuple[dict, ...]
    sources: dict[str, str]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict:
        return {
            "code": self.identity.code,
            "name": self.identity.name,
            "market": self.identity.market,
            "security_type": self.identity.security_type,
            "sources": dict(self.sources),
            "warnings": list(self.warnings),
            "daily": list(self.daily),
            "weekly": list(self.weekly),
            "120m": list(self.m120),
            "30m": list(self.m30),
            "5m": list(self.m5),
            "quality": {
                "daily_history_ok": len(self.daily) >= MIN_DAILY,
                "m30_history_ok": len(self.m30) >= MIN_M30,
                "m120_history_ok": len(self.m120) >= MIN_M120,
                "m5_history_ok": len(self.m5) >= MIN_M5,
                "price_basis": {
                    "weekly": _basis(self.weekly),
                    "daily": _basis(self.daily),
                    "120m": _basis(self.m120),
                    "30m": _basis(self.m30),
                    "5m": _basis(self.m5),
                },
            },
        }


def _headers(referer: str) -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/152 Safari/537.36",
        "Accept": "application/json,text/plain,*/*",
        "Referer": referer,
        "Connection": "close",
    }


def _request(url: str, *, params: dict | None = None, referer: str) -> requests.Response:
    last: Exception | None = None
    for attempt in range(RETRIES):
        try:
            response = requests.get(url, params=params, headers=_headers(referer), timeout=TIMEOUT)
            response.raise_for_status()
            return response
        except Exception as exc:
            last = exc
            if attempt + 1 < RETRIES:
                time.sleep(0.5 * (attempt + 1) + random.uniform(0.05, 0.2))
    raise RuntimeError(str(last))


def _decode_json_or_jsonp(text: str):
    raw = text.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    if "=(" in raw and raw.endswith(");"):
        return json.loads(raw.split("=(", 1)[1][:-2])
    if "=" in raw:
        body = raw.split("=", 1)[1].strip().rstrip(";")
        if body.startswith("(") and body.endswith(")"):
            body = body[1:-1]
        return json.loads(body)
    raise RuntimeError(f"非JSON响应:{raw[:100]}")


def _parse_array(rows: list) -> list[dict]:
    out: list[dict] = []
    for row in rows:
        parts = row.split(",") if isinstance(row, str) else row if isinstance(row, list) else None
        if not parts or len(parts) < 6:
            continue
        try:
            out.append({
                "time": str(parts[0]),
                "open": float(parts[1]),
                "close": float(parts[2]),
                "high": float(parts[3]),
                "low": float(parts[4]),
                "volume": float(parts[5] or 0),
                "amount": float(parts[6] or 0) if len(parts) > 6 else 0.0,
            })
        except (TypeError, ValueError):
            continue
    return out


def provider_symbol(identity: SecurityIdentity) -> str:
    # STEP4不再从代码前缀猜交易所；上游明确market是唯一身份来源。
    return f"{'sh' if identity.market == 1 else 'sz'}{identity.code}"


def secid(identity: SecurityIdentity) -> str:
    return f"{identity.market}.{identity.code}"


def _eastmoney(identity: SecurityIdentity, *, klt: str, limit: int) -> list[dict]:
    response = _request(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid(identity),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": klt,
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": str(limit),
        },
        referer="https://quote.eastmoney.com/",
    )
    payload = response.json()
    rows = _parse_array(((payload.get("data") or {}).get("klines") or []))
    for row in rows:
        row["_adjustment"] = "forward"
    return rows


def _tencent(identity: SecurityIdentity, *, interval: str, limit: int) -> list[dict]:
    symbol = provider_symbol(identity)
    if interval == "day":
        response = _request(
            "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
            params={"param": f"{symbol},day,,,{limit},qfq"},
            referer="https://gu.qq.com/",
        )
        payload = _decode_json_or_jsonp(response.text)
        stock = (payload.get("data") or {}).get(symbol) or {}
        rows = _parse_array(stock.get("qfqday") or stock.get("day") or [])
        basis = "forward" if stock.get("qfqday") else "raw"
    else:
        response = _request(
            "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
            params={"param": f"{symbol},{interval},,{limit}", "_var": "step4_kline"},
            referer="https://gu.qq.com/",
        )
        payload = _decode_json_or_jsonp(response.text)
        rows = _parse_array((((payload.get("data") or {}).get(symbol) or {}).get(interval) or []))
        basis = "raw"
    for row in rows:
        row["_adjustment"] = basis
    return rows


def _sina_intraday(identity: SecurityIdentity, *, scale: str, limit: int) -> list[dict]:
    symbol = provider_symbol(identity)
    response = _request(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData",
        params={"symbol": symbol, "scale": scale, "ma": "no", "datalen": str(min(limit, 1970))},
        referer="https://finance.sina.com.cn/",
    )
    payload = _decode_json_or_jsonp(response.text)
    if not isinstance(payload, list):
        raise RuntimeError("新浪分钟K返回格式异常")
    out: list[dict] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            out.append({
                "time": str(item.get("day") or item.get("time")),
                "open": float(item["open"]),
                "close": float(item["close"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "volume": float(item.get("volume") or 0),
                "amount": float(item.get("amount") or 0),
                "_adjustment": "raw",
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def _normalize_daily(rows: list[dict], *, now: datetime, source: str) -> list[dict]:
    normalized: dict[str, dict] = {}
    for row in rows:
        try:
            dt = parse_cn_time(str(row.get("time") or ""))
            if dt.date() > now.date():
                continue
            complete = dt.date() < now.date() or now.time() >= dt_time(15, 0)
            key = dt.strftime("%Y-%m-%d")
            normalized[key] = {
                "time": key,
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row.get("volume") or 0),
                "amount": float(row.get("amount") or 0),
                "_complete": complete,
                "_source": source,
                "_adjustment": str(row.get("_adjustment") or "unknown"),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return [normalized[key] for key in sorted(normalized)]


def _normalize_m30(rows: list[dict], *, now: datetime, source: str) -> list[dict]:
    normalized: dict[str, dict] = {}
    for row in rows:
        try:
            dt = parse_cn_time(str(row.get("time") or ""))
            if dt.time() not in VALID_M30_ENDS or dt > now:
                continue
            key = dt.strftime("%Y-%m-%d %H:%M:%S")
            normalized[key] = {
                "time": key,
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row.get("volume") or 0),
                "amount": float(row.get("amount") or 0),
                "_complete": True,
                "_source": source,
                "_adjustment": str(row.get("_adjustment") or "unknown"),
            }
        except (KeyError, TypeError, ValueError):
            continue
    return [normalized[key] for key in sorted(normalized)]


def _common_basis(rows: list[dict]) -> str:
    values = {str(row.get("_adjustment") or "unknown") for row in rows}
    return values.pop() if len(values) == 1 else "mixed"


def aggregate_m30_to_m120(rows: list[dict]) -> list[dict]:
    sessions: dict[tuple[object, str], list[tuple[datetime, dict]]] = {}
    for row in rows:
        dt = parse_cn_time(str(row["time"]))
        if dt.time() in {dt_time(10, 0), dt_time(10, 30), dt_time(11, 0), dt_time(11, 30)}:
            session = "AM"
        elif dt.time() in {dt_time(13, 30), dt_time(14, 0), dt_time(14, 30), dt_time(15, 0)}:
            session = "PM"
        else:
            continue
        sessions.setdefault((dt.date(), session), []).append((dt, row))

    out: list[dict] = []
    for key in sorted(sessions):
        seq = sorted(sessions[key], key=lambda pair: pair[0])
        if len(seq) != 4:
            continue
        if any((b[0] - a[0]) != timedelta(minutes=30) for a, b in zip(seq, seq[1:])):
            continue
        items = [item for _, item in seq]
        out.append({
            "time": seq[-1][0].strftime("%Y-%m-%d %H:%M:%S"),
            "open": float(items[0]["open"]),
            "close": float(items[-1]["close"]),
            "high": max(float(item["high"]) for item in items),
            "low": min(float(item["low"]) for item in items),
            "volume": sum(float(item.get("volume") or 0) for item in items),
            "amount": sum(float(item.get("amount") or 0) for item in items),
            "_complete": True,
            "_source": "aggregate_real_30m",
            "_adjustment": _common_basis(items),
        })
    return out


def _basis(rows: tuple[dict, ...] | list[dict]) -> str:
    if not rows:
        return "missing"
    return _common_basis(list(rows))


def _first_usable(
    providers: tuple[tuple[str, str, Callable[[], list[dict]]], ...],
    *,
    normalize: Callable[[list[dict], str], list[dict]],
    minimum: int,
) -> tuple[list[dict], str, list[str]]:
    warnings: list[str] = []
    for name, basis, fetcher in providers:
        try:
            raw = fetcher()
            for row in raw:
                row.setdefault("_adjustment", basis)
            rows = normalize(raw, name)
            if len(rows) < minimum:
                raise RuntimeError(f"有效K线仅{len(rows)}条")
            return rows, name, warnings
        except Exception as exc:
            warnings.append(f"{name}:{exc}")
    raise RuntimeError("；".join(warnings))


def collect_security_bars(identity: SecurityIdentity, *, now: datetime) -> BarCollection:
    daily, daily_source, daily_warnings = _first_usable(
        (
            ("东方财富前复权", "forward", lambda: _eastmoney(identity, klt="101", limit=DAILY_LIMIT)),
            ("腾讯前复权", "forward", lambda: _tencent(identity, interval="day", limit=DAILY_LIMIT)),
        ),
        normalize=lambda rows, source: _normalize_daily(rows, now=now, source=source),
        minimum=MIN_DAILY,
    )
    m30, m30_source, m30_warnings = _first_usable(
        (
            ("东方财富30分钟前复权", "forward", lambda: _eastmoney(identity, klt="30", limit=M30_LIMIT)),
            ("腾讯30分钟", "raw", lambda: _tencent(identity, interval="m30", limit=M30_LIMIT)),
            ("新浪30分钟", "raw", lambda: _sina_intraday(identity, scale="30", limit=M30_LIMIT)),
        ),
        normalize=lambda rows, source: _normalize_m30(rows, now=now, source=source),
        minimum=MIN_M30,
    )
    m5, m5_source, m5_warnings = _first_usable(
        (
            ("东方财富5分钟前复权", "forward", lambda: _eastmoney(identity, klt="5", limit=M5_LIMIT)),
            ("腾讯5分钟", "raw", lambda: _tencent(identity, interval="m5", limit=M5_LIMIT)),
            ("新浪5分钟", "raw", lambda: _sina_intraday(identity, scale="5", limit=M5_LIMIT)),
        ),
        normalize=lambda rows, source: normalize_complete_m5(rows, now=now, source=source),
        minimum=MIN_M5,
    )
    m120 = aggregate_m30_to_m120(m30)
    if len(m120) < MIN_M120:
        raise RuntimeError(f"120分钟历史仅{len(m120)}条")
    weekly = aggregate_weekly(daily, now=now)
    return BarCollection(
        identity=identity,
        daily=tuple(daily[-DAILY_LIMIT:]),
        weekly=tuple(weekly),
        m120=tuple(m120),
        m30=tuple(m30[-M30_LIMIT:]),
        m5=tuple(m5[-M5_LIMIT:]),
        sources={
            "daily": daily_source,
            "weekly": "日线聚合",
            "30m": m30_source,
            "120m": "真实30分钟聚合",
            "5m": m5_source,
        },
        warnings=tuple(daily_warnings + m30_warnings + m5_warnings),
    )
