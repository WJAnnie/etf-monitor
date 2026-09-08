from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from trading_skill.shadow_m5 import attach_real_m5

TZ = ZoneInfo("Asia/Shanghai")


def make_rows(count: int = 240) -> list[dict]:
    rows = []
    ts = datetime(2026, 9, 1, 9, 35, tzinfo=TZ)
    for i in range(count):
        base = 10 + i * 0.01
        rows.append(
            {
                "time": ts.strftime("%Y-%m-%d %H:%M:%S"),
                "open": base,
                "high": base + 0.08,
                "low": base - 0.05,
                "close": base + 0.03,
                "volume": 1000 + i,
                "amount": 0,
            }
        )
        ts += timedelta(minutes=5)
    return rows


def base_payload() -> dict:
    return {
        "symbols": [
            {
                "symbol": "X",
                "name": "测试ETF",
                "derived_counts": {"5m": 0},
                "structures": {},
                "technical": {},
                "execution_5m": {"status": "UNAVAILABLE"},
            }
        ],
        "guardrails": {"5m_synthesized": False},
    }


def write_summary(path: Path, *, fresh: bool = True) -> None:
    path.write_text(
        json.dumps(
            {
                "generated_at": "2026-09-08T14:50:00+08:00",
                "symbols": {
                    "X": {
                        "source": "sina",
                        "fresh_for_analysis": fresh,
                        "using_cache": not fresh,
                        "covers_1400_bar": True,
                        "covers_1450_bar": True,
                        "latest_m5_end_time": "2026-09-08T14:50:00+08:00",
                        "errors": [] if fresh else ["cache"],
                    }
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def write_shard(directory: Path, count: int = 240) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "X.json").write_text(
        json.dumps(
            {
                "generated_at": "2026-09-08T14:50:00+08:00",
                "symbol": {
                    "key": "X",
                    "name": "测试ETF",
                    "market": "CN",
                    "source": "sina",
                    "m5": make_rows(count),
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_missing_m5_summary_never_synthesizes(tmp_path):
    payload = attach_real_m5(
        base_payload(),
        m5_summary_path=tmp_path / "missing.json",
        m5_market_dir=tmp_path / "market5",
    )
    assert payload["guardrails"]["5m_synthesized"] is False
    assert payload["guardrails"]["real_5m_loaded_symbols"] == 0
    assert payload["symbols"][0]["execution_5m"]["reason"] == "M5_SUMMARY_MISSING"


def test_stale_or_cached_m5_is_blocked_even_when_shard_exists(tmp_path):
    summary = tmp_path / "summary.json"
    market = tmp_path / "market5"
    write_summary(summary, fresh=False)
    write_shard(market)
    payload = attach_real_m5(base_payload(), m5_summary_path=summary, m5_market_dir=market)
    assert payload["guardrails"]["real_5m_loaded_symbols"] == 0
    assert payload["symbols"][0]["execution_5m"]["status"] == "DATA_INCOMPLETE"
    assert payload["m5_failures"][0]["reason"] == "M5_NOT_FRESH"


def test_insufficient_real_m5_history_is_blocked(tmp_path):
    summary = tmp_path / "summary.json"
    market = tmp_path / "market5"
    write_summary(summary, fresh=True)
    write_shard(market, count=239)
    payload = attach_real_m5(base_payload(), m5_summary_path=summary, m5_market_dir=market)
    assert payload["guardrails"]["real_5m_loaded_symbols"] == 0
    assert payload["m5_failures"][0]["reason"] == "M5_HISTORY_INSUFFICIENT"


def test_fresh_real_m5_runs_structure_and_technical_pipeline(tmp_path):
    summary = tmp_path / "summary.json"
    market = tmp_path / "market5"
    write_summary(summary, fresh=True)
    write_shard(market)
    payload = attach_real_m5(base_payload(), m5_summary_path=summary, m5_market_dir=market)
    item = payload["symbols"][0]
    assert payload["guardrails"]["5m_synthesized"] is False
    assert payload["guardrails"]["real_5m_loaded_symbols"] == 1
    assert payload["m5_failures"] == []
    assert item["derived_counts"]["5m"] == 240
    assert item["execution_5m"]["status"] == "OK"
    assert item["execution_5m"]["source"] == "sina"
    assert item["execution_5m"]["policy"] == "REAL_5M_ONLY"
    assert item["structures"]["5m"]["status"] == "OK"
    assert item["technical"]["5m"]["status"] == "OK"
