# Phase 0 + Phase 1 audit: workspace quarantine and position lifecycle contract

Generated 2026-10-05T06:21:18Z by running the commands below; blocks are verbatim command output.

## 1. Git verification

### Phase 0 results
```
fix/studies-flat-grading -> f1c7d630653c166cb7dda5397890ed9397516fd3
diagnostic/block1-isolation-harness -> 440ca5fac80d935c16a8c45fd489f2d3f9153503
feature/engine-ab-handoff-lifecycle -> 411712afaecaeb44a6899a12db94ac547fef0186
diagnostic/pid-controller-authority-hierarchy -> 440ca5fac80d935c16a8c45fd489f2d3f9153503
f1c7d63 parent: 440ca5fac80d935c16a8c45fd489f2d3f9153503
$ git log --oneline -3 fix/studies-flat-grading
f1c7d63 fix(studies): grade unchanged price outcomes neutrally
440ca5f fix(governor): inner-loop HOLD outranks FSRN conviction exit during min hold
d1b71eb chore: strip raw bar-level jsonl traces from repository, retain summaries
$ git log --oneline -2 diagnostic/block1-isolation-harness
440ca5f fix(governor): inner-loop HOLD outranks FSRN conviction exit during min hold
d1b71eb chore: strip raw bar-level jsonl traces from repository, retain summaries
$ git log --oneline -3 feature/engine-ab-handoff-lifecycle
411712a feat(lifecycle): position ownership contract and Engine A-only intraday exits
440ca5f fix(governor): inner-loop HOLD outranks FSRN conviction exit during min hold
d1b71eb chore: strip raw bar-level jsonl traces from repository, retain summaries
```
### $ git status
```
On branch feature/engine-ab-handoff-lifecycle
Untracked files:
  (use "git add <file>..." to include in what will be committed)
	docs/experiment_outputs/steam/after/block1/
	docs/experiment_outputs/steam/before/block1/
	outputs/block1_isolation_final/
	outputs/diagnostics/
	scripts/diagnostics/
	tests/test_block1_isolation_harness.py

nothing added to commit but untracked files present (use "git add" to track)
```
### $ git branch -v
```
  claude/r5-branch-security-audit-j089zs        ab70c7c [behind 1] R5 Step 5 executor: anchor ancestry on the protocol's frozen engine commit
+ codex/r5-causal-expectancy                    812fbea Lock in cascade audit numerical and cold-context regressions
  codex/r5-paper-apply-blocker-closure          3e5e4f6 Add R5 native plant durability and restart reconciliation
  codex/r5-step5-sealed-calibration-oos         ece5600 Keep executor validation assets within sealed tooling boundary
  codex/r5-upgrades-integrated                  e5a7119 [gone] R5 Step 5: run protocol V2 with advisory governor authority
  codex/r5-upgrades-publish                     c332f49 R5 Mark V upgrades and Step 5 sealed calibration tooling (squashed for publication)
  diagnostic/block1-isolation-harness           440ca5f fix(governor): inner-loop HOLD outranks FSRN conviction exit during min hold
+ diagnostic/pid-controller-authority           675a42b feat(dcs): lock 3-year calibrated parameters for Supervisor and Black Boxes 01-10
  diagnostic/pid-controller-authority-hierarchy 440ca5f fix(governor): inner-loop HOLD outranks FSRN conviction exit during min hold
* feature/engine-ab-handoff-lifecycle           411712a feat(lifecycle): position ownership contract and Engine A-only intraday exits
  fix/studies-flat-grading                      f1c7d63 fix(studies): grade unchanged price outcomes neutrally
  master                                        a7d09e5 ROOT CAUSE ANALYSIS: Why -Rs.102 P&L and 172x improvement roadmap
  r5-governor-audit                             30c3fb5 [behind 10] R5: governor entry/exit authority, Mark V gate, MiCOM protection, audit tools
  r5-governor-closed-loop                       d1b71eb chore: strip raw bar-level jsonl traces from repository, retain summaries
```
### $ git diff --stat origin/diagnostic/pid-controller-authority-hierarchy
```
 revision2_external/orchestrator.py        |  42 ++++-
 revision5/position_lifecycle.py           | 177 +++++++++++++++++++
 tests/test_position_lifecycle_contract.py | 275 ++++++++++++++++++++++++++++++
 3 files changed, 492 insertions(+), 2 deletions(-)
```
### $ git diff --stat origin/diagnostic/pid-controller-authority-hierarchy HEAD  (committed state; same result expected)
```
 revision2_external/orchestrator.py        |  42 ++++-
 revision5/position_lifecycle.py           | 177 +++++++++++++++++++
 tests/test_position_lifecycle_contract.py | 275 ++++++++++++++++++++++++++++++
 3 files changed, 492 insertions(+), 2 deletions(-)
```
### Boundary checks
```
$ git diff --name-only origin/diagnostic/pid-controller-authority-hierarchy HEAD
revision2_external/orchestrator.py
revision5/position_lifecycle.py
tests/test_position_lifecycle_contract.py
$ git diff --name-only origin/diagnostic/pid-controller-authority-hierarchy HEAD -- revision5/governor_authority.py revision5/governor.py revision2_external/composite_study_signal.py  (PR #7 and studies files)
(empty = untouched)
$ git ls-remote origin refs/heads/diagnostic/pid-controller-authority-hierarchy
440ca5fac80d935c16a8c45fd489f2d3f9153503	refs/heads/diagnostic/pid-controller-authority-hierarchy
$ git ls-remote origin refs/heads/feature/engine-ab-handoff-lifecycle refs/heads/fix/studies-flat-grading
(empty = nothing pushed)
```

