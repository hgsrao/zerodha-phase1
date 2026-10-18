# R6 supplied-core defect inventory (29 verified by adversarial review)

Supplied `r6_avr_core.py` is unchanged (sha 62956d15…). Fixes live in `r6_avr_core_fixed.py` (F1–F9) and are proven by `test_r6_core_fixes.py`: every fix FAILS on the original and PASSES fixed.

| # | Severity | Defect | Function | Verify votes | Current containment | Fix / test |
|---|---|---|---|---|---|---|
| 1 | blocking | EXIT/TRIP/REDUCE re-issued with a fresh event_id every bar while a closing order is outstanding | `UnitMaster.decide` | 3/3 | — | F5 (position-scoped close event_id) |
| 2 | blocking | SafetyPermit has no identity, so a stale or other-symbol safety permit is accepted | `UnitMaster.decide` | 3/3 | — | F7 (optional SafetyPermit.identity) |
| 3 | blocking | Back-calculation gain kaw*dt > 1 over-corrects the integral, so the output chatters and the AVR | `PositionalPI.step` | 3/3 | — | F1 (PI chatter fixed; closed-loop dt=3 limit cycle OPEN, strict xfail) |
| 4 | blocking | ExposureAVR authorizes ENTRY above target (desired/capacity > target) and REDUCEs the same shar | `ExposureAVR.step` | 3/3 | REDUCE disabled; target=max in runs | NOT FIXED |
| 5 | blocking | ExposureAVR.step raises on the second symbol of the same bay on the same bar | `ExposureAVR.step` | 1/3 | same as d1 | NOT FIXED |
| 6 | blocking | One-bar measurement-integrity failure becomes a latched protective TRIP that de-excites and liq | `VoltageAVR.step` | 3/3 | — | F2 |
| 7 | blocking | restore() of a pre-trip checkpoint silently clears a live latch and rewinds causality | `VoltageAVR.restore` | 3/3 | — | F4 |
| 8 | blocking | ExposureAVR rejects a second symbol of the same bay on the same bar | `ExposureAVR.step` | 2/3 | gateway steps ExposureAVR once per bay/bar (lifecycle test 1) | NOT FIXED |
| 9 | blocking | Bay headroom is granted in full to every same-bar or in-flight candidate | `ExposureAVR.step` | 2/3 | gateway reservations (lifecycle tests 1-2) | NOT FIXED |
| 10 | major | Held position plus a governor ENTRY vote forces a full EXIT | `UnitMaster.decide` | 3/3 | held-position path disabled | NOT FIXED |
| 11 | major | A BLOCK vote (missing or stale data) on a held position triggers an invented EXIT | `UnitMaster.decide` | 3/3 | — | F2 (VoltageAVR votes HOLD on integrity fault) |
| 12 | major | held_quantity is not checked against position_id (FLAT with held=50 is accepted) | `UnitMaster.decide` | 3/3 | — | F6 |
| 13 | major | The OEL/UEL clamp to the live field value has no hysteresis, so it chatters and drives the unit | `VoltageAVR.step` | 3/3 | VoltageAVR bench-only | NOT FIXED |
| 14 | major | A single missing or invalid measurement latches a manual-reset protective TRIP (and an exposure | `VoltageAVR.step` | 3/3 | — | F2 |
| 15 | major | Caller-supplied dt is not checked against causal timestamp elapsed time, so the slew rate in pu | `VoltageAVR.step` | 3/3 | VoltageAVR bench-only | NOT FIXED |
| 16 | major | The Q droop and the Q limits have no explicit pu base, so the same physical MVAR gives opposite | `VoltageAVR.step` | 3/3 | VoltageAVR bench-only | NOT FIXED |
| 17 | major | reset_latch accepts a healthy sample from an older bar, not the current measurement | `VoltageAVR.reset_latch` | 3/3 | — | F3 |
| 18 | major | Reset forces command to field_min and zeroes both integrators, so the unit re-trips ANSI 27 a f | `VoltageAVR.reset_latch` | 3/3 | — | F3 |
| 19 | major | Measurement-integrity / stale AVR state emitted as tier-1 PROTECTION TRIP in UnitMaster | `UnitMaster.decide` | 3/3 | — | F2 |
| 20 | major | Authorized healthy reset_latch re-trips on its own reset value (command reset to field_min) | `VoltageAVR.reset_latch` | 3/3 | — | F3 |
| 21 | major | reset_latch accepts stale/non-causal/negative-field evidence and resets with no latch present | `VoltageAVR.reset_latch` | 3/3 | — | F3 |
| 22 | major | Each symbol in a bay is asked to close the entire bay excess, so the bay is over-reduced | `ExposureAVR.step` | 3/3 | REDUCE disabled | NOT FIXED |
| 23 | major | Acknowledged pending exits are not represented, so the same excess is requested again on each b | `ExposureAVR.step` | 3/3 | held-position path disabled | NOT FIXED |
| 24 | major | Measurement-integrity failures latch a manual-reset protective trip | `VoltageAVR.step` | 3/3 | — | F2 |
| 25 | major | VoltageAVR checkpoint is missing limiter state, mode and last state, so restart is not determin | `VoltageAVR.snapshot` | 3/3 | — | F9 |
| 26 | major | reset_latch accepts a stale pre-fault sample or a negative field sample | `VoltageAVR.reset_latch` | 3/3 | — | F3 |
| 27 | minor | Safety mandatory trip drops simultaneous governor/AVR trip reasons | `UnitMaster.decide` | 3/3 | — | F8 |
| 28 | minor | While latched, new protective conditions are never evaluated or recorded | `VoltageAVR.step` | 3/3 | VoltageAVR bench-only | NOT FIXED |
| 29 | minor | restore accepts integrators, commands and latches that step can never produce | `VoltageAVR.restore` | 3/3 | VoltageAVR bench-only | NOT FIXED |

Reproductions: each row's `repro_test_path` and run output are in `~/R6_forensics/R6_AVR_REVIEW_RAW.json`.
