# R5-D01.1 permanent real-orchestrator regression closure — audit

## 1. Activity identity

Activity R5-D01.1: close the permanent-test gap left by the accepted R5-D01 production repair. Test-only; no production source change authorized or made. Repository `/home/srinivas/projects/zerodha-r5-governor-refactor`.

## 2. Baseline receipt (Gate 0, before any edit)
```
# D01.1 Gate 0/1 snapshot 2026-10-05T11:37:05Z
/home/srinivas/projects/zerodha-r5-governor-refactor
feature/engine-ab-handoff-lifecycle
411712afaecaeb44a6899a12db94ac547fef0186
--- status
 M revision2_external/broker_adapter_kite.py
 M revision2_external/orchestrator.py
 M revision2_external/paper_execution.py
?? docs/experiment_outputs/steam/after/block1/
?? docs/experiment_outputs/steam/before/block1/
?? docs/r5_remediation_project/
?? outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md
?? outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md
?? outputs/R5_D01_1_evidence/
?? outputs/R5_D01_ACCEPTANCE_AUDIT.md
?? outputs/R5_D01_VERIFICATION_REPORT.md
?? outputs/R5_D01_evidence/
?? outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md
?? outputs/block1_isolation_final/
?? outputs/diagnostics/
?? outputs/r5_remediation/
?? revision2_external/broker_reconciliation.py
?? revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_store.py
?? revision5/engine_b_management.py
?? revision5/handoff_manager.py
?? scripts/diagnostics/
?? tests/test_block1_isolation_harness.py
?? tests/test_broker_reconciliation_lifecycle.py
?? tests/test_combined_cycle_handoff.py
?? tests/test_combined_cycle_runtime.py
?? tests/test_engine_b_management.py
?? tests/test_paper_combined_cycle_adapter.py
?? tests/test_r5_d01_product_reconciliation.py
--- diff --stat
 revision2_external/broker_adapter_kite.py | 158 +++++++++++++++++++-----------
 revision2_external/orchestrator.py        |  15 ++-
 revision2_external/paper_execution.py     | 112 +++++++++++++++++++++
 3 files changed, 227 insertions(+), 58 deletions(-)
```
`git diff --check` exit 0. Branch and HEAD equal the expected values.

## 3. Production hashes before (Gate 1)
```
--- production sha256 BEFORE D01.1
c32672308254fc73169f4924216b90b5e587cfbc3a0f6d879207592ab7810a87  revision2_external/orchestrator.py
4630aa718441b13f28694bb4aba082077717517c98a772e808a3508c48e52c7f  revision2_external/paper_execution.py
2a45ddc3d33a59065d130b33514d39f5536c1133b654170cc8ed75ff9b6f6705  revision2_external/broker_adapter_kite.py
8432563f9f0bd16a9bd4a2156dcb6cf27ccd7561d906616b73629d60173d1ec9  revision5/combined_cycle_runtime.py
44a1f15de7db53a1ae0fd1ea989960bf82ec49d09fe38d984ed6f757945d6cc3  revision5/combined_cycle_store.py
91139eeae0cf8294064e13c5e6b54bfb3d82b42c623482701f185b4e23acdce9  revision5/handoff_manager.py
10489260037910cf153312c11ce048b1b8c56c5a28c31c9f7d1e771fc37d9a1b  revision5/engine_b_management.py
9bedf66d754ac44ed520ffa5127d3d9c8ec34f0b80cdef33e78ca98eb4c4e666  revision5/position_lifecycle.py
e29348cad9b352a415226793171853aec37fa2ae817cc5fe76b6d7e4f5b53b55  revision2_external/broker_reconciliation.py
--- D01 test file
dc1897e6092325887bdefc8b5f7495f206676a3e35495c15d103a777a21ac16c  tests/test_r5_d01_product_reconciliation.py
```
Accepted D01 hunk at the start of D01.1 (`revision2_external/paper_execution.py:20-22`), unchanged:
```
L1+16	        result = super().place_order(symbol, side, quantity, order_type, market_price,
L2+16	                                     config, parameter_registry)
L3+16	        if result["passed"]:
L4+16	            # A fill carries an explicit product on the position.  Conversion, protection and snapshot below
L5+16	            # already read an absent product as MIS; a converted position keeps its explicit product.
L6+16	            self.positions[symbol].setdefault("product", "MIS")
L7+16	            cost = leg_cost(result["filled_price"], result["filled_quantity"], side)
L8+16	            self.booked_costs += cost
```

## 4. Test design (Gate 2)

| Item | Value |
|---|---|
| input source | repository Block 1 loader `scripts/run_r5_step5_candidate.prepare_block` over the existing verified 1-minute CSVs and NIFTY/VIX grid files; loader slice sha256 `c63f4f79...` |
| symbol | TITAN |
| trial/config | Trial 007 parameters (`d5638725...`), protocol v2 (`revision5/step5_sealed_calibration_protocol_v2.json`, `6861cdd2...`), governor_authority=full, PAPER_APPLY, active_paper, compact telemetry |
| minimum slice | 60 warmup rows + 375 rows of session 2024-02-13 + 1 successor row = 436 TITAN rows; NIFTY/VIX 15-min rows 2023-08-14 .. 2024-02-13 (3,104 each). The fill is at 2024-02-13 09:33, the exit at 09:36, both inside session 1 |
| orchestrator | normal `Revision2ExternalEngineOrchestrator(...)` constructor as in `execute_block`, plus `combined_cycle_runtime=` |
| paper broker | the orchestrator's own `CostedPaperBrokerAdapter` (asserted before `run()`) |
| runtime | real `CombinedCycleRuntime` + `CombinedCycleStore` (SQLite in tmp_path) + `HandoffManager(HandoffConfig(enabled=True))` + `EngineBController(EngineBPolicy(enabled=True))` |
| expected fill | one SELL TITAN position through `run()` |
| expected first post-fill reconciliation | broker MIS position matching the record, lifecycle A_OPEN/MIS, no exception |

