# R5-D01 verification report

Scope: infrastructure correctness only. No strategy, profitability or readiness claim is made or implied.

## 1. Defect and reproduction (BEFORE the edit)

Defect: with a `CombinedCycleRuntime` attached, the real `Orchestrator.run()` aborts on the first bar after a fill: `CombinedCycleReconciliationError: broker quantity/product differs from durable owner`.

Command (identical BEFORE and AFTER; script sha256 in `outputs/R5_D01_evidence/d01_replay_probe.py.sha256`; `R5_D01_DATA_ROOT` is a directory of symlinks: `revision2 -> <repo>/revision2`, `local_workspace -> zerodha-phase1/local_workspace`):
```
R5_D01_DATA_ROOT=$DR ~/.venvs/zerodha-phase1-r5/bin/python outputs/R5_D01_evidence/d01_replay_probe.py {runtime|baseline} <out.json> [SYMBOLS]
```
Input: the repository's own Block 1 loader (`scripts/run_r5_step5_candidate.prepare_block`), existing 1-minute CSVs and NIFTY/VIX grid files, Trial 007 parameters, protocol v2, full governor authority. No newly generated bars.

### BEFORE, runtime attached (verbatim)
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: revision5.combined_cycle_runtime.CombinedCycleRuntime
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=False exception=CombinedCycleReconciliationError: broker quantity/product differs from durable owner reconcile_calls=1 trades=None
exit status: 1
```
### BEFORE, baseline without runtime (verbatim)
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: None
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=True exception=None reconcile_calls=0 trades=1
exit status: 0
```

Data root listing:
```
DATA ROOT (symlinks to the repo's own revision2/ manifest and zerodha-phase1's local_workspace/ grid manifest): /tmp/block1_data_root_lw22ltrp
total 0
drwx------   2 srinivas srinivas    80 Oct  5 11:43 .
drwxrwxrwt 195 root     root     25800 Oct  5 16:04 ..
lrwxrwxrwx   1 srinivas srinivas    54 Oct  5 11:43 local_workspace -> /home/srinivas/projects/zerodha-phase1/local_workspace
lrwxrwxrwx   1 srinivas srinivas    62 Oct  5 11:43 revision2 -> /home/srinivas/projects/zerodha-r5-governor-refactor/revision2
```

## 2. Product contract (determined from existing code; nothing invented)

Raw evidence: `outputs/R5_D01_evidence/before/product_contract_evidence.txt`. Findings:
1. Lifecycle contract (`revision5/position_lifecycle.py`): `A_OPEN`/`TRANSFER_REQUESTED` require owner ENGINE_A / product MIS; `B_OPEN` requires ENGINE_B / CNC.
2. The paper broker's own consumers read an absent position product as MIS: conversion source check (`paper_execution.py` `request_product_conversion`: `position.get("product", "MIS") == from_product`), `ensure_protection` (`position.get("product", "MIS") != product`) and `snapshot()` (`.get("product", "MIS")` for orders and positions).
3. The live adapter's order default is `product="MIS"`.
4. The only consumer that does not apply that convention is `CombinedCycleRuntime.reconcile` (`position.get('product') != record.product`), which is strict by design.
5. The real paper fill (parent `PaperBrokerAdapter._apply_fill_to_position`) never stores a product key.
Conclusion: the broker is the authoritative owner of position product; its fill did not record the product its own consumers assume. The repair is in the broker (explicit product on a filled position); `reconcile` was not touched, adds no fallback, and still fails closed on any genuine absence or mismatch. This is a judgement from the code above; if you read the evidence differently the contract should be treated as UNRESOLVED (R5-D01 DOMAIN CONTRACT UNRESOLVED) and the one-line change reverted.

## 3. Edit budget and the actual change

| FILE | FUNCTION | EXPECTED HUNKS | ACTUAL |
|---|---|---|---|
| revision2_external/paper_execution.py | CostedPaperBrokerAdapter.place_order | 1 (~3 lines) | 1 hunk, 3 lines |
| tests/test_r5_d01_product_reconciliation.py | new file | 1 new file | 1 new file (10 tests) |
| outputs/R5_D01_VERIFICATION_REPORT.md, outputs/R5_D01_evidence/ | new | report + evidence | as listed |

D01 hunk (diff of BEFORE copy vs current file):
```diff
19a20,22
>             # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
>             # already read an absent product as MIS; a converted position keeps its explicit product.
>             self.positions[symbol].setdefault("product", "MIS")
```

Pre-existing versus D01 (rule G): `paper_execution.py` already carried an uncommitted +109-line change before D01 (saved in `outputs/R5_D01_evidence/before/paper_execution.py.diff_vs_HEAD.BEFORE`, BEFORE sha256 `2f301d0f...`). That change was not touched. The only difference between the BEFORE and AFTER diffs-vs-HEAD is the D01 hunk (`outputs/R5_D01_evidence/after/paper_execution.py.diff_vs_HEAD.AFTER`). AFTER sha256 `4630aa71...`. Every other audited file hash is identical to the pre-D01 snapshot (`outputs/R5_D01_evidence/after/audited_hashes_AFTER.txt` vs `outputs/R5_D01_evidence/before/pre_D01_state.txt`).

