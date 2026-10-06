# R5 Recovery Hardening — Execution Audit

Status: VERIFIED PAPER REPLAY RESUME; LIVE/DIRECT CNC ADMISSIONS REMAIN BLOCKED.

## Identity and preservation

Repository: /home/srinivas/projects/zerodha-r5-governor-refactor
Branch: feature/engine-ab-handoff-lifecycle. HEAD: 411712afaecaeb44a6899a12db94ac547fef0186.
Existing dirty work was preserved. receipt.json hashes the changed source and tests; isolated .diff files compare this activity with its pre-change snapshots. No commit or push occurred. No live broker calls were made. Sealed protocol and frozen Trial007 hashes are unchanged; see protected_inputs.json. No holdout or raw reference output was edited.

## Implemented activities

1. **GTT geometry:** introduced one shared gtt_sell_limit helper used by the Kite adapter, simulated adapter and startup geometry comparison. SELL limit is trigger * 0.95, rounded down to the actual instrument tick, rather than rounding to two decimals without tick validation. For the synthetic CNC position: trigger 90.00, limit 85.50. Existing 0.5% GTTs are not silently modified or duplicated; old identity/fingerprint mismatches require reconciliation.
2. **Delivery-authorisation trip:** _execute_exit catches recognised TPIN/e-DIS/CDSL/POA/DDPI rejection messages in InputException/OrderException and failed receipt payloads. It sets the global admission halt, records a PLANT_TRIP event, and raises DeliveryAuthorisationTrip with PLANT_TRIP_DELIVERY_UNAUTHORIZED. Existing positions remain open; no successful close or net P&L is invented. Ordinary parameter errors retain their original exception type. The Kite adapter surfaces the trip code; startup propagates a recognised GTT authorisation failure as the same fatal trip. No outside notification was sent.
3. **ECS state:** fleet_state and boot_checkpoints receive additive ecs_demand_pu REAL columns with range checks. The hashed boot payload also contains the value; hydration validates and restores plant_control.ecs._previous_demand before returning prepared state. The REAL boot column must match the payload. Paper prefix checkpoints verify ECS demand and native cursor alongside the prior state projection.
4. **Cursor:** startup restores the exact durable native index/timestamp. Optional next_timestamp must strictly advance the saved instant, with exchange-local/UTC conversion. It never guesses time from the latest trade (trades are sparse relative to ticks). No saved tick cursor means no claimed cursor certification.
5. **Verified paper continuation:** resume_verified_paper_replay requires a fresh offline PAPER_APPLY engine, empty paper broker, fresh simulation lifecycle store and an existing interrupted journal. It reconstructs all controllers through the original warmup and sealed historical input prefix, verifies each saved checkpoint and hash chain, then appends new ticks. Saved commands reconstruct electrical trips/lockouts through their normal execution path. Source, package, configuration, universe, data and combined-cycle policy identities are checked. Live adapters, already-hydrated books, absent/empty/completed journals and mismatched inputs cannot use this entry point. Reconstruction simulates historical orders; it sends no broker requests.

Warmup is intentionally not bypassed merely because a WAL position exists. A position payload does not contain every PA, chart, MPC and exit-PID baseline. Full reconstruction supplies those histories in the supported paper path. This does not implement direct live-stock-PID hydration.

## Validation

Focused recovery validation: 43 passed, 96 warnings in 47.26 seconds.
R5 integration/controller suite: **329 passed, 568 warnings in 94.68 seconds**, exit code 0. Exact command and log: r5_command.txt and r5_suite.log.
Repository-wide validation: **13 failed, 1172 passed, 8 skipped, 713 warnings, 4 errors, 146 subtests passed in 499.11s (0:08:19)**, exit code 1. The initial run stopped at four collection errors (missing ecs_runtime_v2, blocks.block_5_risk_manager and ray); the continued run executed the remaining tests. Exact command: repository_continue_command.txt; full log: repository_continue.log.

Three failures are in legacy Kite adapter tests (old receipt shape and blind network write retries). Running those tests against the saved pre-change adapter reproduces all three; adapter_before_validation.log records that check. Ten failures are in unchanged Revision 4 tests: one error-message assertion and nine cross-session/reconciliation cases encountering a tuple where gates_proper.py expects a GateDecision. These are recorded as outstanding repository findings, not silently marked passed. No clean repository-wide certification is claimed. The new R5 changes were verified by the separate 329-test suite.