## 5. Input provenance (Gate 3): DERIVED_REAL_REPLAY_FIXTURE

The existing data is outside the repository at machine-specific absolute paths, so a derived fixture was created. Rows are copied unchanged from the loader output; nothing was generated, resampled or edited. `tests/fixtures/r5_d01/PROVENANCE.json` (verbatim):
```json
{
 "classification": "DERIVED_REAL_REPLAY_FIXTURE",
 "origin": "Block 1 (sessions 2024-02-13..2024-02-19) of revision5/step5_sealed_calibration_protocol_v2.json, Stage A; symbol TITAN",
 "loader": "scripts/run_r5_step5_candidate.prepare_block",
 "loader_slice_sha256": "c63f4f79a4f60b8a48a63bc00a303f6e9c6f99987ade08b3d6ce93403736bf50",
 "stock_manifest": {
  "path": "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json",
  "sha256": "2dcb04eb74fc2abec77a838b20dfb5ce6d730c10ba359203a7fc045eccffef66"
 },
 "stock_source_csv": "see manifest entry for TITAN (file sha256 f9b34afbceee6fc7269255fc4d2363a057d1ec5b8921e75b729b440b14533a1e, verified by the loader)",
 "grid_source": "local_workspace/records/grid-manifest-local.json (NIFTY 50 15min sha256 c2e78705f61b5bad66257890ed1ec0cd3703571e721c16e1b2d2128ee4ea5443, INDIA VIX 15min sha256 9992deda85005fdb9ee2d6684f8a84a3cabd3f591655450207aeeaacbe7c1a8d, verified by the loader)",
 "params_source": {
  "path": "/home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_007.json",
  "sha256": "d563872531a950e721e86058493e489511585fc016a5c1209c486912885a6359"
 },
 "files": {
  "titan_1min_block1_session1.csv": {
   "sha256": "870219ad920fbbbd78172e4882b05325c90bad0e8c1e8ea3fcf9e8ed3197da68",
   "rows": 436,
   "first": "2024-02-12 14:30:00+05:30",
   "last": "2024-02-14 09:15:00+05:30"
  },
  "nifty_50_15min_prefix.csv": {
   "sha256": "902ddbb561713b99a5a5fde0ab07e3fffc483f0c522341433b1342b30b97fafc",
   "rows": 3104,
   "first": "2023-08-14 04:00:00+00:00",
   "last": "2024-02-13 10:00:00+00:00"
  },
  "india_vix_15min_prefix.csv": {
   "sha256": "ec8c3eaf36ef2ad5c81e4a2caa07008f50933362c00785be81b741fad36f3640",
   "rows": 3104,
   "first": "2023-08-14 04:00:00+00:00",
   "last": "2024-02-13 10:00:00+00:00"
  }
 },
 "loader_rows": {
  "titan": 1936,
  "nifty": 3203,
  "vix": 50164
 },
 "round_trip_exact_equal_to_loader_rows": true,
 "grid_context_identical_full_vs_selected_decisions_checked": 375,
 "extractor_sha256": "2aef3788364f82f800a79910a2ba2903718acb940433d4f8db91242b1ca1e035"
}```
Extraction procedure: `tests/fixtures/r5_d01/extract_fixture.py` (sha256 `2aef3788...`). Proofs it performs and that succeeded: (1) the CSVs read back are exactly equal (values and dtypes, `check_exact=True`) to the loader rows; (2) for all 375 scored decision times of session 1 the causal NIFTY/VIX grid context (availability, reason, source timestamp, age, aligned frame) computed from the selected rows equals the one computed from the full loader feeds, bit for bit.
Equivalence with the full replay: running the permanent test's orchestrator on the fixture produces the trade record whose ledger hash is `051143f6bd1d5988...`, identical to the full Block 1 TITAN replay recorded in outputs/R5_D01_evidence (runtime and baseline runs).
Holdout/Step-6: none of these rows are holdout or Step-6 data; Block 1 is Stage A.

Files and hashes:
```
2aef3788364f82f800a79910a2ba2903718acb940433d4f8db91242b1ca1e035  tests/fixtures/r5_d01/extract_fixture.py
ec8c3eaf36ef2ad5c81e4a2caa07008f50933362c00785be81b741fad36f3640  tests/fixtures/r5_d01/india_vix_15min_prefix.csv
902ddbb561713b99a5a5fde0ab07e3fffc483f0c522341433b1342b30b97fafc  tests/fixtures/r5_d01/nifty_50_15min_prefix.csv
a7ff640ca02d7f6358158febab3682513b287934fd2b4a3d315181edc4edc4fb  tests/fixtures/r5_d01/PROVENANCE.json
870219ad920fbbbd78172e4882b05325c90bad0e8c1e8ea3fcf9e8ed3197da68  tests/fixtures/r5_d01/titan_1min_block1_session1.csv
d563872531a950e721e86058493e489511585fc016a5c1209c486912885a6359  tests/fixtures/r5_d01/trial_007_params.json
```

