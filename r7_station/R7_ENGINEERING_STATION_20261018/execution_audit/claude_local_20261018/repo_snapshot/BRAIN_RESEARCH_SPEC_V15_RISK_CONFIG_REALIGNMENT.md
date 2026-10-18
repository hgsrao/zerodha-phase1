# V15 — Risk Configuration Realignment

Not a strategy research version like V9-V14 - this documents a change to
the production risk-gate *configuration* (`Config` in
`institutional_engine_v34_p01d_candidate.py`) and a correctness fix to
`entry_gate_dry_run.py`, made 2026-08-14 in response to a request to
"restructure to reality and current standards in the trading world."

## Sourced standards (checked live, 2026-08-14)

- Risk per trade: **0.5-1%** of equity is the standard retail/prop range.
- Daily loss limit: **3-5%** of capital is typical across prop-firm and
  retail risk frameworks.
- Max drawdown: prop firms **5-10%**; **~15%** from peak equity is commonly
  cited as reasonable for a personal trial account.
- Zerodha MIS (intraday) auto-squares off ~3:20pm daily; a position meant
  to survive is CNC (delivery), not a same-day-only product relying on a
  rare RMS conversion fallback.

Sources: TradeZella risk-management guide, PropFirmShop risk rules,
MyFundedCapital risk standards, Zerodha's margin-policy and product
documentation.

## What changed

| Field | Before | After | Why |
|---|---|---|---|
| `trial_capital` | ₹20,000 | ₹100,000 | Matches the real trial size discussed and used throughout this session's validation (V9-V14 backtests). |
| `max_daily_loss` wiring | Passed to `RunnerEntryAuthorizer` as `daily_loss_limit` | No longer passed | Was mathematically inert - see below. Field kept in `Config` (removing it outright would have broken ~7 already-passing test files that construct `Config` for unrelated reasons; this way is a zero-behavior-change fix). |
| `target_risk_pct` (0.5%), `max_trade_risk_pct` (0.75%) | unchanged | unchanged | Already inside the sourced 0.5-1% range. |
| `daily_hard_halt_pct` (2%), `rolling_week_halt_pct` (4%), `trial_drawdown_halt_pct` (5%) | unchanged | unchanged | Conservative end of sourced 3-5% / 5-15% ranges - appropriate for a trial account. |

## Two dead-config findings

1. **`max_daily_loss` never bound.** `RunnerEntryAuthorizer` computes
   `effective_daily_halt = daily_hard_halt * 1,000,000` before comparing
   against any `daily_loss_limit` override
   (`run_production_p01d_candidate.py:854-862`). For any realistic capital,
   `daily_hard_halt * 1,000,000` dwarfs any absolute rupee figure that
   could be passed in, so the override could never be the binding
   constraint. It read like a real ₹2,000 stop and wasn't one. Fixed by no
   longer passing it - `daily_hard_halt_pct` (2% of capital, already
   correctly scaling with `trial_capital`) is documented as the real,
   sole daily-loss protection.

2. **`max_simultaneous_positions` isn't enforced by count.** The actual
   check (`run_production_p01d_candidate.py:936-937`) hardcodes
   `deployed_capital > 0 or pending_buy_exposure > 0` - i.e. exactly one
   position, always, regardless of this field's value. **Not fixed here** -
   see Problem B below.

## entry_gate_dry_run.py correctness fix

The first version of this tool (built earlier the same day) only called
the shallow `P03RiskController.evaluate_entry()` directly - capital
ceiling, the (now-documented-inert) daily-loss figure, and kill switch.
It never reached `RunnerEntryAuthorizer._apply_policy_and_reserve()`, the
real P0-3B-F policy layer that actually enforces daily hard halt, rolling
week halt, trial drawdown halt, daily entry lock, per-trade risk sizing,
the simultaneous-position limit, daily entry count, same-symbol lock,
entry cooldown, and daily turnover. A candidate could show `allowed: true`
from the old tool while the real system would have rejected it for any of
those reasons.

Fixed by calling `RunnerEntryAuthorizer`'s real internals directly -
`_fresh_snapshot`, `controller.evaluate_entry`, then
`_apply_policy_and_reserve` - the same sequence `authorize_buy()` uses,
skipping only the `live_trading_enabled` check at its very first line
(which is what makes this a dry run; `authorize_buy()` itself is never
called). All parameters now come from a real `Config()` instance rather
than duplicated constants, so they cannot drift out of sync with
production again.

## Problem B — explicitly not done here

Every strategy actually validated in this research program (V11) holds
positions for weeks across up to 4 concurrent, sector-capped positions,
rebalanced monthly. The execution engine
(`institutional_engine_v34_p01d_candidate.py`,
`run_production_p01d_candidate.py`) was built for V9: a single position,
MIS (intraday-only) product, 15-minute cooldowns, "daily" entry/turnover
semantics. `product="MIS"` appears at 20+ call sites - order construction,
position reconciliation, order fingerprinting, protective-stop
monitoring - as load-bearing logic, not a config value.

Making the engine actually capable of running V11 live requires rewriting
the execution/reconciliation lifecycle for multi-week CNC holds and real
multi-position accounting (including making `max_simultaneous_positions`
real, above). That is a dedicated future engineering effort, deliberately
not attempted alongside this configuration change - doing both at once
would risk introducing real bugs into the exact code that exists to
prevent real-money mistakes, for a strategy whose validation (per this
session's ongoing discussion) doesn't yet justify that investment.

## Release boundary

Configuration and tooling correctness only. Does not authorize enabling
live trading, unlocking Gate 4, connecting a production execution runner,
or placing, modifying, or cancelling broker orders. `LIVE_TRADING_ENABLED`
remains false; no broker calls were made producing this document.
