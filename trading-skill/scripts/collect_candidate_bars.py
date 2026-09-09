from __future__ import annotations

import argparse
import json
import os
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

from trading_skill.a_share_bars import aggregate_m5, aggregate_weekly, normalize_complete_m5, parse_cn_time

CN_TZ = ZoneInfo("Asia/Shanghai")
TIMEOUT = 10
RETRIES = 3
MAX_WORKERS = 4
MIN_DAILY = 120
MIN_M5 = 240


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
                time.sleep(0.7 * (attempt + 1) + random.uniform(0.1, 0.4))
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
    raise RuntimeError(f"非JSON响应: {raw[:120]}")


def tx_symbol(code: str) -> str:
    if code.startswith(("8", "4", "92")):
        return f"bj{code}"
    if code.startswith(("6", "68", "60", "601", "603", "605")):
        return f"sh{code}"
    return f"sz{code}"


def secid(code: str, market: int) -> str:
    return f"{1 if market == 1 else 0}.{code}"


def _parse_array(rows: list, *, time_index: int = 0) -> list[dict]:
    out = []
    for row in rows:
        parts = row.split(",") if isinstance(row, str) else row if isinstance(row, list) else None
        if not parts or len(parts) < 6:
            continue
        try:
            out.append(
                {
                    "time": str(parts[time_index]),
                    "open": float(parts[1]),
                    "close": float(parts[2]),
                    "high": float(parts[3]),
                    "low": float(parts[4]),
                    "volume": float(parts[5] or 0),
                    "amount": float(parts[6] or 0) if len(parts) > 6 else 0.0,
                }
            )
        except (TypeError, ValueError):
            continue
    return out


def tencent_daily(code: str, limit: int = 500) -> list[dict]:
    symbol = tx_symbol(code)
    response = _request(
        "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
        params={"param": f"{symbol},day,,,{limit},qfq"},
        referer="https://gu.qq.com/",
    )
    payload = _decode_json_or_jsonp(response.text)
    stock = (payload.get("data") or {}).get(symbol) or {}
    rows = stock.get("qfqday") or stock.get("day") or []
    return _parse_array(rows)


def eastmoney_daily(code: str, market: int, limit: int = 500) -> list[dict]:
    response = _request(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid(code, market),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "101",
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": str(limit),
        },
        referer="https://quote.eastmoney.com/",
    )
    payload = response.json()
    return _parse_array(((payload.get("data") or {}).get("klines") or []))


def tencent_m5(code: str, limit: int = 600) -> list[dict]:
    symbol = tx_symbol(code)
    errors = []
    for url in ("https://ifzq.gtimg.cn/appstock/app/kline/mkline", "http://ifzq.gtimg.cn/appstock/app/kline/mkline"):
        try:
            response = _request(
                url,
                params={"param": f"{symbol},m5,,{limit}", "_var": "full_a_m5", "r": f"{random.random():.16f}"},
                referer="https://gu.qq.com/",
            )
            payload = _decode_json_or_jsonp(response.text)
            rows = (((payload.get("data") or {}).get(symbol) or {}).get("m5") or [])
            parsed = _parse_array(rows)
            if parsed:
                return parsed
            raise RuntimeError("腾讯5分钟为空")
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError(" | ".join(errors))


def sina_m5(code: str, limit: int = 600) -> list[dict]:
    symbol = tx_symbol(code)
    response = _request(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData",
        params={"symbol": symbol, "scale": "5", "ma": "no", "datalen": str(min(limit, 1970))},
        referer="https://finance.sina.com.cn/",
    )
    payload = _decode_json_or_jsonp(response.text)
    if not isinstance(payload, list):
        raise RuntimeError("新浪5分钟返回格式异常")
    out = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        try:
            out.append(
                {
                    "time": str(item.get("day") or item.get("time")),
                    "open": float(item["open"]),
                    "close": float(item["close"]),
                    "high": float(item["high"]),
                    "low": float(item["low"]),
                    "volume": float(item.get("volume") or 0),
                    "amount": float(item.get("amount") or 0),
                }
            )
        except (KeyError, TypeError, ValueError):
            continue
    return out