## 6. Integration topology
```
build_orchestrator(): Revision2ExternalEngineOrchestrator(normal ctor) + CombinedCycleRuntime(CombinedCycleStore, HandoffManager, EngineBController)
test body: orch.run({TITAN: frames}, warmup=60)
  -> Engine A signal/entry -> CostedPaperBrokerAdapter.place_order (fill)
  -> _register_position_lifecycle -> CombinedCycleRuntime.register_fill (store row, ensure_protection)
  -> next bars: _maybe_exit -> CombinedCycleRuntime.handle_bar -> CombinedCycleRuntime.reconcile  [TEST_OBSERVATION_SPY records, then calls the real method]
  -> exit -> _close_position_lifecycle -> CombinedCycleRuntime.close
```

## 7. New permanent test

File `tests/test_r5_d01_orchestrator_run_integration.py` (sha256 `ff8b901c...`), verbatim:
```python
"""R5-D01.1 permanent integration regression: the real offline replay path that originally exposed R5-D01.

Path exercised (nothing below is constructed by the test):
  Revision2ExternalEngineOrchestrator (normal constructor) -> Orchestrator.run() -> Engine A signal/entry ->
  CostedPaperBrokerAdapter fill -> lifecycle registration (_register_position_lifecycle -> CombinedCycleRuntime.register_fill) ->
  next bars (_maybe_exit -> CombinedCycleRuntime.handle_bar) -> CombinedCycleRuntime.reconcile.

Input classification: DERIVED_REAL_REPLAY_FIXTURE.  tests/fixtures/r5_d01/ holds rows copied unchanged from the output of the
repository's own Block 1 loader (scripts/run_r5_step5_candidate.prepare_block) for TITAN, the NIFTY/VIX 15-minute feeds and the
Trial 007 parameter file; see tests/fixtures/r5_d01/PROVENANCE.json for sources, hashes, row counts and the extraction procedure.
The bars are historical market data, not generated data.

Real-replay coverage of this test is SELL only: the verified TITAN replay produces one SELL trade.  BUY and SELL product behaviour
is covered separately by tests/test_r5_d01_product_reconciliation.py.

TEST_OBSERVATION_SPY: `observe_reconcile` below records the arguments of each call to CombinedCycleRuntime.reconcile and then
calls the real method unchanged; it neither alters a return value or an exception nor touches broker, lifecycle or product state.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.combined_cycle_runtime import CombinedCycleRuntime
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.handoff_manager import HandoffConfig, HandoffManager

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "r5_d01"
PROTOCOL = Path(__file__).resolve().parents[1] / "revision5" / "step5_sealed_calibration_protocol_v2.json"
SYMBOL = "TITAN"


def load_inputs():
    titan = pd.read_csv(FIXTURES / "titan_1min_block1_session1.csv")
    titan["timestamp"] = pd.to_datetime(titan["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    feeds = {}
    for name, file in (("nifty", "nifty_50_15min_prefix.csv"), ("vix", "india_vix_15min_prefix.csv")):
        feed = pd.read_csv(FIXTURES / file)
        feed["timestamp"] = pd.to_datetime(feed["timestamp"], utc=True)
        feeds[name] = feed
    return titan, feeds["nifty"], feeds["vix"]


def test_fixture_files_match_their_recorded_hashes():
    import hashlib
    provenance = json.loads((FIXTURES / "PROVENANCE.json").read_text())
    assert provenance["classification"] == "DERIVED_REAL_REPLAY_FIXTURE"
    for name, record in provenance["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == record["sha256"], name
    assert hashlib.sha256((FIXTURES / "trial_007_params.json").read_bytes()).hexdigest() == provenance["params_source"]["sha256"]


def build_orchestrator(tmp_path):
    """Normal construction of the orchestrator exactly as the Step 5 block executor builds it, plus a real combined-cycle runtime."""
    protocol = json.loads(PROTOCOL.read_text())
    contract = protocol["block_execution_contract"]
    params = json.loads((FIXTURES / "trial_007_params.json").read_text())
    titan, nifty, vix = load_inputs()
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    equity = float(contract["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    store = CombinedCycleStore(tmp_path / "cycle.db")
    runtime = CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)),
                                   EngineBController(EngineBPolicy(enabled=True)))
    orch = Revision2ExternalEngineOrchestrator(
        [SYMBOL], registry, calibration_overrides=params, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(nifty, vix), real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="compact",
        governor_authority="full", combined_cycle_runtime=runtime)

    observed = []
    real_reconcile = runtime.reconcile

    def observe_reconcile(engine, snapshot):                    # TEST_OBSERVATION_SPY
        position = engine.broker.get_position(snapshot.record.symbol)
        observed.append({"trade_id": snapshot.record.position_id, "state": snapshot.record.lifecycle_state,
                         "record_product": snapshot.record.product, "broker_quantity": position.get("quantity"),
                         "broker_product": position.get("product"), "fills_so_far": len(engine.broker.fills)})
        return real_reconcile(engine, snapshot)

    runtime.reconcile = observe_reconcile
    return orch, runtime, store, observed, titan, int(contract["stock_warmup_bars_per_symbol"])


def test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point(tmp_path):
    orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)

    # The concrete broker and the runtime are established before anything runs.
    assert type(orch.broker).__qualname__ == "CostedPaperBrokerAdapter"
    assert orch.broker.environment == "paper"
    assert orch.combined_cycle_runtime is runtime and type(runtime).__qualname__ == "CombinedCycleRuntime"

    report = orch.run({SYMBOL: titan}, warmup=warmup)           # the real replay; any reconciliation error propagates

    # A real Engine A fill happened through run() and the paper broker.
    trades = report["trades"]
    assert len(trades) == 1 and trades[0]["symbol"] == SYMBOL and trades[0]["side"] == "SELL"
    assert len(orch.broker.fills) >= 2 and orch.broker.fills[0]["side"] == "SELL"

    # Lifecycle and durable runtime state were created by production code.
    trade_id, quantity = trades[0]["trade_id"], trades[0]["quantity"]
    assert trade_id in orch._position_lifecycle
    durable = store.load(trade_id).record
    assert (durable.symbol, durable.direction, durable.owner_engine, durable.product) == (SYMBOL, "SELL", "ENGINE_A", "MIS")
    assert durable.lifecycle_state == "CLOSED" and store.list_open() == [] and orch.open_trades == {}

    # Reconciliation ran after the fill, more than once, with broker and durable owner in agreement.
    assert len(observed) >= 2, "the replay must continue past the first post-fill reconciliation"
    assert observed[0]["fills_so_far"] >= 1, "the first reconciliation must come after the paper fill"
    for call in observed:
        assert call["state"] == "A_OPEN" and call["record_product"] == "MIS" and call["broker_product"] == "MIS"
        assert call["broker_quantity"] == -quantity
```
Two tests: a fixture-integrity test (hashes against PROVENANCE.json) and ONE integration test whose body constructs through `build_orchestrator`, asserts the concrete broker/environment/runtime, calls `orch.run(...)`, and asserts the fill, the production-created lifecycle/durable state, and that reconciliation ran after the fill more than once with broker and owner in agreement. The pre-existing D01 test file was not modified.

