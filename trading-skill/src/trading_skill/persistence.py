from __future__ import annotations
import hashlib, json, sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Callable, Any

class AnalysisStatus(StrEnum): RUNNING="RUNNING"; READY="READY"; FAILED="FAILED"
class RunStage(StrEnum): INIT="INIT"; DATA_READY="DATA_READY"; ANALYSIS_READY="ANALYSIS_READY"; STATE_COMMITTED="STATE_COMMITTED"; RESULT_BUILT="RESULT_BUILT"; PUBLISHED="PUBLISHED"; DISPATCH_PENDING="DISPATCH_PENDING"; COMPLETE="COMPLETE"

@dataclass(frozen=True, slots=True)
class StateEvent:
    event_id:str; symbol:str; object_type:str; object_id:str; event_type:str; old_state:str|None; new_state:str|None
    reason_codes:tuple[str,...]; evidence_ids:tuple[str,...]; effective_at:str; recorded_at:str; analysis_id:str
    schema_version:str="1.0.0"; engine_version:str="1.0.0"; sequence_number:int=0
@dataclass(frozen=True, slots=True)
class Snapshot:
    snapshot_id:str; analysis_id:str; symbol:str; as_of:str; payload:dict; data_version:str; schema_version:str; engine_versions:dict; state_hash:str
@dataclass(frozen=True, slots=True)
class AnalysisManifest:
    analysis_id:str; status:AnalysisStatus; started_at:str; completed_at:str|None=None; data_as_of:str|None=None; result_checksum:str|None=None; error_summary:str|None=None
@dataclass(frozen=True, slots=True)
class RunJournal:
    analysis_id:str; last_completed_stage:RunStage; publish_status:str="PENDING"

def canonical_json(obj:Any)->str:
    return json.dumps(obj,sort_keys=True,separators=(",",":"),ensure_ascii=False,default=str)
def state_hash(payload:dict)->str:
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()

class StateRepository:
    def __init__(self, path:str|Path):
        self.path=str(path); self.db=sqlite3.connect(self.path)
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.executescript("""
        CREATE TABLE IF NOT EXISTS current_state(symbol TEXT PRIMARY KEY,payload TEXT NOT NULL,updated_at TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS trackers(tracker_id TEXT PRIMARY KEY,payload TEXT NOT NULL,revision INTEGER NOT NULL);
        CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY,symbol TEXT NOT NULL,sequence_number INTEGER NOT NULL,payload TEXT NOT NULL,UNIQUE(symbol,sequence_number));
        CREATE TABLE IF NOT EXISTS snapshots(snapshot_id TEXT PRIMARY KEY,analysis_id TEXT NOT NULL,symbol TEXT NOT NULL,payload TEXT NOT NULL,state_hash TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS manifests(analysis_id TEXT PRIMARY KEY,payload TEXT NOT NULL,status TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS journals(analysis_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        """); self.db.commit()
    def close(self): self.db.close()
    def load_current(self,symbol):
        row=self.db.execute("SELECT payload FROM current_state WHERE symbol=?",(symbol,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_current(self,symbol,payload):
        now=datetime.now().astimezone().isoformat()
        self.db.execute("INSERT INTO current_state VALUES(?,?,?) ON CONFLICT(symbol) DO UPDATE SET payload=excluded.payload,updated_at=excluded.updated_at",(symbol,canonical_json(payload),now))
    def save_tracker(self,tracker_id,payload,revision):
        self.db.execute("INSERT INTO trackers VALUES(?,?,?) ON CONFLICT(tracker_id) DO UPDATE SET payload=excluded.payload,revision=excluded.revision",(tracker_id,canonical_json(payload),revision))
    def load_tracker(self,tracker_id):
        row=self.db.execute("SELECT payload,revision FROM trackers WHERE tracker_id=?",(tracker_id,)).fetchone()
        return (json.loads(row[0]),row[1]) if row else None
    def append_event(self,event:StateEvent):
        seq=event.sequence_number or self.db.execute("SELECT COALESCE(MAX(sequence_number),0)+1 FROM events WHERE symbol=?",(event.symbol,)).fetchone()[0]
        payload=asdict(event); payload["sequence_number"]=seq
        self.db.execute("INSERT OR IGNORE INTO events(event_id,symbol,sequence_number,payload) VALUES(?,?,?,?)",(event.event_id,event.symbol,seq,canonical_json(payload)))
        return seq
    def list_events(self,symbol):
        return [json.loads(r[0]) for r in self.db.execute("SELECT payload FROM events WHERE symbol=? ORDER BY sequence_number",(symbol,))]
    def save_snapshot(self,snap:Snapshot):
        self.db.execute("INSERT INTO snapshots VALUES(?,?,?,?,?)",(snap.snapshot_id,snap.analysis_id,snap.symbol,canonical_json(asdict(snap)),snap.state_hash))
    def load_snapshot(self,snapshot_id):
        row=self.db.execute("SELECT payload FROM snapshots WHERE snapshot_id=?",(snapshot_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_manifest(self,m:AnalysisManifest):
        self.db.execute("INSERT INTO manifests VALUES(?,?,?) ON CONFLICT(analysis_id) DO UPDATE SET payload=excluded.payload,status=excluded.status",(m.analysis_id,canonical_json(asdict(m)),m.status.value))
    def load_manifest(self,analysis_id):
        row=self.db.execute("SELECT payload FROM manifests WHERE analysis_id=?",(analysis_id,)).fetchone()
        return json.loads(row[0]) if row else None
    def save_journal(self,j:RunJournal):
        self.db.execute("INSERT INTO journals VALUES(?,?) ON CONFLICT(analysis_id) DO UPDATE SET payload=excluded.payload",(j.analysis_id,canonical_json(asdict(j))))
    def transaction_commit(self, *, symbol:str, current:dict, trackers:list[tuple[str,dict,int]], events:list[StateEvent], snapshot:Snapshot):
        try:
            self.db.execute("BEGIN")
            for e in events:self.append_event(e)
            for tid,p,r in trackers:self.save_tracker(tid,p,r)
            self.save_snapshot(snapshot); self.save_current(symbol,current); self.db.commit()
        except Exception:
            self.db.rollback(); raise

class ReplayClock:
    def __init__(self, now:datetime): self.now=now
    def assert_visible(self, timestamp:datetime):
        if timestamp>self.now: raise ValueError("FUTURE_LEAKAGE_BLOCKED")

class MigrationRegistry:
    def __init__(self): self._m:dict[tuple[str,str],Callable[[dict],dict]]={}
    def register(self,src,dst,fn): self._m[(src,dst)]=fn
    def migrate(self,payload,src,dst):
        if src==dst:return payload
        fn=self._m.get((src,dst))
        if not fn: raise ValueError("VERSION_MISMATCH")
        return fn(dict(payload))

def atomic_publish(path:str|Path, result:dict)->str:
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True); raw=canonical_json(result); checksum=hashlib.sha256(raw.encode()).hexdigest(); tmp=path.with_suffix(path.suffix+".tmp")
    with open(tmp,"w",encoding="utf-8") as f:
        f.write(raw); f.flush()
        try:
            import os; os.fsync(f.fileno())
        except OSError: pass
    tmp.replace(path); return checksum

def verify_publish(path:str|Path, checksum:str)->bool:
    raw=Path(path).read_text(encoding="utf-8")
    return hashlib.sha256(raw.encode()).hexdigest()==checksum
