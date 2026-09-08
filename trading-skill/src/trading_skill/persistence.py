from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Callable


class AnalysisStatus(StrEnum):
    RUNNING = "RUNNING"
    READY = "READY"
    FAILED = "FAILED"


class RunStage(StrEnum):
    INIT = "INIT"
    DATA_READY = "DATA_READY"
    ANALYSIS_READY = "ANALYSIS_READY"
    STATE_COMMITTED = "STATE_COMMITTED"
    RESULT_BUILT = "RESULT_BUILT"
    PUBLISHED = "PUBLISHED"
    DISPATCH_PENDING = "DISPATCH_PENDING"
    COMPLETE = "COMPLETE"


@dataclass(frozen=True, slots=True)
class StateEvent:
    event_id: str
    symbol: str
    object_type: str
    object_id: str
    event_type: str
    old_state: str | None
    new_state: str | None
    reason_codes: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    effective_at: str
    recorded_at: str
    analysis_id: str
    schema_version: str = "1.0.0"
    engine_version: str = "1.0.0"
    sequence_number: int = 0


@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id: str
    analysis_id: str
    symbol: str
    as_of: str
    payload: dict
    data_version: str
    schema_version: str
    engine_versions: dict
    state_hash: str
    event_sequence_number: int = 0


@dataclass(frozen=True, slots=True)
class AnalysisManifest:
    analysis_id: str
    status: AnalysisStatus
    started_at: str
    completed_at: str | None = None
    data_as_of: str | None = None
    result_checksum: str | None = None
    error_summary: str | None = None


