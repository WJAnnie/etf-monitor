# M3 Chan Signal Core Review

Freeze candidate: `M3_CHAN_SIGNAL_CORE_FROZEN`

## Scope reviewed

- TrendType classification and conservative completion
- standard trend divergence only when a valid trend exists
- full structural-leg MACD evidence
- canonical First Buy / First Sell
- canonical Second Buy / Second Sell trackers
- canonical Third Buy / Third Sell trackers
- Second+Third signal overlap
- confirmation timestamps / future-leakage blocking

## Critical findings resolved

1. The initial candidate implemented Second/Third Buy but lacked true mirrored Second/Third Sell trackers. This was treated as a BLOCKER, not a documentation exception.
2. `SecondSellTracker` and `ThirdSellTracker` were added with independent states and stable structural semantics.
3. Third Sell is now the exact mirror of Third Buy: the first completed upward return is standard 3S only when `return_high <= ZD`; equality is valid and one tick above is invalid.
4. First Sell cannot overlap another standard sell; Second Sell and Third Sell may overlap when both structures are independently valid.

## Permanent rules

- no trend => no standard trend divergence
- MACD weakening alone cannot create a Chan divergence
- canonical signals are structure-driven, never indicator-created
- Second Buy is not defined by staying above the First Buy price
- Third Buy/Third Sell use the first completed return only
- candidate/forming signals may invalidate; confirmed historical signals are not rewritten

## Regression evidence

Pre-freeze GitHub Actions regression after sell-mirror and fundamental audit fixes: 137 tests passed.

## Freeze recommendation

APPROVE only if the final freeze commit and post-merge `master` CI remain green. No BLOCKER is knowingly open in the M3 scope.
