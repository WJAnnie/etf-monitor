from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from trading_skill.decision import Action, RiskState
from trading_skill.monitoring import (
    AlertDeduper,
    DeliveryStatus,
    FeishuAdapter,
    HealthStatus,
    MonitoringLevel,
    ServerChanAdapter,
    build_alert,
    build_trade_alert,
    dispatch,
    dispatch_ready,
    persist_system_health,
    scan_slot_context,
    system_health,
)
from trading_skill.persistence import (
    AnalysisStatus,
    RunJournal,
    RunStage,
    Snapshot,
    StateEvent,
    StateRepository,
    atomic_publish,
    state_hash,
)
from trading_skill.runtime import AnalysisRunCoordinator, can_dispatch
from trading_skill.universe import UniverseInput, UniverseScanner


TZ = ZoneInfo("Asia/Shanghai")
NOW = datetime(2026, 9, 8, 14, 30, tzinfo=TZ)


def _snapshot(analysis_id: str = "a1", *, symbol: str = "X", payload: dict | None = None) -> Snapshot:
    payload = payload or {"state": "WAIT_2B"}
    return Snapshot(
        "snap-1",
        analysis_id,
        symbol,
        NOW.isoformat(),
        payload,
        "data-v1",
        "1.0.0",
        {"chan": "1.0.0"},
        state_hash(payload),
    )


def _event(event_id: str = "e1", *, analysis_id: str = "a1", new_state: str = "B") -> StateEvent:
    return StateEvent(
        event_id,
        "X",
        "tracker",
        "tracker-1",
        "STATE_CHANGE",
        "A",
        new_state,
        (),
        (),
        NOW.isoformat(),
        NOW.isoformat(),
        analysis_id,
    )


def _alert(analysis_id: str = "a1", *, old_state: str | None = None, new_state: str = "PREPARE"):
    return build_alert(
        symbol="X",
        analysis_id=analysis_id,
        snapshot_id="snap-1",
        action=Action.PREPARE_BUY,
        risk=RiskState.L0,
        old_state=old_state,
        new_state=new_state,
        event_time=NOW,
    )