## 8. Forbidden-shortcut audit (Gate 5)
```
5:  CostedPaperBrokerAdapter fill -> lifecycle registration (_register_position_lifecycle -> CombinedCycleRuntime.register_fill) ->
85:                         "broker_product": position.get("product"), "fills_so_far": len(engine.broker.fills)})
109:    assert trade_id in orch._position_lifecycle
```
Every textual match is explained: line 5 is the module docstring describing the production path; line 85 is the spy's read-only `position.get("product")` on the real broker (no write); line 109 is a post-run assertion that production registered the trade id. Not matched by the grep but relevant: `runtime.reconcile = observe_reconcile` installs the TEST_OBSERVATION_SPY (it records, then returns `real_reconcile(engine, snapshot)` unchanged; no try/except, so an exception propagates unchanged — shown by the pre-D01 run below, where the spy recorded the failing call and the real method's exception reached the test). The test does not construct trade dicts, `open_trades`, lifecycle records, store rows, broker positions or products, does not call `register_fill`, `reconcile` or `place_order` itself, uses no mock/monkeypatch/`__new__`, and does not catch any exception.

## 9. PRE-D01 counterfactual (Gate 7)

Isolated temp tree `/tmp/claude-1000/d01_1_before_tree` = copy of the working tree (excluding .git, outputs, docs, data, work, diagnostic_output) with only `revision2_external/paper_execution.py` replaced by `outputs/R5_D01_evidence/before/paper_execution.py.BEFORE`. SHA256 of the temp file `2f301d0fb261675fc9f0cac6e5af4171d9d8ff22107f2a03735a2a717d1eaa15` equals the BEFORE value recorded in `R5_D01_evidence/before/pre_D01_state.txt`. The test file in the temp tree is byte-identical to the working-tree one (sha256 `ff8b901c...`). The working-tree production file was never edited backwards or forwards.
```
# COUNTERFACTUAL: identical new test on PRE-D01 production code (temp tree; only revision2_external/paper_execution.py differs from the working tree)
cwd: /tmp/claude-1000/d01_1_before_tree
command: cd /tmp/claude-1000/d01_1_before_tree && PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -v -p no:cacheprovider -p no:warnings tests/test_r5_d01_orchestrator_run_integration.py
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
rootdir: /tmp/claude-1000/d01_1_before_tree
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 2 items
tests/test_r5_d01_orchestrator_run_integration.py::test_fixture_files_match_their_recorded_hashes PASSED [ 50%]
tests/test_r5_d01_orchestrator_run_integration.py::test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point FAILED [100%]
=================================== FAILURES ===================================
_ test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point _
tmp_path = PosixPath('/tmp/pytest-of-srinivas/pytest-32/test_orchestrator_run_reconcil0')
    def test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point(tmp_path):
        orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)
    
        # The concrete broker and the runtime are established before anything runs.
        assert type(orch.broker).__qualname__ == "CostedPaperBrokerAdapter"
        assert orch.broker.environment == "paper"
        assert orch.combined_cycle_runtime is runtime and type(runtime).__qualname__ == "CombinedCycleRuntime"
    
>       report = orch.run({SYMBOL: titan}, warmup=warmup)           # the real replay; any reconciliation error propagates
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
tests/test_r5_d01_orchestrator_run_integration.py:100: 
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
revision2_external/orchestrator.py:1626: in run
    self._maybe_exit(
revision2_external/orchestrator.py:1105: in _maybe_exit
    if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
revision5/combined_cycle_runtime.py:115: in handle_bar
    self.reconcile(engine,snapshot)
tests/test_r5_d01_orchestrator_run_integration.py:86: in observe_reconcile
    return real_reconcile(engine, snapshot)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
_ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ _ 
self = <revision5.combined_cycle_runtime.CombinedCycleRuntime object at 0x7dabc8eaebd0>
engine = <revision2_external.orchestrator.Revision2ExternalEngineOrchestrator object at 0x7dabc8ef9580>
snapshot = RuntimeSnapshot(record=PositionLifecycleRecord(position_id='trade-1', symbol='TITAN', direction='SELL', owner_engine='...il_mode': 'R', 'trail_distance_r': 2.0, 'atr_multiple': 3.0, 'trail_activation_r': 1.0}, 'positions': {}}}, revision=1)
    def reconcile(self, engine, snapshot):
        position=engine.broker.get_position(snapshot.record.symbol)
        expected=float(snapshot.trade['quantity'])*(1 if snapshot.record.direction=='BUY' else -1)
        if abs(float(position.get('quantity',0))-expected)>1e-6 or position.get('product')!=snapshot.record.product:
>           raise CombinedCycleReconciliationError('broker quantity/product differs from durable owner')
E           revision5.combined_cycle_runtime.CombinedCycleReconciliationError: broker quantity/product differs from durable owner
revision5/combined_cycle_runtime.py:51: CombinedCycleReconciliationError
=========================== short test summary info ============================
FAILED tests/test_r5_d01_orchestrator_run_integration.py::test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point
========================= 1 failed, 1 passed in 1.01s ==========================
exit status: 1
```
Evidence script on the same temp tree (broker class/environment printed before the run; the spy shows the failing first reconciliation, with the broker position carrying no product):
```
# evidence script against the pre-D01 temp tree
command: PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python outputs/R5_D01_1_evidence/fixture_run_evidence.py /tmp/claude-1000/d01_1_before_tree
Traceback (most recent call last):
  File "/tmp/claude-1000/d01_1_before_tree/revision2_external/orchestrator.py", line 1105, in _maybe_exit
    if getattr(self, "combined_cycle_runtime", None) is not None and self.combined_cycle_runtime.handle_bar(
                                                                     ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/tmp/claude-1000/d01_1_before_tree/revision5/combined_cycle_runtime.py", line 115, in handle_bar
    self.reconcile(engine,snapshot)
  File "/tmp/claude-1000/d01_1_before_tree/tests/test_r5_d01_orchestrator_run_integration.py", line 86, in observe_reconcile
    return real_reconcile(engine, snapshot)
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/tmp/claude-1000/d01_1_before_tree/revision5/combined_cycle_runtime.py", line 51, in reconcile
    raise CombinedCycleReconciliationError('broker quantity/product differs from durable owner')
revision5.combined_cycle_runtime.CombinedCycleReconciliationError: broker quantity/product differs from durable owner
paper_execution module: /tmp/claude-1000/d01_1_before_tree/revision2_external/paper_execution.py sha256 2f301d0fb261675fc9f0cac6e5af4171d9d8ff22107f2a03735a2a717d1eaa15
input classification: DERIVED_REAL_REPLAY_FIXTURE | symbol: TITAN | fixture rows: {'titan_1min_block1_session1.csv': 436, 'nifty_50_15min_prefix.csv': 3104, 'india_vix_15min_prefix.csv': 3104}
broker class (before run): revision2_external.paper_execution.CostedPaperBrokerAdapter | environment: paper
RUN RAISED: CombinedCycleReconciliationError : broker quantity/product differs from durable owner
reconcile calls observed before the exception: [{'trade_id': 'trade-1', 'state': 'A_OPEN', 'record_product': 'MIS', 'broker_quantity': -5, 'broker_product': None, 'fills_so_far': 1}]
exit status: 1
```
Result: **FAIL** (1 failed, 1 passed — the fixture-integrity test is not an integration test). Cause: `CombinedCycleReconciliationError: broker quantity/product differs from durable owner` raised by `CombinedCycleRuntime.reconcile` at the first reconciliation after the fill (`broker_product: None`), i.e. the original D01 failure at the same integration boundary.

