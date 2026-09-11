from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import requests

from monitor import notify_feishu, notify_serverchan
from scripts.pipeline_ready_check import evaluate_summary, load_summary
from scripts.pipeline_status import update_status

MARKER = "<!-- portfolio-advice -->"
TIMEOUT = 12
SUMMARY_PATH = Path(os.getenv("MARKET_SUMMARY_PATH", "data/latest_market_summary.json"))


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

    code = result.get("code", result.get("StatusCode"))
    if str(code) not in ("0", "None"):
        message = result.get("msg") or result.get("StatusMessage") or result
        raise RuntimeError(f"飞书 Webhook 通知失败: code={code}, message={message}")
    return "飞书 Webhook"


def send_all(title: str, body: str) -> dict[str, dict[str, Any]]:
    """Attempt both channels independently and return an auditable result."""
    results: dict[str, dict[str, Any]] = {}
    try:
        if os.getenv("FEISHU_WEBHOOK", "").strip():
            channel = notify_feishu_webhook(title, body)
        else:
            channel = notify_feishu(title, body)
        print(f"[OK] {channel} 通知接口返回成功")
        results["feishu"] = {"status": "success", "channel": channel}
    except Exception as exc:
        print(f"[ERROR] 飞书通知失败: {exc}")
        results["feishu"] = {"status": "failed", "error": str(exc)}

    try:
        channel = notify_serverchan(title, body)
        print(f"[OK] {channel} 通知接口返回成功")
        results["serverchan"] = {"status": "success", "channel": channel}
    except Exception as exc:
        print(f"[ERROR] Server酱通知失败: {exc}")
        results["serverchan"] = {"status": "failed", "error": str(exc)}

    return results


def _event_context() -> tuple[str, str, str, int | None, int | None]:
    event_name = os.getenv("GITHUB_EVENT_NAME", "local").strip() or "local"
    event_path = os.getenv("GITHUB_EVENT_PATH", "").strip()
    if not event_path:
        return event_name, "", "", None, None

    payload = json.loads(Path(event_path).read_text(encoding="utf-8"))
    comment = payload.get("comment") or {}
    issue = payload.get("issue") or {}
    body = str(comment.get("body") or "").strip()
    issue_number = issue.get("number")
    comment_id = comment.get("id")
    return event_name, body, str(issue_number or ""), issue_number, comment_id


def _status_value(results: dict[str, dict[str, Any]], key: str) -> str:
    return (results.get(key) or {}).get("status", "not_attempted")


def _overall(results: dict[str, dict[str, Any]]) -> str:
    values = [_status_value(results, key) for key in ("feishu", "serverchan")]
    if values == ["success", "success"]:
        return "success"
    if "success" in values:
        return "degraded"
    return "failed"


def _record_base(event_name: str, issue_number: int | None, comment_id: int | None) -> None:
    update_status(
        run_id=os.getenv("GITHUB_RUN_ID", ""),
        event=event_name,
        issue_number=issue_number,
        comment_id=comment_id,
        github_issue="comment_received" if issue_number else "not_applicable",
    )


def _scheduled_blocked_alert_body() -> str:
    return "行情数据未达到14:00分析标准，今日持仓建议暂不推送；请查看 GitHub Actions 运行日志。"


def main() -> int:
    event_name, raw_body, issue_label, issue_number, comment_id = _event_context()
    _record_base(event_name, issue_number, comment_id)

    if event_name == "schedule":
        gate = evaluate_summary(load_summary(SUMMARY_PATH))
        update_status(market_data=gate.status.lower(), market_data_reason=gate.reason)

        if gate.status == "HOLIDAY_SKIP":
            update_status(analysis="holiday_skip", overall="skipped")
            print(f"[SKIP] {gate.reason}")
            return 0

        if gate.status != "READY":
            results = send_all(
                "⚠️ 14:00 持仓分析未生成",
                _scheduled_blocked_alert_body(),
            )
            update_status(
                analysis="blocked",
                feishu=_status_value(results, "feishu"),
                serverchan=_status_value(results, "serverchan"),
                notification_errors={
                    key: value.get("error")
                    for key, value in results.items()
                    if value.get("status") == "failed"
                },
                overall=_overall(results),
            )
            return 0 if "success" in {
                _status_value(results, "feishu"),
                _status_value(results, "serverchan"),
            } else 1

        update_status(analysis="awaiting_report", overall="pending")
        print("[OK] 14:00 行情已通过质量门，等待持仓分析正文评论。")
        return 0

    if event_name == "workflow_dispatch" and not raw_body:
        update_status(analysis="manual_dry_run", overall="success")
        print("[OK] 手动运行仅验证编排与状态记录；未提供报告正文。")
        return 0

    if MARKER not in raw_body:
        update_status(analysis="skipped", overall="success")
        print("不是持仓建议评论，跳过推送。")
        return 0

    report = raw_body.replace(MARKER, "").strip()
    if not report:
        update_status(analysis="skipped", overall="success")
        print("持仓建议评论为空，跳过推送。")
        return 0

    gate = evaluate_summary(load_summary(SUMMARY_PATH))
    update_status(market_data=gate.status.lower(), market_data_reason=gate.reason)

    if gate.status == "HOLIDAY_SKIP":
        update_status(analysis="holiday_skip", overall="skipped")
        print(f"[SKIP] {gate.reason}")
        return 0

    if gate.status != "READY":
        alert_title = "⚠️ 14:00 持仓建议未推送"
        alert_body = (
            f"行情质量门未通过，已阻止把旧/不完整行情转发为今日建议。\n"
            f"原因：{gate.reason}\n"
            f"生成时间：{gate.generated_at or '未知'}\n"
            f"失败字段：{', '.join(gate.failed_fields) or '无'}"
        )
        results = send_all(alert_title, alert_body)
        update_status(
            analysis="blocked",
            feishu=_status_value(results, "feishu"),
            serverchan=_status_value(results, "serverchan"),
            notification_errors={
                key: value.get("error")
                for key, value in results.items()
                if value.get("status") == "failed"
            },
            overall=_overall(results),
        )
        return 0 if "success" in {_status_value(results, "feishu"), _status_value(results, "serverchan")} else 1

    title = "📊 14:00 持仓操作建议"
    if issue_label:
        title += f" · #{issue_label}"
    results = send_all(title, report)
    update_status(
        analysis="accepted",
        feishu=_status_value(results, "feishu"),
        serverchan=_status_value(results, "serverchan"),
        notification_errors={
            key: value.get("error")
            for key, value in results.items()
            if value.get("status") == "failed"
        },
        overall=_overall(results),
    )
    print(f"[OK] 持仓建议编排完成：overall={_overall(results)}")
    return 0 if "success" in {_status_value(results, "feishu"), _status_value(results, "serverchan")} else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        update_status(analysis="failed", overall="failed", fatal_error=str(exc))
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)