@dataclass(frozen=True, slots=True)
class RunJournal:
    analysis_id: str
    last_completed_stage: RunStage
    publish_status: str = "PENDING"
    artifact_path: str | None = None
    result_checksum: str | None = None


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def state_hash(payload: dict) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_checksum(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class StateRepository:
    def __init__(self, path: str | Path):
        self.path = str(path)
        self.db = sqlite3.connect(self.path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS current_state(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS trackers(tracker_id TEXT PRIMARY KEY,payload TEXT NOT NULL,revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,sequence_number INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(symbol,sequence_number));
            CREATE TABLE IF NOT EXISTS snapshots(snapshot_id TEXT PRIMARY KEY,analysis_id TEXT NOT NULL,symbol TEXT NOT NULL,payload TEXT NOT NULL,state_hash TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS manifests(analysis_id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS journals(analysis_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS alert_dedup(dedup_key TEXT PRIMARY KEY,alert_id TEXT NOT NULL,symbol TEXT NOT NULL,event_date TEXT NOT NULL,priority INTEGER NOT NULL,state_fingerprint TEXT,recorded_at TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS delivery_records(delivery_id TEXT PRIMARY KEY,alert_id TEXT NOT NULL,channel TEXT NOT NULL,status TEXT NOT NULL,attempt INTEGER NOT NULL,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS idx_dedup_symbol_date ON alert_dedup(symbol,event_date);
            """
        )
        self.db.commit()

    def close(self):
        self.db.close()

    def load_current(self, symbol: str):
        row = self.db.execute("SELECT payload FROM current_state WHERE symbol=?", (symbol,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_current(self, symbol: str, payload: dict):
        now = datetime.now().astimezone().isoformat()
        self.db.execute(
            "INSERT INTO current_state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",
            (symbol, canonical_json(payload), now),
        )

    def save_tracker(self, tracker_id: str, payload: dict, revision: int):
        self.db.execute(
            "INSERT INTO trackers VALUES(?,?,?) ON CONFLICT(tracker_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision",
            (tracker_id, canonical_json(payload), revision),
        )

    def load_tracker(self, tracker_id: str):
        row = self.db.execute("SELECT payload,revision FROM trackers WHERE tracker_id=?", (tracker_id,)).fetchone()
        return (json.loads(row[0]), row[1]) if row else None

    def append_event(self, event: StateEvent):
        existing = self.db.execute("SELECT sequence_number FROM events WHERE event_id=?", (event.event_id,)).fetchone()
        if existing:
            return existing[0]
        seq = event.sequence_number or self.db.execute(
            "SELECT COALESCE(MAX(sequence_number),0)+1 FROM events WHERE symbol=?", (event.symbol,)
        ).fetchone()[0]
        payload = asdict(event)
        payload["sequence_number"] = seq
        self.db.execute(
            "INSERT INTO events(event_id,symbol,sequence_number,payload) VALUES(?,?,?,?)",
            (event.event_id, event.symbol, seq, canonical_json(payload)),
        )
        return seq

    def list_events(self, symbol: str, *, after_sequence: int = 0):
        return [
            json.loads(row[0])
            for row in self.db.execute(
                "SELECT payload FROM events WHERE symbol=? AND sequence_number>? ORDER BY sequence_number",
                (symbol, after_sequence),
            )
        ]

    def save_snapshot(self, snap: Snapshot):
        self.db.execute(
            "INSERT INTO snapshots VALUES(?,?,?,?,?)",
            (snap.snapshot_id, snap.analysis_id, snap.symbol, canonical_json(asdict(snap)), snap.state_hash),
        )

    def load_snapshot(self, snapshot_id: str):
        row = self.db.execute("SELECT payload FROM snapshots WHERE snapshot_id=?", (snapshot_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def latest_valid_snapshot(self, symbol: str):
        rows = self.db.execute(
            "SELECT payload,state_hash FROM snapshots WHERE symbol=? ORDER BY rowid DESC", (symbol,)
        ).fetchall()
        for raw, stored_hash in rows:
            try:
                snap = json.loads(raw)
                if state_hash(snap["payload"]) == stored_hash == snap["state_hash"]:
                    return snap
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
        return None

    def recover_current(self, symbol: str, *, reducer: Callable[[dict, dict], dict] | None = None):
        try:
            current = self.load_current(symbol)
            if current is not None:
                return current
        except (json.JSONDecodeError, TypeError):
            pass
        snap = self.latest_valid_snapshot(symbol)
        if snap is None:
            raise ValueError("STATE_CORRUPTION")
        recovered = dict(snap["payload"])
        later_events = self.list_events(symbol, after_sequence=int(snap.get("event_sequence_number", 0)))
        if later_events and reducer is None:
            raise ValueError("STATE_RECOVERY_REDUCER_REQUIRED")
        for event in later_events:
            recovered = reducer(recovered, event)  # type: ignore[misc]
        return recovered

    def save_manifest(self, manifest: AnalysisManifest):
        self.db.execute(
            "INSERT INTO manifests VALUES(?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET payload=excluded.payload,status=excluded.status",
            (manifest.analysis_id, canonical_json(asdict(manifest)), manifest.status.value),
        )

    def load_manifest(self, analysis_id: str):
        row = self.db.execute("SELECT payload FROM manifests WHERE analysis_id=?", (analysis_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save_journal(self, journal: RunJournal):
        self.db.execute(
            "INSERT INTO journals VALUES(?,?) ON CONFLICT(analysis_id) DO UPDATE SET payload=excluded.payload",
            (journal.analysis_id, canonical_json(asdict(journal))),
        )

    def load_journal(self, analysis_id: str):
        row = self.db.execute("SELECT payload FROM journals WHERE analysis_id=?", (analysis_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def has_alert_dedup(self, dedup_key: str) -> bool:
        return self.db.execute("SELECT 1 FROM alert_dedup WHERE dedup_key=?", (dedup_key,)).fetchone() is not None

    def record_alert_dedup(self, *, dedup_key: str, alert_id: str, symbol: str, event_date: str, priority: int, state_fingerprint: str | None):
        self.db.execute(
            "INSERT OR IGNORE INTO alert_dedup VALUES(?,?,?,?,?,?,?)",
            (dedup_key, alert_id, symbol, event_date, priority, state_fingerprint, datetime.now().astimezone().isoformat()),
        )
        self.db.commit()

    def p2_alert_count(self, symbol: str, event_date: str, *, p2_priority: int = 2) -> int:
        return self.db.execute(
            "SELECT COUNT(*) FROM alert_dedup WHERE symbol=? AND event_date=? AND priority=?",
            (symbol, event_date, p2_priority),
        ).fetchone()[0]

    def record_delivery(self, record: Any):
        payload = asdict(record) if hasattr(record, "__dataclass_fields__") else dict(record)
        self.db.execute(
            "INSERT INTO delivery_records VALUES(?,?,?,?,?,?,?) ON CONFLICT(delivery_id) DO UPDATE SET status=excluded.status,attempt=excluded.attempt,payload=excluded.payload,updated_at=excluded.updated_at",
            (
                payload["delivery_id"], payload["alert_id"], payload["channel"], str(payload["status"]), int(payload["attempt"]),
                canonical_json(payload), datetime.now().astimezone().isoformat(),
            ),
        )
        self.db.commit()

    def load_delivery(self, delivery_id: str):
        row = self.db.execute("SELECT payload FROM delivery_records WHERE delivery_id=?", (delivery_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def transaction_commit(self, *, symbol: str, current: dict, trackers: list[tuple[str, dict, int]], events: list[StateEvent], snapshot: Snapshot):
        try:
            self.db.execute("BEGIN")
            last_seq = snapshot.event_sequence_number
            for event in events:
                last_seq = max(last_seq, self.append_event(event))
            for tracker_id, payload, revision in trackers:
                self.save_tracker(tracker_id, payload, revision)
            self.save_snapshot(replace(snapshot, event_sequence_number=last_seq))
            self.save_current(symbol, current)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise


class ReplayClock:
    def __init__(self, now: datetime):
        self.now = now

    def assert_visible(self, timestamp: datetime):
        if timestamp > self.now:
            raise ValueError("FUTURE_LEAKAGE_BLOCKED")


class MigrationRegistry:
    def __init__(self):
        self._migrations: dict[tuple[str, str], Callable[[dict], dict]] = {}

    def register(self, source: str, destination: str, fn: Callable[[dict], dict]):
        self._migrations[(source, destination)] = fn

    def migrate(self, payload: dict, source: str, destination: str):
        if source == destination:
            return payload
        fn = self._migrations.get((source, destination))
        if not fn:
            raise ValueError("VERSION_MISMATCH")
        return fn(dict(payload))


def atomic_publish(path: str | Path, result: dict) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = canonical_json(result)
    checksum = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        handle.write(raw)
        handle.flush()
        try:
            import os
            os.fsync(handle.fileno())
        except OSError:
            pass
    tmp.replace(path)
    return checksum


def verify_publish(path: str | Path, checksum: str) -> bool:
    target = Path(path)
    return target.exists() and file_checksum(target) == checksum
