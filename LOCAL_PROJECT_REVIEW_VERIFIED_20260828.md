# Local Project Review — Source-Verified Summary

Date: 2026-08-28  
Scope: Current documented P01-D production candidate and its read-only entry-gate observatory.  
Method: Small, bounded reviews through local `qwen3-coder:30b`, followed by direct source and offline-test verification.

## Safety boundary

- The documented Engine 5 launcher starts `entry_gate_dry_run.py`, not live order execution.
- The dry-run gate refuses `live_trading_enabled=True`, exposes no order API, and marks physical dispatch `BLOCKED_LIVE_TRADING_DISABLED` regardless of the hypothetical risk result.
- Its reservations and position observations use dedicated isolated files rather than the production engine or production entry-control state.
- The current production candidate's normal BUY boundary is `KiteBrokerAdapter.place_order()`.
- A BUY is rejected unless it uses the `V3.4_ENTRY` tag and an entry authorizer is installed. The authorizer receives the adapter's live-trading flag before the underlying SDK can be called.
- `RunnerEntryAuthorizer.authorize_buy()` blocks locally and persists a durable halt when live trading is disabled. When enabled, it requires valid fresh snapshots, identical initial/final snapshot identity, two P03 risk approvals, and a successful policy reservation.
- MARKET orders are blocked. Emergency SELL is deliberately separate from BUY authorization and has explicit symbol, quantity, LTP, trigger, tick-alignment, protection, tag, and broker-response validation.

## Candidate provenance

- Momentum telemetry is produced by `read_only_shadow_collector.py` from read-only quotes and includes an external cross-sectional variant.
- ORB telemetry is produced by `orb_shadow_collector.py` from read-only quotes and observer state.
- The dry-run gate accepts only valid positive-price rows marked by the expected momentum or ORB decision fields.
- Collector failures produce blocking/failure telemetry. Snapshot failure in the dry-run gate blocks every candidate for that cycle.
- The momentum collector can optionally calculate shadow intents, but still has no broker-write or production-state capability.

## Risk and persistence findings

- Limits are capital-derived: target risk, maximum trade risk, daily entry lock, daily hard halt, trailing-seven-day halt, trial drawdown halt, turnover, entry count, same-symbol lock, cooldown, and simultaneous-exposure controls.
- Policy state is reserved before a possible broker side effect.
- Entry-control saves use a flushed temporary file followed by `os.replace`, providing atomic file replacement. Cross-process synchronization was not established by the reviewed slice and should not be assumed solely from atomic replacement.
- A durable risk halt requires explicit clearance; merely removing a kill-switch condition does not automatically clear it.

## Verification results

- 87 focused offline tests passed across authorization, percentage risk controls, negative dispatch, and the dry-run gate.
- 10 local-runner tests passed, and the runner compiled successfully.
- Tests directly cover: successful authorized fake BUY, denied BUY with zero fake broker calls, missing/invalid/stale/account-mismatched snapshots, final snapshot change, restart persistence, authorizer bypass prevention, emergency-exit availability, percentage thresholds, entry locks, turnover, trailing seven-day behavior, isolated dry-run files, and unconditional dry-run dispatch blocking.
- No live broker connection, credential inspection, trading script execution, or real order was performed during this review.

## Important scope limits

- The repository contains historical, staging, checkpoint, frozen, and later P02 artifacts with their own order paths. The no-bypass conclusion applies to the current entry point identified by the reviewed manifest/runbook and its P01-D candidate, not automatically to every archived file.
- The three orientation documents describe two different scopes: Pipeline V2 canonicalization and the eight-engine operational startup suite. Treating the canonicalization entry point as the trading-system entry point would be incorrect.
- Local-model output is advisory. Multiple material inaccuracies were found and corrected during source verification; it must not be accepted without this verification cycle.

## Package reports

- Package 1: `review_report_20260828_161251.md`
- Package 2 valid retry: `review_report_20260828_171132.md`
- Package 3: `review_report_20260828_172620.md`
- Package 4: `review_report_20260828_172917.md`
- Package 5: `review_report_20260828_173056.md`
- Package 6: `review_report_20260828_173308.md`

The earlier `review_report_20260828_170925.md` is invalid because the model returned degenerate repeated output; it is retained only as diagnostic evidence.
