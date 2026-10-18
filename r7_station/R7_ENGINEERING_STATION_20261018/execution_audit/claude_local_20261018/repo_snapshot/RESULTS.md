# Isolated R5 governor refactor: completed block-1 replay

Implemented in `/home/srinivas/projects/zerodha-r5-governor-refactor`, branch
`r5-governor-closed-loop`, based on commit `7ded8417074b828312eeb77210826ecc0cd6a07d`.
Changes remain uncommitted and are supplied as `governor-refactor.patch`. The active
`zerodha-phase1` checkout and Stage-A processes were not modified or interrupted.

The replay covered 13, 14, 15, 16 and 19 February 2024, full governor authority, protocol-default
parameters, GTG1 and GTG2 only. The baseline is a snapshot of the earlier verified traces;
it was not rerun in this task. Both variants match protocol ID, full calibration parameter
payload, symbols, sessions and slice SHA256
`c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50`.

| Measure | GTG1 before | GTG1 after | GTG2 before | GTG2 after |
|---|---:|---:|---:|---:|
| Trades | 3 | 3 | 8 | 7 |
| Mean bars held | 2.67 | 3.67 | 6.88 | 10.14 |
| Maximum bars held | 5 | 7 | 16 | 27 |
| Net P&L | -286.33 | -371.15 | -539.62 | -557.56 |
| Position evaluations | 8 | 11 | 55 | 71 |

GTG1 exits changed from two immediate FSRN exits plus one stop-gap to two sustained-deterioration
exits plus one ordinary stop. GTG2 exits changed from seven immediate FSRN exits plus one stop
to three sustained-deterioration exits plus four stops. These are full-policy comparisons;
they do not isolate each change's contribution to profitability.

## Direct evidence of PID actuation

GTG2 had 9 evaluations after MFE activation and 5 applied stop-price changes with positive
incremental tightening attributable to PID over the same-bar no-PID proposal.

A particularly clear TCS SELL example holds MFE constant:

| Timestamp | MFE R | u | Effective gap R | Protected floor R | Stop price |
|---|---:|---:|---:|---:|---:|
| 14 Feb 11:37 | 0.436307 | 1.239093 | 0.326091 | 0.110216 | 4076.744601 |
| 14 Feb 11:38 | 0.436307 | 1.381260 | 0.311874 | 0.124433 | 4076.332840 |

The smaller gap raised the R floor by 0.014217 and lowered the short's stop by 0.411761.
The second change cannot be explained by increased MFE: MFE was unchanged. The stop was
armed for the next bar, preserving existing protective-stop execution sequencing.

GTG1 computed varying PID gaps on all 11 evaluations but never reached 0.30R MFE. Therefore
it applied zero PID stop tightenings; every pre-activation floor stayed at -1R. This is the
requested activation guard working, not evidence of a disconnected PID.

## FSRN persistence and timing

All five new fuel-cut exits had current conviction below 0.20, an entry-relative drop of at
least 0.10, and at least two consecutive deteriorating evaluated bars. All five executed one
minute after their arming timestamp. LT's decision at 09:34 on 13 February used a side-opposed
studies direction, yielding conviction zero versus entry baseline 0.717661; its persistence
counter was two. It filled at 09:35, rather than the baseline's 09:34 exit.

The entry baseline uses PA exit_confidence, not entry confidence, and the same raw studies
confidence as the in-position signal. Percentile ranks still govern entry eligibility.

## Validation and limits

- 70 focused tests passed, including legacy controller checks, single-gamma boundaries,
  new MFE/PID/conviction tests, BUY/SELL stop anchors, and synthetic traced/untraced equality.
- Historical comparisons asserted exact input matching, stop monotonicity, gap formula and
  no pre-activation tightening. All passed.
- Historical fuel-cut persistence and one-minute armed-exit timing assertions passed.
- The original baseline reports have passthrough_verified=true. The new historical runs
  were not duplicated uninstrumented; experimental passthrough was tested on the synthetic replay.
- Gain scheduling remains FIXED. This change did not wire the missing runtime environment.
- Gamma was 1 in the historical replay; non-unit behavior is covered by unit tests.
- Combined net P&L worsened from -825.95 to -928.71 (about -102.76). This is one small block,
  not a tuned performance result. Stop actuation and persistence are demonstrated; improved
  returns are not.

The exact setup, replay and comparison commands are in IMPLEMENTATION.md. The policy is opt-in
and not part of the sealed calibration parameter registry. No Stage-B or live deployment was performed.
