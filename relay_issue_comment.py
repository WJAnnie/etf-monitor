from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from monitor import send_notifications

MARKER = "<!-- portfolio-advice -->"


def main() -> int:
    event_path = os.getenv("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        raise RuntimeError("GITHUB_EVENT_PATH 未配置")

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    comment = payload.get("comment") or {}
    issue = payload.get("issue") or {}

    body = str(comment.get("body") or "").strip()
    if MARKER not in body:
        print("不是持仓建议评论，跳过推送。")
        return 0

    clean_body = body.replace(MARKER, "").strip()
    if not clean_body:
        print("持仓建议评论为空，跳过推送。")
        return 0

    issue_number = issue.get("number", "")
    title = "📊 14:00 持仓操作建议"
    if issue_number:
        title += f" · #{issue_number}"

    send_notifications(title, clean_body)
    print("[OK] 持仓建议已转发到飞书和 Server酱")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)
