from __future__ import annotations

import argparse
from pathlib import Path

from trading_skill.notifications import notify_feishu


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, default=Path("reports/latest_market_report.txt"))
    args = parser.parse_args()
    text = args.report.read_text(encoding="utf-8").strip()
    if not text:
        raise RuntimeError("市场报告为空")
    lines = text.splitlines()
    title = lines[0].strip() if lines else "📊 A股盘后报告"
    body = "\n".join(lines[1:]).lstrip()
    channel = notify_feishu(title, body)
    print(f"[OK] 即时市场报告已通过{channel}发送")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
