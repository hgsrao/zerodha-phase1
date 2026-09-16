# P01D Intraday Revalidation — 2026-08-14

Independent revalidation of the P01D intraday candidate, run as a project
separate from the frozen P02 CNC engine. Objective revised mid-run per
owner decision: **not** preparation for a live trial, but an
execution-engine safety certification, once I1 established there is no
strategy currently wired into the live path.

`LIVE_TRADING_ENABLED` remained `False` throughout. No production runner
was started. No real broker order was placed. P02 frozen release was not
opened for write, not imported, not modified.

## I0 — Baseline

| Item | Value |
|---|---|
| Candidate engine | `institutional_engine_v34_p01d_candidate.py` — SHA256 `ff40619e600668a381e6d0d9e0d1be942a798a021e7cc2f6e545093464b20d0f`, 2586 lines |
| Candidate runner | `run_production_p01d_candidate.py` — SHA256 `b8b900057de1c5dae76b15e97aee0873e054a682a7d4fd78b0e7bbd0483d36a3`, 1801 lines |
| Patch lineage on disk | 6 `_PRE_*_20260812` snapshots (entry fix, duplicate removal, submit fix/patch, indent repair, restart recovery) |
| `LIVE_TRADING_ENABLED` | `False`, confirmed at 4 locations in the runner |
| Production runner running | No — zero `python.exe`/`pythonw.exe` processes at start of this revalidation |
| Bot state | `bot_state_v34.json`: `status=FLAT`, `active_trade=null`, `realised_net_pnl=0`, no halt, `trading_day=2026-08-14` |
| Test collection baseline | 577 tests / 54 files (root, excluding `work/`, `frozen_releases/`, vendored `.testdeps_*`) |
| P02 contact | None |

## I1 — Strategy-edge revalidation

```
EDGE VERDICT: INSUFFICIENT EVIDENCE FOR LIVE TRADING
```

Independently confirmed by code, not just by trusting prior session notes:

- The runner's operational loop (`run_production_p01d_candidate.py` line
  1773) calls only `engine.step()` every second.
- `TradingEngineV34.step()` is a pure state machine — reconciliation,
  protective-stop polling, halt handling. It never calls
  `self.request_entry(...)` or any signal/candidate-scoring function.
- Repo-wide search for `.request_entry(` shows it is called only by unit
  tests and by the separate P02 multi-position engine — **never by the
  P01D live path.**
- No momentum/ORB/signal-generation function exists in either candidate
  file.
- The one validated strategy (V11) is structurally incompatible with this
  engine (`product="MIS"` load-bearing at 20+ call sites; V11 is
  multi-position/monthly-rebalance). The intraday/scalping idea was
  separately set aside as unbacktestable.

Per the standing rule — no strategy-edge evidence, no live trial,
regardless of engine quality — **this closes the live-trial question for
tomorrow.** I1 is CLOSED. The rest of this document certifies the
execution shell on its own merits, for future use once a real intraday
strategy is designed and connected through its own gate (not V11).

## I2 — Execution-safety certification

**Order dispatch choke point.** `place_order()` in the broker adapter
(`run_production_p01d_candidate.py:737`) is the sole path to
`kite.place_order`, other than `submit_emergency_exit` (a SELL-only exit
path). Findings:
- MARKET orders are unconditionally rejected.
- BUY dispatch requires a non-null `entry_authorizer`, the exact tag
  `V3.4_ENTRY`, and a passing `authorize_buy()` call — before the
  `live_trading` check even runs.
- `not self.live_trading` raises unconditionally after that — physically
  blocking dispatch given today's `False` setting.
- `submit_emergency_exit` checks `not self.live_trading` as its *first*
  line, then validates symbol, quantity, tick alignment (no silent
  rounding), trigger-below-LTP, frozen `market_protection=-1`, and exact
  exit tag, before ever reaching the broker call.
- No path found that reaches either dispatch method while bypassing the
  authorizer or the live-trading gate.

**Fail-closed density.** 175 `trigger_hard_halt` / `FAIL_CLOSED` /
`DURABLE_HALT` occurrences in the 2586-line engine file — halts are the
default response to any unrecognized broker state, not an edge case.

**Kill switch.** File-existence check (`KILL_SWITCH`), enforced inside
`P03RiskController.evaluate_entry` before any entry can be authorized.

**ENTRY_SUBMITTING / ENTRY_UNKNOWN.** Dedicated `_reconcile_unknown_entry`
path (~90 lines) — the durable "submitting" intent is persisted *before*
the broker call, so a crash mid-submission recovers into `ENTRY_UNKNOWN`
and reconciles against broker reality rather than assuming success or
failure.

**Duplicate-order prevention.** Entry/exit submission fingerprints
(tag + qty + price + exchange + product) gate re-submission; more than one
matching broker order forces a hard halt rather than picking one
arbitrarily.

**Partial fills.** Cumulative fill high-water-mark tracking
(`_check_cumulative_fill_hwm`) halts hard on any fill-count regression;
`PARTIAL_POSITION` is a fully modeled state for partial protective-stop
fills, not a gap.

**Restart recovery.** `reconcile_startup()` plus re-entry into `STARTUP`
from `step()`; explicitly tested that restarting from `ENTRY_SUBMITTING`
or `ENTRY_UNKNOWN` never re-submits a duplicate order.

