import scripts.enrich_cross_industry_v3 as mod


def _item(code: str, name: str):
    return {"code": code, "name": name, "market": 1 if code.startswith("6") else 0}


def test_cross_industry_resolution_retries_transient_failure(monkeypatch):
    attempts = {}

    def fake_fetch(code: str, market: int):
        attempts[code] = attempts.get(code, 0) + 1
        if code == "600001" and attempts[code] == 1:
            raise RuntimeError("临时502")
        return "船舶制造" if code == "600001" else "半导体设备"

    monkeypatch.setattr(mod, "fetch_actual_industry", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)

    resolved, errors, retried = mod.resolve_cross_industries(
        [_item("600001", "测试一"), _item("688002", "测试二")]
    )
    assert resolved == {"600001": "船舶制造", "688002": "半导体设备"}
    assert errors == []
    assert retried == 1
    assert attempts["600001"] == 2


def test_cross_industry_resolution_keeps_final_failure_unresolved(monkeypatch):
    attempts = {}

    def fake_fetch(code: str, market: int):
        attempts[code] = attempts.get(code, 0) + 1
        raise RuntimeError("持续不可用")

    monkeypatch.setattr(mod, "fetch_actual_industry", fake_fetch)
    monkeypatch.setattr(mod.time, "sleep", lambda _: None)

    resolved, errors, retried = mod.resolve_cross_industries([_item("600001", "测试一")])
    assert resolved == {}
    assert retried == 1
    assert attempts["600001"] == 2
    assert len(errors) == 1
    assert errors[0]["code"] == "600001"
    assert "首轮" in errors[0]["error"] and "重试" in errors[0]["error"]
