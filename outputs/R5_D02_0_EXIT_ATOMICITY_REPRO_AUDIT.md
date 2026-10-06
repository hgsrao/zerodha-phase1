# R5-D02.0 Exit-side atomicity reproduction and root-cause audit

Date: 2026-10-06. Forensic only. D02 here means exit-side atomicity/consistency, not the stale-CNC defect (F-N02). Allowed artifacts only: `tests/test_r5_d02_exit_atomicity_repro.py` and this report.

## Repository identity and tree provenance
- Repo `/home/srinivas/projects/zerodha-r5-governor-refactor`, branch `feature/engine-ab-handoff-lifecycle`, HEAD `411712afaecaeb44a6899a12db94ac547fef0186`.
- Pre/post `git status --short`, `git diff --name-status`, `git diff --stat`, `git diff --check` identical except one added untracked line: `?? tests/test_r5_d02_exit_atomicity_repro.py`. `git diff --check` clean. Snapshots: `/tmp/r5d02/pre_git.txt`, `/tmp/r5d02/post_git.txt`.
- Production hashes (218 `.py` files under `revision2_external`, `revision5`, `scripts`, `runtime`, plus `canonical_parameter_registry.py`, `calibration_config.py`): before = after. Exit-path files:
  - `revision2_external/orchestrator.py` `7a0e7ad71590c28f09d14733d88eec72aeb0e630d1430a24996470c17f0cf96b`
  - `revision2_external/paper_execution.py` `764488fe2b54fe9ee6c2228600a0f69ec246664043bde70d753d3bcc134ac43f`
  - `revision5/combined_cycle_runtime.py` `b9266e54d211e1cb0a42c2066ad5179e0b46002a5244beb9dc544c98c16c6bac`
  - `revision5/combined_cycle_store.py` `11d904f1aa38d0409e520a715431631aba33ccd4abc34179082a83e42791d4dc`
  - `revision5/handoff_manager.py` `4cf6353bbada29a6292cd42556ae7ead6412e363796bb6edee4aa6979a66beda`
  - `revision5/engine_b_management.py` `7f91886ee2faa46b40d2339834c9e31aa823489dedefbdb309b87dadd548fa10`
  - `revision5/position_lifecycle.py` `9bedf66d754ac44ed520ffa5127d3d9c8ec34f0b80cdef33e78ca98eb4c4e666`
- New test hash `c05a8e85ec6e4284acd8ed376fbc16657628694b2e3e86154a7b95aae06a924b`.

## Conclusion
**The original hypothesis is partly refuted.** In the current source `open_trades` is cleared before the durable close, so the "open_trades retained" contradiction no longer exists (an existing test, `test_r5_close_persistence_fault.py`, already pins that). A narrower D02 remains and reproduces deterministically: after a successful broker close, a failure in the durable close leaves the **durable lifecycle `A_OPEN` while the broker is flat, the completed trade is recorded and the in-memory lifecycle is `CLOSED`**, and the **realized-R close feedback never runs**. No production code retries or recovers from that state.

## Gate 2: current exit call graph (`revision2_external/orchestrator.py::_execute_exit`, line 1018)
| # | Region | Side effect |
|---|---|---|
| 1 | 1019-1020 | `_assert_paper_plant_broker`, `_verify_broker_position_reconciles` (refuse if ledger and broker disagree) |
| 2 | 1028 | `self.broker.place_order(...)` closing order. External side effect: broker position becomes flat. Exceptions (`InputException`, `OrderException`) set `_execution_halted` and re-raise (1033). |
| 3 | ~1040-1120 | build the `completed` dict; no state mutation |
| 4 | 1124 | `self.completed_trades.append(completed)` |
| 5 | ~1125-1137 | loss/cooldown counters, `_equity_curve.append` |
| 6 | 1138 | `del self.open_trades[symbol]` |
| 7 | ~1139-1143 | pop exit-controller state, `governor.confirm_position_closed`, `_record_mtm` |
| 8 | 1144 -> 1234-1245 | `_close_position_lifecycle`: in-memory `lifecycle.close_position(record)` (CLOSED), then `combined_cycle_runtime.close(...)`; any exception sets `_execution_halted` and raises |
| 9 | 1146-1165 | `_register_realized_r_close_feedback` (exactly-once receipts, merit + bay governor), inside a `try` |
| 10 | 1166-1173 | `finally`: `combined_cycle_runtime.close(...)` again, now with feedback receipts |
| 11 | after | research telemetry (`CONTROLLER_OUTCOME`, expectancy, closed-loop `record_outcome`) |

