# R6 Unit Master: native Engine B/C active-paper integration report

Worktree `~/projects/zerodha-r6-no-adaptation`, branch `r6-no-adaptation`, HEAD **459a085** (tree identical to 9ce42b1, no global adaptation).
Original branch `feature/r5-parameter-authority` at 63dee10 untouched. Offline only (no Kite, no live broker). **Nothing committed.**

## 1. Files changed

**No tracked file is modified** (`git diff --stat` is empty). Everything is new and untracked under `revision6_avr/`:

| File | Status | SHA-256 (prefix) |
|---|---|---|
| r6_avr_core.py, test_r6_avr_core.py, run_r6_avr_bench.py | supplied, **unchanged** (checksums OK) | 62956d15 / c2fc9dbc / 98668f82 |
| r6_active_local_gateway.py, r6_broker_exposure_probe.py, test_r6_native_ledger_local.py | supplied by you earlier, unchanged | 01d5ba29 / 822c4866 / 4a470aa2 |
| **r6_native_active.py** | new: `R6ActivePaperMixin` + `r6_classes()` (subclasses of the real B/C orchestrators) | 8f1b061c |
| **run_r6_native_replay.py** | new: offline runner over the project's own `run_engine_b` / `run_engine_c` | 9686cee3 |
| **test_r6_fsr_single_application.py** | new: 14 FSR tests on the real `_plant_entry_gate` / sizing code | 7074b371 |
| **test_r6_native_active_lifecycle.py** | new: 8 reservation/ack lifecycle tests | bf6c5c73 |

All hashes are in `R6_FILE_HASHES.txt`.

## 2. Call flow

**Native, unchanged:** PA → ID → MPC → pre-sizing safety → Mark V `_governor_entry` (comparator + FSR gate) → sizing (×FSR) → paper dispatch cap → `_plant_entry_gate` (MiCOM, 86, cooldown, SEL-300G, bay comparator, legacy AVR lot + UEL, HRSG headroom, ×FSR) → gross/sector caps → post-sizing safety → entry-decision gates → execution gate → lot rounding → native B/C `_submit_entry_order`.

**R6 active:** identical, except that inside `_plant_entry_gate`, *after* the native result:
1. The bay is stepped **once per completed bar**. Before that, reservations whose real orders show `ACCEPTED`/`FILLED`/terminal in `order_audit` are settled.
2. Ledger probe: broker net positions (partial fills included), acknowledged unfilled entries and real marks feed `ExposureAVR` (`ActiveBayGate.begin_bar`).
3. `UnitMaster.decide(governor vote, AVR bay-capacity vote, SafetyPermit built from the native result)`.
4. Result = `min(native, UnitMaster)`. A reservation is held and then bound to the real order id in `_register`. If no order is created, it is released at the end of `_admit_entry_candidate`.
5. A `R6_UNIT_MASTER_ENTRY` event is logged every time: candidate, timestamp, symbol, bay, FSR, native qty, R6 qty, bay capital, allowed/filled/pending/available, action, reason, authority.

Any probe/gate error, unsettled order or replayed bar → **BLOCK for new entries** (`R6_FAIL_CLOSED:…`). Held positions are not touched.

## 3. Mark V governor / PID integrity
- Governor, plant and protection code are unchanged, and R6 has no position-path override, so every exit stays native: HARD STOP, TARGET, ratchet floor, MAX HOLD, exhaust trip, FSR exit, z-exit, MiCOM/SEL/plant trips.
- Evidence: in the replays, exit reasons are identical between baseline and active (e.g. B: `FSR_BELOW_EXIT:FSRN` 3, `target` 1, `GOVERNOR_PATH_ERROR` 1).
- At full capacity the **native telemetry SHA-256 is identical** in baseline and active, for both engines.
- R5 regression: governor, authority hierarchy, paper apply, plant control and durability tests: **150/150 passed**.

## 4. FSR: applied once (finding: not double)
- Sizing `S = size(base·FSR)`; plant `P = ⌊min(avr_lot, ⌊headroom/p⌋)·FSR⌋`; final `min(S, P) = FSR·min(·)`, never FSR².
- Tests for FSR 0 / 0.5 / 0.75 / 1.0, with the AVR lot limiting and with headroom limiting: **pass**.
- Real trace: INFY candidate-4, AVR lot 20 × FSR 0.887 → 17; sizing 115; final 17.
- **Finding (unchanged code):** on the sizing side FSR is usually *masked*. `risk_budget = min(base·FSR·conviction, ₹5,000 cap)` and the safety notional max both bind, so sized quantities at FSR 1.0/0.75/0.5 are identical (test-pinned). FSR then acts only through the plant cap. That is not double derating, but it is weaker than intended. **No fix applied: this is your policy decision.**

## 5. Acknowledgment and reservation lifecycle
- **Lifecycle tests (8/8):** same-bar shared bay budget, release when no order is created, unacknowledged order → bay blocked, settling only on a real `ACCEPTED`, a partial fill not double counted (filled 10 + pending 50 of 60), replayed bar → fail closed, missing mark → fail closed, R6 never above native, refusal if global adaptation is on or governor authority is not full.
- **Real replays:** all 5 entry orders per engine were bound to R6 reservations and settled by real `ACCEPTED`+`FILLED` events. 0 unsettled at end, 0 reconciliation breaks.
- **Defect fixed in the adapter:** the probe counted topology symbols the run doesn't trade, and Engine C crashed (`KeyError: 'BHARTIARTL'`). The adapter now passes only the run's symbols.