## 2. File provenance (sha256 of the committed blobs and of the working-tree files)

```
9bedf66d754ac44ed520ffa5127d3d9c8ec34f0b80cdef33e78ca98eb4c4e666  /home/srinivas/projects/zerodha-r5-governor-refactor/revision5/position_lifecycle.py   (HEAD blob: fd6a9ab8049e4f4e54e0fde7c9a464828dc9f903, status: clean-vs-HEAD)
bad3238d20e45bfdc45334297a04458093a11b404bfe69a6418cf49b1ab6f616  /home/srinivas/projects/zerodha-r5-governor-refactor/revision2_external/orchestrator.py   (HEAD blob: 40bc054532862de5c45dfcabe05df32f51414cb1, status: clean-vs-HEAD)
ecb1d4c0f4c3e486bdba30097d82e465a5b3f46a5efcd7040b8a81933b89dc41  /home/srinivas/projects/zerodha-r5-governor-refactor/tests/test_position_lifecycle_contract.py   (HEAD blob: 9e236ec6920fec96b42efd27a7e015904b49b1f1, status: clean-vs-HEAD)
```

### Orchestrator diff (the only modified existing file), verbatim
```diff
diff --git a/revision2_external/orchestrator.py b/revision2_external/orchestrator.py
index b40efda..40bc054 100644
--- a/revision2_external/orchestrator.py
+++ b/revision2_external/orchestrator.py
@@ -63,6 +63,7 @@ from revision2.transaction_costs import leg_cost, paper_fill_price
 from revision5.ccpp_protection_cubicles import nifty_intertie_measurements
 from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
 from revision5.governor_position_policy import absolute_conviction, update_conviction
+from revision5 import position_lifecycle as lifecycle
 from revision5.governor_authority import (
     PARAMETER_NAMES as GOVERNOR_AUTHORITY_PARAMETERS, BarTelemetry, GovernorAuthorityConfig,
     GovernorInputError, bar_telemetry, causal_percentile_rank, entry_decision as governor_entry_decision,
@@ -271,6 +272,9 @@ class Revision2ExternalEngineOrchestrator:
         self._last_close: Dict[str, float] = {}
         self._mtm_equity_curve: List[Tuple[str, float]] = [("", starting_equity)]
         self._mtm_peak = starting_equity
+        # Ownership contract per trade_id (revision5/position_lifecycle.py).  Kept out of the trade
+        # dicts so completed-trade records are unchanged; a trade with no record is Engine A / MIS.
+        self._position_lifecycle: Dict[str, lifecycle.PositionLifecycleRecord] = {}
         self._mtm_max_drawdown_fraction = 0.0
         self._active_trading_date = None
         self._day_start_equity = starting_equity
@@ -977,6 +981,7 @@ class Revision2ExternalEngineOrchestrator:
                 # Any profitable exit breaks consecutive loss streak
                 self.symbol_consecutive_losses[symbol] = 0
             self._equity_curve.append(self._equity())
+            self._close_position_lifecycle(trade)
             del self.open_trades[symbol]
             self._exit_controller_states.pop(symbol, None)
             _, governor = self._governor_for(symbol)
@@ -1052,6 +1057,33 @@ class Revision2ExternalEngineOrchestrator:
         if self.telemetry_mode == "full":
             self.controller_telemetry.append(event)
 
+    def _register_position_lifecycle(self, symbol: str, trade: Dict[str, Any], timestamp) -> None:
+        """Every replay fill starts as an Engine A / MIS position."""
+        entry = float(trade["entry_price"])
+        self._position_lifecycle[trade["trade_id"]] = lifecycle.open_position(
+            position_id=trade["trade_id"], symbol=symbol, direction=trade["side"],
+            initial_risk_r=abs(entry - float(trade["stop_price"])), anchor_price=entry,
+            initial_stop_price=float(trade["stop_price"]), created_bar_timestamp=pd.Timestamp(timestamp))
+
+    def _close_position_lifecycle(self, trade: Dict[str, Any]) -> None:
+        record = self._position_lifecycle.get(trade.get("trade_id"))
+        if record is not None and record.is_open:
+            self._position_lifecycle[trade["trade_id"]] = lifecycle.close_position(record)
+
+    def _owner_engine(self, trade: Dict[str, Any]) -> str:
+        """Owning engine of an open trade; a trade without a lifecycle record is Engine A (intraday MIS)."""
+        record = self._position_lifecycle.get(trade.get("trade_id"))
+        return record.owner_engine if record is not None else lifecycle.ENGINE_A
+
+    def _transition_position_lifecycle(self, trade: Dict[str, Any], new_state: str, timestamp) -> None:
+        """Apply one contract transition and record it; illegal transitions raise."""
+        before = self._position_lifecycle[trade["trade_id"]]
+        after = lifecycle.transition(before, new_state)
+        self._position_lifecycle[trade["trade_id"]] = after
+        self._record_controller_event("POSITION_LIFECYCLE_TRANSITION", timestamp, trade.get("symbol", before.symbol), {
+            "trade_id": trade.get("trade_id"), "from_state": before.lifecycle_state, "to_state": after.lifecycle_state,
+            "owner_engine": after.owner_engine, "product": after.product})
+
     def _maybe_exit(
         self, symbol: str, timestamp, bar, signal, held_bars: int, session_last_bar: bool,
         chart_studies_confidence: float, chart_studies_audit: Optional[Dict[str, Any]] = None,
@@ -1067,7 +1099,12 @@ class Revision2ExternalEngineOrchestrator:
         studies_direction = (chart_studies_audit or {}).get("direction")
         if studies_direction is not None and studies_direction != (1 if trade["side"] == "BUY" else -1):
             chart_studies_confidence = 0.0
-        if pd.Timestamp(timestamp).strftime("%H:%M") >= self.entry_decision_engine.config.force_close_time:
+        # Intraday square-off duties belong to Engine A only.  An Engine B (B_OPEN / CNC) position
+        # bypasses force_close_time and the MIS session close, but stays under every protective exit
+        # below: drawdown halt, MiCOM trip, hard/governor stop, target, max hold and governor exits.
+        owned_by_engine_a = self._owner_engine(trade) == lifecycle.ENGINE_A
+        if owned_by_engine_a and (pd.Timestamp(timestamp).strftime("%H:%M")
+                                  >= self.entry_decision_engine.config.force_close_time):
             self._execute_exit(symbol, timestamp, trade, float(bar["open"]), "force_close_time")
             return
         halt_dd = min(float(self.config.require("drawdown_halt_threshold")),
@@ -1251,7 +1288,7 @@ class Revision2ExternalEngineOrchestrator:
         if exit_price is not None:
             self._execute_exit(symbol, timestamp, trade, exit_price, reason)
             return
-        if session_last_bar:
+        if session_last_bar and owned_by_engine_a:
             self._execute_exit(symbol, timestamp, trade, float(bar["close"]), "mis_session_close")
             return
         # maximum_hold_bars is now a REAL, independent hard ceiling -- it
@@ -2053,6 +2090,7 @@ class Revision2ExternalEngineOrchestrator:
                         "governor_stop_price": float(plan.stop_price), "governor_mfe_r": 0.0,
                         "governor_entry_conviction": governor_entry.get("entry_absolute_conviction"),
                     })
+                    self._register_position_lifecycle(symbol, self.open_trades[symbol], next_ts)
                     _, governor = self._governor_for(symbol)
                     if governor is not None:
                         governor.begin_position(hard_stop_r=-1.0, position_id=f"trade-{self._trade_sequence}")
```

