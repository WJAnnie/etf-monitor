# M6 Decision Engine Review

Freeze candidate: `M6_DECISION_ENGINE_FROZEN`

## Scope reviewed

- Opportunity evidence ledger and weighted score
- S/A/B/C grade thresholds and hysteresis
- independent Risk State L0-L4
- blocker composition
- action priority / state machine
- fundamental, parent, data, portfolio and technical execution gates

## Permanent rules

- Opportunity cannot offset Risk
- S-grade opportunity with L2 Risk does not buy/add; it pauses new risk
- L4 or Fundamental Hard Veto exits an existing position context
- Daily First Buy remains a theoretical signal but v1 strategy defaults to `WAIT_2B`
- add actions require a new confirmed structure
- incomplete data blocks new risk

Frozen strategy policy is stored under `configs/strategy.yaml`.

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M6 scope.
