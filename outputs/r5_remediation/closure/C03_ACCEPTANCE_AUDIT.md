# C03 integrated lifecycle acceptance

Status: PAPER QUALIFICATION VERIFIED; natural BUY carry acceptance remains distinct.

Source checkout: `/home/srinivas/projects/zerodha-codex-lifecycle-acceptance`.

## Executed evidence

`/home/srinivas/.venvs/zerodha-phase1-r5/bin/python -m pytest tests/test_r5_lifecycle_acceptance.py tests/test_r5_d01_orchestrator_run_integration.py -q`

Result: **6 passed, 32 warnings in 5.24s**. Warnings are the existing NumPy timedelta deprecation in grid_context.py.

`/home/srinivas/.venvs/zerodha-phase1-r5/bin/python scripts/diagnostics/r5_lifecycle_acceptance.py --output outputs/c03_lifecycle_acceptance_final`

The script records per-scenario SQLite WAL state, receipt JSON (source hashes and bar trace), and ledger CSV. It requires fresh output databases and refuses evidence overwrite. No production engine source, frozen parameter, reference output, or holdout file is edited.

| Qualification | Expected observed behavior | Net P&L (fixture only) |
|---|---|---:|
| Constructed BUY, acknowledged conversion | Durable intent visible from independent SQLite reader before broker action; one entry fill, CNC ACK, Engine B ownership; two sessions recorded; real B structural reversal executes next bar | 58.016072535000056 |
| Constructed BUY, acknowledged conversion, protective priority | Pending B discretionary exit loses to opening-gap stop | -111.79961214749997 |
| Constructed BUY, rejected conversion | Ownership remains Engine A/MIS; stop still closes | -111.79961214749997 |
| Constructed BUY, UNKNOWN conversion | Ownership remains Engine A/TRANSFER_REQUESTED; admissions halt; protective stop still closes, without resubmission | -111.79961214749997 |
| Derived real TITAN replay | Actual orchestrator.run natural SELL admission, execution, reconciliation and closure | -67.3743473955011 |

Each constructed scenario verifies broker flatness, no open lifecycle rows, durable CLOSED receipt and completed trade, retained DONE feedback receipt, one merit history event and one assigned bay governor history event, with three subsequent duplicate callback attempts leaving both histories unchanged.

## Scope and limits

The constructed BUY fixture intentionally seeds a real paper fill and registers it through production lifecycle APIs, then uses unchanged runtime management and execution. This tests lifecycle qualification independent of strategy admission. It is explicitly not a natural Trial000 BUY, a strategy-performance sample, or the full 48-symbol Block 1. Policy uses structural_window=2 and trail_mode=NONE solely to produce bounded qualification; these are explicitly prototype fixture settings, not frozen trading changes.

The natural historical fixture uses the recorded Trial007 one-session TITAN inputs and the actual `run()` path. It naturally produces SELL and cannot demonstrate CNC carry. Do not replace absent natural BUY handoff evidence with the constructed case.

Next-session protection is verified against simulated paper contingent orders; it is not exchange-hosted GTT continuity. The runtime is paper-only. No live broker calls are performed. This package does not prove live account authorization or gap-fill guarantees.

Exactly-once assertion here is duplicate suppression within the process and durable CLOSED feedback receipt persistence. Restart replay/compaction is a separate C04 acceptance. Engine B state retention is unchanged here.

## Integration files

- `scripts/diagnostics/r5_lifecycle_acceptance.py`
- `tests/test_r5_lifecycle_acceptance.py`
- this report
- generated `outputs/c03_lifecycle_acceptance_final/` evidence directories (root should preserve or regenerate under nominated final integration output).
