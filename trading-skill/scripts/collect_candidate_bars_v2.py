from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

from scripts.collect_candidate_bars import (
    CN_TZ,
    _decode_json_or_jsonp,
    _parse_array,
    _request,
    eastmoney_daily,
    eastmoney_m5,
    normalize_daily,
    secid,
    sina_m5,
    tencent_daily,
    tencent_m5,
    tx_symbol,
)
from trading_skill.a_share_bars import aggregate_weekly, normalize_complete_m5, parse_cn_time

MAX_WORKERS = 6
DAILY_LIMIT = 1200
M5_LIMIT = 1200
M30_LIMIT = 1600
MIN_DAILY = 120
LONG_DAILY = 500
MIN_M5 = 500
MIN_M30 = 480
MIN_M120 = 100
VALID_M30_ENDS = {
    dt_time(10, 0), dt_time(10, 30), dt_time(11, 0), dt_time(11, 30),
    dt_time(13, 30), dt_time(14, 0), dt_time(14, 30), dt_time(15, 0),
}


def tencent_m30(code: str, limit: int = M30_LIMIT) -> list[dict]:
    symbol = tx_symbol(code)
    response = _request(
        "https://ifzq.gtimg.cn/appstock/app/kline/mkline",
        params={"param": f"{symbol},m30,,{limit}", "_var": "full_a_m30"},
        referer="https://gu.qq.com/",
    )
    payload = _decode_json_or_jsonp(response.text)
    rows = (((payload.get("data") or {}).get(symbol) or {}).get("m30") or [])
    return _parse_array(rows)


def sina_m30(code: str, limit: int = M30_LIMIT) -> list[dict]:
    symbol = tx_symbol(code)
    response = _request(
        "https://quotes.sina.cn/cn/api/jsonp_v2.php/=/CN_MarketDataService.getKLineData",
        params={"symbol": symbol, "scale": "30", "ma": "no", "datalen": str(min(limit, 1970))},
        referer="https://finance.sina.com.cn/",
    )
    payload = _decode_json_or_jsonp(response.text)
    if not isinstance(payload, list):
        raise RuntimeError("新浪30分钟返回格式异常")
    out = []
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
            })
        except (KeyError, TypeError, ValueError):
            continue
    return out


def eastmoney_m30(code: str, market: int, limit: int = M30_LIMIT) -> list[dict]:
    response = _request(
        "https://push2his.eastmoney.com/api/qt/stock/kline/get",
        params={
            "secid": secid(code, market),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
            "klt": "30",
            "fqt": "1",
            "beg": "0",
            "end": "20500101",
            "lmt": str(limit),
        },
        referer="https://quote.eastmoney.com/",
    )
    payload = response.json()
    return _parse_array(((payload.get("data") or {}).get("klines") or []))


def normalize_complete_m30(rows: list[dict], *, now: datetime, source: str) -> list[dict]:
    normalized: dict[str, dict] = {}
    for row in rows:
        try:
            dt = parse_cn_time(str(row.get("time") or ""))
            if dt.time() not in VALID_M30_ENDS or dt > now:
                continue
            item = {
                "time": dt.strftime("%Y-%m-%d %H:%M:%S"),
                "open": float(row["open"]),
                "close": float(row["close"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "volume": float(row.get("volume") or 0),
                "amount": float(row.get("amount") or 0),
                "_complete": True,
                "_source": source,
            }
            normalized[item["time"]] = item
        except (KeyError, TypeError, ValueError):
            continue
    return [normalized[key] for key in sorted(normalized)]


def aggregate_m30_to_m120(rows: list[dict]) -> list[dict]:
    sessions: dict[tuple[object, str], list[tuple[datetime, dict]]] = defaultdict(list)
    for row in rows:
        dt = parse_cn_time(str(row["time"]))
        if dt.time() in {dt_time(10, 0), dt_time(10, 30), dt_time(11, 0), dt_time(11, 30)}:
            session = "AM"
        elif dt.time() in {dt_time(13, 30), dt_time(14, 0), dt_time(14, 30), dt_time(15, 0)}:
            session = "PM"
        else:
            continue
        sessions[(dt.date(), session)].append((dt, row))

    out = []
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
            "_source": "真实30分钟聚合",
        })
    return out


def fetch_daily_v2(code: str, market: int) -> tuple[list[dict], str, list[str]]:
    warnings = []
    # 优先前复权长历史，避免除权除息在长期缠论结构中制造假跳空。
    for name, fn in (
        ("东方财富前复权", lambda: eastmoney_daily(code, market, DAILY_LIMIT)),
        ("腾讯", lambda: tencent_daily(code, DAILY_LIMIT)),
    ):
        try:
            rows = fn()
            if len(rows) < MIN_DAILY:
                raise RuntimeError(f"日线仅{len(rows)}条")
            return rows[-DAILY_LIMIT:], name, warnings
        except Exception as exc:
            warnings.append(f"{name}:{exc}")
    raise RuntimeError("；".join(warnings))


