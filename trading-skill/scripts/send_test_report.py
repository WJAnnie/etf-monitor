from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from trading_skill.notifications import build_system_test_report, notify_feishu


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "success"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--shadow-result", type=Path, default=Path("shadow-results/latest_shadow_smoke.json"))
    parser.add_argument("--core-ok", default=os.getenv("CORE_TEST_OK", "false"))
    parser.add_argument("--shadow-ok", default=os.getenv("SHADOW_TEST_OK", "false"))
    parser.add_argument("--core-count", default=os.getenv("CORE_TEST_COUNT", ""))
    parser.add_argument("--note", default=os.getenv("TEST_REPORT_NOTE", ""))
    args = parser.parse_args()

    payload = None
    if args.shadow_result.exists():
        payload = json.loads(args.shadow_result.read_text(encoding="utf-8"))

    count = None
    if str(args.core_count).strip().isdigit():
        count = int(args.core_count)

    title, body = build_system_test_report(
        payload,
        core_ok=_truthy(args.core_ok),
        shadow_ok=_truthy(args.shadow_ok),
        core_test_count=count,
        note=args.note or None,
    )
    channel = notify_feishu(title, body)
    print(f"[OK] 中文测试报告已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
