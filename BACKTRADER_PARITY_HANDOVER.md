# Backtrader parity — durable work handover

Last updated: 2026-09-04 (Asia/Kolkata)

## Authoritative location

- GitHub repository: `hgsrao/zerodha-phase1`
- Working branch: `codex/backtrader-parity`
- Local Linux folder: `/home/shrinivas/ECS_Project_backtrader_parity`
- Branch base: Revision 2 commit `bf6fe58`

This branch is the durable source of truth for the independent Backtrader
comparison. Do not restart this work from the older Phase 2 reports. Those
reports initially declared parity from summary metrics, but the later audit
invalidated that conclusion.

## Objective

Run Backtrader and the in-house Revision 2 engine with the same:

1. frozen, deduplicated market bars;
2. 68 target parameters;
3. 20 hard safety/gate parameters;
4. signal, sizing, and 18-gate decisions;
5. next-bar execution and NSE session contract;
6. stop/target collision policy;
7. Zerodha round-trip costs; and
8. starting capital and shared portfolio limits.

Compare canonical event ledgers and repair the in-house engine at the first
proven divergence. Summary P&L similarity alone is not parity.

## Completed

- Backtrader `1.9.78.123` installed on the Linux laptop.
- `revision2/backend_contract.py` created.
- One deterministic snapshot now carries all 68 target and 20 safety
  parameters to either backend.
- The snapshot includes registry and effective-configuration hashes.
- `BackendEvent` defines comparable signal, gate, order, fill, exit, cost,
  and equity events with separate decision and actual-event timestamps.
- `first_divergence()` reports the first exact ledger mismatch, including a
  missing event on either side.
- Contract tests pass.
- `revision2/in_house_backend.py` and `revision2/backtrader_backend.py`
  (2026-09-04, same branch): real event emission for the SINGLE-SYMBOL
  `Revision2Orchestrator` on both backends -- Backtrader reuses the exact
  same box objects (PA/ID/MPC/SafetyGates/PositionManager/P01D/
  EntryDecisionEngine) and lets Backtrader own order execution, fills, and
  cash/position accounting, per this document's design principle.
  `tests/test_revision2_backend_parity.py` locks in what real comparison
  runs proved:
  - signal, gate_decision, order, and fill events (the entry side) now
    match EXACTLY, byte-for-byte, after fixing seven real wiring bugs the
    comparison itself found: a timezone/UTC conversion bug in Backtrader's
    PandasData feed, a warmup off-by-one, an order-vs-fill price semantic
    mismatch (MPC's plan price vs the broker's post-slippage fill price
    compared against each other), a fill-timing mislabeling (entries are
    stamped at the decision bar instead of the bar they actually fill on),
    an exit event-shape mismatch, a Backtrader slippage-model mismatch, and
    an entry_bar_idx off-by-one in the Backtrader wiring that shifted every
    `minimum_hold_bars` check by one bar and changed which exit reason
    actually fired on real data.
  - Two real, OPEN divergences remain, deliberately not patched (both touch
    core trading/accounting semantics, not instrumentation):
    1. **Exit timing**: Backtrader's plain Market order for a stop/target/
       signal exit fills one bar later than `_maybe_exit()`'s intra-bar
       fill at the trigger price. Reason/side/quantity agree; only the
       fill bar differs. Fixing this for real would mean switching
       Backtrader's exit orders to Stop/Limit types instead of Market.
    2. **Equity definition**: `Revision2Orchestrator`'s per-bar equity is
       realized-P&L-only (no entry costs, no unrealized P&L reflected until
       the run's final aggregate `_transaction_costs()` call), while
       Backtrader's `broker.getvalue()` is a true real-time mark-to-market.
       This is the same class of gap already fixed for
       `Revision2PortfolioOrchestrator` (its `mtm_equity_curve`, from an
       earlier, separate audit pass) but never applied to the single-symbol
       orchestrator.
  - Scope note: this covers the SINGLE-symbol orchestrator, not yet
    `Revision2PortfolioOrchestrator` (this doc's "Next implementation
    step" #1) -- chosen first because it proves the harness, the event
    contract, and the box-reuse approach all actually work before taking on
    the added complexity of the 48-symbol shared portfolio/broker state.
    That step is still open.
- A ten-box deep audit is saved in `TEN_BOX_DEEP_AUDIT_20260904.md`. It
  disproves the stronger claim that every gate and parameter is active and
  records the correction order before parity/calibration.

## Next implementation step

Single-symbol event emission on both backends is done (see "Completed"
above) with two real, open, documented divergences (exit timing, equity
definition). Remaining:

1. Decide and fix the exit-timing gap for real: either give Backtrader's
   exit orders Stop/Limit semantics so they fill intra-bar like
   `_maybe_exit()` does, or change `_maybe_exit()` to defer fills to the
   next bar like a real market order -- these are the two honest options,
   not something to paper over with more instrumentation.
2. Decide and fix the equity-definition gap: extend
   `Revision2Orchestrator` with a real mark-to-market equity curve,
   matching `Revision2PortfolioOrchestrator`'s existing `mtm_equity_curve`
   fix, rather than leaving per-bar equity realized-only.
3. Add event emission to `Revision2PortfolioOrchestrator` (the 48-symbol
   shared-portfolio engine) without changing its trading decisions --
   reuse `revision2/in_house_backend.py`'s `trace_sink`-translation
   approach; `Revision2PortfolioOrchestrator.run()` does not yet accept a
   `trace_sink` parameter the way the single-symbol orchestrator does, so
   that needs adding first.
4. Implement the matching Backtrader adapter for the multi-symbol case
   (one `Cerebro` run with multiple `PandasData` feeds, one strategy
   instance managing all symbols' box state -- mirroring
   `Revision2PortfolioOrchestrator`'s own single-shared-broker design).
5. Freeze the five-symbol input manifest and verify hashes/deduplication.
6. Run both engines and save both JSONL ledgers plus a machine-readable
   divergence report.
7. Fix one divergence class at a time and rerun both engines.
8. Separately: the TEN_BOX_DEEP_AUDIT_20260904.md findings (18-gate
   defects, incomplete cost model, session/accounting gaps) are real and
   independent of this parity work -- worth their own correction pass,
   not blocking parity progress.

## Resume on another computer

For an existing clone:

```bash
git fetch origin --prune
git switch codex/backtrader-parity
git pull --ff-only
git log -1 --oneline
python3 -m pytest -q tests/test_revision2_backend_contract.py
```

For a new clone:

```bash
git clone https://github.com/hgsrao/zerodha-phase1.git
cd zerodha-phase1
git switch codex/backtrader-parity
```

Expected minimum checkpoint: the commit containing this document and the
canonical backend parity contract. Always read this file before continuing.

## Save discipline

At every coherent milestone:

1. run the relevant tests;
2. update this handover;
3. commit only the parity milestone;
4. push `codex/backtrader-parity`; and
5. verify the remote branch points to the new commit.

Never claim that work is available on another computer until the remote commit
has been verified.
