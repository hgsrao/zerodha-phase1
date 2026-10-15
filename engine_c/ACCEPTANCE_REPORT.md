# R5 Engine C (Backtrader native execution): acceptance report

Branch `feature/r5-backtrader-engine-c`, created from Engine B commit `500ababc2cbf12a33902712ce8174ae5cca9322a`
(`feature/r5-nautilus-engine-b`). Backtrader 1.9.78.123, Python 3.13, numpy 2.5.2, pandas 2.3.3.

## Verdict

| Criterion | Status |
|---|---|
| Engine A and Engine B unchanged | **PASS**: no file under Engine A/B code, golden masters, tests or data differs from `500abab`. Golden master 17/17 identical. Engine B tests 9/9. Shared suite result identical to Engine B's acceptance run (see Tests). |
| Engine C executes through native Backtrader | **PASS**: Cerebro, `PandasData` feeds, `BackBroker` (cash, positions, orders), a Backtrader `CommInfoBase` commission scheme, native slippage, the native `FixedBarPerc` volume filler and native order/trade notifications. R5 books positions only from Backtrader execution bits. |
| Accounting reconciles | **PASS** on every synthetic replay: 0 breaks. Backtrader realized P&L, commissions, cash, value and closed trades equal the R5 fill-derived ledger and the R5 trade ledger within 1e-4 INR. |
| Engine C tests | **PASS**: see Tests for exact counts. |
| Real-data INFY replay and 48-symbol replay | **NOT RUN here**: the frozen 48-symbol dataset and the grid feeds are not in this environment (only the manifest is). Commands to run them offline on the operator machine are at the end. No synthetic data was substituted for them. |

Engine C is accepted on synthetic data only. Final acceptance needs the two real-data runs below.

## Data-contract correction: Diwali Muhurat sessions

The operator's first real INFY run was refused with `historical-data contract violated: {'INFY': {'outside_session': 120}}`. The 120 rows are the Diwali Muhurat evening sessions of 2023-11-12 and 2024-11-01, which the contract treated like any other out-of-hours row.

**Fix.** `engine_c/data_contract.py` now carries `SPECIAL_SESSIONS`, an explicit list of two sourced entries. Each entry names its date, its first and last permitted 1-minute bar label, its kind, whether the regular session ran that day, and its source. Nothing is inferred from the data.

| Date | Permitted bar labels (IST, open time) | Bars | Source |
|---|---|---|---|
| 2023-11-12 (Sunday) | 18:15 to 19:14 | 60 | NSE circular NSE/CMTR/59124, 27 Oct 2023: normal market 18:15–19:15 (pre-open 18:00–18:08, block deal 17:45–18:00, closing session 19:25–19:35 are excluded) |
| 2024-11-01 (Friday, regular-session holiday) | 18:00 to 18:59 | 60 | NSE Muhurat timings as published by Business Standard, Fyers and Angel One: normal market 18:00–19:00 (pre-open 17:45–18:00 excluded); NSE Clearing circular NCL/CMPT/64632 gives the same 18:00–19:00 window |

The two windows hold 120 labels in total, the same count as the refused rows. The rows themselves could not be inspected here, so a row at any other minute will still be refused on the desktop, which is the intended behaviour.

Rules, applied to every raw row in file order before the replay window is chosen:

* Ordinary dates keep 09:15–15:29 unchanged.
* On a special-session date only that date's window is valid. Regular-hours rows on those two dates are fatal, because the regular session did not run.
* Evening rows on any other date, and minutes just outside a special window (pre-open, the end minute, the closing session), stay fatal.
* Missing minutes on a special date are counted against its 60 expected bars, not 375. Special dates are not listed as sessions ending before force close.
* The audit reports `special_session_rows` and a per-date `special_sessions` summary, for the whole file and for the replay window.
* Rows with different UTC offsets in one file are now a fatal `mixed_utc_offsets` finding. Before this change they raised a Python error instead of a contract refusal.

`load_real` now calls `audit_and_select`, which audits every raw row of every file, refuses on any fatal finding, and only then selects the last `--bars` rows dated on or before the grid end. That order is unchanged from before; it is now a separate function so it can be tested without the frozen dataset. Frozen data, manifests, grid feeds, Engine A, Engine B, the shared R5 code and the golden masters are untouched.

