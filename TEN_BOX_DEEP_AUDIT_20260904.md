# Revision 2 ten-box deep audit

Date: 2026-09-04  
Audited branch: `codex/backtrader-parity`  
Basis: committed Revision 2 runtime, static import/parameter tracing, behavioral tests, and end-to-end perturbation tests.

## Executive verdict

The ten-box path is real executable Python, not pseudocode, but it is not yet
correct to claim that every external model is integrated or every parameter is
causally active. The production path does not use an external predictive model.
PA, ID, MPC, the 18 gates, optimizers, and broker simulator are project-local
implementations. NumPy and pandas provide numerical/dataframe operations.
Backtrader is presently an independent parity backend under construction, not
part of the ten-box decision path. Ridge/scikit-learn and XGBoost exist elsewhere
in the repository but are not imported by Revision 2. LEAN and vectorbt are only
recommendations/research references and are not integrated.

## Actual runtime chain

1. StartupCapabilityLock (`StartupGate`)
2. DataIngestion
3. L2DataCertifier
4. Predictive Analytics (PA)
5. Intelligent Discrimination (ID)
6. Model Predictive Control (MPC)
7. SafetyGates target layer plus the separate 18-gate `EntryDecisionEngine`
8. PositionManager
9. P01D order construction
10. UnifiedExecution session check

`PaperBrokerAdapter` follows the ten boxes and owns simulated fills/positions.

## Box-by-box result

| Box | What actually runs | Verdict |
|---|---|---|
| StartupCapabilityLock | Validates mode, adapter type, parameter types/ranges, and paper/live separation | Partially sound; the pushed branch cannot collect its full tests because `ecs_runtime_v2.py` is not committed |
| DataIngestion | Symbol allow/deny lists | Wired, but it does not itself load or certify frozen files |
| L2DataCertifier | Required OHLCV columns, NaNs, and `high >= low` | Incomplete: no duplicate timestamp, monotonicity, timezone, positivity, OHLC geometry, gap, or frozen-hash enforcement |
| PA | Custom momentum, VWAP deviation, ATR volatility, volume confirmation, smoothing, persistence, and regime multipliers | Executable and parameterized; it is not Ridge/XGBoost or another external model |
| ID | Custom confidence, red-band, estimated-slippage and risk/reward filters | Executable; risk/reward and slippage are heuristics, not an external model or observed execution estimate |
| MPC | ATR stop/target plan plus custom bounded PID adjustments | Executable; this is not a standard external MPC solver. It changes modeled entry price before the broker adds slippage again, requiring contract review |
| SafetyGates | Six target-surface checks plus 18 entry gates | Materially incomplete; several gates are placeholders or receive constant inputs (details below) |
| PositionManager | Risk budget, buffer, caps, integer/lot sizing | Partly wired; lot map is indexed by BUY/SELL instead of symbol, rebalance cadence is read but unused, and several settings are masked by tighter caps |
| P01D | Builds order type, limit price, timeout and retry metadata | Partly wired; the broker call ignores limit price, retry delay/retries/timeout have no lifecycle implementation |
| UnifiedExecution | String time-window comparison and diagnostic exploration score | Partly wired; no exchange calendar or forced MIS close. Optimizer intensities do not control the authoritative supervisor run configuration |

## External engines and models

| Component | Present in repository/environment | Used by ten-box runtime |
|---|---:|---:|
| pandas / NumPy | Yes | Yes, PA/data calculations |
| Backtrader 1.9.78.123 | Yes on this laptop | No; parity backend only |
| PaperBrokerAdapter | Yes | Yes, but it is in-house code |
| 18-gate EntryDecisionEngine | Yes | Yes, but it is in-house code |
| Random Search / TPE / CMA-ES | Yes | Used by calibration supervisor, outside the ten boxes; implementations are in-house |
| Ridge / scikit-learn | Legacy/research files | No |
| XGBoost | Legacy/research files | No |
| SciPy differential evolution/statistics | Legacy/research files | No |
| QuantConnect LEAN | Referenced only | No |
| vectorbt | Referenced only | No |
| Zerodha/Kite live adapter | Stub class exists | No functional live integration in this path |

## Parameter activation findings

- Registry surface: 68 target parameters plus 20 hard safety parameters.
- Static tracing finds references to all 68 target names.
- A name being read or included in a hash is not proof that it can change a
  trade. Existing end-to-end tests explicitly document masked/inert settings.
- `max_positions_per_symbol` is structurally dead because both orchestrators
  prevent a second position before the sizing box sees it.
- `capital_per_trade_fraction` and `capital_allocation_mode` can be masked by
  the fixed symbol-concentration cap.
