from __future__ import annotations

import argparse
import json
import random
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

from portfolio_market_data import (
    CN_TZ,
    SYMBOLS,
    MarketDataError,
    atomic_write_json,
    decode_json_or_jsonp,
    parse_array_rows,
    parse_bar_time,
    parse_object_rows,
    read_json_file,
    request_with_retry,
)

M5_DIR = Path("data/market5")
M5_SUMMARY = Path("data/latest_market_5m_summary.json")
MIN_M5_ROWS = 240
M5_LIMIT = 600
MAX_WORKERS = 4


def _start_is_session_bar(dt: datetime, market: str) -> bool:
    t = dt.time()
    if dt.minute % 5 != 0 or dt.second != 0:
        return False
    if market.startswith("CN"):
        return dt_time(9, 30) <= t <= dt_time(11, 25) or dt_time(13, 0) <= t <= dt_time(14, 55)
    if market.startswith("HK"):
        return dt_time(9, 30) <= t <= dt_time(11, 55) or dt_time(13, 0) <= t <= dt_time(15, 55)
    return True


def _end_is_session_bar(dt: datetime, market: str) -> bool:
    t = dt.time()
    if dt.minute % 5 != 0 or dt.second != 0:
        return False
    if market.startswith("CN"):
        return dt_time(9, 35) <= t <= dt_time(11, 30) or dt_time(13, 5) <= t <= dt_time(15, 0)
    if market.startswith("HK"):
        return dt_time(9, 35) <= t <= dt_time(12, 0) or dt_time(13, 5) <= t <= dt_time(16, 0)
    return True


def normalize_complete_m5(rows: list[dict], *, source: str, market: str, now: datetime) -> list[dict]:
    """Return only completed 5m bars with END timestamps in Asia/Shanghai.

    Yahoo timestamps are interval starts. Tencent/Sina/EastMoney are treated as interval ends,
    matching the existing 15m collector contract. No incomplete bar is retained.
    """
    out: list[dict] = []
    for row in rows:
        dt = parse_bar_time(row.get("time"))
        if dt is None:
            continue
        if source == "yahoo":
            if not _start_is_session_bar(dt, market):
                continue
            end = dt + timedelta(minutes=5)
        else:
            end = dt
            if not _end_is_session_bar(end, market):
                continue
        if end > now:
            continue
        item = dict(row)
        item["time"] = end.strftime("%Y-%m-%d %H:%M:%S")
        out.append(item)
    out.sort(key=lambda r: r["time"])
    # Deduplicate provider glitches deterministically by normalized end timestamp.
    dedup: dict[str, dict] = {}
    for item in out:
        dedup[item["time"]] = item
    return list(dedup.values())


def tencent_m5(symbol: dict, limit: int) -> list[dict]:
    code = symbol["tx_symbol"]
    urls = [
        "http://ifzq.gtimg.cn/appstock/app/kline/mkline",
        "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
    ]
    errors = []
    for url in urls:
        try:
            response = request_with_retry(
                url,
                params={"param": f"{code},m5,,{limit}", "_var": "m5_shadow", "r": f"{random.random():.16f}"},
                referer="https://gu.qq.com/",
            )
            payload = decode_json_or_jsonp(response.text)
            stock = (payload.get("data") or {}).get(code) or {}
            rows = stock.get("m5") or []
            if not rows:
                raise MarketDataError(f"Tencent m5 empty; stock_keys={list(stock.keys())[:8]}")
            return parse_array_rows(rows, "Tencent m5")
        except Exception as exc:
            errors.append(f"{url}: {exc}")
    raise MarketDataError(" | ".join(errors))


def sina_m5(symbol: dict, limit: int) -> list[dict]:
    code = symbol["tx_symbol"]
    if not code.startswith(("sh", "sz", "bj")):
        raise MarketDataError(f"Sina CN endpoint unsupported symbol: {code}")
    response = request_with_retry(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData",
        params={"symbol": code, "scale": "5", "ma": "no", "datalen": str(min(limit, 1970))},
        referer="https://finance.sina.com.cn/",
    )
    payload = decode_json_or_jsonp(response.text)
    if not isinstance(payload, list):
        raise MarketDataError(f"Sina unexpected payload type={type(payload).__name__}")
    return parse_object_rows(payload, "Sina scale=5")


