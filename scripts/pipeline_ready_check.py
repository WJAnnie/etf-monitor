#!/usr/bin/env python3
"""Quality gate for the 14:00 portfolio-advice pipeline.

The collector publishes a summary snapshot. This module decides whether that
snapshot is safe to use for a same-day advice notification. It deliberately
returns HOLIDAY_SKIP separately from NOT_READY so a market holiday is not
reported as a data outage.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

CN_TZ = ZoneInfo("Asia/Shanghai")
SUMMARY = Path("data/latest_market_summary.json")

REQUIRED_FIELDS = (
    "market_activity_today",
    "all_usable",
    "all_fresh",
    "all_cover_1400_bar",
    "all_ready_for_1400_analysis",
)


@dataclass(frozen=True)
class Readiness:
    status: str
    reason: str
    generated_at: str | None = None
    failed_fields: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["failed_fields"] = list(self.failed_fields)
        return payload


def _parse_datetime(value: object) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=CN_TZ) if parsed.tzinfo is None else parsed.astimezone(CN_TZ)


def load_summary(path: Path = SUMMARY) -> dict:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def evaluate_summary(summary: dict, *, now: datetime | None = None) -> Readiness:
    current = (now or datetime.now(CN_TZ)).astimezone(CN_TZ)
    if not summary:
        return Readiness("NOT_READY", "market summary missing")

    missing = tuple(field for field in REQUIRED_FIELDS if field not in summary)
    if missing:
        return Readiness("NOT_READY", "market summary contract incomplete", failed_fields=missing)

    generated_at = str(summary.get("generated_at") or "")
    generated = _parse_datetime(generated_at)
    if generated is None:
        return Readiness("NOT_READY", f"invalid generated_at: {generated_at or '<empty>'}")

    if generated.date() != current.date():
        return Readiness(
            "NOT_READY",
            f"stale generated_at: {generated_at}; expected {current.date().isoformat()}",
            generated_at=generated_at,
        )

    # New snapshots carry an explicit calendar decision. Fall back to the old
    # field only for backward compatibility with already-published snapshots.
    expected_trading_day = summary.get("expected_trading_day")
    if expected_trading_day is None:
        expected_trading_day = summary.get("market_activity_today") is True
    else:
        expected_trading_day = expected_trading_day is True

    if not expected_trading_day:
        reason = str(summary.get("market_calendar_reason") or "scheduled_exchange_closure")
        return Readiness(
            "HOLIDAY_SKIP",
            f"calendar says non-trading day: {reason}",
            generated_at=generated_at,
        )

    failed = tuple(field for field in REQUIRED_FIELDS if summary.get(field) is not True)
    if failed:
        return Readiness(
            "NOT_READY",
            "market data quality gate failed",
            generated_at=generated_at,
            failed_fields=failed,
        )

    return Readiness("READY", "market data ready for 14:00 analysis", generated_at=generated_at)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=SUMMARY)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()

    result = evaluate_summary(load_summary(args.summary))
    if args.as_json:
        print(json.dumps(result.as_dict(), ensure_ascii=False))
    else:
        details = f" failed={','.join(result.failed_fields)}" if result.failed_fields else ""
        print(f"{result.status}: {result.reason}{details}")

    return {"READY": 0, "HOLIDAY_SKIP": 2}.get(result.status, 1)


if __name__ == "__main__":
    raise SystemExit(main())
