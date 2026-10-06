# C01 EXPERIMENTAL_PAPER_LIFECYCLE run

**EXPERIMENTAL. C01 paper wiring only. Not a sealed CANDIDATE_EVALUATION, no live readiness, no qualifying-BUY claim.**

- status: `COMPLETED`
- loader substituted (test only): `False`
- actual 48-symbol run certified: `True`
- engine identity: `ENGINE_PARENT_DRIFT_RECORDED` (source recorded, never claimed sealed)
- source aggregate sha256: `c1efefdec7fb58f8097849b4cfecbbcd2f1a7fa371b7a6608e078eb8abcd58ad`

| metric | value |
|---|---|
| gross P&L | -4110.439399999963 |
| net P&L | -7043.537287141787 |
| MTM max drawdown (fraction) | 0.007043537287141662 |
| MTM max drawdown (percent) | 0.7043537287141662 |
| fills (total) | 52 |
| fills BUY/SELL | UNAVAILABLE_FROM_WORKER_RESULT |
| completed trades | 52 |
| trades BUY / SELL | 2 / 50 |
| total costs | 2933.0978871418 |
| safety violations | 0 |
| governor entry decisions | {"ENTRY:GOVERNOR_ENTRY": 1516, "NO_ACTION:EXHAUST_SPREAD_HOLD": 20, "NO_ACTION:FSR_BELOW_ENTRY_HURDLE:FSRN": 3945, "NO_ACTION:GOVERNOR_OVERSPEED_LIMIT": 2121, "NO_ACTION:GRID_REFERENCE_UNAVAILABLE": 257} |
| governor position decisions | {"EXIT:FSR_BELOW_EXIT:FSRN": 38, "EXIT:GOVERNOR_PATH_ERROR": 11, "HOLD:GOVERNOR_LOAD_SHED": 118, "HOLD:GOVERNOR_TRACKING": 94} |
| ID counts | UNAVAILABLE_FROM_WORKER_RESULT |
| Gate12 counts | UNAVAILABLE_FROM_WORKER_RESULT |

## Limits

- C01 paper wiring only: historical offline replay, Stage B Block 1, no broker or network access.
- Not a sealed CANDIDATE_EVALUATION; source identity is recorded, not blessed as the sealed engine parent.
- No live readiness and no qualifying-BUY claim is made by this run.
- End-of-run disposition is the WAL factory default (CLOSE); multi-session PERSIST and morning hydration are not exercised.
