# Trading Skill v1

Deterministic Chan-theory trading engine developed inside `etf-monitor` without changing the legacy monitor runtime.

## Current frozen milestone

`M0_STROKE_CORE_FROZEN`

Implemented scope:

- deterministic domain primitives and tick-normalized prices;
- raw OHLCV validation with timezone checks;
- recursive K-line inclusion handling;
- strict confirmed top/bottom fractals;
- `RELAXED_LATE` stroke construction (default);
- `STRICT_OLD` shadow audit mode;
- active-stroke same-identity extension and revision history semantics;
- Gold fixtures GS-001 through GS-003;
- no-lookahead confirmation timestamps.

Out of scope for M0: Segment, Center, Trend, Divergence, buy/sell points, indicators and trading actions.

## Run tests

```bash
cd trading-skill
python -m pip install -e '.[dev]'
python -m pytest
```

## Theory boundary

Geometry engines must not import MACD, volume, fundamental, portfolio or action logic. Every downstream stage will be added only after the preceding freeze gate is reviewed.
