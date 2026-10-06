# Closure review of C02/C04/C06 production components

**Scope.** This is a static read-only review. I ran no tests, because the tools can't run them. I made no edits and no `git` calls. I read these in full:
- `paper_state_journal.py`
- `state_recovery.py`
- `fleet_loading_controller.py`
- `morning_recovery.py`
- `combined_cycle_runtime.py`
- `engine_b_management.py`
- `broker_reconciliation.py`
- `live_execution_boundary.py`

I read the relevant sections of `plant_control.py` (425–558) and `orchestrator.py` (409–455, 900–1280, 1535–1800, 2205–2330). For the tests I only listed names and read `tests/test_fleet_loading_integration.py` lines 54–148. Test names below show that coverage exists. They are not evidence that anything passes.

## Findings against your checklist (no defect found, static only)

- **Same-epoch duplicate feedback after compaction: sound.**
  - `orchestrator.py:933` calls `journal.feedback_applied` before the in-memory receipt check.
  - It consults `applied_feedback(epoch, event_id)`, so an evicted `DONE` receipt still dedups in the same epoch.
  - `state_recovery.py:166-181` re-verifies the state hash, the `DONE` receipt, the fingerprint and the checksum.
  - An in-memory `DONE` with no epoch row fails closed (`orchestrator.py:938-941`).
  - A failed commit rolls back the memory and the DB together (`orchestrator.py:966-974`).
- **Fresh-epoch prefix replay reconstructs instead of skipping: sound.**
  - `bind()` creates a new `uuid` epoch on every run (`paper_state_journal.py:172-174`), so `feedback_applied` returns False for historical events.
  - Each consumer is re-applied once.
  - `commit_feedback` (`state_recovery.py:198-204`) requires the rebuilt payload to equal the stored `fleet_state` row and checksum, or it raises.
  - `checkpoint()` (`:274-278`) does the same byte-for-byte comparison per checkpoint.
- **Atomic checkpoints: the checkpoint itself is atomic.** It is one `BEGIN IMMEDIATE` transaction with hash chaining and a gap check, and `after_commit` runs after the commit. `checkpoint_boot` pins the fleet state and the lifecycle rows in one transaction. The two non-atomic points are covered in D4.
- **Closed-state eviction: sound.**
  - `is_durably_closed` (`paper_state_journal.py:213-245`) requires all of the following:
    - the epoch row;
    - a verified chain head at `cursor-1`;
    - a `CLOSED` lifecycle;
    - a completed trade equal across the store, the ledger and the checkpoint;
    - `DONE` in both the checkpoint and the store;
    - broker quantity 0.
  - `CombinedCycleRuntime.close` saves `CLOSED` before it calls `engine_b.evict` (`combined_cycle_runtime.py:57-60`).
  - The paper broker snapshot's `positions` is a list, matching what `is_durably_closed` iterates (`paper_execution.py:102-105`).
- **Fleet policy identity: sound.**
  - The identity hash includes `fleet_loading_policy.to_dict()` in both the paper journal (`:150`) and morning recovery (`morning_recovery.py:31`).
  - `restore_state` rechecks the policy and its identity (`fleet_loading_controller.py:176-182`).
- **Fleet units: sound.** Everything is in pu. `capacity_pu=reference` (`plant_control.py:523`) makes the output a ceiling of ECS demand × limit. Headroom is formed only by `new_risk_headroom_pu`. There is one sample per timestamp, and the same-timestamp branch (`plant_control.py:483-511`) handles trip and bay-mask changes without a second integration. Open questions on the time unit are in D2.
- **Morning startup stays blocked: sound.**
  - `prepare` sets `_execution_halted=True` on its first line (`morning_recovery.py:120`).
  - Nothing in the repo sets it back to False; the only `= False` is the init at `orchestrator.py:272`.
  - `run()` refuses a prepared engine (`orchestrator.py:1580`), and so does verified resume (`:429`).
  - Fleet state is only parked in `_pending_fleet_loading_recovery_state`, which no code consumes.
  - The next cursor is only enforced when `next_timestamp` is passed. Omitting it still can't admit.