The focused tests cover typed delivery failures without false close records, ordinary input errors, adapter/GTT error propagation without blind retries, exact ECS restoration, mixed-time-zone cursor advancement and overlap rejection, absent-prefix rejection, and two real subprocess crash/restart cases (before entry and with an open position). The separate boot tests retain negative inventory/protection checks.

## Saved end-to-end evidence

| Fixture | Restored ECS | Admission result | Protection result | Ledger result |
|---|---:|---|---|---|
| Derived real TITAN one-session sealed replay, crash before entry | Reconstructed from exact history | Paper admissions resumed after checkpoint prefix verified | CNC GTT not involved; this trade is SELL / Engine A | Byte-for-byte identical to uninterrupted ledger |
| Synthetic BUY -> production handoff -> CNC shutdown/reboot | 0.8 | Direct boot remains halted | One simulated GTT, trigger 90.00 / limit 85.50 | Submission/hydration fixture, not an entry-edge or live-execution test |

Persistent paper witness: paper_resume_receipt.json. One saved prefix checkpoint verified; 375 subsequent checkpoints appended; admissions_resumed=true; warmup_bypassed=false.
Baseline and resumed JSON ledger SHA256: be894a7a802f7dcdd222d0cc5ce2878eac5193b587d1d08ecdb0999d33cadffb.
Ledger files: paper_resume/baseline_ledger.json, paper_resume/resumed_ledger.json and resumed_ledger.csv.

| Symbol | Side | Entry IST | Exit IST | Quantity | Gross P&L | Friction | Net P&L |
|---|---|---|---|---:|---:|---:|---:|
| TITAN | SELL | 2024-02-13 09:33 | 2024-02-13 09:36 | 5 | -51.0010 | 16.37335 | -67.37435 |

This loss is preserved by recovery. No improvement in profitability is claimed. The fixture is one derived real-data session, not a new complete 48-symbol Block 1 experiment.
CNC witness: cnc_boot/recovered.json. The CNC fixture uses a real paper fill and production handoff but synthetic trade/history inputs. It has no certified native tick cursor (null/-1), so direct admissions remain blocked. Separate tests validate exact persisted cursor restoration.

## Operational corrections and remaining gates

A 5% SELL limit still does not guarantee execution: trigger 95 gives limit 90.25; an opening best bid of 90 remains below that sell limit. A deeply buffered limit can also be outside the permitted circuit range. GTT readback proves an active contingent LIMIT instruction, not liquidation, ongoing partial-fill reconciliation, or broker/exchange availability. The paper GTT adapter is a submission/verification fixture; paper bar-stop execution does not model a full limit-order book. Ongoing live GTT rejection/partial-fill monitoring and live resume execution still require verification.

DDPI is not universally a physical submission requirement or irrevocable permanent authorisation. Zerodha supports online activation for eligible accounts and permits revocation. Eligible DDPI/POA or required CDSL authorisation must actually be active for delivery debit. The code reacts to a reported authorisation failure; it does not activate DDPI or prove future delivery authorisation from GTT acceptance.

The direct morning hydrate path still does not restore every stock PID, ECS cache/reference, electrical lockout and live execution cursor. It retains its halt. The verified path completed here is deterministic offline paper reconstruction, not an instant 14:15 -> 14:20 live restart. Existing journals generated under a different source identity require controlled migration/reconciliation rather than ignoring their identity mismatch. The old separate paper prefix and lifecycle databases are not claimed to form a distributed atomic transaction.

## Source contracts

GTT LIMIT order fields and trigger readback: https://kite.trade/docs/connect/v3/gtt/
GTT limitations, circuit-range rejection and delivery authorisation: https://zerodha.com/tos/gtt
DDPI activation options: https://support.zerodha.com/category/your-zerodha-account/your-profile/ddpi/articles/activate-ddpi
Kite exception categories: https://kite.trade/docs/connect/v3/exceptions/

Shutdown was requested after completing this work. The final receipt will record the shutdown command outcome after test results and artifacts are saved.

Shutdown outcome: blocked_by_operating_system. Details: shutdown.json.

Final shutdown disposition: blocked by GNOME TextEditor unsaved-document inhibitors. No force/ignore-inhibitors shutdown was performed. A sudo fallback also required interactive authentication. Project files, ledger, audit and receipts were synced to disk. Save or close TextEditor before shutting down.