## 3. Test execution receipt

### $ pytest -v tests/test_position_lifecycle_contract.py  (verbatim stdout+stderr, run now at HEAD)
```
============================= test session starts ==============================
platform linux -- Python 3.12.14, pytest-9.1.1, pluggy-1.6.0 -- /home/srinivas/.venvs/zerodha-phase1-r5/bin/python
cachedir: .pytest_cache
rootdir: /home/srinivas/projects/zerodha-r5-governor-refactor
plugins: timeout-2.4.0, typeguard-4.6.0, socket-0.8.1
collecting ... collected 67 items

tests/test_position_lifecycle_contract.py::test_new_position_is_engine_a_mis_a_open PASSED [  1%]
tests/test_position_lifecycle_contract.py::test_full_handoff_chain_flips_ownership_only_on_acknowledgement PASSED [  2%]
tests/test_position_lifecycle_contract.py::test_rejected_transfer_returns_to_engine_a PASSED [  4%]
tests/test_position_lifecycle_contract.py::test_direct_a_open_to_b_open_is_illegal PASSED [  5%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[A_OPEN-A_OPEN] PASSED [  7%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[A_OPEN-TRANSFER_REQUESTED] PASSED [  8%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[A_OPEN-B_OPEN] PASSED [ 10%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[A_OPEN-CLOSED] PASSED [ 11%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[TRANSFER_REQUESTED-A_OPEN] PASSED [ 13%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[TRANSFER_REQUESTED-TRANSFER_REQUESTED] PASSED [ 14%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[TRANSFER_REQUESTED-B_OPEN] PASSED [ 16%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[TRANSFER_REQUESTED-CLOSED] PASSED [ 17%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[B_OPEN-A_OPEN] PASSED [ 19%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[B_OPEN-TRANSFER_REQUESTED] PASSED [ 20%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[B_OPEN-B_OPEN] PASSED [ 22%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[B_OPEN-CLOSED] PASSED [ 23%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[CLOSED-A_OPEN] PASSED [ 25%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[CLOSED-TRANSFER_REQUESTED] PASSED [ 26%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[CLOSED-B_OPEN] PASSED [ 28%]
tests/test_position_lifecycle_contract.py::test_every_state_pair_matches_the_legal_transition_table[CLOSED-CLOSED] PASSED [ 29%]
tests/test_position_lifecycle_contract.py::test_reject_is_only_valid_from_transfer_requested PASSED [ 31%]
tests/test_position_lifecycle_contract.py::test_closed_is_terminal_and_unknown_states_are_rejected PASSED [ 32%]
tests/test_position_lifecycle_contract.py::test_identity_and_risk_survive_every_transition PASSED [ 34%]
tests/test_position_lifecycle_contract.py::test_record_is_immutable PASSED [ 35%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[direction-LONG] PASSED [ 37%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[owner_engine-ENGINE_C] PASSED [ 38%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[product-NRML] PASSED [ 40%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[lifecycle_state-OPEN] PASSED [ 41%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[position_id-] PASSED [ 43%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[symbol-] PASSED [ 44%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[initial_risk_r-0.0] PASSED [ 46%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[initial_risk_r--1.0] PASSED [ 47%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[initial_risk_r-nan] PASSED [ 49%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[anchor_price-0.0] PASSED [ 50%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[anchor_price-inf] PASSED [ 52%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[current_stop_price-nan] PASSED [ 53%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[current_stop_price-95] PASSED [ 55%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[created_bar_timestamp-2024-02-13] PASSED [ 56%]
tests/test_position_lifecycle_contract.py::test_invalid_field_values_are_rejected[created_bar_timestamp-value14] PASSED [ 58%]
tests/test_position_lifecycle_contract.py::test_state_requires_matching_owner_and_product[A_OPEN-ENGINE_B-MIS] PASSED [ 59%]
tests/test_position_lifecycle_contract.py::test_state_requires_matching_owner_and_product[A_OPEN-ENGINE_A-CNC] PASSED [ 61%]
tests/test_position_lifecycle_contract.py::test_state_requires_matching_owner_and_product[TRANSFER_REQUESTED-ENGINE_B-CNC] PASSED [ 62%]
tests/test_position_lifecycle_contract.py::test_state_requires_matching_owner_and_product[B_OPEN-ENGINE_A-CNC] PASSED [ 64%]
tests/test_position_lifecycle_contract.py::test_state_requires_matching_owner_and_product[B_OPEN-ENGINE_B-MIS] PASSED [ 65%]
tests/test_position_lifecycle_contract.py::test_initial_stop_must_be_on_the_protective_side[BUY-100.0] PASSED [ 67%]
tests/test_position_lifecycle_contract.py::test_initial_stop_must_be_on_the_protective_side[BUY-101.0] PASSED [ 68%]
tests/test_position_lifecycle_contract.py::test_initial_stop_must_be_on_the_protective_side[SELL-100.0] PASSED [ 70%]
tests/test_position_lifecycle_contract.py::test_initial_stop_must_be_on_the_protective_side[SELL-99.0] PASSED [ 71%]
tests/test_position_lifecycle_contract.py::test_stop_only_tightens PASSED [ 73%]
tests/test_position_lifecycle_contract.py::test_engine_a_trade_is_force_closed_at_1525 PASSED [ 74%]
tests/test_position_lifecycle_contract.py::test_engine_a_trade_is_closed_at_the_session_last_bar PASSED [ 76%]
tests/test_position_lifecycle_contract.py::test_trade_without_a_lifecycle_record_is_engine_a PASSED [ 77%]
tests/test_position_lifecycle_contract.py::test_transfer_requested_position_is_still_engine_a_owned_and_squared_off PASSED [ 79%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_survives_the_1525_force_close[BUY] PASSED [ 80%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_survives_the_1525_force_close[SELL] PASSED [ 82%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_survives_the_mis_session_close[BUY] PASSED [ 83%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_survives_the_mis_session_close[SELL] PASSED [ 85%]
tests/test_position_lifecycle_contract.py::test_engine_b_buy_hard_stop_still_exits[2024-02-13 11:00] PASSED [ 86%]
tests/test_position_lifecycle_contract.py::test_engine_b_buy_hard_stop_still_exits[2024-02-13 15:26] PASSED [ 88%]
tests/test_position_lifecycle_contract.py::test_engine_b_sell_hard_stop_still_exits[2024-02-13 11:00] PASSED [ 89%]
tests/test_position_lifecycle_contract.py::test_engine_b_sell_hard_stop_still_exits[2024-02-13 15:26] PASSED [ 91%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_is_closed_by_the_drawdown_halt_kill_switch PASSED [ 92%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_is_closed_by_a_micom_trip PASSED [ 94%]
tests/test_position_lifecycle_contract.py::test_engine_b_trade_still_obeys_the_maximum_hold_ceiling PASSED [ 95%]
tests/test_position_lifecycle_contract.py::test_closing_a_trade_closes_its_lifecycle_record PASSED [ 97%]
tests/test_position_lifecycle_contract.py::test_orchestrator_rejects_an_illegal_lifecycle_transition PASSED [ 98%]
tests/test_position_lifecycle_contract.py::test_lifecycle_records_are_not_added_to_completed_trade_records PASSED [100%]

============================== 67 passed in 0.83s ==============================
exit status: 0
```

