from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from trading_skill.a_share_bars import CN_TZ
from trading_skill.data.market_bars import SecurityIdentity, collect_security_bars


MAX_WORKERS = 6


def atomic_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _identity(item: dict) -> SecurityIdentity:
    raw_market = item.get("market")
    if raw_market is None:
        raise ValueError("STEP4_IDENTITY_MARKET_REQUIRED")
    return SecurityIdentity(
        code=str(item.get("code") or ""),
        name=str(item.get("name") or ""),
        market=int(raw_market),
        security_type=str(item.get("security_type") or ""),
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--queue", type=Path, default=Path("full-a-results/deep_scan_queue_latest.json"))
    parser.add_argument("--output", type=Path, default=Path("full-a-results/step4_bars_latest.json"))
    parser.add_argument("--workers", type=int, default=MAX_WORKERS)
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()

    now = datetime.now(CN_TZ)
    payload = json.loads(args.queue.read_text(encoding="utf-8"))
    candidates = list(payload.get("candidates") or [])
    identity_errors: list[dict] = []
    tasks: list[tuple[dict, SecurityIdentity]] = []
    for item in candidates:
        try:
            tasks.append((item, _identity(item)))
        except Exception as exc:
            identity_errors.append({
                "code": item.get("code"),
                "name": item.get("name"),
                "error": str(exc),
            })

    results: list[dict] = []
    errors: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, min(int(args.workers), 10))) as pool:
        futures = {pool.submit(collect_security_bars, identity, now=now): (item, identity) for item, identity in tasks}
        for future in as_completed(futures):
            item, identity = futures[future]
            try:
                collection = future.result().as_dict()
                collection["step3_status"] = item.get("status")
                collection["step3_tier"] = item.get("tier")
                collection["research_priority"] = item.get("research_priority")
                collection["source_routes"] = item.get("source_routes") or []
                collection["category"] = item.get("category")
                results.append(collection)
            except Exception as exc:
                errors.append({
                    "code": identity.code,
                    "name": identity.name,
                    "market": identity.market,
                    "security_type": identity.security_type,
                    "error": str(exc)[:1000],
                })

    results.sort(key=lambda item: (str(item.get("security_type")), str(item.get("code"))))
    output = {
        "mode": "STEP4_OFFICIAL_MULTI_TIMEFRAME_BARS",
        "generated_at": now.isoformat(),
        "source_queue": str(args.queue),
        "design_contract": {
            "market_identity_is_required": True,
            "market_is_never_inferred_from_code_prefix": True,
            "weekly_from_daily": True,
            "m120_from_real_m30": True,
            "m30_is_direct_real_history": True,
            "m5_is_direct_real_history": True,
            "price_basis_is_explicit": True,
            "this_stage_emits_trade_signal": False,
        },
        "candidate_count": len(candidates),
        "identity_valid_count": len(tasks),
        "loaded_count": len(results),
        "identity_errors": identity_errors,
        "errors": errors,
        "symbols": results,
    }
    atomic_json(args.output, output)

    print(
        f"STEP4队列={len(candidates)}，身份有效={len(tasks)}，五周期成功={len(results)}，"
        f"身份错误={len(identity_errors)}，行情错误={len(errors)}"
    )
    if errors:
        print("行情异常前10:", errors[:10])

    if args.strict:
        problems: list[str] = []
        if identity_errors:
            problems.append(f"STEP4存在证券身份缺失:{len(identity_errors)}")
        if candidates and len(results) / len(candidates) < 0.75:
            problems.append(f"STEP4五周期行情成功率过低:{len(results)}/{len(candidates)}")
        bad_market = [item for item in results if item.get("market") not in {0, 1}]
        if bad_market:
            problems.append(f"STEP4输出出现非法market:{len(bad_market)}")
        incomplete = [
            item for item in results
            if not all((item.get("quality") or {}).get(key) for key in (
                "daily_history_ok", "m30_history_ok", "m120_history_ok", "m5_history_ok"
            ))
        ]
        if incomplete:
            problems.append(f"STEP4已加载证券存在周期历史门槛不足:{len(incomplete)}")
        if problems:
            raise SystemExit("；".join(problems))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
