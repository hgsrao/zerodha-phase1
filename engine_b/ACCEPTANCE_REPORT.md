# R5 Engine B (NautilusTrader native execution): acceptance report

Branch: `feature/r5-nautilus-engine-b` (base `199226b`, candidate/r5-active-ccpp-integrated). Nothing committed or pushed.
Nautilus Trader 1.231.0 (cp313 wheel), Python 3.13, numpy 2.5.2, pandas 2.3.3.

## Verdict

| Criterion | Status |
|---|---|
| Engine A regression-identical | **PASS**: 17/17 golden-master scenarios identical; full suite unchanged (880 passed / 16 failed / 7 skipped, same 16 failures as before the refactor) |
| Engine B executes through native Nautilus | **PASS**: orders go to Nautilus `SimulatedExchange`. R5 books positions only from Nautilus `OrderFilled` events, and the paper broker is disabled (raises on use). |
| Accounting reconciles | **PASS**: Nautilus gross = R5 ledger gross exactly. Commissions and net agree to within paisa rounding. 0 reconciliation breaks. |
| New tests pass | **PASS**: 9/9 Engine B tests, plus a 3-scenario verification script |
| Real historical-data smoke test + 48-symbol replay | **BLOCKED in this environment**: the frozen dataset and grid feeds are not present here (`integrity check failed: 0 mismatched, 48 missing`). This must run on the operator machine; commands are below. No synthetic substitution was made. |

Engine B is accepted on synthetic data only. Final acceptance still needs the real-data runs in the last section to pass on the operator machine.

## Architecture as built

The flow per completed bar:

1. Nautilus delivers a completed 1-minute `Bar` (stamped at bar close).
2. `R5NautilusStrategy.on_bar` passes it to `EngineBOrchestrator.on_completed_bars`. That method certifies the row (BB03 Pandera), enforces strictly increasing time, appends it, and calls the shared `_process_tick`.
3. The shared R5 code makes every decision: PA/ID/MPC, closed loop, governor, CCPP plant, safety gates, sizing and exits.
4. Approved intents go out through `_submit_entry_order` / `_execute_exit` as native Nautilus `MarketOrder` (IOC).
5. Nautilus emits `OrderAccepted / OrderFilled / OrderCanceled / OrderRejected / OrderDenied`.
6. The shared bookkeeping `_on_entry_order_result` / `_on_exit_order_result` runs with the actual fill. This includes the CCPP bind/release, governor and closed-loop feedback, and the R5 trade ledger.
7. Nautilus portfolio and account provide independent P&L and commissions.

* **No strategy duplication.** Engine B overrides only the execution-port seams: `_next_bar_timestamp`, `_entry_reference_price`, `_entries_suspended`, `_broker_position`, `_broker_realized_pnl`, `_equity`, `_submit_entry_order`, `_execute_exit`, and a one-line `_maybe_exit` guard while an order for the symbol is working.
* **Option A (Engine B only).** The plan is built on the completed decision bar's close. Nautilus fills the IOC market order at that bar's close. The position is booked at Nautilus's actual average fill price, with the planned stop and target distances re-anchored to the fill. Quantity can only shrink (on a partial fill). After the fill, three checks apply: a fill beyond the planned stop, the ₹5,000 rupee cap, and HRSG-effective bay capacity. Any breach arms the existing R5 plant-trip exit (`plant_trip:ENGINE_B_*`). Nothing is ever upsized.
* **Pending / partial / rejected / cancelled orders:**
  * One working order per symbol; new entries and exit re-evaluation for that symbol are suspended while it works.
  * A partial entry is booked at the filled quantity once the IOC remainder is cancelled.
  * A partial exit's residual is re-sent (up to 3 per bar), then parked and re-sent at each following bar. The close is booked once, at the VWAP across all exit child orders, and the CCPP release happens only then.
  * A rejected or denied entry is disposed with no booking.
  * Two consecutive exit rejections halt new entries; protective exits stay enabled.
