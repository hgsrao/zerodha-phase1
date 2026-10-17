# Revision 7 rebuilt market-control engine

This package rebuilds the market-control layer on the existing shared native
engine. No live client is created. The delivery manifest records installation.
Source and tests here are executable; there are no ellipsis placeholders.

## Engine construction

```python
from revision7_avr import Revision7Engine
from revision7_avr.r7_state import default_configs
from revision5.topology import SYMBOL_TO_BAY

configs = default_configs(list(SYMBOL_TO_BAY))   # registry droop and budgets
engine = Revision7Engine(list(SYMBOL_TO_BAY), r7_configs=configs,
                         r7_decision_log_path='r7_decisions.jsonl')
# engine.run(certified_frames, warmup=60)
```

`default_configs` takes each bay's droop R and capital weight from the R5
parameter registry (R = 0.040, 0.040, 0.055, 0.055, 0.075) and splits the bay
weight equally over the bay's full certified symbol list, so choosing a subset
keeps the same per-symbol budgets. These budgets are research inputs, not adopted
capital policy. `validation_config(c)` gives the first controlled validation
tuning (Kd = 0) without changing the baseline default. Native safety/plant sizing can only reduce
the admitted quantity. The wrapper never creates orders or fills itself.

The engine class is constructed from the existing shared native orchestrator.
`engine_class(base)` supports an explicitly supplied compatible native subclass.
This architecture does not depend on test harnesses.

## Signal ownership

The studies tap forwards the native evaluation once, captures its current OHLCV
row, and hands it to `_observe_conviction`. The native studies/conviction update
runs once. R7 then advances features, AVR, exposure tracking and pulse exactly
once. Warmup follows the same path. If upstream admission prevents studies,
the completed tick receives a degraded observation with entry eligibility off.
The plant gate reads the current cached pulse and calls the native gate. It
does not update controller state or reinterpret candidate conviction as studies.

Nifty context comes from the native timestamp-bound grid snapshot. Its fractional
deviation is divided by `r7_dispatch_scale` (default 0.01, provisional strategy
scale) then bounded. Missing grid context blocks new R7 admissions and is visible
through `eligible=False`. The two native-engine smoke tests have no supplied
grid feed and therefore prove lifecycle coverage, not trade fills.

## Physical versus market equations

Physical R6 units retain R=0.04 in all five bays, M=8 seconds gas / 5 seconds
steam, H=M/2, distinct turbine lag and real rotor swing. Invoke
`r7_physical_governor_step` only with explicit physical power inputs. Machine
MVA/pole data remain unavailable, so no J is invented.

Trading governor (`r7_market_governor.py`), all in signed symbol-budget pu:

- Strategy reference: E_ref = clamp(0.5*AVR + 0.3*study + 0.2*dispatch).
  Study is the signed studies direction*confidence, once per bar. Dispatch is the
  Nifty grid deviation / `r7_dispatch_scale`.
- Common measurement: portfolio gross utilization U = filled gross notional /
  authorized gross limit (equity x max_gross_fraction). Projected U adds working
  entry orders, valued conservatively; a missing price fails closed. U is never
  clamped.
- Droop-inspired capital sharing (`r7_allocator.py`): each symbol may increase
  same-side exposure by at most its share of the remaining headroom; shares are
  budget/R over the universe, fixed per run and order independent. This is capital
  sharing, not physical frequency droop. R is settable within 0.02 to 0.10 and fixed
  during a run. A neutral reference approves zero, reductions are always approved,
  and rising utilization never pushes a target further from flat or flips its sign.
- PID tracks approved target minus measured exposure (never clamped). Kp is
  independent of R. Integral uses conditional anti-windup and is frozen while a
  native order is working. Derivative is on measurement, optionally filtered.
  Command slew and response lag use exchange-session seconds; an overnight
  interval counts as one bar. Target feed-forward keeps engagement at zero error.

The price/EMA/relative-volume `market_speed_pu` is retained only as diagnostic
telemetry. It is not physical speed and is never passed to the R6 speed-droop
equation or the trading governor. The response filter is not rotor inertia; true
physical swing remains in the R6 units described above.

AVR closes a loop around an internal participation model. OHLCV relative volume
is a liquidity proxy, not order-book liquidity or measured spread/depth. All
tuning and decision thresholds remain provisional research values.

## Recovery

`r7_snapshot` (schema 2) includes configuration, symbol histories, caches, AVR,
governor (including derivative filter state), pulse timing, physical units, bay
totals, R7 counters, and the decision count with its rolling SHA-256 chain. It
does not contain the per-bar decision history, so its size is bounded. The
complete history streams to the external JSONL decision log and can be verified
against the checkpoint chain. `r7_restore` validates into temporary objects
before replacing control state. This restores R7 state only. A matching native
broker/order ledger and native strategy state must be restored separately. Native
crash-journal persistence of this payload has not been implemented or certified.
No claim of full engine restart is made.

## Remaining integration boundaries

- Existing native Mark V entry gates, plant AVR sizing and protective exits stay
  authoritative. This proposal does not remove inherited vetoes or make market
  pulses own the normal exit decision using the adapter described below.
- Existing R7 factories used by the old replay command are replaced by the
  explicit `Revision7Engine(..., r7_configs=...)` construction API. The old
  command must not be advertised as compatible with this proposal.
- Native reservation/acknowledgement behavior is inherited, not reimplemented.
  Tests verify quantity delegation; no full historical order-ledger replay has
  been executed for this proposal.

## Review and installation

Normal exits: opposite pulses for long/short positions, or sustained neutral
release, request exit after the configured confirmation count (default two
completed bars) and native minimum hold. The adapter arms a native pending exit
with reason r7_coordinated_exit. It executes at the following open through the
actual native broker and ledger. Re-evaluating the arming bar cannot fill it.
Stop/target gaps, hard/ratchet stops, plant/grid trips, drawdown, force-close,
maximum hold and reconciliation retain authority. Operator decision 2026-10-17:
the exact-match discretionary allowlist contains only `governor_exit:GOVERNOR_Z_EXIT`.
FSRN/FSRT limiter exits, stressed regime, saturation, governor path error,
controller-path and final-controller exits, and any unknown reason execute
natively and are never withdrawn by pulse, hold, cooldown or slew logic.
R7 checkpoints include confirmation state. Native pending trade/order state is
restored with the native ledger.

The governor corrections are on branch `r7-governor-corrections` (baseline commit
`f0ad7af` holds the trees exactly as found). R5/R6 sources are unchanged.
Historical Engine B/C replay acceptance has not been completed; nothing here is a
production-readiness claim.