## 10. Current-D01 result (Gate 8)
```
command: PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest -v -p no:cacheprovider -p no:warnings tests/test_r5_d01_orchestrator_run_integration.py
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 2 items

tests/test_r5_d01_orchestrator_run_integration.py::test_fixture_files_match_their_recorded_hashes PASSED [ 50%]
tests/test_r5_d01_orchestrator_run_integration.py::test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point PASSED [100%]

============================== 2 passed in 5.24s ===============================
exit status: 0
```

## 11. Fill and post-fill reconciliation evidence
```
command: PYTHONDONTWRITEBYTECODE=1 /home/srinivas/.venvs/zerodha-phase1-r5/bin/python outputs/R5_D01_1_evidence/fixture_run_evidence.py
paper_execution module: /home/srinivas/projects/zerodha-r5-governor-refactor/revision2_external/paper_execution.py sha256 4630aa718441b13f28694bb4aba082077717517c98a772e808a3508c48e52c7f
input classification: DERIVED_REAL_REPLAY_FIXTURE | symbol: TITAN | fixture rows: {'titan_1min_block1_session1.csv': 436, 'nifty_50_15min_prefix.csv': 3104, 'india_vix_15min_prefix.csv': 3104}
broker class (before run): revision2_external.paper_execution.CostedPaperBrokerAdapter | environment: paper
broker class: revision2_external.paper_execution.CostedPaperBrokerAdapter | environment: paper
runtime class: revision5.combined_cycle_runtime.CombinedCycleRuntime | store: CombinedCycleStore | handoff: HandoffManager | engine_b: EngineBController
bars in fixture frame: 436 | scored bars processed: 375
fills through paper broker: 2 | first fill: {'symbol': 'TITAN', 'side': 'SELL', 'quantity': 5}
trades: 1 | side: SELL | qty: 5 | entry: 2024-02-13 09:33:00+05:30 @ 3559.5838 | exit: 2024-02-13 09:36:00+05:30 @ 3569.784 | reason: governor_exit:FSR_BELOW_EXIT:FSRN | bars_held: 3
reconcile calls observed (TEST_OBSERVATION_SPY): 4 | after the fill (fills_so_far>=1): 4
  reconcile 1 {'trade_id': 'trade-1', 'state': 'A_OPEN', 'record_product': 'MIS', 'broker_quantity': -5, 'broker_product': 'MIS', 'fills_so_far': 1}
  reconcile 2 {'trade_id': 'trade-1', 'state': 'A_OPEN', 'record_product': 'MIS', 'broker_quantity': -5, 'broker_product': 'MIS', 'fills_so_far': 1}
  reconcile 3 {'trade_id': 'trade-1', 'state': 'A_OPEN', 'record_product': 'MIS', 'broker_quantity': -5, 'broker_product': 'MIS', 'fills_so_far': 1}
  reconcile 4 {'trade_id': 'trade-1', 'state': 'A_OPEN', 'record_product': 'MIS', 'broker_quantity': -5, 'broker_product': 'MIS', 'fills_so_far': 1}
trade ledger sha256 (json.dumps(trades, sort_keys, default=str)): 051143f6bd1d5988a0bf97a5b53d23b4a165177e0b84834d5cc728a583a334c1
durable record: CLOSED ENGINE_A MIS | open in store: 0 | open_trades: [] | handoff requests: 0
broker position after run: {'quantity': 0, 'avg_price': 3559.5837999999994, 'product': 'MIS'}
exit status: 0
```
Counts: scored bars processed 375 (436 rows - 60 warmup - 1 successor); fills 2 (entry SELL 5 + exit); reconcile calls 4, all after the fill (the first at fills_so_far=1), all with record A_OPEN/MIS and broker MIS quantity -5; final durable record CLOSED/ENGINE_A/MIS; no handoff request. Real-replay D01.1 coverage = SELL only.

