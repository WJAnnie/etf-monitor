# M9 Persistence / Replay Review

Freeze candidate: `M9_PERSISTENCE_REPLAY_FROZEN`

## Scope reviewed

- SQLite transactional state repository
- mutable Current State cache
- immutable snapshots and deterministic state hashes
- append-only state events
- persistent trackers
- ReplayClock / future-data blocking
- analysis manifest RUNNING/READY/FAILED
- atomic tmp -> fsync -> rename publication
- crash recovery and run journal
- schema migration registry

## Critical rules

- Current State is a cache, not the sole historical truth
- tracker identities and first-return/first-retracement semantics survive restart
- historical events are append-only
- future confirmations are rejected during replay
- a future-published fundamental report is unavailable before its publication time
- state is committed before publication; READY is the last successful transition
- a corrupted Current State is recovered from a valid snapshot plus explicitly reduced events
- if post-snapshot events exist and no reducer is supplied, recovery fails instead of guessing
- unrecoverable state blocks new risk

## Regression evidence

Production-contract tests cover transaction rollback, snapshot/current recovery, persistent dedup/delivery, atomic publication and crash-resume behavior.

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M9 scope.
