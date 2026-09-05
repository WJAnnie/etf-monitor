from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path

import requests

from monitor import notify_feishu, notify_serverchan

MARKER = "<!-- portfolio-advice -->"
TIMEOUT = 12


def notify_feishu_webhook(title: str, body: str) -> str:
    webhook = os.getenv("FEISHU_WEBHOOK", "").strip()
    if not webhook:
        raise RuntimeError("FEISHU_WEBHOOK 未配置")

    payload: dict[str, object] = {
        "msg_type": "text",
        "content": {"text": f"{title}\n{body}"},
    }

    secret = os.getenv("FEISHU_SECRET", "").strip()
    if secret:
        timestamp = str(int(time.time()))
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        payload["timestamp"] = timestamp
        payload["sign"] = base64.b64encode(digest).decode("utf-8")

    resp = requests.post(webhook, json=payload, timeout=TIMEOUT)
    resp.raise_for_status()
    try:
        result = resp.json()
    except ValueError as exc:
        raise RuntimeError(f"飞书 Webhook 返回非 JSON: {resp.text[:180]!r}") from exc

    code = result.get("code", result.get("StatusCode", 0))
    if str(code) not in ("0", "None"):
        message = result.get("msg") or result.get("StatusMessage") or result
        raise RuntimeError(f"飞书 Webhook 通知失败: code={code}, message={message}")
    return "飞书 Webhook"


def send_all(title: str, body: str) -> None:
    errors: list[str] = []

    # 优先使用自定义机器人 Webhook；未配置时兼容仓库原有的应用机器人方式。
    try:
        if os.getenv("FEISHU_WEBHOOK", "").strip():
            channel = notify_feishu_webhook(title, body)
        else:
            channel = notify_feishu(title, body)
        print(f"[OK] {channel} 通知接口返回成功")
    except Exception as exc:
        errors.append(f"飞书: {exc}")
        print(f"[ERROR] 飞书通知失败: {exc}")

    try:
        channel = notify_serverchan(title, body)
        print(f"[OK] {channel} 通知接口返回成功")
    except Exception as exc:
        errors.append(f"Server酱: {exc}")
        print(f"[ERROR] Server酱通知失败: {exc}")

    if len(errors) == 2:
        raise RuntimeError("；".join(errors))


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

    send_all(title, clean_body)
    print("[OK] 持仓建议已转发到飞书和 Server酱")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)