### Mutation check (tests discriminate)

With the router gate replaced by `owned_by_engine_a = True` (reverting the decoupling), the same file gave **8 failed, 59 passed**: the Engine B survival tests and the Engine B 15:26 protective-exit tests failed. The edit was reverted; the orchestrator at HEAD is the committed version (blob above).

### Full regression suite (before this report, at the committed code)
```
$ pytest -q tests tests_external --ignore=tests/legacy_quarantine -p no:warnings
.................................................                        [100%]
904 passed, 7 skipped, 146 subtests passed in 464.85s (0:07:44)
done
```

### Replay parity (the contract must not change replay output)

Block 1, protocol v2, Trial 007 parameters, full authority, symbols TITAN,INFY,TCS,RELIANCE,SBIN,ICICIBANK,HDFCBANK,LT. Each run in its own process; baseline = detached worktree of 440ca5f (removed afterwards), candidate = this branch.

```
arm 10: trades old/new 7/7
  trade_ledger_sha256 old aaee797a9ea74c7f2d6eadbef0383edf598cee085028e44b18fab5234d3c11f2
  trade_ledger_sha256 new aaee797a9ea74c7f2d6eadbef0383edf598cee085028e44b18fab5234d3c11f2  equal=True
  controller_trace_sha256 old 3de1751c378ae884d898b58ac1c3df2f2d290f247937eb0eebbf132f41c87410
  controller_trace_sha256 new 3de1751c378ae884d898b58ac1c3df2f2d290f247937eb0eebbf132f41c87410  equal=True
  slice_sha256 equal=True  pids old/new 57566/57568
arm 11: trades old/new 47/47
  trade_ledger_sha256 old d09ef9f56dad3a24447770c1d0f06cd43a23bc3bd2563e48eee0efe8e21b2160
  trade_ledger_sha256 new d09ef9f56dad3a24447770c1d0f06cd43a23bc3bd2563e48eee0efe8e21b2160  equal=True
  controller_trace_sha256 old a8d208a8458d447292ffb421d22a13b44a4624107d599c45a44d1d5a70e9e471
  controller_trace_sha256 new a8d208a8458d447292ffb421d22a13b44a4624107d599c45a44d1d5a70e9e471  equal=True
  slice_sha256 equal=True  pids old/new 57567/57565
```