* **Reconciliation.** After every fill and at every tick, the Nautilus net position must equal R5's booked trade plus any working order's filled quantity. A mismatch, an orphan fill, or a gross-ledger mismatch halts new entries (fail closed) and is reported.
* **Same-tick semantics.** The venue uses `use_message_queue=False`, so Nautilus delivers fill events synchronously inside `submit_order`. R5 therefore sees a confirmed fill before ranking its next same-tick candidate, exactly as Engine A does.
* **Commissions.** `R5LegCostFeeModel` charges each Nautilus fill `revision2.transaction_costs.leg_cost(fill_px, fill_qty, side)`. Nautilus books it in INR, rounded to the paisa.

## Engine A golden master (regression oracle)

* `scripts/capture_r5_golden_master.py` runs 17 deterministic offline scenarios through `Revision2ExternalEngineOrchestrator.run()`:
  * plant probe, seeds 11–14;
  * single-signal pipeline, seeds 11–14;
  * default governor, shadow closed loop, plant SHADOW, and no plant;
  * multi-day random walk;
  * 4 regime-structured multi-day runs covering stop, target, stop-gap, regime, saturation, MiCOM, governor and end-of-run exits.
* For each scenario it records the canonical SHA-256 of the full report plus 10 section hashes and the funnel.
* Baseline: `golden_master/r5_shared_orchestrator/baseline.json`, sha256 `a0165d7e38d79c12ac60dbb7a2b52567c6912fdb6efce48977d6d3c73d39cc8d`.

| Check | Code | PYTHONHASHSEED | Result |
|---|---|---|---|
| capture | pre-refactor | 0 | 17 scenarios written |
| determinism 1 | pre-refactor | 1 | PASS 17/17 |
| determinism 2 | pre-refactor | random | PASS 17/17 |
| regression | **refactored** | 2 | **PASS 17/17** |

Scenario funnels (identical before and after the refactor):

```
probe_paper_apply_full_seed11                f527be8b32de5b75  orders=4 fills=4 trades=4 net=791.63
probe_paper_apply_full_seed12                81dc2ddd6b65c960  orders=3 fills=3 trades=3 net=733.78
probe_paper_apply_full_seed13                3af84eb6f13f7bea  orders=4 fills=4 trades=4 net=651.55
probe_paper_apply_full_seed14                ac354df3d6f6ed4b  orders=2 fills=2 trades=2 net=500.35
single_signal_paper_apply_full_seed11        da22602b51b37122  orders=2 fills=2 trades=2 net=306.61
single_signal_paper_apply_full_seed12        bc2070461c69898b  orders=0
single_signal_paper_apply_full_seed13        124b54322ec660b8  orders=2 fills=2 trades=2 net=230.34
single_signal_paper_apply_full_seed14        4a376b5867ccb457  orders=0
probe_paper_apply_default_governor_seed11    7462f4046fb40aae  orders=4 fills=4 trades=4 net=1181.07
probe_shadow_closed_loop_seed12              9b8fdd460d6d6200  orders=3 fills=3 trades=3 net=804.03
probe_plant_shadow_seed13                    11c2b482d726cbfe  orders=12 fills=12 trades=12 net=9628.23
probe_no_plant_seed14                        035e57a7215d4089  orders=8 fills=8 trades=8 net=6129.99
multiday_paper_apply_full                    8a5f093212af4386  orders=0 (Gate 12 rejects all)
regime_multiday_paper_apply_full             b5d61e5cf144264f  orders=1 fills=1 trades=1 net=22.36
regime_multiday_paper_apply_default_governor e0a62ad7938b0a65  orders=1 fills=1 trades=1 net=42.79
regime_multiday_plant_shadow                 ea529d6cf4f19d9f  orders=61 fills=61 trades=61 net=36242.06
regime_multiday_no_plant                     3a9595ae02a36f53  orders=61 fills=61 trades=61 net=36242.06
```

