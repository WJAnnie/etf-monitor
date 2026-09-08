# M2 Center Core — Read-only Architecture Review

## Verdict

**SAFE TO FREEZE: `M2_CENTER_CORE_FROZEN`**

New M2 local suite at review: **25 passed**. GitHub CI must run all frozen M0+M1+M2 tests before merge.

## Findings

### BLOCKER
None.

### HIGH
None.

### MEDIUM
- M2 intentionally exposes deterministic center primitives (seed, extension, leave/return, relation, recursion) rather than classifying TrendType. Trend logic remains forbidden until M3.

### LOW
- Base-center construction from `NormalizedSegment` uses an explicit timeframe adapter because M1's frozen segment object intentionally did not encode chart timeframe.

## Boundary checks

1. Center seed requires three completed lower-level motions — PASS.
2. First and third motions share direction and the middle is opposite — PASS.
3. `ZD=max(low)` and `ZG=min(high)` with `ZD<=ZG` — PASS.
4. Touching overlap is valid — PASS.
5. A forming third motion cannot confirm a center — PASS.
6. Initial `ZD/ZG` never drift during extension — PASS.
7. Extension updates envelope `DD/GG` without level upgrade — PASS.
8. Leave-up requires the completed motion low above `ZG`; leave-down mirrors it — PASS.
9. Leave is not destruction — PASS.
10. First completed outside return emits a generic center-break-return foundation event, not a Third Buy/Sell signal — PASS.
11. Re-entry into the center becomes `RETURNING`, not a Third Buy — PASS.
12. Center relation requires same timeframe and same Chan level — PASS.
13. Independent upward relation requires `DD_B > GG_A` — PASS.
14. Independent downward relation requires `GG_B < DD_A` — PASS.
15. Core-separated but envelope-overlapping centers are `EXPANSION_PENDING` — PASS.
16. Expansion is not Trend classification — PASS.
17. Recursive higher center consumes completed lower-level TrendType-compatible motions — PASS.
18. Three Center objects are not accepted as a higher-center shortcut — PASS.
19. Timeframe remains unchanged while Chan level increases recursively — PASS.
20. CenterStack retains simultaneous lower/higher levels and rejects cycles — PASS.
21. No indicator/risk/action dependency exists — PASS.
22. Center engine emits no canonical First/Second/Third Buy/Sell labels — PASS.
23. Structural and confirmation timestamps remain separate — PASS.
24. GS-007/008/009 are frozen regressions — PASS.

## Frozen Gold regressions

- GS-007 Standard Center
- GS-008 Center Extension
- GS-009 Center Expansion Pending

## Next gate

Task 028 TrendType may begin only while M0, M1 and M2 Gold suites stay green.
