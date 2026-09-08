# M7 Position Sizing Review

Freeze candidate: `M7_POSITION_SIZING_FROZEN`

## Scope reviewed

- structural stop validation
- grade/risk-based trade risk budget
- risk-based notional sizing
- stock archetype caps
- portfolio stop-risk caps
- first-tranche fractions
- A-share lot rounding

## Frozen defaults

- S risk 0.9%, A 0.7%, B 0.4%, C 0%
- L2+ new risk multiplier = 0
- Core Leader cap 15%, Quality 10%, Turnaround 6%
- single trade 1.0%, industry 1.8%, theme 2.5%, total stop risk 4.5%
- first tranche S/A/B = 35% / 30% / 20% of allowed maximum
- usable risk buffer = 0.85

## Permanent rules

- stops must be structural
- a stop/protection cannot be loosened to manufacture a larger position
- position size is risk-first, then constrained by stock and portfolio caps
- rounding cannot increase planned risk beyond approved risk

Frozen values are stored in `configs/risk.yaml`.

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M7 scope.
