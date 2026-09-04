# Ten-box remediation tracker

Branch: `codex/ten-box-remediation`  
Started: 2026-09-04 (Asia/Kolkata)

Every phase must pass its focused tests, update this file, commit, push, and
verify the remote commit before the next phase begins.

## Phase 1 — reproducible safety foundation

- [x] Make a clean clone collect the runtime/startup tests without depending
      on the uncommitted `ecs_runtime_v2.py` readiness facade.
- [x] Repair the P01D token API/test contract.
- [x] Remove literal thresholds from Gates 07, 10, 14, and 15.
- [x] Add real event-time order-deduplication state.
- [x] Split pre-submit gates from post-fill reconciliation/slippage checks.
- [x] Use injected historical event time in broker records.

Phase 1A verification: 26 tests and 23 subtests passed. Gate 07 now uses
`max_market_data_age_seconds`; Gate 10 uses its own derating threshold and
multiplier; Gate 14 uses the execution timeout; Gate 15 uses reconciliation
tolerance; Gate 13 derives duplicates from prior accepted order events.

Phase 1B verification: 32 tests and 23 subtests passed. Gates 15/16 now run
only against the broker's actual fill, a failed post-fill check triggers an
immediate corrective flatten, and historical order IDs/timestamps are
deterministic when an event timestamp is supplied.

Phase 1C verification: 43 tests and 23 subtests passed. The startup tests no
longer import an uncommitted facade which claimed parameter-name acknowledgement
was execution, and the P01D expiry test now uses the frozen dataclass API.

## Phase 2 — data and accounting

- [ ] Enforce timestamp presence, parsing, timezone, ordering, uniqueness, and
      conflicting-duplicate rejection.
- [ ] Enforce complete OHLCV geometry and positive prices/nonnegative volume.
- [ ] Centralize the complete Zerodha intraday equity charge schedule.
- [ ] Reconcile trade-ledger net P&L, broker P&L, costs, cash, and equity.

## Phase 3 — NSE session and MIS execution

- [ ] Separate decision timestamp and next-bar fill timestamp.
- [ ] Reject a next-bar fill that crosses into another trading date.
- [ ] Close MIS positions at the configured force-close event.
- [ ] Close at the last available same-session bar if the exact close event is
      missing; never liquidate retrospectively on the next date.
- [ ] Reset daily-loss state from start-of-day equity.
- [ ] Define opening-gap and same-bar stop/target collision rules.

## Phase 4 — real order lifecycle

- [ ] Pass and enforce LIMIT prices.
- [ ] Implement pending/partial/filled/rejected/cancelled lifecycle semantics.
- [ ] Enforce acknowledgement timeout, retry count, and retry delay.
- [ ] Make actual fill quantity and price drive post-fill gates.
- [ ] Persist gate and order telemetry.

## Phase 5 — parameter causality

- [ ] Correct lot-size lookup to use symbol.
- [ ] Implement or explicitly remove rebalance/pyramiding controls.
- [ ] Separate optimizer controls from trading parameters.
- [ ] Remove masked/dead parameters from calibration until behavior exists.
- [ ] Prove every remaining calibratable parameter changes a real downstream
      decision or ledger on a suitable deterministic fixture.

## Phase 6 — Backtrader parity

- [ ] Finish canonical in-house event emission.
- [ ] Finish Backtrader adapter using the identical parameter snapshot.
- [ ] Freeze and hash the deduplicated five-symbol dataset.
- [ ] Run single-symbol parity and repair first divergence iteratively.
- [ ] Run five-symbol parity and reconcile all signals, gates, orders, fills,
      exits, costs, and equity.

## Phase 7 — sealed calibration

- [ ] Create immutable train, validation, and untouched test partitions.
- [ ] Run identical candidate sets through both engines.
- [ ] Compare acceptance decisions and candidate rankings.
- [ ] Freeze a winner only after out-of-sample acceptance.
- [ ] Run the 48-symbol shared-capital portfolio only after five-symbol parity.