Full suite (`pytest tests/ tests_external/ --ignore=tests/legacy_quarantine`):
* Before the refactor: 880 passed / 16 failed / 7 skipped.
* After the refactor: 880 passed / 16 failed / 7 skipped, with the identical failure set.
* All 16 failures are the frozen-dataset loader returning `None` (`'NoneType' object has no attribute 'tail'`) or real-data-only tests.
* Eight of these exercise the orchestrator on real data. They must also be run on the operator machine (command below).

## Engine B results (synthetic smoke feeds only, seeded, on the NSE 0.05 tick grid; P&L meaningless)

| Scenario | Native orders submitted / accepted | Rejected | Cancelled | Fill events | Partial entry | Exit residual children / parked | Positions closed | Trades | R5 gross | R5 costs | R5 net | Nautilus gross | Nautilus commissions | Nautilus net | Recon checks | Breaks |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| INFY 500 bars | 2 / 2 | 0 | 0 | 2 | 0 | 0 / 0 | 1 | 1 | 406.0000 | 36.8944 | 369.1056 | 406.0000 | 36.89 | 369.11 | 443 | 0 |
| INFY 500 bars, thin liquidity (`liquidity_consumption=True`) | 5 / 5 | 0 | 4 | 3 | 1 (15 of 29) | 3 / 1 | 1 | 1 | 214.5000 | 19.0860 | 195.4140 | 214.5000 | 19.09 | 195.41 | 445 | 0 |
| INFY + TCS 1,125 bars × 3 sessions | 6 / 6 | 0 | 0 | 6 | 0 | 0 / 0 | 3 | 3 | 430.0500 | 62.2890 | 367.7610 | 430.0500 | 62.29 | 367.76 | 2,138 | 0 |
| INFY 500 bars, instrument `max_quantity=10` | 1 / 0 | 1 (Nautilus DENIED: "quantity 29 invalid (> maximum trade size of 10)") | 0 | 0 | 0 | 0 / 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 |

Verification script (instrumentation only, no production code), PASS on the first three scenarios:
* duplicate working orders: none;
* `|Nautilus fill commission − round(leg_cost(fill), 2)|` = 0.000000 on every fill;
* a reconciliation ran after every fill;
* every CCPP `release_position` happened with the Nautilus position already flat (none premature);
* the Nautilus vs R5 commission gap is at most ₹0.0044, which is paisa rounding.

