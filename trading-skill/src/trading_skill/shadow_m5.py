from __future__ import annotations

import json
from pathlib import Path

from trading_skill.domain.enums import Timeframe
from trading_skill.shadow import (
    analyze_structure,
    infer_tick_size,
    rows_to_raw_bars,
    run_shadow_smoke,
    technical_summary,
)


def attach_real_m5(payload: dict, *, m5_summary_path: Path, m5_market_dir: Path) -> dict:
    """Attach real 5m analysis to an existing Shadow Smoke payload.

    Only a per-symbol `fresh_for_analysis=true` snapshot is eligible. Cached/stale/missing
    5m data is never converted into an execution signal and 15m is never used as a substitute.
    """
    if not m5_summary_path.exists():
        payload["m5_snapshot"] = None
        payload["guardrails"]["real_5m_loaded_symbols"] = 0
        for item in payload.get("symbols") or []:
            item["execution_5m"] = {
                "status": "UNAVAILABLE",
                "reason": "M5_SUMMARY_MISSING",
                "policy": "DO_NOT_SYNTHESIZE_5M_FROM_15M",
            }
        return payload

    summary = json.loads(m5_summary_path.read_text(encoding="utf-8"))
    payload["m5_snapshot"] = summary.get("generated_at")
    summary_symbols = summary.get("symbols") or {}
    loaded = 0
    failures: list[dict] = []

    for item in payload.get("symbols") or []:
        key = str(item.get("symbol"))
        meta = summary_symbols.get(key) or {}
        if not meta:
            item["execution_5m"] = {
                "status": "UNAVAILABLE",
                "reason": "M5_SYMBOL_MISSING",
                "policy": "DO_NOT_SYNTHESIZE_5M_FROM_15M",
            }
            failures.append({"symbol": key, "reason": "M5_SYMBOL_MISSING"})
            continue
        if not meta.get("fresh_for_analysis"):
            item["execution_5m"] = {
                "status": "DATA_INCOMPLETE",
                "reason": "M5_NOT_FRESH",
                "source": meta.get("source"),
                "using_cache": bool(meta.get("using_cache")),
                "errors": meta.get("errors") or [],
                "policy": "DO_NOT_SYNTHESIZE_5M_FROM_15M",
            }
            failures.append({"symbol": key, "reason": "M5_NOT_FRESH"})
            continue

        shard_path = m5_market_dir / f"{key}.json"
        if not shard_path.exists():
            item["execution_5m"] = {
                "status": "UNAVAILABLE",
                "reason": "M5_SHARD_MISSING",
                "policy": "DO_NOT_SYNTHESIZE_5M_FROM_15M",
            }
            failures.append({"symbol": key, "reason": "M5_SHARD_MISSING"})
            continue

        shard = json.loads(shard_path.read_text(encoding="utf-8"))
        symbol = shard.get("symbol") or {}
        rows = list(symbol.get("m5") or [])
        if len(rows) < 240:
            item["execution_5m"] = {
                "status": "DATA_INCOMPLETE",
                "reason": "M5_HISTORY_INSUFFICIENT",
                "bars": len(rows),
                "policy": "DO_NOT_SYNTHESIZE_5M_FROM_15M",
            }
            failures.append({"symbol": key, "reason": "M5_HISTORY_INSUFFICIENT", "bars": len(rows)})
            continue

        raw = rows_to_raw_bars(
            rows,
            symbol=key,
            market=str(symbol.get("market") or "CN"),
            timeframe=Timeframe.M5,
            source=str(meta.get("source") or symbol.get("source") or "real_m5"),
            tick_source_field="m5",
        )
        structure = analyze_structure(raw, tick_size=infer_tick_size(symbol, field="m5"))
        technical = technical_summary(rows, complete=True)
        status = structure.get("status")
        if status not in ("OK", "UNRESOLVED"):
            failures.append({"symbol": key, "reason": "M5_STRUCTURE_DATA_FAILURE", "status": status})

        item.setdefault("derived_counts", {})["5m"] = len(rows)
        item.setdefault("structures", {})["5m"] = structure
        item.setdefault("technical", {})["5m"] = technical
        item["execution_5m"] = {
            "status": "OK" if status in ("OK", "UNRESOLVED") else "DATA_INCOMPLETE",
            "source": meta.get("source"),
            "bars": len(rows),
            "fresh_for_analysis": True,
            "covers_1400_bar": bool(meta.get("covers_1400_bar")),
            "covers_1450_bar": bool(meta.get("covers_1450_bar")),
            "latest_end_time": meta.get("latest_m5_end_time"),
            "structure_status": status,
            "technical_confirmation": technical.get("confirmation"),
            "policy": "REAL_5M_ONLY",
        }
        loaded += 1

    payload["guardrails"]["5m_synthesized"] = False
    payload["guardrails"]["real_5m_loaded_symbols"] = loaded
    payload["guardrails"]["real_5m_required_for_execution"] = True
    payload["m5_failures"] = failures
    return payload


def run_shadow_smoke_with_m5(
    summary_path: Path,
    market_dir: Path,
    m5_summary_path: Path,
    m5_market_dir: Path,
    output_path: Path | None = None,
) -> dict:
    payload = run_shadow_smoke(summary_path, market_dir, None)
    payload = attach_real_m5(payload, m5_summary_path=m5_summary_path, m5_market_dir=m5_market_dir)
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = output_path.with_suffix(output_path.suffix + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(output_path)
    return payload