**If a replay window contains Muhurat bars.** They are passed through as they are, never dropped or filled. The shared R5 times (no entries after 15:20, force close at 15:25) mean R5 opens nothing in those bars. A synthetic probe with a 60-bar 18:00 block after a full session confirmed this: no order events in the evening block, 0 reconciliation breaks.

**Sources:**
https://archives.nseindia.com/content/circulars/CMTR59124.pdf,
https://zerodha.com/marketintel/bulletin/363312/muhurat-trading-session-on-account-of-diwali-7,
https://www.business-standard.com/markets/news/bse-nse-to-conduct-one-hour-diwali-muhurat-trading-on-november-1-124102000459_1.html,
https://fyers.in/notice-board/muhurat-trading-scheduled-on-friday-november-01-2024/,
https://www.angelone.in/news/market-updates/diwali-muhurat-trading-2024-nov-1,
https://nsearchives.nseindia.com/content/circulars/CMPT64632.pdf

**Limitations of this correction.**

* The 2024 window rests on NSE's announcement as reported by press and brokers, plus the NSE Clearing circular; the NSE capital-market circular itself was not retrieved.
* Whether the frozen INFY file's 120 rows sit exactly on these minutes is verified only when the operator reruns on the desktop.
* The calendar covers only the two dates the dataset was refused on. Other special sessions inside 09:15–15:29 (for example Saturday sessions, or the 2025-10-21 Muhurat session at 13:45–14:45) already pass the time check and appear as missing minutes against 375, as before.
* Real-data execution and reconciliation have **not** passed yet. They need the desktop rerun below.

## Architecture as built

```
engine_c/config.py          Engine C's own venue config: cash, NSE commission schedule, slippage, volume filler, venue max qty
engine_c/bt_market.py       R5 frame -> Backtrader feed (IST bar-open labels), NSE commission scheme, NSEBackBroker
engine_c/strategy.py        bt.Strategy hosting R5: completed bars in, market orders out, notify_order/notify_trade back
engine_c/orchestrator.py    EngineCOrchestrator(Revision2ExternalEngineOrchestrator): execution-port seams only
engine_c/reconciliation.py  FillLedger (independent arithmetic) + Reconciler (per event, per tick, final)
engine_c/data_contract.py   frozen-dataset verification and raw-row audit; shared synthetic feed
engine_c/runner.py          run_engine_c(): one deterministic offline Cerebro run -> JSON report
scripts/run_engine_c_backtrader.py   CLI (--symbols INFY|ALL, --bars, --warmup, --data-dir, --grid-manifest, --report)
scripts/run_engine_a_reference.py    Engine A (unchanged run()) on the same data contract, comparable report
scripts/compare_r5_engines.py        read-only three-engine comparison and divergence classification
tests_engine_c/                      19 tests
engine_c/requirements_engine_c.txt   dependency manifest (adds backtrader only)
```

Flow per Backtrader cycle:

1. Backtrader runs its broker cycle first. Market orders from the previous cycle execute at the open of their symbol's newly delivered bar.
2. `notify_order` delivers SUBMITTED / ACCEPTED / PARTIAL / COMPLETED / CANCELED / MARGIN / REJECTED with the execution bits. R5 books an entry or a close only from those bits. This runs the shared `_on_entry_order_result` / `_on_exit_order_result`, which does the CCPP bind/release, governor and closed-loop feedback and the trade ledger.
3. `Strategy.next` collects the datas that delivered a new bar this cycle. All of them carry one label. It hands them to `on_completed_bars`, which certifies each row (BB03 Pandera), enforces strictly increasing labels per symbol and runs the shared `_process_tick`.
4. Every decision is the shared R5 code: PA/ID/MPC, closed loop, governor, CCPP plant, safety gates, sizing and exits. Approved intents leave through `_submit_entry_order` / `_execute_exit` as Backtrader market orders.

Engine C overrides only these seams: `_next_bar_timestamp`, `_entry_reference_price`, `_entries_suspended`, `_maybe_exit` (a guard while an order works), `_broker_position`, `_broker_realized_pnl`, `_equity`, `_gross_exposure_notional`, `_submit_entry_order`, `_execute_exit`, `_assert_paper_plant_broker`. The paper broker is replaced by a stub that raises on any order or position access. A test scans the `engine_c` imports: no Engine B module, no `nautilus_trader`, no `paper_execution`, no Kite.

