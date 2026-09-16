# Session Summary — 2026-08-14

Full trading day: strategy research, safety-tooling build-out, and a live
(observation-only) full-day run. This is the durable record of what
happened, what it means, and what's next.

## Safety posture, the whole day

- `LIVE_TRADING_ENABLED = False` at every check, all day - never touched.
- Zero real orders placed. Verified independently four ways: the flag
  itself, `bot_state_v34.json` (`status: FLAT`, `realised_net_pnl: "0"`),
  today's real production session log (no order-related line exists), and
  source-level proof that `request_entry()` - the only function that can
  create a trade - is called by nothing in the live path.
- The production bot (`run_production_p01d_candidate.py`) ran cleanly for
  6.5+ hours: clean startup, correct automatic trading-day rollover
  (2026-08-13 → 2026-08-14), zero errors, zero new log lines after startup
  because nothing happened - exactly as intended for an engine with no
  strategy wired into it.
- All five running processes (production bot, momentum shadow, ORB shadow,
  entry-gate dry run, dashboard) stayed healthy for the full day - flat
  memory, seconds of cumulative CPU time, no leaks.

## What got built today

**Strategy validation** (`BRAIN_RESEARCH_SPEC_V10` through `V15`):
- V10: anchored walk-forward harness for V9 (the original hand-tuned
  strategy) - result was weak/negative even after fixing.
- V11: applied the same walk-forward to the published 12-1 cross-sectional
  momentum control - the strongest result of the whole research program.
- Extended history pull (you, via `download_historical_ohlcv.py`): 2016-2026,
  20 symbols, 3 bad ticks found and corrected via the existing
  `prepare_research_ohlcv.py` tool. POLYCAB excluded (2019 listing capped
  history otherwise) → 19-symbol universe, 17-fold walk-forward instead of 5.
  Momentum went from "cautiously encouraging" to genuinely strong: 12/17
  folds profitable, bootstrap 95% CI entirely positive.
- V12: tried wrapping momentum in V9's risk management (ATR stops) - made
  it *worse* (3/17 folds, 100% bootstrap P(loss)). Diagnosed why: the stop
  was calibrated for a 3-day intraday hold, not a monthly one.
- V14: real Zerodha delivery cost schedule (not a flat bps guess) - edge
  shrank but survived (11/17 folds, still entirely positive CI).
- Scalping was investigated and set aside: the statutory-cost side wasn't
  as bad as feared, but there's no way to honestly backtest it (only
  60-minute bars exist) and the parts that actually decide scalping - spread,
  latency - are exactly where retail infrastructure is weakest.

**Live tooling**, all read-only, all tested:
- `orb_shadow_observer.py` / `orb_shadow_collector.py` - exploratory Opening
  Range Breakout observer, explicitly labeled unvalidated.
- `external_momentum_shadow.py` - the validated V11 momentum, live, with
  real transaction costs applied, wired into the existing
  `read_only_shadow_collector.py`.
- `entry_gate_dry_run.py` - runs live candidates through the *real*
  `RunnerEntryAuthorizer` pipeline (not a reimplementation) to get an
  authoritative answer on every real gate - capital ceiling, daily/weekly/
  drawdown halts, entry limits, cooldowns - while remaining physically
  incapable of dispatching an order. Extended today with hypothetical exit
  construction (mirrors `submit_emergency_exit`'s real validation) and exact
  entry-price logging for future exact P&L tracking.
- `v34_observatory_v4.py` - local, no-login, read-only dashboard
  correlating all four windows on one screen.
- `zerodha_delivery_costs.py` / `zerodha_intraday_costs.py` - real, sourced
  Zerodha fee schedules, replacing flat bps guesses throughout.

**Bugs found and fixed during live operation** (the kind static testing
alone doesn't surface):
1. A stale lock file from an earlier session blocked startup - diagnosed
   (dead PID) and cleared.
2. `entry_gate_dry_run.py` only checked the shallow capital-ceiling gate,
   missing the real, tighter policy layer entirely - fixed to call the real
   pipeline in full.
3. That fix re-fetched the broker snapshot once per candidate, hit a
   transient rate limit - fixed to one fetch per cycle, shared.
4. That transient failure had triggered a real `DURABLE_HALT` in the
   isolated state (correctly reproducing what the real system would do) -
   understood, and the isolated file reset.
5. Two tests were reading the live, currently-mutating telemetry file
   instead of an isolated fixture, so they broke the moment the real
   collector started updating it with real data - isolated properly.

**Risk configuration realignment** (`BRAIN_RESEARCH_SPEC_V15`):
- `trial_capital`: ₹20,000 → ₹1,00,000, matching the real validated figure.
- Found and documented two dead config fields: `max_daily_loss` (never
  bound - swamped by a ×1,000,000 multiplier) and `max_simultaneous_positions`
  (hardcoded to exactly one regardless of its value).
- Percentage-based risk fields (0.5-1% per trade, 2-5% daily, 5% drawdown)
  checked against sourced 2026 retail/prop-firm standards - already sound,
  left unchanged.

## Today's actual result, honestly

The real risk gate authorized exactly 2 of the 4 momentum candidates today
(LAURUSLABS, BAJFINANCE) - SBIN and SUNPHARMA were correctly blocked by
`DAILY_ENTRY_LIMIT`. Computed against real entry/exit prices and the real
cost schedule: **net −₹1,073.92, or −1.07% on the full ₹1,00,000 trial
capital** (not the −1.55% the naive 4-stock paper figure showed - that
number assumed a portfolio size the real gate never would have allowed).
One day. Noise, not evidence, in either direction - but now, going forward,
exact rather than reconstructed, since entry prices are logged from today
onward.

## The one finding that matters most for "is this ready"

**It isn't, and the reason isn't the strategy - it's the engine.** Every
strategy actually validated here (V11) holds for weeks across up to 4
concurrent positions, rebalanced monthly. The only execution engine that
exists (`institutional_engine_v34_p01d_candidate.py`) was built for a
completely different shape: one position, MIS (same-day-only) product,
daily entry semantics. `product="MIS"` is load-bearing logic at 20+ call
sites - order construction, reconciliation, protective-stop monitoring -
not a config value. There is currently no code path that could run V11
live even if every other question were answered. This was deliberately not
touched today - it's a dedicated rewrite, not a tweak, and rushing it
alongside everything else would risk the exact kind of bug this whole
project exists to prevent.

## What needs to happen next, in order

1. **Keep running the observation setup on future trading days.** With
   exact entry-price logging now in place, each additional day adds a real,
   exact data point to a genuine (paper) track record - the single cheapest
   way to keep testing the strategy without touching the engine problem.
2. **Decide on the CNC/multi-position engine rewrite.** This is the actual
   blocker to ever trading V11 for real - not a config change, a genuine
   rewrite of the execution/reconciliation lifecycle. Worth scoping as its
   own dedicated session when there's appetite for it, not squeezed in.
3. **If downside protection is still wanted for V11**, it needs a stop
   sized for a monthly hold and walk-forward validated on its own terms -
   V12 already showed guessing here is actively harmful.
4. **Minor housekeeping, no urgency:** `session_logs/` has ~94 near-empty
   folders from today alone (a known side effect of importing production
   code); fine to clean up whenever convenient.
5. **Point V11's validation at even more history if/when available** - 17
   folds is far better than 5, but still not a large number of truly
   independent test periods.

## Release boundary

Nothing here authorizes live trading, unlocks Gate 4, or changes
`LIVE_TRADING_ENABLED`. This document is a record of research and tooling
progress, not a go-live decision.
