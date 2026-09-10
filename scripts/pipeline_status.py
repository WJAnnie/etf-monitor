#!/usr/bin/env python3
"""Persist one observable status record for a portfolio-advice run."""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_PATH = Path("data/pipeline_status.json")


def status_path(path: Path | None = None) -> Path:
    return path or Path(os.getenv("PIPELINE_STATUS_PATH", str(DEFAULT_PATH)))


def read_status(path: Path | None = None) -> dict[str, Any]:
    target = status_path(path)
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def update_status(path: Path | None = None, **updates: Any) -> dict[str, Any]:
    target = status_path(path)
    current = read_status(target)
    current.setdefault("schema_version", 1)
    current.setdefault("pipeline", "portfolio-advice")
    current.update(updates)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()

    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    temporary.write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(target)
    return current


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True)
    parser.add_argument("--value", required=True)
    parser.add_argument("--detail")
    args = parser.parse_args()

    updates: dict[str, Any] = {args.stage: args.value}
    if args.detail:
        updates[f"{args.stage}_detail"] = args.detail
    update_status(**updates)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
