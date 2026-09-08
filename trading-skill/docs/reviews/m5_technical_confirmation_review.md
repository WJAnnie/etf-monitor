# M5 Technical Confirmation Review

Freeze candidate: `M5_TECHNICAL_CONFIRMATION_FROZEN`

## Scope reviewed

- Volume/turnover confirmation state
- MACD `(6,13,4)` numeric engine and state
- BOLL `(20,2)` position context
- KDJ `(9,3,3)` rhythm context
- provisional vs confirmed indicator evidence
- TechnicalBundle execution confirmation

## Permanent policy

Indicators verify a Chan structure; they do not define one.

Therefore:

- MACD cross cannot create a canonical buy/sell
- BOLL lower-band touch cannot create a buy
- upper-rail walk alone is not a sell
- KDJ high saturation alone is not a sell
- volume breakout cannot manufacture a Third Buy
- an incomplete Daily bar produces provisional technical evidence

The frozen defaults are stored under `configs/indicators.yaml`.

## Freeze recommendation

APPROVE only if final freeze-commit and post-merge CI remain green. No knowingly open BLOCKER in M5 scope.