`CombinedCycleRuntime.close` (`revision5/combined_cycle_runtime.py:39-60`) requires a CLOSED record, loads the durable snapshot, writes `completed_trade` and receipts via `store.save` (SQLite `BEGIN IMMEDIATE`, optimistic revision check), then evicts Engine B state and runtime history only after the durable write succeeds.
On the success path the durable close is called twice (step 8, then step 10). Step 8 occurs before step 9 and outside its `try/finally`.

## Gate 3: required invariant
After an externally successful broker close: broker flat, `open_trades` absent, exactly one completed trade, durable lifecycle CLOSED, runtime ownership none/closed, realized-R feedback applied exactly once. A failure after the broker side effect must not leave an unrecoverable mixture without a deterministic idempotent recovery path.

## Gate 4: control (D01.1 derived-real TITAN replay, real `CostedPaperBrokerAdapter`, real runtime/store)
Test `test_control_normal_exit_converges_to_one_coherent_state` (PASS). Trade `trade-1`, SELL, quantity 5, MIS.
| | S0 before exit | S1 broker closed, before durable close | END |
|---|---|---|---|
| broker qty / product | -5 / MIS | 0 / MIS | 0 / MIS |
| open_trades has TITAN | yes | no | no |
| completed trades | 0 | 1 | 1 (`trade-1`) |
| in-memory lifecycle | A_OPEN | CLOSED | CLOSED |
| durable lifecycle | A_OPEN | A_OPEN | CLOSED |
| Engine B tracks position | no | no | no |
| broker fills (entry SELL / exit BUY) | 1 / 0 | 1 / 1 | 1 / 1 |
| feedback receipts / merit history | {} / 0 | {} / 0 | {trade:DONE} / 1 |
| execution halted | no | no | no |

## Gate 5: failure injection (test-local)
`runtime.close` is wrapped at instance level; the first call raises `InjectedDurableCloseFailure(OSError)`, after the real paper broker has already closed the position. Observation spies on `_execute_exit` and `runtime.close` record state and call the real methods unchanged. No source edit, no hand-mutated state.

## Gate 6/7: reproduction and retry (`test_DEFECT_REPRODUCTION_*`, PASS; these document observed behaviour and do not bless it)
| | S0 | S1 | S2 (after failure propagates) | S3a retry `_execute_exit` | S3b retry `_close_position_lifecycle` | S3c direct `runtime.close()` |
|---|---|---|---|---|---|---|
| broker qty | -5 | 0 | **0** | 0 | 0 | 0 |
| open_trades has TITAN | yes | no | **no** | no | no | no |
| completed trades | 0 | 1 | **1** | 1 | 1 | 1 |
| in-memory lifecycle | A_OPEN | CLOSED | **CLOSED** | CLOSED | CLOSED | CLOSED |
| durable lifecycle | A_OPEN | A_OPEN | **A_OPEN** | A_OPEN | A_OPEN | CLOSED |
| durable open ids | [trade-1] | [trade-1] | [trade-1] | [trade-1] | [trade-1] | [] |
| closing (BUY) fills | 0 | 1 | 1 | 1 | 1 | 1 |
| feedback receipts / merit history | {} / 0 | {} / 0 | **{} / 0** | {} / 0 | {} / 0 | {} / 0 |
| execution halted | no | no | yes | yes | yes | yes |

Retry classification:
- Retry of the production `_execute_exit` with the saved trade: refused with `PositionReconciliationError` ("ledger open_trades expects a SELL position of at least 5.0 to close, but the broker holds 0.0") because the broker is already flat. Class B. No duplicate broker close, no duplicate completed trade.
- Retry of `_close_position_lifecycle`: no-op, because the in-memory record is already CLOSED; durable stays `A_OPEN`.
- Direct `runtime.close(...)` (no production code calls it after a failure; capability evidence only): the durable row becomes CLOSED, but the realized-R feedback is still missing (merit history 0, receipts empty). Class E: the durable lifecycle is repaired and another contradiction (lost feedback) remains.
- Not observed: duplicate broker close (C), duplicate completed trade (D).

## Gate 8: restart/recovery
- Reopening the durable file shows `list_open()` = [`trade-1`], lifecycle `A_OPEN`.
- The existing `CombinedCycleRuntime.restore(engine)` raises `CombinedCycleReconciliationError` (broker quantity differs from durable owner) and re-adopts nothing.
- `morning_recovery.prepare` (`revision5/morning_recovery.py:~140-148`) only supports acknowledged long CNC carry (`RuntimeError: Morning recovery supports acknowledged long CNC carry only`), so it cannot consume an A_OPEN/MIS position. Not exercised dynamically.
- `resume_verified_paper_replay` rebuilds from sealed inputs into a new empty paper broker; it is not a recovery of this contradictory state. Not exercised.
- Conclusion: **RECOVERY NOT AVAILABLE FOR THIS FAILURE** (existing functions refuse; none repairs).