## 4. AFTER: the same real replay

### 4a. TITAN (same scenario as BEFORE), runtime attached (verbatim)
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: revision5.combined_cycle_runtime.CombinedCycleRuntime
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=True exception=None reconcile_calls=4 trades=1
exit status: 0
```
### 4b. TITAN, baseline (verbatim)
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: None
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=True exception=None reconcile_calls=0 trades=1
exit status: 0
```
### 4c. 8 symbols (TITAN,INFY,TCS,RELIANCE,SBIN,ICICIBANK,HDFCBANK,LT), runtime attached (verbatim)
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: revision5.combined_cycle_runtime.CombinedCycleRuntime
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=True exception=None reconcile_calls=56 trades=7
exit status: 0
```
### 4d. 8 symbols, baseline (verbatim)
```
BROKER CLASS: revision2_external.paper_execution.CostedPaperBrokerAdapter  environment='paper'
RUNTIME CLASS: None
ORCHESTRATOR CLASS: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator (constructed normally; run() will be called)
completed=True exception=None reconcile_calls=0 trades=7
exit status: 0
```
Result summary (from the JSON outputs):
```
BEFORE TITAN runtime     completed=False reconcile_calls=1   trades=None sides=None handoff_requests=0 ledger_sha256=n/a error=CombinedCycleReconciliationError: broker quantity/product differs from durable owner
BEFORE TITAN baseline    completed=True  reconcile_calls=0   trades=1 sides=['SELL'] handoff_requests=None ledger_sha256=051143f6bd1d5988 error=None
AFTER  TITAN runtime     completed=True  reconcile_calls=4   trades=1 sides=['SELL'] handoff_requests=0 ledger_sha256=051143f6bd1d5988 error=None
AFTER  TITAN baseline    completed=True  reconcile_calls=0   trades=1 sides=['SELL'] handoff_requests=None ledger_sha256=051143f6bd1d5988 error=None
AFTER  8sym  runtime     completed=True  reconcile_calls=56  trades=7 sides=['SELL'] handoff_requests=0 ledger_sha256=aaee797a9ea74c7f error=None
AFTER  8sym  baseline    completed=True  reconcile_calls=0   trades=7 sides=['SELL'] handoff_requests=None ledger_sha256=aaee797a9ea74c7f error=None
```
Ledger parity: runtime-attached ledger equals the baseline ledger in both scenarios, and the AFTER baselines equal the pre-D01 values (TITAN `051143f6...` recorded in BEFORE/baseline; 8-symbol `aaee797a...` equals the earlier arm-10 run in /tmp/claude-1000/parity_new_10.json, produced before the uncommitted combined-cycle work).
Not exercised by this replay: all 8 trades were SELL, no handoff request was made (`handoff_requests=0`), no position reached `B_OPEN`, no restart occurred. D01 makes no statement about Engine B, handoff or restart behavior.

## 5. Tests

### 5a. New D01 tests, AFTER (verbatim, exit 0)
```
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 10 items

tests/test_r5_d01_product_reconciliation.py::test_concrete_broker_is_the_paper_implementation PASSED [ 10%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY] PASSED [ 20%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL] PASSED [ 30%]
tests/test_r5_d01_product_reconciliation.py::test_fill_does_not_overwrite_a_converted_product PASSED [ 40%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] PASSED [ 50%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] PASSED [ 60%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_quantity_is_still_rejected PASSED [ 70%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_product_contradicting_the_lifecycle_is_still_rejected PASSED [ 80%]
tests/test_r5_d01_product_reconciliation.py::test_missing_broker_position_is_still_rejected PASSED [ 90%]
tests/test_r5_d01_product_reconciliation.py::test_a_position_with_no_product_is_not_defaulted_by_the_runtime PASSED [100%]

============================== 10 passed in 0.79s ==============================
```
### 5b. The same new tests against the BEFORE code (temp copy of the tree with only `paper_execution.py` replaced by the BEFORE copy; sha256 `2f301d0f...`)
```

    def reconcile(self, engine, snapshot):
        position=engine.broker.get_position(snapshot.record.symbol)
        expected=float(snapshot.trade['quantity'])*(1 if snapshot.record.direction=='BUY' else -1)
        if abs(float(position.get('quantity',0))-expected)>1e-6 or position.get('product')!=snapshot.record.product:
>           raise CombinedCycleReconciliationError('broker quantity/product differs from durable owner')
E           revision5.combined_cycle_runtime.CombinedCycleReconciliationError: broker quantity/product differs from durable owner

revision5/combined_cycle_runtime.py:51: CombinedCycleReconciliationError
=========================== short test summary info ============================
FAILED tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY]
FAILED tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL]
FAILED tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY]
FAILED tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL]
========================= 4 failed, 6 passed in 0.85s ==========================
```
The 4 tests that assert the fixed behavior fail on the BEFORE code; the 6 negative/guard tests pass on both.
Classification of test inputs: REAL PAPER BROKER (`CostedPaperBrokerAdapter`; one negative test calls the real parent `PaperBrokerAdapter.place_order`), REAL RUNTIME/STORE/HANDOFF/ENGINE-B objects, normally constructed Orchestrator with `run()` not called, SYNTHETIC_UNIT_FIXTURE trade dict. No mock, shim or monkey-patch. These tests are not replay evidence; section 4 is.
### 5c. Negative detection (rule J)
| Case | Test | How the state is produced | Result |
|---|---|---|---|
| wrong quantity | test_wrong_quantity_is_still_rejected | real partial SELL at the paper broker | rejected |
| wrong product / contradictory lifecycle-vs-broker | test_wrong_product_contradicting_the_lifecycle_is_still_rejected | real `request_product_conversion` while the lifecycle stays A_OPEN/MIS | rejected |
| missing broker position | test_missing_broker_position_is_still_rejected | real full SELL at the paper broker | rejected |
| product genuinely absent | test_a_position_with_no_product_is_not_defaulted_by_the_runtime | real parent `PaperBrokerAdapter` fill (no product key) | rejected (runtime does not default) |
Not covered: the reverse contradiction (lifecycle B_OPEN/CNC vs broker MIS) cannot be produced through real operations without fabricating durable state, so it has no test.
### 5d. Full regression suite (verbatim tail)
```

