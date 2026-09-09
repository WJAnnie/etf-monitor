from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from scripts.collect_full_a_universe import CN_TZ, atomic_json
from trading_skill.deep_scan_handoff import build_deep_scan_queue


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("full-a-results/fundamental_quality_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/deep_scan_queue_latest.json"))
    parser.add_argument("--capacity-max", type=int, default=150)
    parser.add_argument("--soft-target-min", type=int, default=80)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    payload = json.loads(args.input.read_text(encoding="utf-8"))
    stock_rows = list(payload.get("stock_assessments") or [])
    fund_rows = list(payload.get("fund_product_assessments") or [])
    queue = build_deep_scan_queue(
        stock_rows,
        fund_rows,
        capacity_max=args.capacity_max,
        soft_target_min=args.soft_target_min,
    )

    by_type = Counter(item.security_type for item in queue)
    by_status = Counter(item.status for item in queue)
    by_tier = Counter(item.tier.value for item in queue)
    result = {
        "mode": "STEP3_TO_STEP4_DEEP_SCAN_HANDOFF",
        "generated_at": datetime.now(CN_TZ).isoformat(),
        "source_quality_file": str(args.input),
        "policy": {
            "capacity_max": args.capacity_max,
            "soft_target_min": args.soft_target_min,
            "soft_target_is_not_quota": True,
            "reject_never_enters": True,
            "high_risk_never_enters": True,
            "pass_has_priority": True,
            "low_priority_watch_never_fills_quota": True,
            "watch_may_enter_only_as_safe_research_observation": True,
            "this_stage_emits_trade_signal": False,
        },
        "summary": {
            "input_stock": len(stock_rows),
            "input_fund": len(fund_rows),
            "selected": len(queue),
            "by_security_type": dict(by_type),
            "by_status": dict(by_status),
            "by_tier": dict(by_tier),
        },
        "candidates": [item.as_dict() for item in queue],
    }
    atomic_json(args.output, result)

    print("STEP3->4深扫队列:", len(queue))
    print("按证券类型:", dict(by_type))
    print("按质量状态:", dict(by_status))
    print("按队列层级:", dict(by_tier))

    if args.strict:
        problems: list[str] = []
        if len(queue) > args.capacity_max:
            problems.append(f"深扫队列超过容量上限:{len(queue)}>{args.capacity_max}")
        if any(item.status == "REJECT" for item in queue):
            problems.append("REJECT错误进入STEP4队列")
        if any(item.tier.value == "EXCLUDE" for item in queue):
            problems.append("EXCLUDE错误进入STEP4队列")
        if not queue and (stock_rows or fund_rows):
            problems.append("有STEP3候选但深扫队列为空")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
