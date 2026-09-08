# M10 Production Monitoring Review

Freeze candidate: `M10_PRODUCTION_MONITORING_FROZEN`

## Scope reviewed

- staged Universe scan with cheap filters before deep analysis
- annual/interim fundamental profile integration
- leader and industry lifecycle eligibility
- M0-M3 monitoring levels
- trading-day schedule: PREMARKET, 10:30, 11:30, 13:30, 14:30, 14:50, AFTER_CLOSE
- 14:30/14:50 Daily provisional gate
- P1-P4 alert construction and ordering
- persistent state-change dedup
- Feishu / GitHub / ServerChan independent adapters
- system-health degraded mode

## Critical findings resolved

1. Initial P2 daily dedup could suppress a material Risk state change. It was changed so meaningful old_state -> new_state transitions bypass the ordinary P2 daily cap.
2. Initial Universe input accepted precomputed fundamental booleans without a deterministic report evaluator. A separate annual+interim fundamental/valuation engine was added and connected to Universe eligibility.
3. ServerChan remains disabled by default; a disabled channel does not load its secret.

## Permanent production rules

- open positions remain M3 until closed
- position risk outranks new-buy opportunities
- unchanged attention states are deduplicated; real Risk escalation is not
- 14:30 and 14:50 cannot confirm the incomplete Daily bar
- RUNNING or FAILED analysis cannot produce trade delivery
- READY artifacts must pass checksum validation
- one delivery-channel failure does not change a successful Analysis to FAILED and does not block other channels
- ServerChan missing/disabled must not affect Feishu

Frozen schedule/channel policy is stored in `configs/alerts.yaml`.

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M10 scope.
