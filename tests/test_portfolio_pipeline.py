from __future__ import annotations

import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import relay_issue_comment
from portfolio_market_data import build_summary
from scripts.pipeline_ready_check import evaluate_summary
from scripts.pipeline_status import read_status

CN_TZ = ZoneInfo("Asia/Shanghai")


def ready_summary() -> dict:
    now = datetime.now(CN_TZ).isoformat()
    return {
        "generated_at": now,
        "market_activity_today": True,
        "all_usable": True,
        "all_fresh": True,
        "all_cover_1400_bar": True,
        "all_ready_for_1400_analysis": True,
    }


def stale_symbol() -> dict:
    daily_row = {
        "time": "2026-09-10",
        "open": 1.0,
        "close": 1.0,
        "high": 1.0,
        "low": 1.0,
        "volume": 1,
        "amount": 1,
    }
    m15_row = {
        "time": "2026-09-10 15:00:00",
        "open": 1.0,
        "close": 1.0,
        "high": 1.0,
        "low": 1.0,
        "volume": 1,
        "amount": 1,
    }
    return {
        "key": "sample",
        "name": "Sample",
        "secid": "1.000001",
        "tx_symbol": "sh000001",
        "market": "CN",
        "proxy_for": "Sample",
        "proxy_note": "",
        "daily": [daily_row] * 60,
        "m15": [m15_row] * 80,
        "sources": {"daily": "sina", "m15": "sina"},
        "warnings": [],
        "errors": [],
        "cache_origin": {},
    }


class PortfolioPipelineTests(unittest.TestCase):
    def test_normal_weekday_with_stale_bars_is_not_inferred_as_holiday(self) -> None:
        result = {
            "generated_at": "2026-09-11T14:01:00+08:00",
            "symbols": {"sample": stale_symbol()},
        }
        summary = build_summary(
            result,
            datetime(2026, 9, 11, 14, 1, tzinfo=CN_TZ),
        )
        self.assertTrue(summary["market_activity_today"])
        self.assertFalse(summary["all_fresh"])

    def test_ready_and_holiday_are_distinct(self) -> None:
        ready = evaluate_summary(ready_summary())
        self.assertEqual(ready.status, "READY")

        holiday_payload = ready_summary()
        holiday_payload["market_activity_today"] = False
        holiday = evaluate_summary(holiday_payload)
        self.assertEqual(holiday.status, "HOLIDAY_SKIP")

    def test_stale_summary_is_not_ready(self) -> None:
        payload = ready_summary()
        payload["generated_at"] = "2020-01-01T14:00:00+08:00"
        result = evaluate_summary(payload)
        self.assertEqual(result.status, "NOT_READY")
        self.assertIn("stale", result.reason)

    def test_relay_blocks_stale_report_and_records_degraded_delivery(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            event_path = root / "event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "issue": {"number": 1},
                        "comment": {
                            "id": 123,
                            "body": "<!-- portfolio-advice -->\\nold report",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "data" / "latest_market_summary.json").write_text(
                json.dumps({**ready_summary(), "generated_at": "2020-01-01T14:00:00+08:00"}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "issue_comment",
                    "GITHUB_EVENT_PATH": str(event_path),
                    "PIPELINE_STATUS_PATH": str(root / "data" / "pipeline_status.json"),
                    "MARKET_SUMMARY_PATH": str(root / "data" / "latest_market_summary.json"),
                },
                clear=False,
            ), patch.object(
                relay_issue_comment,
                "send_all",
                return_value={
                    "feishu": {"status": "success"},
                    "serverchan": {"status": "failed", "error": "simulated"},
                },
            ) as send:
                old_cwd = Path.cwd()
                try:
                    os.chdir(root)
                    self.assertEqual(relay_issue_comment.main(), 0)
                finally:
                    os.chdir(old_cwd)
                send.assert_called_once()
                status = read_status(root / "data" / "pipeline_status.json")
                self.assertEqual(status["analysis"], "blocked")
                self.assertEqual(status["overall"], "degraded")
                self.assertEqual(status["feishu"], "success")

    def test_relay_sends_ready_report(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "data").mkdir()
            event_path = root / "event.json"
            event_path.write_text(
                json.dumps(
                    {
                        "issue": {"number": 1},
                        "comment": {
                            "id": 456,
                            "body": "<!-- portfolio-advice -->\\nready report",
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "data" / "latest_market_summary.json").write_text(
                json.dumps(ready_summary()),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "GITHUB_EVENT_NAME": "issue_comment",
                    "GITHUB_EVENT_PATH": str(event_path),
                    "PIPELINE_STATUS_PATH": str(root / "data" / "pipeline_status.json"),
                    "MARKET_SUMMARY_PATH": str(root / "data" / "latest_market_summary.json"),
                },
                clear=False,
            ), patch.object(
                relay_issue_comment,
                "send_all",
                return_value={
                    "feishu": {"status": "success"},
                    "serverchan": {"status": "success"},
                },
            ) as send:
                old_cwd = Path.cwd()
                try:
                    os.chdir(root)
                    self.assertEqual(relay_issue_comment.main(), 0)
                finally:
                    os.chdir(old_cwd)
                send.assert_called_once_with("📊 14:00 持仓操作建议 · #1", "ready report")
                status = read_status(root / "data" / "pipeline_status.json")
                self.assertEqual(status["analysis"], "accepted")
                self.assertEqual(status["overall"], "success")


if __name__ == "__main__":
    unittest.main()
