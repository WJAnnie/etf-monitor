import json

import scripts.collect_candidate_bars_v3 as mod


class _Response:
    def __init__(self, payload):
        self.text = json.dumps(payload, ensure_ascii=False)


def _row(day: str, close: float) -> list[str]:
    return [day, str(close - 0.1), str(close), str(close + 0.2), str(close - 0.2), "1000", "0"]


def test_tencent_daily_long_pages_backward_by_end_date(monkeypatch):
    calls = []
    pages = {
        "": [_row("2026-09-08", 10.8), _row("2026-09-09", 10.9)],
        "2026-09-07": [_row("2026-09-06", 10.6), _row("2026-09-07", 10.7)],
        "2026-09-05": [_row("2026-09-05", 10.5)],
    }

    def fake_request(url, *, params=None, referer):
        param = str((params or {}).get("param") or "")
        parts = param.split(",")
        assert parts[0] == "sh600000"
        assert parts[1] == "day"
        assert parts[-1] == "qfq"
        end_date = parts[3]
        calls.append(end_date)
        payload = {"data": {"sh600000": {"qfqday": pages.get(end_date, [])}}}
        return _Response(payload)

    monkeypatch.setattr(mod.v2, "_request", fake_request)
    rows, warnings = mod.tencent_daily_long("600000", limit=5, page_size=2)

    assert warnings == []
    assert [row["time"] for row in rows] == [
        "2026-09-05", "2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09"
    ]
    assert calls == ["", "2026-09-07", "2026-09-05"]


def test_tencent_daily_long_deduplicates_overlapping_pages(monkeypatch):
    calls = []
    pages = {
        "": [_row("2026-09-08", 10.8), _row("2026-09-09", 10.9)],
        "2026-09-07": [_row("2026-09-07", 10.7), _row("2026-09-08", 10.8)],
        "2026-09-06": [_row("2026-09-05", 10.5), _row("2026-09-06", 10.6)],
    }

    def fake_request(url, *, params=None, referer):
        end_date = str((params or {}).get("param") or "").split(",")[3]
        calls.append(end_date)
        return _Response({"data": {"sh600000": {"qfqday": pages.get(end_date, [])}}})

    monkeypatch.setattr(mod.v2, "_request", fake_request)
    rows, _ = mod.tencent_daily_long("600000", limit=4, page_size=2)
    assert [row["time"] for row in rows] == ["2026-09-06", "2026-09-07", "2026-09-08", "2026-09-09"]
    assert len({row["time"] for row in rows}) == 4


def test_tencent_daily_long_short_page_does_not_stop_if_older_history_exists(monkeypatch):
    calls = []
    pages = {
        # 模拟生产接口常见情况：请求500根却只返回略少于500根，但老股仍有更早历史。
        "": [_row("2026-09-07", 10.7), _row("2026-09-08", 10.8), _row("2026-09-09", 10.9)],
        "2026-09-06": [_row("2026-09-04", 10.4), _row("2026-09-05", 10.5), _row("2026-09-06", 10.6)],
        "2026-09-03": [_row("2026-09-03", 10.3)],
    }

    def fake_request(url, *, params=None, referer):
        end_date = str((params or {}).get("param") or "").split(",")[3]
        calls.append(end_date)
        return _Response({"data": {"sh600000": {"qfqday": pages.get(end_date, [])}}})

    monkeypatch.setattr(mod.v2, "_request", fake_request)
    rows, warnings = mod.tencent_daily_long("600000", limit=7, page_size=5)

    assert warnings == []
    assert [row["time"] for row in rows] == [
        "2026-09-03", "2026-09-04", "2026-09-05", "2026-09-06",
        "2026-09-07", "2026-09-08", "2026-09-09",
    ]
    assert calls == ["", "2026-09-06", "2026-09-03"]


def test_tencent_daily_page_uses_alternate_host_after_primary_501(monkeypatch):
    calls = []

    def fake_request(url, *, params=None, referer):
        calls.append(url)
        if url == mod.TENCENT_DAILY_HOSTS[0]:
            raise RuntimeError("501 Not Implemented")
        return _Response({"data": {"sh600000": {"qfqday": [_row("2026-09-09", 10.9)]}}})

    monkeypatch.setattr(mod.v2, "_request", fake_request)
    rows, errors = mod._fetch_tencent_daily_page("sh600000", end_date="", count=500)

    assert len(rows) == 1
    assert calls == list(mod.TENCENT_DAILY_HOSTS)
    assert any("501" in item for item in errors)
