#!/usr/bin/env python3
"""Validate that the 14:00 analysis pipeline has fresh market data before generating advice."""
import json
from pathlib import Path
from datetime import datetime, timezone

SUMMARY = Path("data/latest_market_summary.json")


def main():
    if not SUMMARY.exists():
        raise SystemExit("market summary missing")

    data = json.loads(SUMMARY.read_text(encoding="utf-8"))
    required = [
        "market_activity_today",
        "all_usable",
        "all_fresh",
        "all_cover_1400_bar",
        "all_ready_for_1400_analysis",
    ]

    missing = [k for k in required if k not in data]
    if missing:
        raise SystemExit(f"missing fields: {missing}")

    generated = data.get("generated_at", "")
    today = datetime.now(timezone.utc).astimezone().date().isoformat()

    if not generated.startswith(today):
        raise SystemExit(f"stale generated_at: {generated}")

    failed = [k for k in required if data.get(k) is not True]
    if failed:
        raise SystemExit(f"market not ready: {failed}")

    print("market data ready for 14:00 analysis")


if __name__ == "__main__":
    main()
