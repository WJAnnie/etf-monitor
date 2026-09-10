"""Notification pipeline status recorder.

Used by the portfolio advice pipeline to record whether each delivery stage
completed. This intentionally does not send notifications; it only records
pipeline state so failures are visible instead of silent.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

STATUS_PATH = Path("data/notification_status.json")


def update_status(**kwargs: str) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {}
    if STATUS_PATH.exists():
        data = json.loads(STATUS_PATH.read_text(encoding="utf-8"))

    data.update(kwargs)
    data["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


if __name__ == "__main__":
    update_status(pipeline="initialized")