- **Terminal close on the last-processed bar: sound.**
  - `orchestrator.py:2304` uses `bars.iloc[processed_bar_indices[symbol]]`, the last clock event.
  - `build_clock` stops at `len-1` (`:1541`), so the sentinel row is never selected.
  - A missing processed bar or an entry after the final bar raises and halts (`:2301-2307`).
  - The one reachable abort is D3.
- **Live boundary: no admission with missing inputs.** `_gate` blocks on all of these:
  - offline mode;
  - a non-live adapter or unverified account;
  - no cursor, a cursor for the wrong account, a cursor with no `checkpoint_id`, or a stale or future cursor (5 s window);
  - no ownership, a mismatched account or `checkpoint_id`, or a differing caller inventory;
  - `protection_required` set False on a non-zero record;
  - an incomplete snapshot;
  - a reconciliation failure (this includes missing or short protective orders, loosened triggers and unknown pending orders);
  - cursor or account drift during the broker snapshot.

## Concrete defects and risks

**D1 (medium, morning/fleet, latent): the "validated exposure" check proves nothing and is nearly unusable.**
- `plant_control.py:553-555` requires `validated_actual_exposure_pu == state['actual_exposure_pu']`, the exposure saved at the previous close.
- A caller can satisfy it by echoing the saved value back. The test at `tests/test_fleet_loading_integration.py:72-73` does exactly this with 1.05.
- A genuine fresh broker-derived exposure will almost never equal the saved one, so the restore is rejected.
- A saved state with `timestamp=None` and `actual_exposure_pu=None` can never restore, because the type check rejects None.
- It cannot admit anything today, since nothing uses it to clear `_execution_halted`. It becomes a hole once someone wires it in.
- **Minimal fix:**
  - Drop the equality against the saved value.
  - Require an exposure value carrying broker-truth provenance, for example a typed object from a reconciled snapshot plus mark-to-market.
  - Handle the `None` state explicitly.
  - Make the restore itself require a next cursor that matches the first `evaluate` timestamp. Today it only checks `next_ts > last`.

**D2 (low–medium, fleet sampling): the integration step is inconsistent.**
- `plant_control.py:521` uses `dt = 1.0` s for the first sample. Later samples use the actual spacing, for example 60 s.
- After a restore or an overnight gap, `dt` becomes tens of thousands of seconds. With the default `ki=0.01` and an integral limit of 10, the integrator clamps in one step.
- `test_real_grid_chain_overexposure...` (line 67) pins the first-step value (`-.1`), so the inconsistency is encoded in the test.
- **Fix:** use the nominal bar period for the first sample, and clamp or reset the integrator at session or restore boundaries.
- The policy defaults are labelled placeholders, so this is a tuning and design risk rather than a correctness break.

**D3 (low–medium, terminal close): an entry on the final data bar aborts the run.**
- Entry fills at `bars.iloc[bar_idx+1]` (`orchestrator.py:1846`). The last clock tick therefore fills at the sentinel row, the last bar in the data.
- The only guard is the time-of-day cutoff (`:1799`).
- On truncated data, `:2305` then raises "Terminal reconciliation cannot precede authoritative entry fill" and halts. This is fail-closed, not a wrong price.
- **Fix:** before admission, `continue` if `bar_idx + 1 >= len(bars) - 1`. Do not close at the sentinel.
- This is derived statically from the code paths. I did not reproduce it.

**D4 (low, atomicity across stores).**
- The journal checkpoint (`orchestrator.py:2289`) and the morning boot checkpoint (`:2290-2292`) are separate commits, as are `commit_feedback` and `checkpoint`.
- A crash between the journal checkpoint and the boot checkpoint leaves the lifecycle store ahead of the boot checkpoint. `load_boot` then raises (`state_recovery.py:95`), so the morning recovery is blocked until someone reconciles by hand.
- Paper replay is unaffected, because it reconstructs and verifies.
- This is fail-closed. It is not defect-grade, but it should be documented as an operational limit.