## 6. Multi-symbol bay
- ExposureAVR is stepped once per bay per bar. Same-bar candidates share one budget, and reservations come off it before the next candidate is evaluated (tests 1–2).
- Tight run: 16 (B) / 15 (C) native entries reduced. E.g. INFY 13:25: native 27 → R6 16 against a ₹19,267 bay allowance.

## 7–8. Engine B / C replays (frozen 48-symbol dataset, HCLTECH/INFY/TCS/WIPRO, 2,000 bars to 2026-08-13)

| Run | B trades / net ₹ | C trades / net ₹ | Notes |
|---|---|---|---|
| Baseline (R6 off) | 5 / −17.20 | 5 / −59.60 | plant rejections: daily bay-fill latch 148/146, cutoff 20, AVR_CLAMP 2 |
| Active, util 1.0/1.0 | 5 / −17.20 | 5 / −59.60 | Unit Master ENTRY 22 / 21, BLOCK 170 / 168 (native already 0); native telemetry identical; repeat run identical |
| Active, tight 0.10/0.05 | 5 / −14.59 | 5 / −53.72 | R6 restricted 16 / 15 entries; same trades, smaller size |

- Governor (B baseline): ENTRY 205, FSRN hurdle 432, overspeed 62, grid unavailable 8. FSR on entries: 0.6–1.0 (38 at 1.0).
- Both engines submitted the same 5 orders (same symbols, same quantities). Candidate numbering and governor counts differ slightly between B and C in the **baseline** itself (205 vs 198 ENTRY), so the R6 differences are inherited from the native B/C difference.
- Synthetic smoke: active is identical to baseline. Tight: B ₹485.70 → 337.92 (same 3 trades); C 4 → 3 trades.
- No partial fills or cancellations occurred in these replays. Those paths are covered by the lifecycle tests only (**NOT TESTED** on real venue events).

**INFY SELL 2026-08-06 13:25 (candidate-5):**
1. Mark V ENTRY, FSR 1.0.
2. Sizing 115 → dispatch cap 85.
3. Native plant **admits 27**. The legacy AVR was *not* locked out here, because without adaptation the UEL is not inflated and excitation is not driven to its floor.
4. Unit Master ENTRY 27 (tight: 16).
5. **`Gate12StrategySignals` rejects it: confidence 0.4901 < 0.55.**
6. The R6 reservation was released (no order).

The candidate is not forced. The controlling authority is now the native strategy gate.

## 9. UEL / legacy AVR
- Without global adaptation, the legacy AVR blocks only 2 candidates (vs 133 in R6 at 63dee10).
- **The legacy UEL veto is retained.** The commercial minimum is applied once, by the legacy AVR; the Unit Master is called with minimum 0.
- Moving financial sizing from the legacy AVR to ExposureAVR is **not done**. It needs an approved invariant and approved utilization settings.
- The R6 utilization settings used here (max = target = 1.0, i.e. the native bay allocation) are **not approved**; the tight values are a test case only.

## 10. Tests

| Suite | Result |
|---|---|
| Supplied core + ledger tests (unittest) | 43/43 OK |
| New FSR + lifecycle tests | 22/22 |
| R5 governor / plant / paper / durability | 150/150 |
| Engine B + C suites | 70/72: the 2 inherited failures below |

Inherited, not introduced:
- `test_engine_a_golden_master_still_passes` also fails **17/17 at the clean cb10fac baseline** on this machine (byte drift; orders and P&L match the stored values).
- `test_engine_a_and_b_code_golden_masters_and_data_untouched` fails because the earlier groundwork commit 66c9369 edited 5 files outside its protected list. Still open; needs your approval or a revert.

Supplied-core review (adversarial, 3 skeptics each; raw data in `~/R6_forensics/R6_AVR_REVIEW_RAW.json`): **29 verified defects, none patched (core kept unchanged)**.
- **Contained by this integration:** multi-symbol same bar, bay double allocation, REDUCE/EXIT duplicates and whole-bay over-reduction (position path and REDUCE disabled), SafetyPermit without identity (built inline).
- **Blocking for any future VoltageAVR or position-path use:**
  - back-calculation unstable at dt > 2.5 s;
  - limiter chatter that trips ANSI 27/59;
  - missing data latched as a manual-reset trip that liquidates;
  - reset that is not bumpless and accepts stale evidence;
  - restore that clears latches;
  - an incomplete checkpoint;
  - an implicit Q pu base.

## 11. Rollback
Run the native runners (`engine_b/runner.py`, `engine_c/runner.py`, or `run_r6_native_replay.py … baseline`). Nothing native imports R6. To remove R6 completely: `rm -r revision6_avr/`.

## 12. Verdict: **BLOCKED** (integration works as a restricting entry authority; not acceptance-complete)
- **Done:** Unit Master is authoritative on real B/C entry admission (it restricts, never exceeds native); real ack/fill-bound reservations; shared bay budget; fail-closed; deterministic; governor exits preserved; FSR applied once.
- **Blocking:**
  - REDUCE is disabled (no native partial-reduce path).
  - VoltageAVR is not integrated (no physical measurement exists, and it has verified defects).
  - Utilization setpoints are not approved.
  - The legacy UEL still owns the commercial minimum.
  - Partial-fill, cancel and reject paths are not exercised on real venue events.
  - ExposureAVR is not checkpointed in the B/C runs.
  - The two inherited B/C failures remain.
  - Only a 4-symbol, 2,000-bar window has been replayed.
- **Not production-ready. Nothing committed or pushed; awaiting your approval.**