Engine B tests, `tests_engine_b/test_engine_b_nautilus.py`: **9 passed** (121 s).
1. native SUBMIT → ACCEPTED → FILLED lifecycle;
2. Nautilus accounting = R5 ledger;
3. event chronology and causal clock (decision lag 0 ns; fill at the decision bar's close);
4. Option A post-fill stop/target/risk/plant capacity;
5. no lookahead (future bars rewritten → identical decisions and orders up to the cut);
6. paper broker disabled and offline port required;
7. protected Engine A files unchanged;
8. native Nautilus denial disposed, nothing booked;
9. native partial entry and exit fills reconcile (VWAP).

## Safety checks

* **No future data.**
  * Engine B frames contain only completed bars. `_next_bar_timestamp` raises unless deciding on the latest bar, and the next timestamp is the calendar successor (+1 minute).
  * The only next-row reads left in the shared orchestrator are Engine A's two seams (`orchestrator.py:1139,1142`), which Engine B overrides. Every other `+ 1` index is an inclusive slice ending at the current bar.
  * Test 5 proves future bars cannot change past decisions.
* **No live or Kite path.** Engine B code contains no Kite, broker-network or Nautilus live-node references. The strategy is only ever attached to `BacktestEngine`, `_assert_paper_plant_broker` requires an offline port, and the paper broker is a stub that raises.
* **Protected files untouched:** `revision2/`, `revision2_external/paper_execution.py`, `revision5/` (including `ccpp_unified_plant.py`), `runtime/`. `git diff HEAD` over these paths is empty.

## Git changes (uncommitted)

```
 M revision2_external/orchestrator.py   1287 +++/--- raw; 128 insertions / 41 deletions ignoring whitespace
?? engine_b/__init__.py, nautilus_market.py, orchestrator.py, runner.py, strategy.py   (593 lines)
?? scripts/run_engine_b_nautilus.py                                                   (131+ lines)
?? scripts/capture_r5_golden_master.py                                                (273 lines)
?? tests_engine_b/test_engine_b_nautilus.py                                           (148 lines)
?? golden_master/r5_shared_orchestrator/  (baseline.json + 17 gzipped canonical reports, 11 MB)
?? engine_b/ACCEPTANCE_REPORT.md
```

The orchestrator change is a verbatim extraction generated mechanically:
* `run()` is split into `_prepare_run`, `_process_tick`, `_admit_entry_candidate`, `_submit_entry_order`, `_on_entry_order_result` and `_finalize_run`.
* `_execute_exit` is split into submission plus `_on_exit_order_result`.
* Five named seams are added that keep Engine A's behaviour: the next-bar timestamp, the next-bar open, `_entries_suspended`→False, the paper-broker position, and the paper-broker realized P&L.

## Outstanding limitations

1. **Real-data runs not executed here.** The data isn't in this environment. Both the real-data smoke test and the 48-symbol replay must run on the operator machine.
2. **Fill timing differs from Engine A by design.** Engine B fills at the decision bar's close (Nautilus bar execution, Option A); Engine A fills at the next bar's open. Engine B's trades and P&L are therefore expected to differ from Engine A's. That is a model difference, not a defect.
3. **Default fill model.** The venue uses Nautilus's default `FillModel()`, which applies no extra slippage. Engine A's paper broker applies a slippage band, so Engine B fills are more optimistic by that band.
4. **Shared label on rejection.** A Nautilus-rejected entry is disposed under the shared label `paper_fill_rejected`. The Nautilus reason is kept in `order_audit`.
5. **Performance.** Engine B grows each symbol's frame and re-certifies each incoming row. Observed in this sandbox: about 25 s for a 1-symbol, 500-bar run, and several minutes for a 2-symbol, 1,125-bar run while other jobs were also running. Runtime grows faster than linearly with window length, so a 48-symbol replay should start at `--bars 1200` before longer windows.
6. **Open positions at the end of data.** Nautilus cannot fill after the data ends, so Engine B reports them as open positions instead of force-closing them. End windows at the session close so the R5 15:15/15:25 square-off flattens them.
7. **Hash comparison on synthetic data.** The golden master and the Engine B tests use synthetic data. The real-data orchestrator tests (8 of the 16 data-missing failures) must also pass on the operator machine.

## Exact commands

Sandbox (synthetic, reproducible):

```bash
cd ~/projects/zerodha-phase1 && git checkout feature/r5-nautilus-engine-b
source ~/.venvs/nautilus_r5_clean/bin/activate          # nautilus_trader==1.231.0
python scripts/capture_r5_golden_master.py --check       # Engine A regression: expect "GOLDEN MASTER: PASS"
PYTHONPATH="$PWD" python -m pytest tests_engine_b -q     # expect 9 passed
PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --synthetic-smoke --bars 500
PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --synthetic-smoke --symbols INFY,TCS --bars 1125
```

Operator machine (real frozen data; required for final acceptance):

```bash
cd ~/projects/zerodha-phase1 && source ~/.venvs/nautilus_r5_clean/bin/activate
python -m pytest tests/ tests_external/ -q --tb=no --ignore=tests/legacy_quarantine   # expect 0 data-missing failures
# Real INFY smoke (verifies the 48-file SHA-256 manifest and grid manifest first; exits on any mismatch)
PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --symbols INFY --bars 1200 \
    --report local_workspace/validation/engine_b/infy_real_1200.json
# Only if the smoke run exits 0 (0 breaks, no gross mismatch):
PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --symbols ALL --bars 1200 \
    --report local_workspace/validation/engine_b/all48_real_1200.json
```

Each report JSON contains:
* the Nautilus fill and position ledgers;
* Nautilus gross, commissions, net and performance statistics;
* the R5 trade ledger and funnel;
* the Option A fill rows;
* the full order audit;
* reconciliation checks and breaks.

The CLI exits with code 1 on any reconciliation break or gross mismatch.
