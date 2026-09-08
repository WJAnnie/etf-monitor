from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

from trading_skill.shadow_m5 import run_shadow_smoke_with_m5


def _requests():
    try:
        import requests
    except ImportError as exc:
        raise RuntimeError("requests is required only for Feishu notification") from exc
    return requests


def _notify_webhook(title: str, body: str) -> str:
    requests = _requests()
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        raise RuntimeError("FEISHU_WEBHOOK is not configured")
    payload: dict[str, object] = {"msg_type": "text", "content": {"text": f"{title}\n{body}"}}
    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(string_to_sign.encode("utf-8"), digestmod=hashlib.sha256).digest()
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(digest).decode("utf-8")
    response = requests.post(webhook, json=payload, timeout=12)
    response.raise_for_status()
    result = response.json()
    code = result.get("code", result.get("StatusCode", 0))
    if str(code) not in ("0", "None"):
        raise RuntimeError(f"Feishu webhook failed: {result}")
    return "FEISHU_WEBHOOK"


def _tenant_token() -> str:
    requests = _requests()
    app_id = os.getenv("FEISHU_APP_ID", "").strip()
    app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
    if not app_id or not app_secret:
        raise RuntimeError("FEISHU_APP_ID / FEISHU_APP_SECRET not configured")
    response = requests.post(
        "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
        json={"app_id": app_id, "app_secret": app_secret},
        timeout=12,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") not in (0, "0", None) or not result.get("tenant_access_token"):
        raise RuntimeError(f"Feishu tenant token failed: {result}")
    return str(result["tenant_access_token"])


def _notify_app(title: str, body: str) -> str:
    requests = _requests()
    chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()
    if not chat_id:
        raise RuntimeError("FEISHU_CHAT_ID not configured")
    token = _tenant_token()
    response = requests.post(
        "https://open.feishu.cn/open-apis/im/v1/messages",
        params={"receive_id_type": "chat_id"},
        headers={"Authorization": f"Bearer {token}"},
        json={
            "receive_id": chat_id,
            "msg_type": "text",
            "content": json.dumps({"text": f"{title}\n{body}"}, ensure_ascii=False),
        },
        timeout=12,
    )
    response.raise_for_status()
    result = response.json()
    if result.get("code") not in (0, "0", None):
        raise RuntimeError(f"Feishu app message failed: {result}")
    return "FEISHU_APP"


def notify_feishu(title: str, body: str) -> str:
    # Feishu only. This shadow runner intentionally has no ServerChan code path.
    if os.getenv("FEISHU_WEBHOOK", "").strip():
        return _notify_webhook(title, body)
    return _notify_app(title, body)


def build_message(payload: dict) -> tuple[str, str]:
    failures = payload.get("failures") or []
    m5_failures = payload.get("m5_failures") or []
    analyzed = int(payload.get("analyzed_symbols") or 0)
    total = int(payload.get("total_symbols") or 0)
    ready = int(payload.get("ready_for_1400_analysis_symbols") or 0)
    m5_loaded = int((payload.get("guardrails") or {}).get("real_5m_loaded_symbols") or 0)
    title = "✅ Trading Skill Shadow Smoke" if not failures and not m5_failures else "⚠️ Trading Skill Shadow Smoke"
    lines = [
        f"基础行情快照：{payload.get('generated_from_market_snapshot')}",
        f"5m行情快照：{payload.get('m5_snapshot')}",
        f"真实标的：{analyzed}/{total}",
        f"14:00可分析：{ready}/{total}",
        f"真实5m：{m5_loaded}/{total}",
        f"失败：基础{len(failures)} / 5m{len(m5_failures)}",
        "5m：只使用真实5分钟K线，不允许15m反推",
        "交易：Shadow only，不连接券商、不产生正式买卖提醒",
        "",
    ]
    for item in payload.get("symbols") or []:
        daily = (item.get("structures") or {}).get("daily") or {}
        m30 = (item.get("structures") or {}).get("30m") or {}
        tech30 = (item.get("technical") or {}).get("30m") or {}
        m5 = item.get("execution_5m") or {}
        trend = (daily.get("trend") or {}).get("classification") or "NO_TREND"
        lines.append(
            f"{item.get('name')}｜日线:{trend}｜30m笔:{m30.get('strokes', 0)}｜30m:{tech30.get('confirmation', 'NA')}｜5m:{m5.get('technical_confirmation', m5.get('status', 'NA'))}"
        )
    if failures or m5_failures:
        lines.extend(["", "失败明细："])
        lines.extend(f"- 基础 {x}" for x in failures)
        lines.extend(f"- 5m {x}" for x in m5_failures)
    return title, "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--summary", type=Path, default=Path("../data/latest_market_summary.json"))
    parser.add_argument("--market-dir", type=Path, default=Path("../data/market"))
    parser.add_argument("--m5-summary", type=Path, default=Path("../data/latest_market_5m_summary.json"))
    parser.add_argument("--m5-market-dir", type=Path, default=Path("../data/market5"))
    parser.add_argument("--output", type=Path, default=Path("shadow-results/latest_shadow_smoke.json"))
    parser.add_argument("--strict", action="store_true")
    parser.add_argument("--notify-feishu", action="store_true")
    args = parser.parse_args()

    payload = run_shadow_smoke_with_m5(
        args.summary,
        args.market_dir,
        args.m5_summary,
        args.m5_market_dir,
        args.output,
    )
    print(json.dumps({
        "snapshot": payload.get("generated_from_market_snapshot"),
        "m5_snapshot": payload.get("m5_snapshot"),
        "total": payload.get("total_symbols"),
        "analyzed": payload.get("analyzed_symbols"),
        "ready_1400": payload.get("ready_for_1400_analysis_symbols"),
        "failures": payload.get("failures"),
        "m5_failures": payload.get("m5_failures"),
        "guardrails": payload.get("guardrails"),
    }, ensure_ascii=False, indent=2))

    if args.notify_feishu:
        title, body = build_message(payload)
        channel = notify_feishu(title, body)
        print(f"[OK] shadow smoke notification delivered via {channel}")

    if args.strict:
        if payload.get("failures"):
            print("[FATAL] shadow smoke contains base failures", file=sys.stderr)
            return 1
        if payload.get("m5_failures"):
            print("[FATAL] shadow smoke contains real 5m failures", file=sys.stderr)
            return 1
        if int(payload.get("analyzed_symbols") or 0) != int(payload.get("total_symbols") or 0):
            print("[FATAL] not every symbol was analyzed", file=sys.stderr)
            return 1
        if not payload.get("all_ready_for_1400_analysis"):
            print("[FATAL] base market snapshot is not fully ready for 14:00 analysis", file=sys.stderr)
            return 1
        if int((payload.get("guardrails") or {}).get("real_5m_loaded_symbols") or 0) != int(payload.get("total_symbols") or 0):
            print("[FATAL] real 5m was not loaded for every symbol", file=sys.stderr)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
