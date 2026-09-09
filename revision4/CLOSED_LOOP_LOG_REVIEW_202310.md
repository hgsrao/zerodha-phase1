# Closed-Loop Setpoint Evidence from Prior Sealed Runs

## Scope and evidence quality

This review uses the completed-trade ledgers of both corrected Rev4 validation
finalists for 2–6 October 2023.  The two ledgers contain 345 completed trades;
328 can be joined exactly to their completed decision bar and causal market
features.  No future prices were used to calculate the entry features.

This is a five-session, in-sample diagnostic.  It can generate shadow-control
hypotheses; it cannot justify an enabled trading rule or a calibrated threshold.
Both finalists are parameter variants from the same validation window, so their
trades are not independent samples.

## What the logs show

### Exit outcomes

| Exit type | Trades | Net P&L | Entry signal pattern |
|---|---:|---:|---|
| Stop hit | 96 | −₹1,922.64 | weaker directional momentum, VWAP alignment and relative volume |
| Target hit | 27 | +₹1,061.26 | stronger directional momentum, VWAP alignment and relative volume |
| Time exit | 169 | mixed / near cost-neutral | generally reaches the maximum holding horizon without enough progress |

At the decision bar, the median feature values for target-hit versus stop-hit
trades were:

| Feature | Stop hit | Target hit |
|---|---:|---:|
| Direction-adjusted five-minute move / ATR | 1.43 | 2.24 |
| Direction-adjusted VWAP distance / ATR | 2.30 | 3.84 |
| Current volume / prior 20-bar mean volume | 1.04 | 1.52 |
| Current bar range / 20-bar ATR | 1.17 | 1.58 |

This is not proof that any single feature causes success.  It does show that
target-hit trades entered when direction, VWAP position and activity were more
supportive than stop-hit trades.

### The strongest candidate is VWAP alignment, not a raw volatility gate

The diagnostic tested *descriptive* cutoffs for directional VWAP distance.

| Minimum aligned VWAP distance in ATR | Trades retained | Combined net P&L | Stop hits | Target hits |
|---:|---:|---:|---:|---:|
| 0 | 270 | −₹387.54 | 79 | 24 |
| 1 | 229 | −₹66.69 | 58 | 20 |
| 2 | 202 | +₹36.27 | 50 | 19 |
| 3 | 164 | +₹225.29 | 40 | 18 |
| 4 | 136 | +₹74.84 | 35 | 13 |
| 5 | 120 | +₹7.18 | 32 | 11 |
| 6 | 99 | −₹147.88 | 28 | 6 |

The apparent improvement is non-monotonic beyond 3 ATR and was observed after
looking at this same sample.  Therefore **3 ATR is not an approved threshold**.
The evidence supports collecting VWAP alignment as a continuous shadow score.

### Time of day is relevant but not sufficient by itself

For both finalists, entries in 09:00 and 10:00 IST were negative.  The combined
loss before 11:00 IST was approximately −₹629 across 119 joined trades.  The
11:00–14:59 IST group was much less negative (approximately −₹56 across 186
trades), but the outcomes within individual afternoon hours conflict between
the two finalists.  Time of day is a reasonable shadow feature, not yet an
entry prohibition.

## What this implies for closed-loop setpoints

### Entry loop: supported shadow measurement

The entry controller should retain the existing rolling approved-ID-confidence
baseline and record an additional causal market-alignment score:

```text
VWAP_alignment = direction × (decision_close − session_VWAP) / ATR20
momentum        = direction × (decision_close − close_5_bars_ago) / ATR20
activity        = decision_volume / mean(previous_20_volumes)
```

The first shadow setpoint should be a continuous, bounded score, not an
after-the-fact hard cutoff:

```text
entry_quality_shadow = f(ID confidence baseline,
                         VWAP_alignment,
                         momentum,
                         activity,
                         time-of-day)
```

The PID may compare current quality with this score only in shadow mode.  It
must not change the ID hard gate, risk caps, stop distance, safety gates, or
live order quantity yet.

### Exit loop: evidence says measure progress, but cannot yet fix a schedule

Time exits dominate completed trades and frequently expire at the maximum hold
time.  That supports an exit-progress loop based on `R` progress and bars held.
The prior ledger lacks per-bar maximum favorable excursion (MFE), maximum
adverse excursion (MAE), PID adjustment, trailing-stop level, and Grid/Nifty
state.  Without those fields, the logs cannot identify an honest progress
schedule or PID gains.

### Portfolio/grid loop: not derivable from prior logs

Prior run reports retain aggregate ten-box call counts but not a timestamped
Grid Sync state, sector breadth state, or Nifty state.  There is no evidence
yet for a Nifty/grid-derived portfolio setpoint.  It must first be recorded,
timestamp-aligned, and validated in shadow mode.

## Required telemetry before any controller change

At every candidate and every open-position bar, persist:

1. PA direction/confidence and ID decision/risk-reward;
2. session VWAP, ATR, directional momentum, relative volume, and range/ATR;
3. Grid Sync breadth, sector alignment, and Nifty state once a sealed Nifty
   stream is available;
4. MPC initial stop/target and planned target in `R` units;
5. entry/exit PID setpoint, measurement, error, P/I/D terms, and bounded
   output;
6. per-bar R progress, MFE, MAE, trailing stop, and exit request reason.

## Decision

Keep the closed-loop idea.  The immediate action is **shadow telemetry for an
entry-quality score led by directional VWAP alignment**, plus per-bar exit
progress telemetry.  Do not create a new hard threshold, tune PID gains, add a
Nifty dependency, or alter safety rules until the new records are evaluated on
train and then validation data.
