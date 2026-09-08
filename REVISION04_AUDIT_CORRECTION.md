# Revision 04 audit correction

## Status

**Revision 04 is a prototype event-loop implementation. It is not approved for
parameter calibration, paper trading, or production use.**

The commits `828525d` and `1040e0f` demonstrate that a five-day replay can
create and process paper orders without crashing. They do not establish a
valid 48-symbol, 68-parameter trading system.

## Verified gaps

- `scripts/run_sealed_month.py` hard-codes 45 symbols, not 48.
- `TenBoxPipeline` duplicates simplified logic rather than calling the
  Revision 2 black boxes: Chart Studies is constant and Grid Sync always
  approves.
- The warm-up setting is not enforced, and the replay-time daily P&L control
  reads wall-clock time rather than the event timestamp.
- The order and ledger implementation lacks safe unique IDs, correct holding
  indices, expiry, partial fills, reconciliation, and a certified cost model.
- The mandatory tests do not assert position-limit rejection or future-data
  isolation. Reported Sharpe and profit factor are placeholders.
- The untracked `revision4/` replacement is also incomplete: its effective
  configuration explicitly contains placeholder parameters, and its dataset
  validator checks file presence but does not recompute and compare hashes.

## Required remediation before calibration

1. Freeze and recompute hashes for exactly 48 source files plus the evaluation
   month and its prior warm-up window.
2. Adapt the actual Revision 2 PA, ID, TradePlan/MPC, Position Manager,
   Safety Gates, and P01D components through typed contracts.
3. Use a single chronological 48-symbol portfolio ledger with batch candidate
   ranking, next-bar execution, and an explicit paper-order state machine.
4. Prove all canonical parameters have an executable owner and prove causal
   sensitivity for every calibratable parameter.
5. Add deterministic tests for causality, portfolio constraints, order
   lifecycle, costs, reconciliation, and EOD flattening.

The ₹1,000/day figure is a research target only. It is not a promise or an
acceptance criterion for an unverified engine.
