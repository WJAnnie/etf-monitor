from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_skill.chan.center import CenterMotion, seed_center
from trading_skill.chan.fractal import detect_fractals
from trading_skill.chan.inclusion import process_inclusions
from trading_skill.chan.segment import build_segments
from trading_skill.chan.stroke import build_strokes
from trading_skill.chan.trend import classify_trend
from trading_skill.data.validate import validate_raw_bars
from trading_skill.domain.bar import RawBar
from trading_skill.domain.enums import CenterState, StrokeMode, Timeframe
from trading_skill.indicators import build_bundle

CN_TZ = ZoneInfo("Asia/Shanghai")


def parse_market_time(value: str, *, market: str, timeframe: str, source: str = "") -> datetime:
    raw = str(value).strip()
    parsed: datetime | None = None
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt).replace(tzinfo=CN_TZ)
            break
        except ValueError:
            pass
    if parsed is None:
        parsed = datetime.fromisoformat(raw)
        parsed = parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)

    if timeframe == "daily" and len(raw) <= 10:
        close = time(16, 0) if market.startswith("HK") else time(15, 0)
        return parsed.replace(hour=close.hour, minute=close.minute, second=0, microsecond=0)
    if timeframe == "m15" and source == "yahoo":
        # portfolio_market_data.py stores Yahoo 15m timestamps as interval starts.
        return parsed + timedelta(minutes=15)
    return parsed


def infer_tick_size(symbol: dict, *, field: str) -> Decimal:
    source = str((symbol.get("sources") or {}).get(field, ""))
    if source == "yahoo" and bool(symbol.get("yahoo_symbol_is_proxy")):
        return Decimal("0.001")
    name = str(symbol.get("name") or "").upper()
    tx_symbol = str(symbol.get("tx_symbol") or "").lower()
    if "ETF" in name or (tx_symbol.startswith(("sh", "sz")) and symbol.get("market") == "CN" and "指数" not in name and field != "daily" and symbol.get("proxy_for") != "600977"):
        # Explicit ETF names cover the known portfolio; the second branch is only a fallback.
        if "ETF" in name:
            return Decimal("0.001")
    return Decimal("0.01")


def _session_key(dt: datetime, market: str) -> str | None:
    t = dt.time()
    if market.startswith("HK"):
        if time(9, 30) < t <= time(12, 0):
            return "AM"
        if time(13, 0) < t <= time(16, 0):
            return "PM"
        return None
    if time(9, 30) < t <= time(11, 30):
        return "AM"
    if time(13, 0) < t <= time(15, 0):
        return "PM"
    return None


def _aggregate_chunk(chunk: list[tuple[datetime, dict]]) -> dict:
    rows = [row for _, row in chunk]
    return {
        "time": chunk[-1][0].isoformat(),
        "open": rows[0]["open"],
        "high": max(float(r["high"]) for r in rows),
        "low": min(float(r["low"]) for r in rows),
        "close": rows[-1]["close"],
        "volume": sum(float(r.get("volume") or 0) for r in rows),
        "amount": sum(float(r.get("amount") or 0) for r in rows),
        "_complete": True,
    }


def aggregate_m15(rows: list[dict], *, market: str, source: str, group_size: int) -> list[dict]:
    sessions: dict[tuple[object, str], list[tuple[datetime, dict]]] = defaultdict(list)
    for row in rows:
        dt = parse_market_time(str(row["time"]), market=market, timeframe="m15", source=source)
        session = _session_key(dt, market)
        if session is None:
            continue
        sessions[(dt.date(), session)].append((dt, row))

    aggregated: list[dict] = []
    for key in sorted(sessions):
        seq = sorted(sessions[key], key=lambda x: x[0])
        for start in range(0, len(seq), group_size):
            chunk = seq[start : start + group_size]
            if len(chunk) != group_size:
                continue
            # Refuse to bridge missing 15m bars inside one synthetic bar.
            if any((b[0] - a[0]) != timedelta(minutes=15) for a, b in zip(chunk, chunk[1:])):
                continue
            aggregated.append(_aggregate_chunk(chunk))
    return aggregated


def aggregate_weekly(rows: list[dict], *, market: str) -> list[dict]:
    groups: dict[tuple[int, int], list[tuple[datetime, dict]]] = defaultdict(list)
    for row in rows:
        dt = parse_market_time(str(row["time"]), market=market, timeframe="daily")
        iso = dt.isocalendar()
        groups[(iso.year, iso.week)].append((dt, row))
    out: list[dict] = []
    keys = sorted(groups)
    for idx, key in enumerate(keys):
        seq = sorted(groups[key], key=lambda x: x[0])
        item = _aggregate_chunk(seq)
        # A week still in progress cannot confirm a weekly structural event.
        item["_complete"] = not (idx == len(keys) - 1 and seq[-1][0].weekday() < 4)
        out.append(item)
    return out


