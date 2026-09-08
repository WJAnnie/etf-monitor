# Trading Skill v1.0

Deterministic Chan-theory trading decision, risk, position-management and monitoring engine developed inside `etf-monitor` without replacing the legacy monitor runtime.

## Frozen milestone

`TRADING_SKILL_MVP_V1_FROZEN`

Version: `1.0.0`

The frozen MVP includes:

- deterministic tick-normalized market data, inclusion, fractals and Stroke core;
- Segment feature-sequence Case1/Case2 logic and immutable finalized history;
- Center geometry, extension, leave/return, independent-center and recursion infrastructure;
- TrendType, standard trend divergence and canonical First/Second/Third Buy **and Sell** signals;
- multi-timeframe structure links, 120m bridge semantics, nesting and parent/child failure isolation;
- Volume, MACD `(6,13,4)`, BOLL `(20,2)` and KDJ `(9,3,3)` as confirmation-only evidence;
- deterministic annual+interim fundamental eligibility, hard vetoes, anomalies, leader/industry filters and separate valuation grades;
- Opportunity Grade, independent Risk State, blockers and action state machine;
- structural stops, risk-budget sizing, stock/portfolio caps, A-share lot rounding and tranche planning;
- tranche-specific Entry Thesis, moving protection, sell-scope mapping, reduction, gap/slippage handling and structural re-entry lock;
- SQLite persistence, append-only events, immutable snapshots, ReplayClock, recovery, atomic publish and migration registry;
- staged Universe scan, M0-M3 monitoring, trading-day scheduling, P1-P4 alerts, persistent dedup and channel-isolated Feishu/GitHub delivery;
- ServerChan disabled by default and permanently regression-tested so a missing ServerChan secret cannot break Feishu;
- frozen configuration files under `configs/`.

## Hard theory boundaries

- no valid trend => no standard trend divergence;
- indicators cannot create canonical Chan signals;
- Second Buy is structural and may exist below its reversal anchor;
- Third Buy requires first completed return with `return_low >= ZG`;
- Third Sell mirrors it with `return_high <= ZD`;
- child execution failure does not automatically invalidate its higher-level parent;
- Daily First Buy remains theoretical but v1 strategy defaults to `WAIT_2B`;
- Risk L2+ creates zero new risk budget;
- protection may rise but never loosen;
- failed TEST positions cannot silently become higher-level CORE positions;
- RUNNING/FAILED analysis artifacts cannot dispatch trade alerts;
- READY is published only after state persistence and checksum-valid atomic publication.

## Run tests

```bash
cd trading-skill
python -m pip install -e '.[dev]'
python -m pytest
```

## Runtime boundary

This v1.0 MVP produces deterministic analysis, trade plans, position-management decisions, monitoring state and alerts. It **does not connect to a broker or place live orders**. The next operational phase is Shadow Trading before any broker execution layer is considered.
