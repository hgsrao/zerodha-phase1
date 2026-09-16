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

## Post-foundation update (`e130e9c`)

The new typed-contract foundation is a useful structural start, but it does
not close the gaps above and is not an executable rebuild yet.

- The current hard-test command reports **1 failed, 8 passed**, not 9/9.
  Its dataset test uses a non-existent `/path/to/data` and catches the wrong
  exception type.
- `PipelineAdapter.generate_forecast()` explicitly contains replacement
  placeholders, including a constant chart confidence. It does not invoke a
  Revision 2 PA or chart box.
- `_log_param()` does not return the supplied value. Consequently, calling
  `make_id_decision()` attempts to compare a float with `None` and fails.
- The claimed 68-parameter configuration has 71 declared fields, and the
  parameter-trace test accepts an empty trace (`len(trace) >= 0`). It does not
  prove parameter consumption.
- `PortfolioLedger.close_position()` currently adds zero cash at exit
  (`exit_price * quantity - exit_price * quantity`), so a completed trade
  cannot reconcile its sale proceeds.
- Dataset validation still reads hashes from the manifest without recomputing
  the on-disk file hashes.

These faults must be repaired and covered by failing-then-passing tests before
the foundation can be called ready for an integration replay.

## Post-fix verification (`27345ca`, `f45d639`)

The following targeted repairs are present and the local hard-test command now
reports 8 passed and 1 skipped:

- ledger exit proceeds were corrected;
- parameter logging now returns the fetched value; and
- dataset validation now recomputes file hashes when run against a real data
  directory.

The following claims remain unverified or false:

- `EffectiveConfig.get_all_params()` currently returns **61**, not 68,
  parameters. It is not derived from the canonical registry.
- The trace test proves only that a handful of ID thresholds were read. It
  does not prove coverage, ownership, or causal effect for all calibratable
  parameters.
- The dataset test is skipped, so hash verification has not been exercised
  against the real 48-file dataset by this test suite.
- The future-data test remains an empty `pass` statement; determinism is
  tested only by manually applying the same two ledger mutations.
- The PA/chart adapter still contains explicit placeholder calculations and
  does not call Revision 2 black boxes.

Accordingly, the accurate status is **foundation partially repaired; replay
engine and genuine Revision 2 integration remain required**.

## Canonical registry verification

The canonical registry in this checkout defines a newer target contract:

- 69 target parameters: 47 calibratable and 22 fixed target values;
- 20 separate immutable safety-contract values; and
- the target contract is owned across the declared Revision 2 boxes.

However, the registry's integrity gate currently fails. Its declared frozen
SHA-256 is `963b6cb434e892b0ffb4ed608e66e8f9793bc7c46bfae895505605e023a2ff26`,
while the current canonical payload computes to
`564ca6e245426ade7507fcf5c0e04a520f1ed161e2a48b8ce0ed5f66049b3947`.

Do not replace the declared hash merely to make the check pass. First recover
the approved registry artifact or obtain an explicit, versioned authorization
for a new contract identity. Until then, the Revision 4 configuration adapter
must remain blocked.

Git-history check: the same mismatch is present in commit `23ad9ca`, the
commit that introduced the 69-parameter `saturation_exit_bars` revision and
the declared `963b...` identity. This indicates that the identity was never
verified successfully at introduction; it is not evidence of a later local
checkout mutation.