## Gate 9: failure matrix
| # | Boundary | Broker | Local | Durable | Recoverable | Dup. order risk | Dup. completion risk |
|---|---|---|---|---|---|---|---|
| 1 | failure before broker exit (tested) | still open | unchanged, open_trades kept, 0 completed | A_OPEN | yes, normal retry | none | none |
| 2 | broker rejects exit (code only: `result["passed"]` false skips all bookkeeping) | open | unchanged | A_OPEN | yes; note `_exit_orders_submitted` is still incremented | none | none |
| 3 | broker succeeds, durable close fails (tested) | flat | completed 1, open_trades absent, memory CLOSED, feedback missing | A_OPEN | no automatic path | retry refused by reconciliation | none observed |
| 4 | durable close succeeds, later bookkeeping fails (tested) | flat | completed 1, feedback DONE, memory CLOSED | CLOSED | coherent; exception propagates | none | none |
| 5 | retry after 3 (tested) | flat | refused; durable unchanged | A_OPEN | no | none (refused) | none |
| 6 | restart after 3 (tested) | flat | n/a | A_OPEN | `restore()` refuses | none | none |

## Gate 10: atomicity reality
The broker close is an external, non-rollbackable effect. No transaction can span it. Any repair needs a durable record of intent before (or immediately after) the broker call, an event identity for idempotency, and reconcile-on-retry/restart logic.

## Gate 11: root cause
In the current source the broker close and the in-memory books (completed trade, `open_trades` deletion, in-memory CLOSED) are finalised before the durable lifecycle close, and the durable close runs outside the feedback block with no durable record of the broker close, so a failure at that point leaves the durable row `A_OPEN`, skips the realized-R feedback, and gives retry and restart no way to finalise it.

## Gate 12: repair options (design only, not implemented)
1. **Durable close-intent before broker call.** Write a CLOSE_INTENT row (trade id, side, quantity, event id) in the existing store before `place_order`; after the broker result, mark it FILLED then finalise; on retry/restart, reconcile broker position against the intent and complete finalisation (durable close, feedback) idempotently. Crash model: write-ahead intent plus reconcile. Idempotency: trade id/event id unique key, existing `applied_feedback`-style receipts. Compatibility risk: store schema addition and replay-journal parity.
2. **Post-fill durable fact then finalise.** Right after the broker fill, persist a FILLED_CLOSE fact (fill price/qty) before touching books; all later steps (books, durable CLOSED, feedback) are an idempotent finaliser driven from that fact on retry/restart. Crash model: single durable fact after the external effect (small window between fill and fact). Compatibility risk: needs reconciliation against broker fills to cover that window.
3. **Reorder only.** Run the durable close first, then local books. Crash model: unchanged window. Does not fix the lost-feedback or recovery problems and only moves the contradiction.
**Recommendation: option 1**, limited to the exit path: it is the only one that closes the window between the external effect and the first durable record, and it fits the existing store and receipt conventions.

## Gate 13: tests and commands
Venv `/home/srinivas/.venvs/zerodha-phase1-r5`, `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`.
- D02 reproduction `tests/test_r5_d02_exit_atomicity_repro.py`: 6 passed (control; defect reproduction; retry paths; restart; matrix case 1; matrix case 4).
- D01 component `tests/test_r5_d01_product_reconciliation.py`: 10 passed. D01.1 `tests/test_r5_d01_orchestrator_run_integration.py`: 2 passed.
- Lifecycle/runtime/store group (`test_combined_cycle_runtime`, `test_r5_close_persistence_fault`, `test_r5_close_feedback_recovery`, `test_r5_retention_recovery`, `test_r5_feedback_compaction`, `test_position_lifecycle_contract`, `test_r5_lifecycle_acceptance`, `test_r5_morning_startup`, `test_engine_b_management`, `test_r5_terminal_sentinel`): 129 passed.
- `test_combined_cycle_handoff`, `test_paper_combined_cycle_adapter`: 14 passed.
- No 48-symbol replay, no broad suite, no live broker/API.

## Limitations
- One failure point (first durable close call) was injected; a failure inside the second (post-feedback) durable close was not injected.
- Only the SELL/MIS TITAN replay was used; the CNC/Engine B close path was not exercised dynamically.
- Real SQLite store, real paper broker; real Kite behavior is not modelled.
- The retry paths are the ones I could identify; there is no production retry path to compare against.
- Case 2 (broker rejection) is code evidence only.
