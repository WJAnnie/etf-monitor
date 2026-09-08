# Trading Skill v1

Deterministic Chan-theory trading engine developed inside `etf-monitor` without changing the legacy monitor runtime.

## Current frozen milestone

`M1_SEGMENT_CORE_FROZEN`

Implemented scope through M1:

- deterministic domain primitives and tick-normalized prices;
- raw OHLCV validation, recursive inclusion, strict fractals and M0 stroke engine;
- Segment seed validation: at least three alternating strokes with common overlap;
- raw and standardized feature sequences with independent inclusion processing;
- strict feature fractals;
- Segment Case1 confirmation when the first two feature elements have no gap;
- Segment Case2 confirmation through a separate second feature sequence when a gap exists;
- one reverse stroke remains `STROKE_BREAK_PENDING`, never an automatic segment end;
- Case2 break failure can return the original segment to `ACTIVE`;
- structural endpoint and observed/actual envelope remain separate;
- active segment identity/revision semantics and finalized-history immutability;
- `NormalizedSegment` output for the next Center engine;
- Gold fixtures GS-001 through GS-006.

Out of scope for M1: Center, TrendType, Divergence, buy/sell points, indicators and trading actions.

## Run tests

```bash
cd trading-skill
python -m pip install -e '.[dev]'
python -m pytest
```

## Theory boundary

Geometry engines must not import MACD, volume, fundamental, portfolio or action logic. Segment confirmation is driven only by Stroke / Feature Sequence structure. Downstream stages are added only after the preceding freeze gate is reviewed.
