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
- A ten-box deep audit is saved in `TEN_BOX_DEEP_AUDIT_20260904.md`. It
  disproves the stronger claim that every gate and parameter is active and
  records the correction order before parity/calibration.

## Next implementation step

1. Add event emission to `Revision2PortfolioOrchestrator` without changing
   its trading decisions.
2. Implement the Backtrader adapter against the same parameter snapshot.
3. Freeze the five-symbol input manifest and verify hashes/deduplication.
4. Run both engines and save both JSONL ledgers plus a machine-readable
   divergence report.
5. Fix one divergence class at a time and rerun both engines.

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