**State durability / collision.** Three distinct root-level state files
(`bot_state_v34.json`, `bot_state_v34.lock`, `bot_state_v34_p03_entry_control.json`).
No collision found with any other active runner — P02 currently has no
standalone production runner of its own to collide with.

**Exception handling.** The operational loop's outer `try/except` turns
any unhandled exception into a `CRITICAL` log line and a non-zero exit
rather than silent continuation or retry; transient vs. fail-closed
exceptions are explicitly distinguished
(`_is_transient_observation_exception`, `_is_transient_submission_exception`).

**Credential handling.** `KITE_API_KEY` / `KITE_ACCESS_TOKEN` read from
environment variables only, `FAIL_CLOSED` if either is missing. No log
statement found anywhere that prints a key, token, or secret value.

## I3 — Adversarial matrix

Rather than re-deriving the adversarial matrix from scratch, I verified
the existing dedicated test suite already encodes it, then ran it:
`test_v34_entry_crash_restart_matrix.py`, `test_v34_p01d_unknown_state.py`,
`test_v34_p01d_emergency_exit.py`, `test_v34_p01e_e20_crash_restart.py`,
`test_v34_entry_submit_restart_recovery.py`, `test_v34_gate4_one_shot_safety.py`.
Test names confirm direct coverage of: order-acknowledgement timeout
before/after broker acceptance, duplicate-exact-order rejection, malformed
broker observation, reconciliation-budget exhaustion, restart from
`ENTRY_SUBMITTING`/`ENTRY_UNKNOWN` without duplication, wrong
qty/trigger/symbol treated as non-matches, multiple matching orders
forcing a halt, and explicit anti-pattern tests asserting `UNKNOWN` state
never has "silent submit" permission. All pass (see I4).

**Caveat, stated honestly:** this confirms the pre-existing adversarial
suite is real and comprehensive, not that I personally constructed novel
adversarial cases beyond it today. If you want a harder bar than "the
existing suite is real and passes," a next step would be writing new
adversarial cases targeting scenarios not already named above.

## I4 — Release verification

```
577 passed, 0 failed, 0 errors, 0 skipped  (exit code 0)
```
Command: `pytest -q --ignore=work --ignore=frozen_releases` plus ignores
for vendored `.testdeps_*`/`.pytest_*` dependency snapshot directories
(these are copied Python package installs, not project code — walking
them hits Windows permission errors on system files and is not part of
the project's test surface). Matches the 577-passed baseline recorded at
the 2026-08-14 P02 freeze — no drift.

Files changed by this revalidation: **none** in the existing codebase.
Only new file: this report.

`LIVE_TRADING_ENABLED = False` reconfirmed unchanged after the run.

## I5 — Owner verdict (final, revised)

```
STRATEGY EDGE            FAIL / INSUFFICIENT EVIDENCE
EXECUTION SOFTWARE       STRONG / CURRENT SUITE GREEN
OPERATIONAL READINESS    NOT APPLICABLE FOR LIVE TRADING
OVERALL LIVE VERDICT     NO-GO
```

**Revision note (owner decision, 2026-08-14):** the original draft of this
section called I2-I4 "PASS," which overstated it — 577/577 green on the
*existing* suite is real and worth recording, but it isn't the same claim
as a fresh, harder adversarial certification, and "operational readiness"
doesn't mean anything for a shell with no live trading path in front of
it. Revised wording above is the one of record. **No fresh P01D
adversarial-test work is planned tonight or on any near-term date** — the
existing shell has diminishing value to keep re-testing against nothing;
when a real intraday strategy is eventually designed and wired in, *that*
strategy→P01D integration is what earns a fresh adversarial certification,
not the shell in isolation again.

This is a good result, not a failed project. We now know exactly what
P01D is: a potentially reusable intraday execution shell, currently green,
awaiting a validated strategy it does not yet have.

## What's next

1. No P01D live trial. Full stop.
2. **Correction (2026-08-14, later same night):** "closed until a real
   intraday strategy exists" overstated it — see
   `P01D_HISTORICAL_BRAIN_RECONSTRUCTION_20260814.md`. A real, historical
   V1-V9 brain lineage exists, independently reconfirmed, along with a
   working (if unnamed) shadow-observation system. The accurate statement
   is narrower: **P01D has no strategy bridge feeding `request_entry()`**,
   and separately, the strongest historical candidate (V9/TOP4_SECTOR) has
   a thin, single-symbol-dependent edge (profit factor 1.03) and no
   confirmed complete live shadow day. NO-GO stands; the reason is now
   precise rather than "nothing exists."
3. **Update (later same night):** decision made — keep P01D, build a new
   strategy on top of it rather than discard it. New thread opened:
   `P01D INTRADAY BRAIN V1 — STRATEGY RESEARCH` (S0 done, see
   `P01D_INTRADAY_BRAIN_V1_S0_INTERFACE_SPEC.md`; S1-S9 not started).
   Target: Option A, a selective intraday tactical bot (1-2 trades/day),
   not a scalper. The strategy→P01D integration still gets its own fresh
   adversarial certification once a strategy survives S1-S6 and S7 builds
   the bridge.
4. P02 remains frozen, untouched.
5. The V11 → P02 rebalance-bridge design gate (R0) is open as a separate
   thread — see `V34_V11_P02_REBALANCE_INVARIANTS_SPEC.md`.