tests_external/test_broker_adapter_kite.py:57: Failed
=========================== short test summary info ============================
FAILED tests_external/test_broker_adapter_kite.py::test_valid_market_order_is_translated_and_submitted
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_is_retried_and_eventually_succeeds
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_gives_up_after_max_attempts
3 failed, 946 passed, 7 skipped, 146 subtests passed in 469.18s (0:07:49)
exit status: 1
FAILED tests_external/test_broker_adapter_kite.py::test_valid_market_order_is_translated_and_submitted
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_is_retried_and_eventually_succeeds
FAILED tests_external/test_broker_adapter_kite.py::test_network_exception_gives_up_after_max_attempts
```
Before D01 the same command gave 936 passed / 3 failed / 7 skipped; now 946 passed (= 936 + the 10 new tests) / 3 failed / 7 skipped. The 3 failures are the same pre-existing Kite adapter tests named above; they were not touched (rule M) and are not part of D01.

## 6. Placeholder scan (rule C)
```
### revision2_external/paper_execution.py
(no matches)
### tests/test_r5_d01_product_reconciliation.py
12:No mock, no shim, no monkey-patch.  Nothing here is replay evidence; the replay proof is the separate real-run command in

### pre-existing check: same scan over the BEFORE copy of paper_execution.py
(no matches)
```
The single match is introduced text in the new test file's docstring ("No mock, no shim, no monkey-patch"), a statement that none is used, not a placeholder or stub. The production file has no match before or after.

## 7. Live safety (rule L)

Before every replay the probe printed the concrete classes (verbatim above): `revision2_external.paper_execution.CostedPaperBrokerAdapter`, `environment='paper'`, runtime `revision5.combined_cycle_runtime.CombinedCycleRuntime`; the probe exits with status 3 if the broker is not that class. No Kite adapter was constructed and no real broker write was made.

## 8. Observations outside D01 (not acted on)

* A paper position keeps its `product` after it is flattened (`quantity=0`). `setdefault` does not reset it, so a symbol converted to CNC and later re-opened from flat would keep CNC. This pre-dates D01 (conversion never reset it) and was not addressed.
* Reading the unchanged `reconcile`, the per-bar paths that call `broker.snapshot()` serialise all orders and fills each bar; not measured here.

## 9. Acceptance gates

| Gate | Result |
|---|---|
| G1 defect reproduced BEFORE with the same real replay | PASS |
| G2 concrete broker class printed and is the paper implementation | PASS |
| G3 product contract determined from existing code, not guessed | PASS (judgement; evidence in section 2) |
| G4 edit budget respected (1 production file, 1 hunk, new test file only) | PASS |
| G5 pre-existing uncommitted work preserved (all other audited hashes identical) | PASS |
| G6 real `Orchestrator.run()` with real runtime/store/paper broker completes; `reconcile` executed through the real runtime (4 and 56 calls, 0 errors) | PASS |
| G7 ledger parity: runtime run = baseline run = pre-D01 value | PASS |
| G8 negative detection preserved (wrong quantity, wrong product, missing position, absent product) | PASS |
| G9 new tests fail on the BEFORE code where they should | PASS (4 fail, 6 guards pass) |
| G10 no new failures in the existing suite | PASS (same 3 pre-existing Kite failures) |
| G11 no new placeholder/stub in production code | PASS |
| G12 no real broker write; paper class proven before running | PASS |
| G13 no strategy/profitability claim | PASS |

**R5-D01 VERIFIED_FIXED**  (scope: a real paper fill now carries the product that `CombinedCycleRuntime.reconcile` compares, in the existing Block 1 replay; handoff, Engine B and restart paths remain unverified by D01.)