def rows_to_raw_bars(
    rows: list[dict], *, symbol: str, market: str, timeframe: Timeframe, source: str, tick_source_field: str
) -> tuple[RawBar, ...]:
    bars: list[RawBar] = []
    tf_name = "daily" if timeframe is Timeframe.DAILY else "derived"
    for row in rows:
        value = str(row["time"])
        if timeframe is Timeframe.DAILY:
            ts = parse_market_time(value, market=market, timeframe="daily")
        else:
            ts = datetime.fromisoformat(value)
            ts = ts.replace(tzinfo=CN_TZ) if ts.tzinfo is None else ts.astimezone(CN_TZ)
        bars.append(
            RawBar.make(
                symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=row["open"],
                high=row["high"],
                low=row["low"],
                close=row["close"],
                volume=row.get("volume") or 0,
                amount=row.get("amount") or 0,
                is_complete=bool(row.get("_complete", True)),
                source=f"portfolio_snapshot:{source}:{tf_name}:{tick_source_field}",
            )
        )
    return tuple(bars)


def analyze_structure(raw_bars: tuple[RawBar, ...], *, tick_size: Decimal) -> dict:
    if not raw_bars:
        return {"status": "DATA_INCOMPLETE", "issues": ["EMPTY_INPUT"]}
    validated = validate_raw_bars(raw_bars, tick_size)
    if not validated.result.valid:
        return {"status": "DATA_INCOMPLETE", "issues": list(validated.result.reason_codes)}
    inclusion = process_inclusions(validated.bars)
    if not inclusion.result.valid:
        return {"status": "UNRESOLVED", "issues": list(inclusion.result.reason_codes)}
    fractals = detect_fractals(inclusion.bars)
    strokes = build_strokes(
        fractals.fractals,
        mode=StrokeMode.RELAXED_LATE,
        processed_bars=inclusion.bars,
        raw_bars=validated.bars,
    )
    segments = build_segments(strokes.strokes)

    centers = []
    normalized = segments.normalized_segments
    i = 0
    while i + 2 < len(normalized):
        motions = tuple(
            CenterMotion.from_segment(s, source_timeframe=raw_bars[0].timeframe, level_rank=0)
            for s in normalized[i : i + 3]
        )
        update = seed_center(motions, symbol=raw_bars[0].symbol, target_level_rank=1, base_engine_center=True)
        if update.result.valid and update.center.state is CenterState.CONFIRMED:
            centers.append(update.center)
            i += 3
        else:
            i += 1

    trend = None
    if centers:
        trend_update = classify_trend(
            tuple(centers),
            symbol=raw_bars[0].symbol,
            lower_motion_ids=tuple(s.id for s in normalized),
        )
        trend = trend_update.trend

    latest_stroke = strokes.strokes[-1] if strokes.strokes else None
    latest_segment = segments.segments[-1] if segments.segments else None
    return {
        "status": "OK",
        "raw_bars": len(raw_bars),
        "processed_bars": len(inclusion.bars),
        "fractals": len(fractals.fractals),
        "strokes": len(strokes.strokes),
        "segments": len(segments.segments),
        "finalized_segments": len(normalized),
        "seed_centers": len(centers),
        "latest_stroke": None
        if latest_stroke is None
        else {
            "direction": latest_stroke.direction.value,
            "state": latest_stroke.state.value,
            "revision": latest_stroke.revision,
            "confirmation_timestamp": latest_stroke.confirmation_timestamp.isoformat(),
        },
        "latest_segment": None
        if latest_segment is None
        else {
            "direction": latest_segment.direction.value,
            "state": latest_segment.state.value,
            "revision": latest_segment.revision,
            "confirmation_timestamp": latest_segment.confirmation_timestamp.isoformat()
            if latest_segment.confirmation_timestamp
            else None,
        },
        "trend": None
        if trend is None
        else {
            "classification": trend.current_classification.value,
            "state": trend.state.value,
            "final_type": trend.final_type.value if trend.final_type else None,
            "center_count": len(trend.center_ids),
            "confirmation_timestamp": trend.confirmation_timestamp.isoformat(),
        },
        "issues": [],
    }


def technical_summary(rows: list[dict], *, complete: bool = True) -> dict:
    if not rows:
        return {"status": "DATA_INCOMPLETE"}
    bundle = build_bundle(
        volumes=[float(r.get("volume") or 0) for r in rows],
        closes=[float(r["close"]) for r in rows],
        highs=[float(r["high"]) for r in rows],
        lows=[float(r["low"]) for r in rows],
        bar_complete=complete,
    )
    return {
        "status": "OK",
        "volume": bundle.volume_state.value,
        "macd": bundle.macd_state.value,
        "boll": bundle.boll_state.value,
        "kdj": bundle.kdj_state.value,
        "confirmation": bundle.confirmation.value,
        "provisional": bundle.provisional,
        "reason_codes": list(bundle.reason_codes),
    }