**D5 (low–medium, scale).**
- Every tick writes a full-state JSON payload (`paper_state_journal.py:53-70,283`), including all fills and completed trades. That is O(ticks × history).
- `boot_checkpoints` is append-only with delete triggers, so it grows without bound (`state_recovery.py:16-21,77`).
- Closed `PositionLifecycleRecord`s stay in `engine._position_lifecycle` (`orchestrator.py:1231`).
- This bounds correctness nothing, but it conflicts with a "retention" goal. Receipt compaction alone doesn't bound state size.

**D6 (live boundary, design/robustness; low).**
- **No exit path.** `_gate` applies new-risk reconciliation to every order, so a protective or flatten order is blocked whenever reconciliation fails. A reduce-only path is not defined. Only the caller can know this is intended.
- **Cursor and ownership are caller-supplied.**
  - The boundary only checks that the two `checkpoint_id` values equal each other.
  - Nothing in the repo ties them to `PaperStateJournal` or the boot checkpoint. Only `tests/test_r5_live_execution_boundary.py` constructs them.
  - It cannot tell a durable record from a forged one that agrees with itself.
- **Malformed cursor.** A cursor missing `timestamp` raises `KeyError`, and an unparseable one raises `ValueError`. Neither is `AdmissionBlocked`, so a caller that only catches `AdmissionBlocked` may mishandle them. They still don't admit.
- **TOCTOU window.** Cursor freshness (5 s) is last checked at `:80`, before the intent insert and `place_order` (`:104-119`), with no recheck.
- **Masked error.** If `BEGIN IMMEDIATE` fails, the generic `except` (`:115-117`) runs `ROLLBACK` on a connection with no transaction. That raises a different `OperationalError` and masks the original error.
- **Minimal fixes:** a typed durable-cursor provider, a `reduce_only` gate that skips new-risk reconciliation, and a freshness recheck immediately before `place_order`.

**D7 (info).**
- Fallback close-feedback keys of the form `symbol_object:SYM:id()` change on every run. They bypass reconstruction verification and compaction. Real trades always carry `trade_id`, so this is fixture-only.
- `_feedback_contexts` can keep a stale fingerprint after a failed feedback attempt. A retry overwrites it, so it is harmless.

## Paper correctness vs. unverified live composition

- **Paper:** the epoch-scoped dedup, the deterministic reconstruction and checkpoint chain, the closed-state eviction, the sealed identity, and the blocked-morning posture all read correct. D1 to D3 are the only items that could change behavior.
- **Live composition, not established by any code in this scope:**
  - Nothing supplies the durable cursor or ownership to `LiveExecutionBoundary`.
  - `prepare` requires a real broker and GTT read-back, which only fakes exercise in tests.
  - Per-fill protection, GTT lifecycle and exposure measurement have no live source.
  - `PaperStateJournal`'s own docstring says it is not live recovery.
- Do not treat the boundary tests as evidence of live safety.

## Tests that exist for these areas (not run)

- **Compaction:** `tests/test_r5_feedback_compaction.py`, for example `test_actual_close_compacts_and_duplicate_does_not_mutate_consumers`, `test_fresh_epoch_reconstructs_once_and_preserves_ledger_and_state` and `test_real_process_crash_after_compaction_preserves_resume_ledger`.
- **Eviction:** `tests/test_r5_retention_recovery.py`.
- **Fleet:** `tests/test_fleet_loading_controller.py` and `tests/test_fleet_loading_integration.py`.
- **Morning startup:** `tests/test_r5_morning_startup.py`.
- **Live boundary:** `tests/test_r5_live_execution_boundary.py`.
- **Not seen:** I found no test for D3 (an entry on the final data bar) or for D1's provenance gap.

## Suggested commands for the supervisor

```
pytest tests/test_r5_feedback_compaction.py tests/test_r5_retention_recovery.py \
       tests/test_fleet_loading_controller.py tests/test_fleet_loading_integration.py \
       tests/test_r5_morning_startup.py tests/test_r5_live_execution_boundary.py \
       tests/test_combined_cycle_runtime.py tests/test_engine_b_management.py \
       tests/test_r5_close_feedback_recovery.py tests/test_r5_close_persistence_fault.py -q
```

To reproduce D3, run a short `TITAN`-style fixture truncated so that the last clock tick is before `no_entry_cutoff_time` and an entry signal fires on it, with `end_of_run_disposition='CLOSE'`.
