# Block 1 four-arm isolation report

Symbols: `TITAN` · block 1 · params `trial_007.json` · repeats per arm: 1

## Determinism and isolation

| arm | repeats | ledger SHA identical | controller-trace SHA identical | distinct PIDs | zero-state checks |
|---|---|---|---|---|---|
| 00 control | 1 | True | True | 1 | 30 |
| 10 hold-only | 1 | True | True | 1 | 30 |
| 01 PA-only | 1 | True | True | 1 | 30 |
| 11 interaction | 1 | True | True | 1 | 30 |

## Per-arm outcome (first repeat)

| arm | trades | exits < min hold | FSRN exits < min hold | deferred FSRN exits | median bars held | gross frictionless Rs | friction Rs | net Rs | mean gross R | mean net R |
|---|---|---|---|---|---|---|---|---|---|---|
| 00 control | 1 | 0 | 0 | 0 | 3 | -33.18 | 34.20 | -67.37 | -0.19 | -0.39 |
| 10 hold-only | 1 | 0 | 0 | 0 | 3 | -33.18 | 34.20 | -67.37 | -0.19 | -0.39 |
| 01 PA-only | 10 | 1 | 1 | 0 | 6.0 | 6.08 | 1,229.50 | -1,223.43 | -0.01 | -0.35 |
| 11 interaction | 10 | 0 | 0 | 1 | 6.0 | 18.83 | 1,229.52 | -1,210.69 | -0.01 | -0.34 |

## Exit quality (15 bars after exit; same session, scored sessions only)

DEFENSIVE_SAVE = original stop breached within 15 bars · PREMATURE_CHOKE = original target reached · NOISE_CHURN = neither, full 15 bars observed · CENSORED = fewer than 15 same-session bars left and no touch yet.  A bar touching both levels counts as a stop first.  Mechanical exits (stop/target/force-close/max-hold) are tagged separately because their classification is partly definitional.

| arm | exit kind | DEFENSIVE_SAVE | PREMATURE_CHOKE | NOISE_CHURN | CENSORED |
|---|---|---|---|---|---|
| 00 control | CONVICTION_FSRN | 0 | 0 | 1 | 0 |
| 10 hold-only | CONVICTION_FSRN | 0 | 0 | 1 | 0 |
| 01 PA-only | CONVICTION_FSRN | 0 | 0 | 7 | 0 |
| 01 PA-only | GOVERNOR_GOVERNOR_PATH_ERROR | 0 | 0 | 3 | 0 |
| 11 interaction | CONVICTION_FSRN | 0 | 0 | 7 | 0 |
| 11 interaction | GOVERNOR_GOVERNOR_PATH_ERROR | 0 | 0 | 3 | 0 |

## Friction deconstruction (totals, Rs)

| arm | brokerage | STT (sell leg) | turnover charge | slippage | total friction | bps of entry notional |
|---|---|---|---|---|---|---|
| 00 control | 10.69 | 4.45 | 1.23 | 17.82 | 34.20 | 19.21 |
| 10 hold-only | 10.69 | 4.45 | 1.23 | 17.82 | 34.20 | 19.21 |
| 01 PA-only | 353.95 | 165.88 | 45.81 | 663.86 | 1,229.50 | 18.52 |
| 11 interaction | 353.95 | 165.89 | 45.81 | 663.87 | 1,229.52 | 18.52 |

## Zero-alpha null (random side and entry bar, same stop/target geometry and frozen friction)

Seed 20241005 · 198 trades · stop 1.161 ATR, target 1.741 ATR (medians of the control arm) · risk budget Rs 174 · max hold 89 bars.

Mean gross R (frictionless) -0.1919 · mean net R -1.8915 · mean friction 1.6996 R · total friction 15.26 bps of entry notional · net Rs -64,723.82.

Caveat: the null reuses the engine's frozen cost and slippage functions and the control arm's measured geometry, but it is a standalone simulator, not the orchestrator's candidate/governor/broker pipeline.
