# Rev4 Closed-Loop Implementation Framework

## Decision

Revision 4 will use feedback control to **reduce risk and improve execution
quality**.  It will not use feedback control to force capital deployment,
produce a daily profit target, widen a stop, or weaken a safety contract.

The daily ₹400 objective is a reporting benchmark after costs.  It is not a
controller setpoint.

## Authority hierarchy

```text
Immutable safety contract
  └─ kill switch, drawdown/daily-loss caps, Gate16, cross-session policy, EOD
       └─ Risk gates and shared-portfolio limits
            └─ Portfolio risk-budget derater
                 └─ Position Manager base quantity
                      └─ MPC / PID advisory controls
                           └─ PA, Chart Studies, ID, Grid / regime observations
```

Every arrow flows downward.  A lower layer may reduce or veto an order; it
cannot enlarge a risk cap or override any layer above it.

## The four operating loops

| Loop | Cadence | Setpoint | Measurement | Permitted action |
|---|---|---|---|---|
| Entry quality | per approved candidate | causal quality baseline by symbol and regime | ID confidence, directional VWAP alignment, momentum, activity | reduce base size or reject through an explicit gate |
| Exit progress | per open-position bar | planned R progression supplied by MPC | live R, MFE, MAE, bars held | tighten trail, reduce remaining hold, request exit |
| Portfolio risk budget | per candidate and fill/exit event | permitted risk-at-stop fraction | aggregate loss-at-stop / marked equity | derate to 0–1 or freeze new entries |
| Execution quality | order/fill events | execution limits, not a profit target | staleness, cross-session status, actual slippage/cost | cancel, quarantine, remediate after breach |

No loop is allowed to send a multiplier above 1.0.  A PID is an advisory
derater, not an exposure-seeking controller.

## Portfolio risk-budget controller (first implementation)

Capital deployment is not the controlled variable because a price or stop
width can change notional exposure without changing actual downside risk.

```text
current_risk_at_stop =
  sum(quantity × abs(entry_price − stop_price) for open positions)
  / marked_equity

headroom = allowed_risk_at_stop − current_risk_at_stop
derater  = clamp(headroom / allowed_risk_at_stop, 0.0, 1.0)
final_quantity = floor(position_manager_base_quantity × derater)
```

`allowed_risk_at_stop` must always be at or below the immutable portfolio
risk/exposure limit.  Regime state may lower it; it may not raise it.

The first release is deliberately P-only.  Do **not** add an integral term
until shadow logs show a controllable, stable process.  Integrating every
minute while the portfolio is empty would wind up an artificial demand for
exposure, then oversize the next qualifying order.

## Entry-quality controller

The existing MPC entry PID already compares confidence with a causal rolling
per-symbol confidence baseline and produces a bounded 0.30–1.00 multiplier.
Retain that property.

The proposed new telemetry score is observation-only initially:

```text
entry_quality = f(
  ID confidence,
  directional VWAP alignment / ATR,
  signed 5-minute momentum / ATR,
  relative volume,
  48-symbol breadth / sector alignment,
  optional Nifty alignment
)
```

All fields must be calculated from the decision bar or earlier.  Nifty is
optional and requires a separately manifest-sealed 1-minute feed; until then,
use the 48-symbol internal breadth only.

## Exit-progress controller

For each position, record the initial risk before any trailing adjustment:

```text
R_t = direction × (mark_price_t − entry_price) / initial_stop_distance
```

MPC provides `target_R` and `maximum_hold_bars`; the controller compares
actual `R_t` with a predeclared progression path.  Its output may only tighten
a stop, reduce remaining hold time, or request an Exit Decision.  It can never
widen the initial stop, defer EOD flattening, or override a Gate16 response.

## Execution-quality controller

This is the transferable portion of institutional execution/SOR theory in the
current OHLCV environment:

- decision-to-fill timestamps and implementation shortfall are recorded;
- cross-session or stale orders are rejected/cancelled;
- actual slippage is checked by Gate16;
- a breach triggers the established quarantine, cancellation, adverse flatten,
  reconciliation, and manual-recovery path.

It is not a real smart order router.  Real venue selection, queue position,
and latency optimisation require broker and Level-2 quote data that the
current replay does not have.

## Shadow telemetry contract

Before any controller changes orders, write one immutable event per candidate
and per position-bar containing:

1. sealed data/config/run identities and timestamp;
2. base quantity and final proposed quantity;
3. each setpoint, measurement, error, P/I/D contribution, raw output,
   clipped output, and saturation flag;
4. risk-at-stop before and after the proposed order;
5. PA/ID/MPC/Chart/Grid inputs and regime state;
6. live R, MFE, MAE, trail level, and exit-controller request; and
7. final completed-trade outcome, cost, and exit reason.

## Promotion protocol

1. Implement `PortfolioRiskDerater` in shadow mode only.
2. Run on the sealed training month(s), with no parameter changes.
3. Compare observed and hypothetical orders: turnover, costs, stop hits,
   target retention, drawdown, net P&L, and reconciliation.
4. Choose at most one bounded rule from training evidence.
5. Validate unchanged on the held-out validation period.
6. Keep the test period sealed until configuration selection is final.
7. Only after clean validation may an Optuna study tune a narrow, explicitly
   calibratable group of controller gains.  It must never tune safety values.

## Explicit exclusions

- No controller uses ₹400/day as a setpoint.
- No controller forces 25% or 40% capital deployment.
- No dynamic leverage above the ordinary Position Manager quantity.
- No market-making quoting logic without Level-2 data and an appropriate
  two-sided execution model.
- No SOR claims without multiple venue/broker execution data.
- No safety-contract override through calibration.

## Evidence basis

The relevant sealed validation logs show positive gross but negative net
finalists because costs dominated gross edge.  The first objective is therefore
to improve selective entry, loss containment, and cost-aware execution—not to
increase exposure.  See:

- `revision4/CLOSED_LOOP_LOG_REVIEW_202310.md`
- `revision4/CONTROL_LOOP_COMPARATIVE_RESEARCH.md`
- `diagnostic_output/ray_optuna_corrected_202309_attribution/closed_loop_evidence.json`
