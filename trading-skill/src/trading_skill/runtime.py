from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from trading_skill.persistence import (
    AnalysisManifest,
    AnalysisStatus,
    RunJournal,
    RunStage,
    Snapshot,
    StateEvent,
    StateRepository,
    atomic_publish,
    file_checksum,
    verify_publish,
)


@dataclass(frozen=True, slots=True)
class PublishResult:
    manifest: AnalysisManifest
    journal: RunJournal
    artifact_path: str


class AnalysisRunCoordinator:
    """Coordinates persistence and publish order without containing trading theory."""

    def __init__(self, repository: StateRepository):
        self.repository = repository

    def start(self, analysis_id: str, *, started_at: datetime, data_as_of: datetime | None = None) -> AnalysisManifest:
        manifest = AnalysisManifest(
            analysis_id=analysis_id,
            status=AnalysisStatus.RUNNING,
            started_at=started_at.isoformat(),
            data_as_of=data_as_of.isoformat() if data_as_of else None,
        )
        journal = RunJournal(analysis_id, RunStage.INIT)
        self.repository.save_manifest(manifest)
        self.repository.save_journal(journal)
        self.repository.db.commit()
        return manifest

    def commit_state(
        self,
        *,
        analysis_id: str,
        symbol: str,
        current: dict,
        trackers: list[tuple[str, dict, int]],
        events: list[StateEvent],
        snapshot: Snapshot,
    ) -> RunJournal:
        manifest = self.repository.load_manifest(analysis_id)
        if not manifest or manifest["status"] != AnalysisStatus.RUNNING.value:
            raise ValueError("ANALYSIS_NOT_RUNNING")
        if snapshot.analysis_id != analysis_id:
            raise ValueError("SNAPSHOT_ANALYSIS_MISMATCH")
        self.repository.transaction_commit(
            symbol=symbol,
            current=current,
            trackers=trackers,
            events=events,
            snapshot=snapshot,
        )
        journal = RunJournal(analysis_id, RunStage.STATE_COMMITTED)
        self.repository.save_journal(journal)
        self.repository.db.commit()
        return journal

    def publish_ready(
        self,
        analysis_id: str,
        *,
        artifact_path: str | Path,
        result: dict,
        completed_at: datetime,
    ) -> PublishResult:
        manifest_raw = self.repository.load_manifest(analysis_id)
        journal_raw = self.repository.load_journal(analysis_id)
        if not manifest_raw or manifest_raw["status"] != AnalysisStatus.RUNNING.value:
            raise ValueError("ANALYSIS_NOT_RUNNING")
        if not journal_raw or journal_raw["last_completed_stage"] not in (
            RunStage.STATE_COMMITTED.value,
            RunStage.RESULT_BUILT.value,
            RunStage.PUBLISHED.value,
        ):
            raise ValueError("STATE_NOT_COMMITTED")

        artifact_path = str(artifact_path)
        checksum = atomic_publish(artifact_path, result)
        if not verify_publish(artifact_path, checksum):
            raise ValueError("PUBLISH_CHECKSUM_FAILED")

        # Persist PUBLISHED before READY. READY is intentionally the final state mutation.
        journal = RunJournal(
            analysis_id,
            RunStage.PUBLISHED,
            publish_status="PUBLISHED",
            artifact_path=artifact_path,
            result_checksum=checksum,
        )
        self.repository.save_journal(journal)
        self.repository.db.commit()

        manifest = AnalysisManifest(
            analysis_id=analysis_id,
            status=AnalysisStatus.READY,
            started_at=manifest_raw["started_at"],
            completed_at=completed_at.isoformat(),
            data_as_of=manifest_raw.get("data_as_of"),
            result_checksum=checksum,
        )
        self.repository.save_manifest(manifest)
        self.repository.db.commit()
        return PublishResult(manifest, journal, artifact_path)

    def resume_after_crash(
        self,
        analysis_id: str,
        *,
        artifact_path: str | Path,
        result: dict | None,
        completed_at: datetime,
    ) -> PublishResult:
        manifest_raw = self.repository.load_manifest(analysis_id)
        journal_raw = self.repository.load_journal(analysis_id)
        if not manifest_raw or not journal_raw:
            raise ValueError("RECOVERY_JOURNAL_MISSING")
        if manifest_raw["status"] == AnalysisStatus.READY.value:
            checksum = manifest_raw.get("result_checksum")
            if not checksum or not verify_publish(artifact_path, checksum):
                raise ValueError("READY_ARTIFACT_INVALID")
            manifest = AnalysisManifest(
                analysis_id=analysis_id,
                status=AnalysisStatus.READY,
                started_at=manifest_raw["started_at"],
                completed_at=manifest_raw.get("completed_at"),
                data_as_of=manifest_raw.get("data_as_of"),
                result_checksum=checksum,
            )
            journal = RunJournal(
                analysis_id,
                RunStage.PUBLISHED,
                "PUBLISHED",
                str(artifact_path),
                checksum,
            )
            return PublishResult(manifest, journal, str(artifact_path))

        stage = journal_raw["last_completed_stage"]
        artifact = Path(artifact_path)
        if stage == RunStage.PUBLISHED.value and artifact.exists():
            checksum = journal_raw.get("result_checksum") or file_checksum(artifact)
            if not verify_publish(artifact, checksum):
                raise ValueError("PUBLISHED_ARTIFACT_INVALID")
            ready = AnalysisManifest(
                analysis_id=analysis_id,
                status=AnalysisStatus.READY,
                started_at=manifest_raw["started_at"],
                completed_at=completed_at.isoformat(),
                data_as_of=manifest_raw.get("data_as_of"),
                result_checksum=checksum,
            )
            self.repository.save_manifest(ready)
            self.repository.db.commit()
            journal = RunJournal(analysis_id, RunStage.PUBLISHED, "PUBLISHED", str(artifact), checksum)
            return PublishResult(ready, journal, str(artifact))

        if stage == RunStage.STATE_COMMITTED.value:
            if result is None:
                raise ValueError("RESULT_REQUIRED_FOR_RESUME")
            return self.publish_ready(
                analysis_id,
                artifact_path=artifact,
                result=result,
                completed_at=completed_at,
            )
        raise ValueError("UNSAFE_RECOVERY_STAGE")

    def fail(self, analysis_id: str, *, error_summary: str, completed_at: datetime) -> AnalysisManifest:
        raw = self.repository.load_manifest(analysis_id)
        if not raw:
            raise ValueError("ANALYSIS_MANIFEST_MISSING")
        failed = AnalysisManifest(
            analysis_id=analysis_id,
            status=AnalysisStatus.FAILED,
            started_at=raw["started_at"],
            completed_at=completed_at.isoformat(),
            data_as_of=raw.get("data_as_of"),
            error_summary=error_summary,
        )
        self.repository.save_manifest(failed)
        self.repository.db.commit()
        return failed


def can_dispatch(repository: StateRepository, analysis_id: str, artifact_path: str | Path) -> bool:
    manifest = repository.load_manifest(analysis_id)
    if not manifest or manifest["status"] != AnalysisStatus.READY.value:
        return False
    checksum = manifest.get("result_checksum")
    return bool(checksum and verify_publish(artifact_path, checksum))
