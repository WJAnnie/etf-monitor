# Trading Skill v1

Deterministic Chan-theory trading engine developed inside `etf-monitor` without changing the legacy monitor runtime.

## Current frozen milestone

`M2_CENTER_CORE_FROZEN`

Implemented scope through M2:

- deterministic tick-normalized data, inclusion, fractal and Stroke core;
- Segment seed, feature sequences, Case1/Case2 confirmation and `NormalizedSegment` output;
- Center seed geometry with fixed `ZD/ZG` core and wider `DD/GG` envelope;
- Center extension without core drift or automatic level upgrade;
- deterministic leave / first-return state and generic break-return foundation events;
- independent-center vs expansion-pending classification;
- recursive higher-center infrastructure based on completed lower-level TrendType-compatible motions;
- simultaneous CenterStack levels with cycle protection;
- Gold fixtures GS-001 through GS-009.

Out of scope for M2: TrendType, Divergence, buy/sell points, indicators, risk and trading actions.

## Run tests

```bash
cd trading-skill
python -m pip install -e '.[dev]'
python -m pytest
```

## Theory boundary

Geometry engines cannot import indicators, fundamentals, portfolio or action logic. The Center engine emits center geometry and generic structural events only; it does not classify TrendType, divergence or Third Buy/Sell.