## 12. Existing D01 component tests (Gate 10)
```
command: pytest -q tests/test_r5_d01_product_reconciliation.py
..........                                                               [100%]
10 passed in 0.76s
exit status: 0
```

## 13. Focused regression (Gate 11)
```
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 41 items

tests/test_r5_d01_orchestrator_run_integration.py::test_fixture_files_match_their_recorded_hashes PASSED [  2%]
tests/test_r5_d01_orchestrator_run_integration.py::test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point PASSED [  4%]
tests/test_r5_d01_product_reconciliation.py::test_concrete_broker_is_the_paper_implementation PASSED [  7%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[BUY] PASSED [  9%]
tests/test_r5_d01_product_reconciliation.py::test_real_fill_carries_explicit_mis_product[SELL] PASSED [ 12%]
tests/test_r5_d01_product_reconciliation.py::test_fill_does_not_overwrite_a_converted_product PASSED [ 14%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[BUY] PASSED [ 17%]
tests/test_r5_d01_product_reconciliation.py::test_reconcile_accepts_the_genuine_state_a_real_fill_creates[SELL] PASSED [ 19%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_quantity_is_still_rejected PASSED [ 21%]
tests/test_r5_d01_product_reconciliation.py::test_wrong_product_contradicting_the_lifecycle_is_still_rejected PASSED [ 24%]
tests/test_r5_d01_product_reconciliation.py::test_missing_broker_position_is_still_rejected PASSED [ 26%]
tests/test_r5_d01_product_reconciliation.py::test_a_position_with_no_product_is_not_defaulted_by_the_runtime PASSED [ 29%]
tests/test_combined_cycle_runtime.py::test_actual_orchestrator_hook_handoff_day2_gap PASSED [ 31%]
tests/test_combined_cycle_runtime.py::test_new_stop_effective_next_bar_not_same_low PASSED [ 34%]
tests/test_combined_cycle_runtime.py::test_third_session_close_and_product_mismatch PASSED [ 36%]
tests/test_combined_cycle_runtime.py::test_restart_reconciles_before_restoring_and_does_not_repeat_conversion PASSED [ 39%]
tests/test_paper_combined_cycle_adapter.py::test_conversion_is_not_a_fill_and_duplicate_is_idempotent PASSED [ 41%]
tests/test_paper_combined_cycle_adapter.py::test_restart_preserves_costs_conversion_and_contingent_protection PASSED [ 43%]
tests/test_paper_combined_cycle_adapter.py::test_conversion_rejection_does_not_change_position PASSED [ 46%]
tests/test_broker_reconciliation_lifecycle.py::test_network_after_accept_reconciles_partial_without_duplicate PASSED [ 48%]
tests/test_broker_reconciliation_lifecycle.py::test_ambiguous_without_receipt_never_retries PASSED [ 51%]
tests/test_broker_reconciliation_lifecycle.py::test_duplicate_receipts_block PASSED [ 53%]
tests/test_broker_reconciliation_lifecycle.py::test_conversion_requires_product_quantity_confirmation PASSED [ 56%]
tests/test_broker_reconciliation_lifecycle.py::test_restart_expired_day_stop_blocks_risk PASSED [ 58%]
tests/test_broker_reconciliation_lifecycle.py::test_unknown_broker_position_blocks_risk PASSED [ 60%]
tests/test_broker_reconciliation_lifecycle.py::test_settled_holdings_are_reconciled_and_unknown_holdings_block PASSED [ 63%]
tests/test_broker_reconciliation_lifecycle.py::test_stop_trigger_and_pending_qty_must_cover_without_reversal PASSED [ 65%]
tests/test_broker_reconciliation_lifecycle.py::test_network_conversion_ack_confirms_without_retry PASSED [ 68%]
tests/test_broker_reconciliation_lifecycle.py::test_mixed_holdings_and_net_cannot_be_guessed PASSED [ 70%]
tests/test_broker_reconciliation_lifecycle.py::test_float_order_quantity_rejected_without_submission PASSED [ 73%]
tests/test_combined_cycle_handoff.py::test_restart_duplicate_receipt_and_snapshot PASSED [ 75%]
tests/test_combined_cycle_handoff.py::test_invalid_ack_rolls_back_unknown_and_rejection PASSED [ 78%]
tests/test_combined_cycle_handoff.py::test_closed_race_never_resurrects PASSED [ 80%]
tests/test_combined_cycle_handoff.py::test_stop_revision_and_serialization_faults PASSED [ 82%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:09-1-1-True-True] PASSED [ 85%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:15-1-1-True-True] PASSED [ 87%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-0.74-1-True-True] PASSED [ 90%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-1-0.74-True-True] PASSED [ 92%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-1-1-False-True] PASSED [ 95%]
tests/test_combined_cycle_handoff.py::test_qualification_gate[15:10-1-1-True-False] PASSED [ 97%]
tests/test_combined_cycle_handoff.py::test_late_ack_requires_reconciliation PASSED [100%]

============================== 41 passed in 5.51s ==============================
exit status: 0
```
Passed 41, failed 0, skipped 0, exit 0. No failure to classify.