def fetch_m5_v2(code: str, market: int, now: datetime) -> tuple[list[dict], str, list[str]]:
    warnings = []
    providers = (
        ("腾讯", lambda: tencent_m5(code, M5_LIMIT)),
        ("新浪", lambda: sina_m5(code, M5_LIMIT)),
        ("东方财富", lambda: eastmoney_m5(code, market, M5_LIMIT)),
    )
    for name, fn in providers:
        try:
            rows = normalize_complete_m5(fn(), now=now, source=name)
            if len(rows) < MIN_M5:
                raise RuntimeError(f"完整5分钟仅{len(rows)}条")
            return rows[-M5_LIMIT:], name, warnings
        except Exception as exc:
            warnings.append(f"{name}:{exc}")
    raise RuntimeError("；".join(warnings))


def fetch_m30_v2(code: str, market: int, now: datetime) -> tuple[list[dict], str, list[str]]:
    warnings = []
    # 30分钟同样优先前复权长历史；新浪/腾讯只做网络与数据源兜底。
    providers = (
        ("东方财富前复权", lambda: eastmoney_m30(code, market)),
        ("新浪", lambda: sina_m30(code)),
        ("腾讯", lambda: tencent_m30(code)),
    )
    for name, fn in providers:
        try:
            rows = normalize_complete_m30(fn(), now=now, source=name)
            if len(rows) < MIN_M30:
                raise RuntimeError(f"完整30分钟仅{len(rows)}条")
            return rows[-M30_LIMIT:], name, warnings
        except Exception as exc:
            warnings.append(f"{name}:{exc}")
    raise RuntimeError("；".join(warnings))


def collect_one(item: dict, now: datetime) -> dict:
    code = str(item["code"])
    market = int(item.get("market") or 0)
    daily, daily_source, daily_warnings = fetch_daily_v2(code, market)
    m5, m5_source, m5_warnings = fetch_m5_v2(code, market, now)
    m30, m30_source, m30_warnings = fetch_m30_v2(code, market, now)
    daily = normalize_daily(daily, now=now, source=daily_source)
    m120 = aggregate_m30_to_m120(m30)
    if len(m120) < MIN_M120:
        raise RuntimeError(f"120分钟历史仅{len(m120)}条")
    weekly = aggregate_weekly(daily, now=now)
    return {
        "code": code,
        "name": item.get("name"),
        "market": market,
        "industry_code": item.get("industry_code"),
        "industry_name": item.get("industry_name"),
        "industry_selection_reason": item.get("industry_selection_reason"),
        "prospect_theme": item.get("prospect_theme"),
        "leader_rank": item.get("leader_rank"),
        "leader_score": item.get("leader_score"),
        "change_60d": item.get("change_60d"),
        "fundamental_prefilter": item.get("fundamental_prefilter"),
        "event_summary": item.get("event_summary"),
        "sources": {
            "daily": daily_source,
            "5m": m5_source,
            "30m": f"真实30分钟:{m30_source}",
            "120m": "真实30分钟聚合",
            "weekly": "日线聚合",
        },
        "warnings": daily_warnings + m5_warnings + m30_warnings,
        "weekly": weekly,
        "daily": daily,
        "120m": m120,
        "30m": m30,
        "5m": m5,
        "quality": {
            "daily_history_ok": len(daily) >= MIN_DAILY,
            "daily_long_history_ok": len(daily) >= LONG_DAILY,
            "m5_history_ok": len(m5) >= MIN_M5,
            "m30_history_ok": len(m30) >= MIN_M30,
            "m120_history_ok": len(m120) >= MIN_M120,
            "latest_m5": m5[-1]["time"] if m5 else None,
            "latest_m30": m30[-1]["time"] if m30 else None,
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
        if (item.get("fundamental_prefilter") or {}).get("deep_scan_eligible", (item.get("fundamental_prefilter") or {}).get("eligible"))
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
    results.sort(key=lambda x: (str(x.get("prospect_theme") or "ZZZ"), str(x.get("industry_name")), int(x.get("leader_rank") or 99), str(x.get("code"))))
    payload = {
        "mode": "FULL_A_CANDIDATE_LONG_HISTORY_V2",
        "generated_at": now.isoformat(),
        "candidate_count": len(candidates),
        "loaded_count": len(results),
        "errors": errors,
        "symbols": results,
        "guardrails": {
            "real_5m_only": True,
            "30m_direct_real_history": True,
            "120m_from_real_30m": True,
            "prefer_adjusted_long_history": True,
            "newer_stocks_can_use_shorter_daily_history": True,
            "announcement_evidence_carried_into_scan": True,
            "no_15m_to_5m": True,
            "daily_target": DAILY_LIMIT,
            "m5_target": M5_LIMIT,
            "m30_target": M30_LIMIT,
        },
    }
    atomic_json(args.output, payload)
    print(f"允许深扫候选={len(candidates)}，长历史五周期成功={len(results)}，异常={len(errors)}")
    for item in results:
        print(item["code"], item["name"], item["sources"], len(item["daily"]), len(item["weekly"]), len(item["120m"]), len(item["30m"]), len(item["5m"]), item["quality"]["latest_m5"])
    if errors:
        print("异常前10:", errors[:10])
    if args.strict and candidates:
        ratio = len(results) / len(candidates)
        if ratio < 0.75:
            raise SystemExit(f"长历史五周期成功率过低:{len(results)}/{len(candidates)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
