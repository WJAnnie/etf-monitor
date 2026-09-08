from __future__ import annotations

from collections import defaultdict
from datetime import datetime, time, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")


def parse_cn_time(value: str) -> datetime:
    text = str(value).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=CN_TZ)
        except ValueError:
            pass
    parsed = datetime.fromisoformat(text)
    return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)


def is_cn_5m_end(dt: datetime) -> bool:
    t = dt.time()
    if dt.minute % 5 != 0 or dt.second != 0:
        return False
    return time(9, 35) <= t <= time(11, 30) or time(13, 5) <= t <= time(15, 0)


def normalize_complete_m5(rows: Iterable[dict], *, now: datetime, source: str) -> list[dict]:
    normalized: dict[str, dict] = {}
    for row in rows:
        dt = parse_cn_time(str(row.get("time") or row.get("day") or row.get("date") or ""))
        # 新浪/腾讯/东方财富这里统一按区间结束时间处理。
        if not is_cn_5m_end(dt) or dt > now:
            continue
        try:
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
        except (KeyError, TypeError, ValueError):
            continue
        normalized[item["time"]] = item
    return [normalized[key] for key in sorted(normalized)]


def _session(dt: datetime) -> str | None:
    t = dt.time()
    if time(9, 35) <= t <= time(11, 30):
        return "AM"
    if time(13, 5) <= t <= time(15, 0):
        return "PM"
    return None


def aggregate_m5(rows: Iterable[dict], *, bars_per_group: int) -> list[dict]:
    sessions: dict[tuple[object, str], list[tuple[datetime, dict]]] = defaultdict(list)
    for row in rows:
        dt = parse_cn_time(str(row["time"]))
        session = _session(dt)
        if session is not None:
            sessions[(dt.date(), session)].append((dt, row))
    out: list[dict] = []
    for key in sorted(sessions):
        seq = sorted(sessions[key], key=lambda pair: pair[0])
        for start in range(0, len(seq), bars_per_group):
            chunk = seq[start : start + bars_per_group]
            if len(chunk) != bars_per_group:
                continue
            if any((b[0] - a[0]) != timedelta(minutes=5) for a, b in zip(chunk, chunk[1:])):
                continue
            items = [row for _, row in chunk]
            out.append(
                {
                    "time": chunk[-1][0].strftime("%Y-%m-%d %H:%M:%S"),
                    "open": float(items[0]["open"]),
                    "close": float(items[-1]["close"]),
                    "high": max(float(item["high"]) for item in items),
                    "low": min(float(item["low"]) for item in items),
                    "volume": sum(float(item.get("volume") or 0) for item in items),
                    "amount": sum(float(item.get("amount") or 0) for item in items),
                    "_complete": True,
                    "_source": "aggregate_real_5m",
                }
            )
    return out


def aggregate_weekly(daily_rows: Iterable[dict], *, now: datetime) -> list[dict]:
    groups: dict[tuple[int, int], list[dict]] = defaultdict(list)
    for row in daily_rows:
        dt = parse_cn_time(str(row["time"]))
        iso = dt.isocalendar()
        groups[(iso.year, iso.week)].append(row)
    out: list[dict] = []
    keys = sorted(groups)
    for index, key in enumerate(keys):
        rows = sorted(groups[key], key=lambda item: str(item["time"]))
        latest_dt = parse_cn_time(str(rows[-1]["time"]))
        complete = not (index == len(keys) - 1 and latest_dt.date() == now.date() and now.weekday() < 4)
        out.append(
            {
                "time": str(rows[-1]["time"]),
                "open": float(rows[0]["open"]),
                "close": float(rows[-1]["close"]),
                "high": max(float(item["high"]) for item in rows),
                "low": min(float(item["low"]) for item in rows),
                "volume": sum(float(item.get("volume") or 0) for item in rows),
                "amount": sum(float(item.get("amount") or 0) for item in rows),
                "_complete": complete,
                "_source": "aggregate_daily",
            }
        )
    return out