def yahoo_m5(symbol: dict, limit: int) -> list[dict]:
    code = symbol.get("yahoo_symbol")
    if not code:
        raise MarketDataError("Yahoo fallback not configured")
    response = request_with_retry(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{code}",
        params={"interval": "5m", "range": "60d", "includePrePost": "false", "events": "div,splits"},
        referer="https://finance.yahoo.com/",
    )
    payload = response.json()
    chart = payload.get("chart") or {}
    if chart.get("error"):
        raise MarketDataError(f"Yahoo chart error: {chart['error']}")
    result = (chart.get("result") or [None])[0]
    if not result:
        raise MarketDataError("Yahoo chart missing result")
    timestamps = result.get("timestamp") or []
    quotes = (((result.get("indicators") or {}).get("quote") or [{}])[0])
    opens = quotes.get("open") or []
    closes = quotes.get("close") or []
    highs = quotes.get("high") or []
    lows = quotes.get("low") or []
    volumes = quotes.get("volume") or []
    out = []
    for i, ts in enumerate(timestamps):
        try:
            o, c, h, l = opens[i], closes[i], highs[i], lows[i]
            if None in (o, c, h, l):
                continue
            dt = datetime.fromtimestamp(int(ts), CN_TZ)
            out.append(
                {
                    "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(o),
                    "close": float(c),
                    "high": float(h),
                    "low": float(l),
                    "volume": float(volumes[i] or 0),
                    "amount": 0.0,
                }
            )
        except (IndexError, TypeError, ValueError, OSError):
            continue
    if not out:
        raise MarketDataError("Yahoo returned empty/invalid 5m kline")
    return out[-limit:]


def eastmoney_m5(symbol: dict, limit: int) -> list[dict]:
    response = request_with_retry(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": symbol["secid"],
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "5",
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": str(limit),
        },
        referer="https://quote.eastmoney.com/",
    )
    payload = response.json()
    rows = ((payload.get("data") or {}).get("klines") or [])
    return parse_array_rows(rows, "EastMoney m5")


PROVIDERS = {
    "tencent": tencent_m5,
    "sina": sina_m5,
    "yahoo": yahoo_m5,
    "eastmoney": eastmoney_m5,
}


def provider_order(symbol: dict) -> list[str]:
    configured = symbol.get("provider_order")
    if configured:
        return list(configured)
    # Prefer the actual symbol over a Yahoo proxy whenever possible.
    if symbol.get("yahoo_symbol_is_proxy"):
        return ["tencent", "sina", "eastmoney", "yahoo"]
    return ["tencent", "sina", "yahoo", "eastmoney"]


def fetch_m5(symbol: dict, *, now: datetime, limit: int = M5_LIMIT) -> tuple[list[dict], str, list[str]]:
    warnings: list[str] = []
    for name in provider_order(symbol):
        try:
            raw = PROVIDERS[name](symbol, limit)
            rows = normalize_complete_m5(raw, source=name, market=symbol["market"], now=now)
            if len(rows) < MIN_M5_ROWS:
                raise MarketDataError(f"only {len(rows)} complete rows, need >= {MIN_M5_ROWS}")
            return rows[-limit:], name, warnings
        except Exception as exc:
            warnings.append(f"{name}: {exc}")
    raise MarketDataError(" ; ".join(warnings))


def _latest_end(rows: list[dict]) -> datetime | None:
    return parse_bar_time(rows[-1].get("time")) if rows else None


def _fresh_today(rows: list[dict], now: datetime) -> bool:
    end = _latest_end(rows)
    return bool(end and end.date() == now.date())


def _covers(rows: list[dict], now: datetime, threshold: dt_time) -> bool:
    end = _latest_end(rows)
    return bool(end and end.date() == now.date() and end.time() >= threshold)


