# M4 Multi-Timeframe Core Review

Freeze candidate: `M4_MULTI_TIMEFRAME_CORE_FROZEN`

## Scope reviewed

- independent Chan engines per timeframe
- `StructureLinkGraph` relations
- parent structural component mapping
- 120m bridge activation between Daily and 30m execution
- top-down activation to 5m only when execution context exists
- NestingChain state/maturity
- parent/child failure isolation
- no-lookahead timestamp containment

## Permanent rules

- timeframe is not Chan level
- lower timeframes do not vote against higher timeframes
- a child may add ending/execution evidence but cannot redefine the parent canonical signal
- child failure does not automatically invalidate a valid parent
- parent failure cancels dependent child execution roles
- 5m execution is inactive without a valid higher execution context

## Regression evidence

Tests cover temporal containment, future-confirmation blocking, activation order and the GS-019 semantic: lower execution failure while the Daily parent remains intact.

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M4 scope.
