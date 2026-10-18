# Revision 7 rebuilt market-control engine

This package rebuilds the market-control layer on the existing shared native
engine. No live client is created. The delivery manifest records installation.
Source and tests here are executable; there are no ellipsis placeholders.

## Engine construction

```python
from revision7_avr import Revision7Engine, ControlConfig
from revision5.topology import SYMBOL_TO_BAY

configs = [ControlConfig(s, bay, 1 / 48) for s, bay in SYMBOL_TO_BAY.items()]
engine = Revision7Engine(list(SYMBOL_TO_BAY), r7_configs=configs)
# engine.run(certified_frames, warmup=60)
```

Equal 1/48 budgets in this example are explicit research inputs, not adopted
capital policy. Supply the approved per-symbol allocation schedule. Choosing a
subset must retain those same budgets. Native safety/plant sizing can only reduce
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

Market speed is an explicit trading equivalent: reference_price is the previous
EMA of closes; relative_volume is current volume / prior rolling mean volume.
imbalance=(close/reference_price-1)*sqrt(min(relative_volume,3)).
omega_market=1-clamp(imbalance,-0.5,0.5). Rising volume-backed price pressure is
defined as a speed deficit; the measurement does not contain R or exposure.
This is a market proxy, not an assertion that price measures physical speed.

The exact R6 droop equation drives the market target:
P_droop=clamp(P_reference-(omega_market-1)/0.04,-1,1).
The PID tracks P_droop-actual once. Target feed-forward keeps engagement when
error reaches zero. Derivative is on measurement. Actuator slew and market
response use seconds. The market response filter is not physical rotor inertia;
true physical swing remains in the R6 units described above.

AVR closes a loop around an internal participation model. OHLCV relative volume
is a liquidity proxy, not order-book liquidity or measured spread/depth. All
tuning and decision thresholds remain provisional research values.

## Recovery

`r7_snapshot` includes configuration, symbol histories, caches, AVR, governor,
pulse timing, physical units, bay totals and R7 counters. `r7_restore` validates
into temporary objects before replacing control state. This restores R7 state
only. A matching native broker/order ledger and native strategy state must be
restored separately. Native crash-journal persistence of this additional payload
has not been implemented or certified. No claim of full engine restart is made.

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
maximum hold and reconciliation retain authority. An explicit allowlist makes
legacy discretionary exits advisory; unknown exit reasons retain native authority.
R7 checkpoints include confirmation state. Native pending trade/order state is
restored with the native ledger.

`r7_rebuild.patch` is generated against the preserved six-file R7 baseline. Use
`git apply --check` first. Review the compatibility and authority boundaries
above before installing it. R5/R6 sources are not part of the patch.