## 4. Scope notes and limits

* Phase 0: `fix/studies-flat-grading` points at `f1c7d63`; `diagnostic/block1-isolation-harness` was hard-reset to `440ca5f` (its parent); `feature/engine-ab-handoff-lifecycle` was created at `440ca5f`. The reset discarded no tracked work: `f1c7d63` is preserved on the new branch, and the harness scripts/tests were untracked, so they were not touched.
* The harness branch now has **no commits of its own** (it equals PR #7's head); the harness files remain untracked in this working tree and appear under `git status` on whichever branch is checked out. They are not part of this commit.
* Nothing was pushed. The feature branch exists locally only.
* `initial_risk_r` is defined as 1R in price units per share (|anchor - initial stop|), not a dimensionless multiple; `position_id` is the replay's deterministic `trade-N` id (a random UUID would break replay determinism).
* No code path creates `TRANSFER_REQUESTED` or `B_OPEN` in a replay yet, so the decoupled router is exercised only by the unit tests; replay behavior is unchanged (parity above).
* `end_of_run_reconciliation` still closes every open trade at the end of the replay regardless of owner; `max_hold` and the governor exits also still apply to Engine B trades. Both are deliberate Phase 1 choices to be revisited with the hand-off.
* The new records are kept out of the trade dicts, so completed-trade records have no new keys (asserted by a test).
* A lifecycle registration can raise if a fill's stop is not on the protective side of the fill price; none occurred in the 54 replayed trades or the full suite.