def test_runtime_ready_is_only_set_after_state_commit_and_atomic_publish(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    run = AnalysisRunCoordinator(repo)
    run.start("a1", started_at=NOW, data_as_of=NOW)
    assert repo.load_manifest("a1")["status"] == "RUNNING"

    run.commit_state(
        analysis_id="a1",
        symbol="X",
        current={"state": "B"},
        trackers=[("tracker-1", {"state": "B"}, 2)],
        events=[_event()],
        snapshot=_snapshot(),
    )
    assert repo.load_manifest("a1")["status"] == "RUNNING"
    assert repo.load_journal("a1")["last_completed_stage"] == "STATE_COMMITTED"

    artifact = tmp_path / "analysis.json"
    result = run.publish_ready("a1", artifact_path=artifact, result={"action": "WAIT_2B"}, completed_at=NOW)
    assert result.manifest.status is AnalysisStatus.READY
    assert repo.load_manifest("a1")["status"] == "READY"
    assert can_dispatch(repo, "a1", artifact)
    repo.close()


def test_running_or_failed_analysis_cannot_build_trade_alert():
    kwargs = dict(
        symbol="X",
        analysis_id="a1",
        snapshot_id="s1",
        action=Action.BUY_TRANCHE_1,
        risk=RiskState.L0,
        old_state=None,
        new_state="BUY",
        event_time=NOW,
    )
    assert build_trade_alert(analysis_status="RUNNING", **kwargs) is None
    assert build_trade_alert(analysis_status="FAILED", **kwargs) is None
    assert build_trade_alert(analysis_status="READY", **kwargs) is not None


def test_ready_with_corrupted_artifact_cannot_dispatch(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    run = AnalysisRunCoordinator(repo)
    run.start("a1", started_at=NOW)
    run.commit_state(analysis_id="a1", symbol="X", current={"x": 1}, trackers=[], events=[], snapshot=_snapshot())
    artifact = tmp_path / "analysis.json"
    run.publish_ready("a1", artifact_path=artifact, result={"x": 1}, completed_at=NOW)
    artifact.write_text('{"x":999}', encoding="utf-8")
    alert = _alert()
    touched = {"count": 0}
    adapter = FeishuAdapter(enabled=True, sender=lambda _: touched.__setitem__("count", 1))
    assert dispatch_ready(alert, [adapter], repository=repo, artifact_path=str(artifact)) == ()
    assert touched["count"] == 0
    repo.close()


def test_crash_after_state_commit_resumes_publish_without_reanalysis(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    run = AnalysisRunCoordinator(repo)
    run.start("a1", started_at=NOW)
    run.commit_state(analysis_id="a1", symbol="X", current={"state": "B"}, trackers=[], events=[_event()], snapshot=_snapshot())
    artifact = tmp_path / "analysis.json"

    resumed = run.resume_after_crash(
        "a1",
        artifact_path=artifact,
        result={"action": "HOLD", "from_snapshot": True},
        completed_at=NOW + timedelta(minutes=1),
    )
    assert resumed.manifest.status is AnalysisStatus.READY
    assert can_dispatch(repo, "a1", artifact)
    repo.close()


def test_crash_after_publish_before_ready_promotes_existing_valid_artifact(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    run = AnalysisRunCoordinator(repo)
    run.start("a1", started_at=NOW)
    run.commit_state(analysis_id="a1", symbol="X", current={"x": 1}, trackers=[], events=[], snapshot=_snapshot())
    artifact = tmp_path / "analysis.json"
    checksum = atomic_publish(artifact, {"x": 1})
    repo.save_journal(RunJournal("a1", RunStage.PUBLISHED, "PUBLISHED", str(artifact), checksum))
    repo.db.commit()

    resumed = run.resume_after_crash("a1", artifact_path=artifact, result=None, completed_at=NOW)
    assert resumed.manifest.status is AnalysisStatus.READY
    assert resumed.manifest.result_checksum == checksum
    repo.close()


def test_corrupted_current_recovers_from_valid_snapshot(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    repo.transaction_commit(symbol="X", current={"state": "B"}, trackers=[], events=[], snapshot=_snapshot(payload={"state": "B"}))
    repo.db.execute("UPDATE current_state SET payload='not-json' WHERE symbol='X'")
    repo.db.commit()
    assert repo.recover_current("X") == {"state": "B"}
    repo.close()


def test_recovery_with_events_requires_explicit_reducer_instead_of_guessing(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    snap = _snapshot(payload={"state": "A"})
    repo.transaction_commit(symbol="X", current={"state": "A"}, trackers=[], events=[], snapshot=snap)
    repo.append_event(_event("later", new_state="C"))
    repo.db.execute("UPDATE current_state SET payload='bad-json' WHERE symbol='X'")
    repo.db.commit()
    with pytest.raises(ValueError, match="STATE_RECOVERY_REDUCER_REQUIRED"):
        repo.recover_current("X")
    recovered = repo.recover_current("X", reducer=lambda state, event: {"state": event["new_state"]})
    assert recovered == {"state": "C"}
    repo.close()


def test_alert_dedup_survives_repository_restart(tmp_path):
    path = tmp_path / "state.db"
    repo = StateRepository(path)
    alert = _alert()
    dedup = AlertDeduper(repo)
    assert dedup.should_send(alert)
    dedup.record(alert)
    repo.close()

    repo2 = StateRepository(path)
    dedup2 = AlertDeduper(repo2)
    assert not dedup2.should_send(alert)
    repo2.close()


def test_delivery_retry_reuses_delivery_identity_and_persists_latest_attempt(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    alert = _alert()
    calls = {"n": 0}

    def sender(_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")

    adapter = FeishuAdapter(enabled=True, sender=sender)
    first = dispatch(alert, [adapter], repository=repo)[0]
    second = adapter.deliver(alert, attempt=2)
    repo.record_delivery(second)
    assert first.delivery_id == second.delivery_id
    assert first.status is DeliveryStatus.FAILED_RETRYABLE
    assert second.status is DeliveryStatus.SUCCESS
    assert repo.load_delivery(second.delivery_id)["attempt"] == 2
    repo.close()


def test_serverchan_disabled_missing_secret_still_allows_feishu_with_persistence(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    touched = {"server": 0, "feishu": 0}
    server = ServerChanAdapter(enabled=False, secret_loader=lambda: touched.__setitem__("server", 1))
    feishu = FeishuAdapter(enabled=True, sender=lambda _: touched.__setitem__("feishu", 1))
    records = dispatch(_alert(), [server, feishu], repository=repo)
    assert touched == {"server": 0, "feishu": 1}
    assert records[0].status is DeliveryStatus.SKIPPED_DISABLED
    assert records[1].status is DeliveryStatus.SUCCESS
    repo.close()


def test_universe_scanner_applies_cheap_filters_before_deep_analysis():
    calls: list[str] = []
    scanner = UniverseScanner(max_deep_scan_symbols=2, deep_analyzer=lambda item: calls.append(item.symbol) or {"ok": True})
    records = scanner.scan(
        [
            UniverseInput("A", prospects_score=90, low_position_score=80, heat_score=70),
            UniverseInput("B", hard_veto=True, prospects_score=100, low_position_score=100, heat_score=100),
            UniverseInput("C", prospects_score=80, low_position_score=80, heat_score=80),
            UniverseInput("D", prospects_score=70, low_position_score=70, heat_score=70),
        ]
    )
    assert "B" not in calls
    assert len(calls) == 2
    assert next(record for record in records if record.symbol == "B").universe_eligible is False


def test_open_position_is_monitored_even_when_new_trade_ineligible():
    calls: list[str] = []
    scanner = UniverseScanner(max_deep_scan_symbols=0, deep_analyzer=lambda item: calls.append(item.symbol) or "position-risk")
    records = scanner.scan([UniverseInput("POS", hard_veto=True, open_position=True)])
    record = records[0]
    assert not record.universe_eligible
    assert record.deep_scanned
    assert record.monitoring_level is MonitoringLevel.M3
    assert calls == ["POS"]


def test_overheated_candidate_is_not_automatically_top_ranked():
    scanner = UniverseScanner(max_deep_scan_symbols=0, deep_analyzer=lambda _: None)
    records = scanner.scan(
        [
            UniverseInput("HOT", prospects_score=60, low_position_score=30, heat_score=100, overheated=True),
            UniverseInput("WARM", prospects_score=85, low_position_score=85, heat_score=65),
        ]
    )
    assert records[0].symbol == "WARM"


def test_1430_and_1450_keep_daily_provisional_after_close_confirms():
    assert scan_slot_context("14:30").daily_current_bar_provisional
    assert not scan_slot_context("14:30").daily_confirmation_allowed
    assert scan_slot_context("14:50").daily_current_bar_provisional
    assert scan_slot_context("AFTER_CLOSE").daily_confirmation_allowed
    assert not scan_slot_context("AFTER_CLOSE").daily_current_bar_provisional


def test_system_health_snapshot_can_be_persisted(tmp_path):
    repo = StateRepository(tmp_path / "state.db")
    health = system_health(
        {
            "MARKET_DATA": HealthStatus.HEALTHY,
            "PERSISTENCE": HealthStatus.HEALTHY,
            "CHAN_ENGINE": HealthStatus.HEALTHY,
            "FEISHU": HealthStatus.DEGRADED,
        }
    )
    persist_system_health(repo, health, as_of=NOW)
    saved = repo.load_current("__SYSTEM_HEALTH__")
    assert saved["overall_status"] == "DEGRADED"
    assert saved["new_trade_permission"] is True
    repo.close()