## 14. Stale-CNC acknowledgement

KNOWN SEPARATE DEFECT — NOT TOUCHED. R5-D-STALE-CNC (PRE_EXISTING, demonstrated in R5_D01_ACCEPTANCE_AUDIT.md): MIS -> CNC conversion -> flatten -> new Engine A entry keeps CNC. D01.1 did not change `setdefault`, flatten behavior, `request_product_conversion` or position reuse.

## 15. Anti-placeholder / anti-synthetic audit (Gate 13)
```
### tests/test_r5_d01_orchestrator_run_integration.py
### tests/fixtures/r5_d01/extract_fixture.py
### tests/fixtures/r5_d01/PROVENANCE.json
### outputs/R5_D01_1_evidence/fixture_run_evidence.py
3:Usage: python fixture_run_evidence.py [repo_root]   (repo_root defaults to the working tree; pass a temp tree for the pre-D01 counterfactual)"""
4:import sys, json, hashlib, tempfile, importlib.util, traceback
12:orch, rt, store, obs, titan, warmup = m.build_orchestrator(Path(tempfile.mkdtemp(prefix="d01_1_")))
```
Classification: the new test file and the extraction script have no match. `fixture_run_evidence.py` (an evidence script under outputs/, not part of the permanent suite) matches 'pass' in its usage text ("pass a temp tree", an instruction) and 'temp' in `tempfile`/`mkdtemp` (standard library, a scratch directory) — none is a placeholder or a success mechanism. CSV data files were not scanned (numeric rows). The docstring of the test states the data is not generated; no synthetic data is used.

## 16. Diff classification (Gate 15)
```
$ git diff --check
[exit 0]
$ git status --short
 M revision2_external/broker_adapter_kite.py
 M revision2_external/orchestrator.py
 M revision2_external/paper_execution.py
?? docs/experiment_outputs/steam/after/block1/
?? docs/experiment_outputs/steam/before/block1/
?? docs/r5_remediation_project/
?? outputs/CLAUDE_BRIDGE_SUPERVISOR_RECEIPT.md
?? outputs/CLAUDE_FLEET_LOADING_WORK_ORDER.md
?? outputs/R5_D01_1_evidence/
?? outputs/R5_D01_ACCEPTANCE_AUDIT.md
?? outputs/R5_D01_VERIFICATION_REPORT.md
?? outputs/R5_D01_evidence/
?? outputs/R5_EXISTING_WORK_FORENSIC_AUDIT.md
?? outputs/block1_isolation_final/
?? outputs/diagnostics/
?? outputs/r5_remediation/
?? revision2_external/broker_reconciliation.py
?? revision5/combined_cycle_runtime.py
?? revision5/combined_cycle_store.py
?? revision5/engine_b_management.py
?? revision5/handoff_manager.py
?? scripts/diagnostics/
?? tests/fixtures/
?? tests/test_block1_isolation_harness.py
?? tests/test_broker_reconciliation_lifecycle.py
?? tests/test_combined_cycle_handoff.py
?? tests/test_combined_cycle_runtime.py
?? tests/test_engine_b_management.py
?? tests/test_paper_combined_cycle_adapter.py
?? tests/test_r5_d01_orchestrator_run_integration.py
?? tests/test_r5_d01_product_reconciliation.py
$ git diff --stat
 revision2_external/broker_adapter_kite.py | 158 +++++++++++++++++++-----------
 revision2_external/orchestrator.py        |  15 ++-
 revision2_external/paper_execution.py     | 112 +++++++++++++++++++++
 3 files changed, 227 insertions(+), 58 deletions(-)
$ git diff --name-status
M	revision2_external/broker_adapter_kite.py
M	revision2_external/orchestrator.py
M	revision2_external/paper_execution.py
```
| Class | Content |
|---|---|
| A. PRE-EXISTING DIRTY WORK | modified: `broker_adapter_kite.py`, `orchestrator.py`, `paper_execution.py` (excluding the D01 hunk); untracked: `revision2_external/broker_reconciliation.py`, `revision5/combined_cycle_runtime.py`, `combined_cycle_store.py`, `engine_b_management.py`, `handoff_manager.py`, tests `test_block1_isolation_harness.py`, `test_broker_reconciliation_lifecycle.py`, `test_combined_cycle_handoff.py`, `test_combined_cycle_runtime.py`, `test_engine_b_management.py`, `test_paper_combined_cycle_adapter.py`, `scripts/diagnostics/` |
| B. ACCEPTED D01 PRODUCTION HUNK | `revision2_external/paper_execution.py:20-22` (unchanged) |
| C. D01 EXISTING COMPONENT TEST | `tests/test_r5_d01_product_reconciliation.py` (sha256 `dc1897e6...`, unchanged) and D01 reports/evidence under outputs/ |
| D. D01.1 NEW INTEGRATION TEST | `tests/test_r5_d01_orchestrator_run_integration.py` sha256 `ff8b901c767b18bae0a9512f59d3e5fcbe938574b8f1a68bfba853ca30b08f13` |
| E. D01.1 DERIVED REAL FIXTURE | `tests/fixtures/r5_d01/` (hashes in section 5) |
| F. D01.1 REPORT/EVIDENCE | `outputs/R5_D01_1_INTEGRATION_REGRESSION_AUDIT.md`, `outputs/R5_D01_1_evidence/` |
| G. UNRELATED | `outputs/CLAUDE_*`, `outputs/diagnostics/`, `outputs/r5_remediation/`, `docs/`, `outputs/block1_isolation_final/`, forensic/acceptance reports |