def analyze_symbol(shard: dict, summary_item: dict) -> dict:
    symbol = shard["symbol"]
    key = str(symbol["key"])
    market = str(symbol.get("market") or "CN")
    sources = symbol.get("sources") or {}
    m15_source = str(sources.get("m15") or "")
    daily_rows = list(symbol.get("daily") or [])
    m15_rows = list(symbol.get("m15") or [])

    weekly_rows = aggregate_weekly(daily_rows, market=market)
    m30_rows = aggregate_m15(m15_rows, market=market, source=m15_source, group_size=2)
    m120_rows = aggregate_m15(m15_rows, market=market, source=m15_source, group_size=8)

    datasets = {
        Timeframe.WEEKLY: (weekly_rows, str(sources.get("daily") or ""), "daily"),
        Timeframe.DAILY: (daily_rows, str(sources.get("daily") or ""), "daily"),
        Timeframe.M120: (m120_rows, m15_source, "m15"),
        Timeframe.M30: (m30_rows, m15_source, "m15"),
    }
    structures: dict[str, dict] = {}
    technical: dict[str, dict] = {}
    for timeframe, (rows, source, field) in datasets.items():
        raw = rows_to_raw_bars(
            rows,
            symbol=key,
            market=market,
            timeframe=timeframe,
            source=source,
            tick_source_field=field,
        )
        structures[timeframe.value] = analyze_structure(
            raw,
            tick_size=infer_tick_size(symbol, field=field),
        )
        if timeframe in (Timeframe.DAILY, Timeframe.M120, Timeframe.M30):
            technical[timeframe.value] = technical_summary(rows, complete=bool(rows and rows[-1].get("_complete", True)))

    return {
        "symbol": key,
        "name": symbol.get("name"),
        "proxy_for": symbol.get("proxy_for"),
        "data_quality": {
            "usable": bool(summary_item.get("usable_for_analysis")),
            "fresh": bool(summary_item.get("fresh_for_analysis")),
            "covers_1400": bool(summary_item.get("covers_1400_bar")),
            "using_cache": bool(summary_item.get("using_cache")),
            "proxy_substitution": summary_item.get("proxy_substitution") or {},
            "warnings": summary_item.get("warnings") or [],
            "errors": summary_item.get("errors") or [],
        },
        "derived_counts": {
            "weekly": len(weekly_rows),
            "daily": len(daily_rows),
            "120m": len(m120_rows),
            "30m": len(m30_rows),
            "5m": 0,
        },
        "structures": structures,
        "technical": technical,
        "execution_5m": {
            "status": "UNAVAILABLE",
            "reason": "M5_SOURCE_UNAVAILABLE",
            "policy": "DO_NOT_SYNTHESIZE_5M_FROM_15M",
        },
        "canonical_signal": {
            "status": "NOT_EMITTED_BY_SHADOW_SMOKE",
            "reason": "SMOKE_ADAPTER_VALIDATES_REAL_DATA_THROUGH_STRUCTURE_AND_CONFIRMATION_LAYERS_ONLY",
        },
    }


def run_shadow_smoke(summary_path: Path, market_dir: Path, output_path: Path | None = None) -> dict:
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    symbols = summary.get("symbols") or {}
    results = []
    failures = []
    for key, item in symbols.items():
        shard_path = market_dir / f"{key}.json"
        if not shard_path.exists():
            failures.append({"symbol": key, "reason": "SHARD_MISSING"})
            continue
        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        try:
            result = analyze_symbol(shard, item)
            results.append(result)
            bad = [tf for tf, state in result["structures"].items() if state.get("status") not in ("OK", "UNRESOLVED")]
            if bad:
                failures.append({"symbol": key, "reason": "STRUCTURE_DATA_FAILURE", "timeframes": bad})
        except Exception as exc:
            failures.append({"symbol": key, "reason": f"{type(exc).__name__}: {exc}"})

    payload = {
        "mode": "SHADOW_SMOKE",
        "generated_from_market_snapshot": summary.get("generated_at"),
        "total_symbols": len(symbols),
        "analyzed_symbols": len(results),
        "ready_for_1400_analysis_symbols": summary.get("ready_for_1400_analysis_symbols"),
        "all_ready_for_1400_analysis": summary.get("all_ready_for_1400_analysis"),
        "failures": failures,
        "symbols": results,
        "guardrails": {
            "live_broker_execution": False,
            "5m_synthesized": False,
            "canonical_trade_alerts_enabled": False,
        },
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(output_path)
    return payload