def eastmoney_m5(code: str, market: int, limit: int = 600) -> list[dict]:
    response = _request(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid(code, market),
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
    return _parse_array(((payload.get("data") or {}).get("klines") or []))


def fetch_daily(code: str, market: int) -> tuple[list[dict], str, list[str]]:
    warnings = []
    for name, fn in (("腾讯", lambda: tencent_daily(code)), ("东方财富", lambda: eastmoney_daily(code, market))):
        try:
            rows = fn()
            if len(rows) < MIN_DAILY:
                raise RuntimeError(f"日线仅{len(rows)}条")
            return rows[-500:], name, warnings
        except Exception as exc:
            warnings.append(f"{name}:{exc}")
    raise RuntimeError("；".join(warnings))


def fetch_m5(code: str, market: int, now: datetime) -> tuple[list[dict], str, list[str]]:
    warnings = []
    providers = (
        ("腾讯", lambda: tencent_m5(code)),
        ("新浪", lambda: sina_m5(code)),
        ("东方财富", lambda: eastmoney_m5(code, market)),
    )
    for name, fn in providers:
        try:
            raw = fn()
            rows = normalize_complete_m5(raw, now=now, source=name)
            if len(rows) < MIN_M5:
                raise RuntimeError(f"完整5分钟仅{len(rows)}条")
            return rows[-600:], name, warnings
        except Exception as exc:
            warnings.append(f"{name}:{exc}")
    raise RuntimeError("；".join(warnings))


def normalize_daily(rows: list[dict], *, now: datetime, source: str) -> list[dict]:
    out = []
    for row in rows:
        try:
            dt = parse_cn_time(str(row["time"]))
            is_today = dt.date() == now.date()
            complete = not is_today or now.time() >= dt_time(15, 0)
            out.append(
                {
                    **row,
                    "time": dt.strftime("%Y-%m-%d"),
                    "_complete": complete,
                    "_source": source,
                }
            )
        except Exception:
            continue
    return out


def collect_one(item: dict, now: datetime) -> dict:
    code = str(item["code"])
    market = int(item.get("market") or 0)
    daily, daily_source, daily_warnings = fetch_daily(code, market)
    m5, m5_source, m5_warnings = fetch_m5(code, market, now)
    daily = normalize_daily(daily, now=now, source=daily_source)
    m30 = aggregate_m5(m5, bars_per_group=6)
    m120 = aggregate_m5(m5, bars_per_group=24)
    weekly = aggregate_weekly(daily, now=now)
    return {
        "code": code,
        "name": item.get("name"),
        "market": market,
        "industry_code": item.get("industry_code"),
        "industry_name": item.get("industry_name"),
        "leader_rank": item.get("leader_rank"),
        "leader_score": item.get("leader_score"),
        "change_60d": item.get("change_60d"),
        "fundamental_prefilter": item.get("fundamental_prefilter"),
        "sources": {"daily": daily_source, "5m": m5_source, "30m": "真实5分钟聚合", "120m": "真实5分钟聚合", "weekly": "日线聚合"},
        "warnings": daily_warnings + m5_warnings,
        "weekly": weekly,
        "daily": daily,
        "120m": m120,
        "30m": m30,
        "5m": m5,
        "quality": {
            "daily_history_ok": len(daily) >= MIN_DAILY,
            "m5_history_ok": len(m5) >= MIN_M5,
            "latest_m5": m5[-1]["time"] if m5 else None,
            "covers_1345": bool(m5 and parse_cn_time(m5[-1]["time"]).date() == now.date() and parse_cn_time(m5[-1]["time"]).time() >= dt_time(13, 45)) if now.time() >= dt_time(13, 45) else True,
            "covers_1445": bool(m5 and parse_cn_time(m5[-1]["time"]).date() == now.date() and parse_cn_time(m5[-1]["time"]).time() >= dt_time(14, 45)) if now.time() >= dt_time(14, 45) else True,
        },
    }


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--universe", type=Path, default=Path("full-a-results/universe_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/candidate_bars_latest.json"))
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    universe = json.loads(args.universe.read_text(encoding="utf-8"))
    candidates = [
        item for item in universe.get("leader_candidates", [])
        if (item.get("fundamental_prefilter") or {}).get("eligible")
    ]
    results = []
    errors = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(collect_one, item, now): item for item in candidates}
        for future in as_completed(futures):
            item = futures[future]
            try:
                results.append(future.result())
            except Exception as exc:
                errors.append({"code": item.get("code"), "name": item.get("name"), "error": str(exc)})
    results.sort(key=lambda x: (str(x.get("industry_name")), int(x.get("leader_rank") or 99), str(x.get("code"))))
    payload = {
        "mode": "FULL_A_CANDIDATE_REAL_BARS",
        "generated_at": now.isoformat(),
        "candidate_count": len(candidates),
        "loaded_count": len(results),
        "errors": errors,
        "symbols": results,
        "guardrails": {"real_5m_only": True, "30m_from_real_5m": True, "120m_from_real_5m": True, "no_15m_to_5m": True},
    }
    atomic_json(args.output, payload)
    print(f"财务通过候选={len(candidates)}，五周期数据成功={len(results)}，异常={len(errors)}")
    for item in results:
        print(item["code"], item["name"], item["sources"], len(item["daily"]), len(item["120m"]), len(item["30m"]), len(item["5m"]), item["quality"]["latest_m5"])
    if errors:
        print("异常:", errors)
    if args.strict and candidates:
        ratio = len(results) / len(candidates)
        if ratio < 0.80:
            raise SystemExit(f"候选五周期真实行情成功率过低:{len(results)}/{len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