## 17. Production hashes after (Gate 14)
```
# production sha256 AFTER D01.1 2026-10-05T11:42:14Z
c32672308254fc73169f4924216b90b5e587cfbc3a0f6d879207592ab7810a87  revision2_external/orchestrator.py
4630aa718441b13f28694bb4aba082077717517c98a772e808a3508c48e52c7f  revision2_external/paper_execution.py
2a45ddc3d33a59065d130b33514d39f5536c1133b654170cc8ed75ff9b6f6705  revision2_external/broker_adapter_kite.py
8432563f9f0bd16a9bd4a2156dcb6cf27ccd7561d906616b73629d60173d1ec9  revision5/combined_cycle_runtime.py
44a1f15de7db53a1ae0fd1ea989960bf82ec49d09fe38d984ed6f757945d6cc3  revision5/combined_cycle_store.py
91139eeae0cf8294064e13c5e6b54bfb3d82b42c623482701f185b4e23acdce9  revision5/handoff_manager.py
10489260037910cf153312c11ce048b1b8c56c5a28c31c9f7d1e771fc37d9a1b  revision5/engine_b_management.py
9bedf66d754ac44ed520ffa5127d3d9c8ec34f0b80cdef33e78ca98eb4c4e666  revision5/position_lifecycle.py
e29348cad9b352a415226793171853aec37fa2ae817cc5fe76b6d7e4f5b53b55  revision2_external/broker_reconciliation.py
# D01 component test
dc1897e6092325887bdefc8b5f7495f206676a3e35495c15d103a777a21ac16c  tests/test_r5_d01_product_reconciliation.py
```
```
ALL PRODUCTION FILE HASHES IDENTICAL (9 files)
```

## 18. Completion matrix (Gate 17)

| Criterion | Result |
|---|---|
| production D01 change remains unchanged | YES (paper_execution.py sha256 4630aa71... before and after) |
| no production file changed during D01.1 | YES (9 hashes identical) |
| permanent integration test exists | YES |
| normal Revision2ExternalEngineOrchestrator constructed | YES |
| Orchestrator.run() actually called | YES (test body) |
| real CostedPaperBrokerAdapter used | YES (asserted) |
| environment == paper | YES (asserted) |
| real CombinedCycleRuntime used | YES |
| real store/handoff/B controller used | YES |
| input is existing real replay OR traceable DERIVED_REAL_REPLAY_FIXTURE | YES (DERIVED_REAL_REPLAY_FIXTURE, section 5) |
| fill occurs naturally through run() | YES (SELL 5 TITAN) |
| lifecycle/runtime registration occurs naturally | YES (store row created by production code) |
| at least one reconciliation occurs after fill | YES (4) |
| test progresses beyond original failure point | YES (calls 2-4 and a completed run) |
| PRE-D01 code fails the IDENTICAL test for D01 reason | YES (FAIL, CombinedCycleReconciliationError, broker_product None) |
| current D01 code passes the IDENTICAL test | YES |
| existing 10 D01 component tests pass | YES (10) |
| focused combined-cycle tests show no D01.1 regression | YES (41 passed) |
| fail-closed reconciliation has not been weakened | YES (combined_cycle_runtime.py hash identical) |
| stale-CNC was not modified | YES |
| no live path executed | YES (paper class printed before every run; no Kite adapter constructed) |
| production hashes unchanged | YES |

**R5-D01 STATUS: CLOSED_VERIFIED** (every mandatory criterion above is satisfied).

## 19. Claims permitted

* R5-D01 has permanent automated regression coverage through the real offline `Orchestrator.run()` / paper broker / `CombinedCycleRuntime` path.
* The identical integration test fails on pre-D01 production code (with the D01 exception) and passes on the accepted D01 production code.
* Real-replay D01.1 coverage is SELL.

## 20. Claims NOT permitted

Not claimed: R5 or the combined cycle is production/live ready or complete; handoff, Engine B or restart/recovery is verified; the strategy is profitable; BUY has real-replay integration coverage; all R5 defects are fixed; D02 is fixed; stale-CNC is fixed. The fixture covers one session of one symbol; it is not the full Block 1 replay.