def collect_symbol(symbol: dict, *, now: datetime) -> dict:
    key = symbol["key"]
    old = read_json_file(M5_DIR / f"{key}.json")
    old_symbol = old.get("symbol") or {}
    try:
        rows, source, warnings = fetch_m5(symbol, now=now)
        return {
            **symbol,
            "m5": rows,
            "source": source,
            "warnings": warnings,
            "errors": [],
            "using_cache": False,
            "timestamp_semantics": "interval_end",
        }
    except Exception as exc:
        cached = list(old_symbol.get("m5") or [])
        if len(cached) >= MIN_M5_ROWS:
            return {
                **symbol,
                "m5": cached,
                "source": "cache",
                "warnings": list(old_symbol.get("warnings") or []),
                "errors": [f"live refresh failed; using cache: {exc}"],
                "using_cache": True,
                "timestamp_semantics": "interval_end",
            }
        return {
            **symbol,
            "m5": [],
            "source": "none",
            "warnings": [],
            "errors": [str(exc)],
            "using_cache": False,
            "timestamp_semantics": "interval_end",
        }


def build_summary(items: list[dict], *, now: datetime) -> dict:
    symbols: dict[str, dict] = {}
    for item in items:
        rows = list(item.get("m5") or [])
        enough = len(rows) >= MIN_M5_ROWS
        fresh = enough and _fresh_today(rows, now) and not item.get("using_cache") and not item.get("errors")
        symbols[item["key"]] = {
            "name": item["name"],
            "market": item["market"],
            "proxy_for": item["proxy_for"],
            "proxy_note": item["proxy_note"],
            "data_file": f"{M5_DIR.as_posix()}/{item['key']}.json",
            "source": item.get("source"),
            "proxy_substitution": bool(item.get("source") == "yahoo" and item.get("yahoo_symbol_is_proxy")),
            "m5_count": len(rows),
            "latest_m5_end_time": _latest_end(rows).isoformat() if _latest_end(rows) else None,
            "history_ok": enough,
            "fresh_for_analysis": fresh,
            "covers_1400_bar": _covers(rows, now, dt_time(14, 0)),
            "covers_1450_bar": _covers(rows, now, dt_time(14, 50)),
            "using_cache": bool(item.get("using_cache")),
            "warnings": item.get("warnings") or [],
            "errors": item.get("errors") or [],
        }
    total = len(symbols)
    history = sum(bool(x["history_ok"]) for x in symbols.values())
    fresh = sum(bool(x["fresh_for_analysis"]) for x in symbols.values())
    cover1400 = sum(bool(x["covers_1400_bar"]) for x in symbols.values())
    cover1450 = sum(bool(x["covers_1450_bar"]) for x in symbols.values())
    return {
        "generated_at": now.isoformat(),
        "total_symbols": total,
        "history_ok_symbols": history,
        "fresh_symbols": fresh,
        "symbols_covering_1400_bar": cover1400,
        "symbols_covering_1450_bar": cover1450,
        "all_history_ok": history == total,
        "all_fresh": fresh == total,
        "all_cover_1400_bar": cover1400 == total,
        "all_cover_1450_bar": cover1450 == total,
        "timestamp_semantics": "interval_end",
        "note": "Real 5m bars only. Yahoo start timestamps are normalized to interval end. No 15m-to-5m synthesis.",
        "symbols": symbols,
    }


def collect(*, now: datetime | None = None) -> dict:
    now = now or datetime.now(CN_TZ)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        items = list(pool.map(lambda s: collect_symbol(s, now=now), SYMBOLS))
    summary = build_summary(items, now=now)
    for item in items:
        atomic_write_json(
            M5_DIR / f"{item['key']}.json",
            {"generated_at": now.isoformat(), "symbol": item},
        )
    atomic_write_json(M5_SUMMARY, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--strict", action="store_true", help="fail unless all 9 symbols have fresh real 5m history")
    args = parser.parse_args()
    summary = collect()
    print(
        f"5m quality: history={summary['history_ok_symbols']}/{summary['total_symbols']} "
        f"fresh={summary['fresh_symbols']}/{summary['total_symbols']} "
        f"covers_1400={summary['symbols_covering_1400_bar']}/{summary['total_symbols']} "
        f"covers_1450={summary['symbols_covering_1450_bar']}/{summary['total_symbols']}"
    )
    for key, item in summary["symbols"].items():
        print(key, item["source"], item["m5_count"], item["latest_m5_end_time"], item["fresh_for_analysis"], item["errors"])
    if args.strict and not (summary["all_history_ok"] and summary["all_fresh"]):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
