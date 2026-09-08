# M1 Segment Core — Read-only Architecture Review

## Verdict

**SAFE TO FREEZE: `M1_SEGMENT_CORE_FROZEN`**

New M1 local test status at review: **21 passed**. The GitHub PR gate must additionally run the complete M0+M1 suite before merge.

## Findings

### BLOCKER
None.

### HIGH
None.

### MEDIUM
- M1 uses deterministic engine-boundary stroke fixtures for Segment Gold cases. End-to-end raw-bar historical fixtures should be added as the broader Gold corpus grows; this does not alter Segment theory truth.

### LOW
- Multiple adjacent finalized segments are supported by the builder loop, but the M1 Gold set intentionally focuses on Case1, Case2, and stroke-break boundaries.

## Boundary checks

1. Segment seed is not equivalent to “three strokes = finalized segment” — PASS.
2. Seed requires at least three alternating strokes and common overlap — PASS.
3. UP segment feature sequence consumes DOWN strokes; DOWN consumes UP strokes — PASS.
4. Touching feature intervals are not a gap — PASS.
5. Feature-sequence inclusion is recursive and preserves source-stroke lineage — PASS.
6. Feature inclusion is isolated to each feature sequence; Case2 second sequence is built separately — PASS.
7. Feature fractals use strict high/low geometry — PASS.
8. Case1 requires the target feature fractal and no E1/E2 gap — PASS.
9. Case2 requires the target feature fractal with a gap plus an opposite feature fractal in a second sequence — PASS.
10. Case2 confirmation does not require filling the original gap — PASS.
11. No recursive Case2 is performed inside the second feature sequence — PASS.
12. A single reverse/active stroke does not finalize the segment — PASS.
13. Failed Case2 confirmation can return the original segment to ACTIVE — PASS.
14. Structural endpoint time and later confirmation time are distinct — PASS.
15. Structural endpoint and actual observed envelope are distinct fields — PASS.
16. Active Segment identity is stable and revisioned — PASS.
17. Finalized Segment history is immutable under ordinary future updates — PASS.
18. `NormalizedSegment` can be emitted only from finalized Segment objects — PASS.
19. Segment code imports no Center/Trend/Divergence/indicator/risk/action logic — PASS.
20. No lookahead: an incomplete confirming feature element cannot finalize a segment — PASS.

## Frozen Gold regressions

- GS-004 Segment Case1
- GS-005 Segment Case2
- GS-006 Stroke Break ≠ Segment Break

## Next gate

Task 021 Center Domain may begin only while M0 and M1 Gold regressions remain green.