### Event-time and execution-time semantics

* A bar's label is its OPEN time in exchange-local IST. The frozen dataset carries `+05:30`. Naive input is taken as IST, and a UTC/IST mix-up is caught because the rows fall outside 09:15–15:29.
* A bar's values become available at its close (label + 1 minute), when Backtrader delivers it. R5 decides then, on that bar and earlier bars only.
* The entry plan is built on the decision bar's close, because the next open is unknown at decision time.
* Backtrader fills the market order at the OPEN of that symbol's next delivered bar, with adverse `slippage_perc` (default 0.0005) capped at that bar's high/low. With contiguous bars, fill time equals decision time: label t's close is label t+1's open, so the fill latency R5's Gate 14 sees is 0 s.
* **No same-bar fills.** No order ever fills on the bar that produced its decision. Backtrader's cheat-on-close and cheat-on-open are not used.
* **Exits.** R5 decides exits on the completed bar: stop or target touched intrabar, governor, plant, time and force-close. They fill at the next bar's open. There are no intrabar stop or target fills. This is conservative: a stop exit can fill beyond the stop by up to one bar's move.
* **Re-anchoring.** The entry is booked at Backtrader's average fill price. The planned stop and target distances are re-anchored to the fill, and quantity can only shrink. Three conditions arm the existing R5 plant-trip exit: a fill beyond the planned stop (opening gap), the ₹5,000 rupee cap, or HRSG-effective bay capacity. A test exercises the gap case.
* **Concurrency.** One working order per symbol. While it works, new entries and exit re-evaluation for that symbol are suspended.
* **Capacity reservations.** R5's own admission counts only booked positions, so working entries reserve capacity:
  * `_gross_exposure_notional` adds their notional, which R5's gross check then sees;
  * an Engine C pre-submit check covers concurrency, sector and bay headroom with working entries included.
  * A breach is disposed as `engine_c_pending_capacity:<reason>` and counted.
* **Fail closed.** A reconciliation break halts new entries and cancels working entries.
* **Short sessions.** If a session in the data ends before R5's 15:25 force-close bar, the position is flattened at the next session's first opportunity and reported in `session_carry_exits`. It is never back-dated.
* **End of data.** Open positions stay open and are reported with Backtrader's unrealized P&L. Working orders are cancelled natively in `Strategy.stop()`. An order created on the final bar never reaches a broker cycle and stays SUBMITTED; it is reported, never filled.

### Order lifecycle as supported by Backtrader

| State | Source in Engine C | R5 effect |
|---|---|---|
| Submitted, Accepted | `BackBroker` with `checksubmit` | audit only |
| Completed | broker cycle at the next open | entry booked / position closed |
| Partial | native `FixedBarPerc` filler (`volume_fill_perc`) | entry: remainder cancelled natively (IOC semantics), filled quantity booked; exit: remainder keeps working at later bars, close booked at the VWAP of all exit fills |
| Canceled | `broker.cancel` (IOC remainder, fail-closed halt, end of data) | unfilled entry disposed |
| Margin | `checksubmit` cash check | entry disposed, nothing booked |
| Rejected | `NSEBackBroker` venue rule: quantity above the symbol's maximum order quantity, through Backtrader's `order.reject()` | entry disposed; 2 consecutive exit rejections halt new entries, protective exits continue |
| Expired | not reachable: Backtrader market orders never expire | — |

`BackBroker` has no rejection path for a plain market order other than orphan bracket children. The venue rule above is therefore Engine C's extension. It uses Backtrader's own order states and notifications, and it is the analogue of Engine B's Nautilus `max_quantity` denial.

A market order submitted in the current cycle cannot be cancelled before Backtrader's next broker cycle executes it, because `BackBroker.cancel` only removes orders from its matching queue. The fail-closed halt therefore cancels only entries still in that queue.

### Independent reconciliation

There are three views:

* **Backtrader:** `getposition` size and average price, `getcash`, `getvalue`, and the P&L and commission on each execution bit and closed `Trade`.
* **`FillLedger`:** positions, average prices, realized P&L and cash recomputed from the fills, with commission from the R5 cost model `leg_cost` rather than Backtrader's scheme.
* **The R5 trade ledger.**