- `rebalance_frequency_minutes` is read only; no rebalance scheduler uses it.
- `learning_rate_exploration_factor` changes a diagnostic value but is excluded
  from trading calibration because it does not affect the ledger.
- `phase1_exploration_intensity` and `phase2_optimization_intensity` feed a
  diagnostic `exploration_bias`; the authoritative supervisor uses its own run
  configuration instead.
- `retry_delay_seconds` is read but no retry loop consumes it.
- `limit_order_offset_percent` creates a limit price which is not passed to the
  paper broker, so order execution remains next-open market-style.
- Six safety names are not directly consumed in the audited runtime logic:
  `drawdown_derate_multiplier`, `drawdown_derate_threshold`,
  `max_market_data_age_seconds`, `max_reconciliation_qty_diff`,
  `order_dedup_window_seconds`, and `order_timeout_seconds_execution`.

## 18-gate defects

1. Gate 07 uses a literal 30-second stale-data threshold instead of the
   canonical `max_market_data_age_seconds` value.
2. Historical runs always supply market-data age as zero.
3. Gate 10 uses literal drawdown `0.18` instead of the configured derating
   threshold.
4. Gate 13 receives `seen_recent=False` unconditionally, so duplicate-order
   protection never rejects an order.
5. Gate 14 is always called with elapsed time `0.0` and compares it to broker
   offline duration, not the canonical execution timeout.
6. Gate 15 is called before execution with expected and actual quantity equal;
   it cannot detect a partial or mismatched fill.
7. Gate 16 compares the planned entry price with itself before the broker fill;
   actual fill slippage cannot be rejected by this invocation.
8. Gate 17 can reject a new entry after cutoff but no session-calendar-aware
   forced close is implemented.
9. Gate 18 receives broker/circuit-breaker constants in historical runs rather
   than exercised state transitions.
10. The gate logger methods are no-ops, so gate-level durable telemetry is not
    provided by the logger itself.

## Accounting/execution defects relevant to Backtrader parity

- Entry fill uses next-bar open, but the portfolio ledger stores the decision
  timestamp as `entry_timestamp`.
- Open positions are liquidated only at end of the entire run, not at each NSE
  session boundary; this permits overnight MIS positions.
- Daily loss is derived from the lifetime equity peak rather than start-of-day
  equity and is therefore not daily accounting.
- Stop is checked before target on a bar that touches both, but gap behavior and
  fill-at-open behavior are not modeled explicitly.
- The cost model includes capped brokerage, one exchange-rate term, and sell
  STT. It omits the full documented Zerodha statutory schedule (including GST,
  SEBI charge, and side-specific stamp duty), so “identical Zerodha costs” is
  not yet proven.
- `PaperBrokerAdapter` uses wall-clock UUID/timestamps, making raw fill records
  nondeterministic and detached from historical event time.

## Verification results

- Focused committed tests: 40 passed, 121 subtests passed, 1 failed.
- Failure: the P01D expiry test calls namedtuple `_replace` on a dataclass token;
  this is a test/API contract mismatch that must be resolved.
- Startup suite collection fails on a clean branch because
  `ecs_runtime_v2.py` is uncommitted in another checkout.
- Existing sensitivity tests are useful and caught earlier dead PID wiring, but
  they explicitly exempt several masked/inert parameters. They do not justify
  the statement that every parameter is active end to end.

## Required correction order

1. Commit or remove the missing runtime dependency and make clean-clone tests collect.
2. Replace constant gate inputs and literal thresholds with real canonical state.
3. Implement event-time order deduplication, timeout, post-fill reconciliation,
   and actual-fill slippage validation.
4. Implement NSE session-calendar MIS liquidation and daily P&L reset.
5. Centralize the complete Zerodha cost model and use it in both backends.
6. Make LIMIT/retry/timeout behavior real or remove those inactive parameters.
7. Fix symbol lot mapping and decide whether pyramiding/rebalancing are supported;
   remove unsupported settings from calibration until then.
8. Expand data certification and frozen-manifest enforcement.
9. Finish canonical event emission and run Backtrader parity.
10. Only after parity, seal train/validation/test partitions and calibrate.

## Final conclusion

The architecture is a meaningful executable foundation, but it is not fully
hooked up and not all parameters are active. The most serious gaps are in safety
gate inputs, session/accounting semantics, order lifecycle behavior, complete
cost modeling, and clean-clone reproducibility. External predictive models are
not currently part of the ten-box runtime; this should be stated explicitly
rather than inferred from legacy files elsewhere in the repository.
