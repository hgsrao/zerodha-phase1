# R6 commissioning report v2: physical governor R/M/J + active-paper Unit Master

Worktree `~/projects/zerodha-r6-no-adaptation`, branch `r6-no-adaptation` at 459a085. **No tracked file modified; nothing committed or pushed.** The original R5 worktree is untouched (tracked files at 63dee10; 23 untracked scripts from another session left alone). Offline only.

## Files (all new, under `revision6_avr/`; hashes in `R6_FILE_HASHES_v2.txt`)
- `r6_turbine_governor.py`: physical-equivalent R/M/J governor. It extends the other session's version and stays API-compatible with it.
- `test_r6_turbine_governor_acceptance.py`: 36 tests. The other session's `test_r6_turbine_governor.py` (5) is kept.
- `r6_avr_core_fixed.py` + `test_r6_core_fixes.py`: fixes F1–F9 on a copy. The supplied `r6_avr_core.py` is unchanged (62956d15).
- `r6_native_active.py`: added `r6_snapshot` / `r6_restore`. The other session added an isolated `r6_physical_governor_step`, which the trading path never calls.
- `test_r6_native_active_lifecycle.py` (+ restart test), `test_r6_venue_events_engine_c.py` (3 real venue-event tests).
- `R6_CORE_DEFECT_INVENTORY.md`: the 29 defects ranked by severity.

## A. Physical turbine governor R/M/J: **PASS** as an isolated simulation component (not as plant fidelity)

**Equations:**
- Speed error: `e = speed_pu − 1`.
- Droop: `P_cmd = clamp(P_ref − e/R, Pmin, Pmax)`.
- Turbine lag: `τ·dPm/dt = P_cmd − Pm` (exact zero-order-hold step).
- Swing: `M·dω/dt = Pm − Pe − D(ω−1)`, with M = 2H.
- Inertia: `ω_mech = 2πf/pole_pairs`, `H = Jω²/(2S)`, `J = M·S/ω²`.
- Physical dt in seconds, sub-stepped at 10 ms.

**Five-bay configuration:**

| Bay | Type | R | M (provisional) | H | D | τ |
|---|---|---|---|---|---|---|
| GTG1_HEAVY_INDUSTRY | gas | 0.040 | 8.0 | 4.0 | 1.00 | 0.30 |
| GTG2_TECH_TELECOM | gas | 0.040 | 8.0 | 4.0 | 0.90 | 0.26 |
| CSTG1_BFSI | steam | 0.040 | 5.0 | 2.5 | 1.20 | 0.80 |
| CSTG2_CONSUMER_AUTO | steam | 0.040 | 5.0 | 2.5 | 1.25 | 0.90 |
| BPSTG_HEALTHCARE | steam | 0.040 | 5.0 | 2.5 | 1.50 | 1.40 |

D and τ come from `revision5/machine_archetypes.py` (provenance: not OEM, not for commissioning). There is no MVA, pole-pair or J data in the repository, so J is derived only when the operator supplies them; a contradictory J is rejected.

**+0.1 pu load-step results:**
- **ROCOF:** GTG −0.0123 /s vs STG −0.0196 /s (= −ΔP/M).
- **Nadir:** GTG1 0.99513 vs BPSTG 0.98988.
- **Steady state:** about −190 mHz (the analytic droop equilibrium) on all five bays.
- **Settling:** 1.3 s (GTG) to 7.8 s (BPSTG).

**Tests: 41/41** (36 acceptance + 5 from the other session). Acceptance items:
- 1–3: settings;
- 4: R unchanged after 5,000 steps;
- 5: R immune to Mark V gain adaptation, direct mutation and restore;
- 6–7: droop sign;
- 8: inertia ordering;
- 9–10: M/J/H consistency and rejection;
- 11: saturation;
- 12: turbine lag separate from rotor inertia;
- 13: independent state;
- 14: invalid input and timestamps rejected without changing state;
- 15: deterministic and stable;
- 16: reset/restore keep R.

Items 17–18 (Mark V and protective exits unchanged): native governor/plant/paper/durability **150/150**; the governor source is untouched.

**Gemini reference defects, each corrected:**
- Its undefined `error` and `raw_pid − error/R` are replaced by an explicit speed-error droop. This module contains no PID; the Mark V PID is untouched.
- Its `dt/(M+dt)` is a first-order filter, not inertia. It is modelled as turbine lag τ, and the inertia as the swing equation in M.
- R ≤ 0, M ≤ 0, nonfinite inputs and dt ≤ 0 are rejected.
- Output is saturated. There is no integrator, so no windup.
- A machine base and J/H are added; gas and steam bays are configured separately.

**Limits:**
- No real rotor-speed or electrical-power measurements exist, so this is not wired to trading.
- M = 8 / 5 are provisional. With them the steam units have *less* inertia than the gas units, the reverse of the repository archetypes (steam H 5.5–8.0). Please confirm.

**Rollback:** delete the two files; nothing imports them except the opt-in hook.

## B. Active-paper Engine B/C Unit Master: **BLOCKED**
**Revalidated, no regression:** all 6 frozen replays reproduce exactly (same trades, P&L, native telemetry hash and R6 decisions):

| Run | Engine B | Engine C |
|---|---|---|
| Baseline | 5 trades, −₹17.20 | 5 trades, −₹59.60 |
| Active, full capacity | 5 trades, −₹17.20 | 5 trades, −₹59.60 |
| Active, tight capacity | 5 trades, −₹14.59 | 5 trades, −₹53.72 |

The INFY SELL disposition is unchanged: Gate12, confidence 0.4901 < 0.55.

**New evidence:**
- **Checkpoint/restore:** a mid-run restart is identical to an uninterrupted run, and incompatible settings are rejected.
- **Real Engine C venue events:** partial fill + IOC cancel, venue REJECTED and MARGIN all settle their reservations, with no reconciliation breaks.
- **Tests:** R6 tests 78 passed + 1 strict xfail; supplied tests OK; R5 core 150/150; B/C 70/72 (the same two inherited failures).

**Blocking:**
- REDUCE and held-position path disabled.
- VoltageAVR not integrated.
- Open closed-loop limit cycle at dt = 3 s (needs approved gains).
- 12 defects contained, not fixed.
- Utilization setpoints unapproved.
- Legacy UEL still owns the commercial minimum.
- Settlement is lazy, so reservations stay conservative at end of data.
- Partial-fill and reject events are tested in Engine C only, on synthetic data.
- The two inherited B/C failures.
- Replay limited to 4 symbols × 2,000 bars. The other session's 48-symbol one-day B run exists, but I did not verify it.

**Rollback:** run in `baseline` mode, or `rm -r revision6_avr/`.

Not production-ready. No profitability claim follows from the governor simulation.