When the checks run:

* Per symbol, after every execution and every booking: position (Backtrader = fill ledger = R5 expected), average price, and the R5 entry price.
* At every tick start, the per-symbol checks plus account-level cash, realized gross, commission, and outstanding orders (Backtrader's live orders against R5's working orders).
* At completion, 10 rows: realized gross vs R5 trades, commission vs the per-fill R5 model, commission vs the R5 trade ledger, unrealized, cash, value, the equity identity, and closed-trade count, gross and net.

Tolerances: quantity exact, price 1e-6, money 1e-4 INR.

There is one documented convention difference, and it is reported as EXPLAINED with its amount, never suppressed. The R5 trade ledger charges `leg_cost` once per aggregated leg. A partially filled order is charged per fill, with the ₹20 brokerage cap applied per fill. They differ only when a partial fill's turnover exceeds about ₹66.7k.

## Tests (`tests_engine_c/test_engine_c_backtrader.py`)

`PYTHONPATH="$PWD" python -m pytest tests_engine_c -v`: **39 tests**. After the Muhurat correction: **38 passed, 1 skipped** in 355 s with `ENGINE_C_SKIP_GOLDEN_MASTER=1`, the skipped one being the golden-master test, which was run separately and passed (see Regressions). The original 19 tests passed in 1,342 s with the golden-master test included (about 5 minutes; skip it with `ENGINE_C_SKIP_GOLDEN_MASTER=1`). There were 14,391 warnings. They come from two places:

* numpy 2.5's deprecation of the "generic" timedelta unit, raised wherever `pd.Timedelta` is built in shared R5 code, Engine B's synthetic feed and Engine C;
* Backtrader 1.9.78.123's use of `datetime.utcnow`.

None of the warnings comes from a failed check.

