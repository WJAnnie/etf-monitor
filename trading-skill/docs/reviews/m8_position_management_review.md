# M8 Position Management Review

Freeze candidate: `M8_POSITION_MANAGEMENT_FROZEN`

## Scope reviewed

- tranche-specific Entry Thesis
- TEST / TACTICAL / CONFIRMATION / CORE / TREND_ADD roles
- thesis invalidation and explicit promotion
- moving structural protection
- sell-signal scope mapping
- target-exposure reduction
- gap/slippage exit records
- structural re-entry and repeated-child-failure lock

## Permanent rules

- why a tranche was bought determines what invalidates it
- a failed TEST cannot silently become CORE
- promotion requires a new confirmed higher-level structure
- protection can rise but cannot move lower
- cost basis is not the primary structural stop
- low-timeframe sells cannot automatically liquidate a healthy Daily Core
- Daily First Sell removes tactical risk first; Daily Second Sell materially affects Core; Daily Third Sell targets zero exposure
- re-entry creates a new trade thesis and requires a new structure
- repeated child failures under the same parent lead to `REENTRY_LOCK`

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M8 scope.