| # | Requirement | Test |
|---|---|---|
| 1 | Native entry/fill/exit lifecycle | `test_native_backtrader_entry_fill_exit_lifecycle` |
| 2 | Independent broker accounting | `test_independent_backtrader_accounting_reconciles` |
| 3 | Causal ordering, no lookahead, data contract | `test_causal_clock_warmup_and_next_bar_fills`, `test_no_lookahead_future_bars_cannot_change_past_decisions`, `test_data_contract_detects_missing_duplicate_and_out_of_order_bars`; Muhurat calendar: `test_special_session_calendar_is_explicit_and_sourced`, `test_muhurat_session_rows_are_legitimate`, `test_evening_rows_outside_the_calendar_are_fatal` (11 cases), `test_malformed_timestamps_are_fatal` (6 cases), `test_replay_window_is_invariant_to_muhurat_rows_and_audits_the_whole_file` |
| 4 | Multi-symbol isolation | `test_multi_symbol_isolation_and_ordering`: 3 symbols with overlapping positions, per-symbol native vs R5 trades; reversed input order gives an identical report |
| 5 | Risk stop and target | `test_stop_target_anchoring_and_exit_semantics`, `test_gap_through_planned_stop_arms_plant_trip_exit`, `test_short_session_position_is_flattened_at_next_session` |
| 6 | Plant capacity and exposure | `test_working_entries_reserve_concurrency_sector_and_bay_capacity` (real R5 config: 5-position cap, sector cap, GTG bay allocation) |
| 7 | Duplicate-order prevention | `test_one_working_order_per_symbol_and_no_duplicate_submissions` |
| 8 | Rejection, cancellation, margin | `test_native_rejection_margin_and_unfilled_order_disposal`, `test_order_working_at_end_of_data_is_cancelled_not_filled` |
| 9 | Partial fills | `test_native_partial_entry_and_exit_fills_reconcile` (Backtrader `FixedBarPerc`) |
| 10 | Commission and slippage | `test_commission_scheme_matches_r5_cost_model_and_cap_effect_is_explained` (plus the slippage checks in #3 and #5) |
| 11 | Deterministic repeatability | `test_deterministic_repeatability_in_process_and_across_hash_seeds` (second in-process run and a CLI subprocess with `PYTHONHASHSEED=7`) |
| 12 | Engine A/B regression protection | `test_engine_a_and_b_code_golden_masters_and_data_untouched` (git diff vs `500abab`), `test_engine_c_is_independent_of_engine_a_and_b_execution_code`, `test_engine_a_golden_master_still_passes` (runs the 17-scenario golden master) |

Regressions on this branch:

| Check | Result |
|---|---|
| `python scripts/capture_r5_golden_master.py --check` (Engine A, 17 scenarios) | **GOLDEN MASTER: PASS** (before and after Engine C, and again after the Muhurat correction) |
| `pytest tests_engine_b -q` (Engine B) | **9 passed** in 100 s (also after the Muhurat correction) |
| `pytest tests/ tests_external/ --ignore=tests/legacy_quarantine` (shared suite) | **880 passed, 16 failed, 7 skipped**. These counts are identical to Engine B's acceptance run. The 16 failures are the tests that need the frozen dataset, which is absent here. |
| `git diff 500abab -- engine_b tests_engine_b golden_master revision2 revision2_external revision5 runtime data tests tests_external ...` | empty |

## Replays (synthetic smoke feed shared with Engine B; seeded, NSE 0.05 tick grid; P&L meaningless)

| Scenario | Orders submitted / fill events | Trades | R5 gross | R5 costs | R5 net | Backtrader realized gross | Backtrader commission | Backtrader net | Recon checks | Breaks | Final rows |
|---|---|---|---|---|---|---|---|---|---|---|---|
| INFY 500 bars | 2 / 2 | 1 (target) | 380.2560 | 36.8977 | 343.3583 | 380.2560 | 36.8977 | 343.3583 | 886 | 0 | 10/10 PASS |
| INFY + TCS + HDFCBANK, 500 bars | 6 / 6 | 3 (2 target, 1 governor) | 998.7695 | 122.0205 | 876.7491 | 998.7695 | 122.0205 | 876.7491 | 1,776 | 0 | 10/10 PASS |
| 8 symbols, 800 bars (3 sessions) | 18 / 18 | 9 (5 governor, 3 target, 1 plant trip) | 1326.2681 | 200.6232 | 1125.6448 | 1326.2681 | 200.6232 | 1125.6448 | 6,705 | 0 | 10/10 PASS |
| INFY 500, thin volume, `volume_fill_perc=50` | 2 / 3 | 1: entry 24 of 29 (remainder cancelled), exit 21 + 3 over two bars | — | — | — | — | — | — | — | 0 | 10/10 PASS |
| INFY 500, venue max quantity 10 | 1 / 0 | 0: native REJECTED "quantity 29 exceeds venue maximum 10" | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | PASS |
| INFY 500, broker cash ₹20k vs R5 capital ₹1M | 1 / 0 | 0: native MARGIN | 0 | 0 | 0 | 0 | 0 | 0 | — | 0 | PASS |

The 8-symbol, 800-bar run took 359 s here.

## Three-engine comparison (same synthetic data, INFY + TCS + HDFCBANK, 500 bars)

`scripts/compare_r5_engines.py` on independently generated A, B and C reports:

| Pair | Data identity | Matched decisions | Decision | Timing | Execution model | Accounting | Net |
|---|---|---|---|---|---|---|---|
| A vs B | bar counts and symbols equal; hashes not in B's report | 3 of 3 | 0 | 3 | 3 | 0 | A 791.58, B 995.52 |
| A vs C | **verified** (per-symbol SHA-256) | 3 of 3 | 0 | 3 | 3 | 0 | A 791.58, C 876.75 |
| B vs C | bar counts and symbols equal | 3 of 3 | 0 | 0 | 6 | 0 | B 995.52, C 876.75 |

All three engines made the same three decisions on the same bars. Their prices and exit bars differ for documented model reasons:

* **Engine A** fills entries at the next open with the paper band, and fills stops and targets at the level on the touching bar.
* **Engine B** fills entries and exits at the decision bar's close, with no slippage.
* **Engine C** fills entries and exits at the next bar's open with slippage.

B and C fill at the same instant (the decision bar's close equals the next bar's open), so they differ only in price. The single-symbol INFY 500 comparison gives the same picture.

## Known limitations

1. **Real-data runs not executed here.** The frozen dataset is not in this environment.
2. **No intrabar stop or target fills.** Exits fill at the next open, which is conservative. Engine A's stop and target fills at the level are optimistic by comparison.
3. **Same-cycle cancel.** An order submitted in the current cycle cannot be cancelled before it executes, which is a `BackBroker` property.
4. **Rejected status.** Backtrader has no native rejection for plain market orders. The venue max-quantity rule is Engine C's extension through `order.reject()`.
5. **Expired** is unreachable for market orders.
6. **Partial fills** exist only with the `FixedBarPerc` volume model (off by default, like Engine B's default). A partial entry also trips R5's Gate 15 quantity reconciliation, which halts new entries by R5's own rule.
7. **Data gaps.** A missing next bar delays a fill to the symbol's next delivered bar. R5's Gate 14 (30 s) then fails and R5 halts new entries. This is reported, not hidden.
8. **Performance.** The 8-symbol, 800-bar run took 359 s here, about 0.06 s per symbol-bar. A profile shows roughly half the time in the shared supervisory bridge (it rebuilds a pydantic model per symbol per tick) and about a fifth in per-row BB03 certification. Both are shared R5 code and left untouched. Estimate for 48 symbols × 1,200 bars: about an hour or more on one core. Start with INFY.
9. **Engine B identity in comparisons.** Engine B reports carry no per-symbol bar hashes, so the comparison verifies B's data only by symbols, bar counts and source.
10. **Special-session calendar.** Only the 2023-11-12 and 2024-11-01 Muhurat sessions are listed (see the data-contract correction above). Extending it means adding a sourced `SpecialSession` entry.

## Exact offline commands

Install (adds one package to the existing R5 venv, upgrades nothing):

```bash
cd ~/projects/zerodha-phase1 && git fetch origin && git checkout feature/r5-backtrader-engine-c
source ~/.venvs/nautilus_r5_clean/bin/activate
pip install --no-deps backtrader==1.9.78.123
```

Synthetic validation (reproducible anywhere):

```bash
python scripts/capture_r5_golden_master.py --check                       # expect GOLDEN MASTER: PASS
PYTHONPATH="$PWD" python -m pytest tests_engine_b -q                     # expect 9 passed
PYTHONPATH="$PWD" python -m pytest tests_engine_c -q                     # expect 39 passed
PYTHONPATH="$PWD" python scripts/run_engine_c_backtrader.py --synthetic-smoke --bars 500
```

Real frozen data (operator machine; verifies the 48-file SHA-256 manifest and the grid manifest first, audits every raw row, exits 2 on a contract violation and 1 on any reconciliation break):

```bash
# --data-dir defaults to $DATA_DIR, else the manifest's data_dir
PYTHONPATH="$PWD" python scripts/run_engine_c_backtrader.py --symbols INFY --bars 1200 \
    --grid-manifest local_workspace/records/grid-manifest-local.json \
    --report local_workspace/validation/engine_c/infy_real_1200.json
# only if that exits 0:
PYTHONPATH="$PWD" python scripts/run_engine_c_backtrader.py --symbols ALL --bars 1200 \
    --grid-manifest local_workspace/records/grid-manifest-local.json \
    --report local_workspace/validation/engine_c/all48_real_1200.json
```

Three-engine comparison on the same real window:

```bash
PYTHONPATH="$PWD" python scripts/run_engine_a_reference.py --symbols INFY --bars 1200 \
    --report local_workspace/validation/engine_a/infy_real_1200.json
PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --symbols INFY --bars 1200 \
    --report local_workspace/validation/engine_b/infy_real_1200.json
PYTHONPATH="$PWD" python scripts/compare_r5_engines.py \
    --engine-a local_workspace/validation/engine_a/infy_real_1200.json \
    --engine-b local_workspace/validation/engine_b/infy_real_1200.json \
    --engine-c local_workspace/validation/engine_c/infy_real_1200.json \
    --out local_workspace/validation/compare/infy_real_1200.json
```

The Engine C report JSON contains:

* the execution model and Engine C config (with its hash);
* per-symbol data identity (bars, first and last label, SHA-256) and the raw-row data audit;
* the order audit (every Backtrader state), the decisions with their dispositions, and the entry fills (decision, availability and fill labels, slippage, anchoring, breaches);
* the R5 trades;
* Backtrader accounting and closed trades;
* every reconciliation check context, any breaks, and the final rows;
* the equity curve and the orders outstanding at the end.
