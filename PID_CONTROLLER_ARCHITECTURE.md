# PID Controller Architecture: Current State & Components

## Current PID Inputs (4 Separate Feeds)

```
┌─────────────────────────────────────────────────────────────────┐
│                    CONTINUOUS EXIT CONTROLLER (Box 6)            │
│                   per symbol, every bar a position is open       │
└─────────────────────────────────────────────────────────────────┘

INPUT #1: PA CONFIDENCE
  ├─ Source: PredictiveAnalyticsBox (Box 4/5)
  ├─ Range: 0.0 - 1.0 (signal strength)
  ├─ Update: Every bar
  ├─ PID: Independent (Track 1)
  │   ├─ Setpoint: Rolling 20-bar baseline of PA confidence
  │   ├─ Error: setpoint - current_confidence
  │   ├─ Gains: Kp=0.05, Ki=0.02, Kd=0.01
  │   ├─ Clamp: ±1.0 (anti-windup)
  │   └─ Output: confidence_tightness [0.5 to 1.0]
  └─ Purpose: "Has PA signal faded relative to recent baseline?"

INPUT #2: CHART STUDIES CONFIDENCE
  ├─ Source: Ichimoku/Bollinger/Stochastic/VWAP studies
  ├─ Range: 0.0 - 1.0 (technical signal strength)
  ├─ Update: Every bar
  ├─ PID: Independent (Track 2, NEVER MERGED with Track 1)
  │   ├─ Setpoint: Rolling 20-bar baseline of studies confidence
  │   ├─ Error: setpoint - current_chart_studies_confidence
  │   ├─ Gains: Same (Kp=0.05, Ki=0.02, Kd=0.01)
  │   ├─ Clamp: ±1.0
  │   └─ Output: studies_tightness [0.5 to 1.0]
  └─ Purpose: "Have technical indicators turned bearish?"

INPUT #3: PRICE CURVE (Favorable Extreme)
  ├─ Source: Real OHLCV bars
  ├─ Value: High-water-mark if BUY, low-water-mark if SHORT
  ├─ Update: Only when price makes new progress (not pullback)
  ├─ Conversion: favorable_extreme → stop price distance
  ├─ Function: Trailing stop anchoring
  └─ Purpose: "Ratchet stop up on real progress, not noise"

INPUT #4: TIME HELD (Decay Over Bars)
  ├─ Source: Internal state counter
  ├─ Value: bars_held / max_hold_bars (0.0 to 1.0)
  ├─ Update: Every bar
  ├─ Conversion: time_tightness = 1.0 - 0.5 * time_fraction
  │   ├─ Fresh (0 bars): tightness = 1.0 (relax exit)
  │   └─ Old (max bars): tightness = 0.5 (force exit)
  └─ Purpose: "Long-held trades get pulled in even if signals look OK"

PLUS: DROOP (Non-input Constant)
  ├─ Source: ATR (real volatility measurement)
  ├─ Formula: droop_distance = ATR × trailing_stop_atr_mult (4.0x)
  ├─ Purpose: Scale stop distance to market volatility
  └─ Note: NOT part of feedback loop, sets amplitude of allowed motion
```

---

## The Closed Loop (Feedback System)

```
EVERY BAR:

[Position Open]
    │
    ├─→ [Measure #1] PA Confidence (0.0-1.0)
    │        │
    │        ├─ vs. baseline (rolling mean of past 20 bars)
    │        ├─ Error = baseline - current
    │        └─ PID processes error → tightness
    │
    ├─→ [Measure #2] Chart Studies Confidence (0.0-1.0)
    │        │
    │        ├─ vs. baseline (rolling mean of past 20 bars)
    │        ├─ Error = baseline - current
    │        └─ PID processes error → tightness
    │
    ├─→ [Measure #3] Current Close vs Favorable Extreme
    │        │
    │        ├─ IF BUY: high-water-mark
    │        ├─ IF SHORT: low-water-mark
    │        └─ Distance = anchor for stop
    │
    ├─→ [Measure #4] Bars Held
    │        │
    │        └─ Time decay: 1.0 → 0.5 over max_hold_bars
    │
    ├─→ [COMBINE] Take minimum tightness (most conservative)
    │        │
    │        ├─ confidence_tightness
    │        ├─ studies_tightness
    │        └─ time_tightness
    │        = combined_tightness (0.5 to 1.0)
    │
    ├─→ [CALCULATE] Stop Distance
    │        │
    │        ├─ droop = ATR × 4.0 (fixed multiplier)
    │        ├─ stop_distance = droop × combined_tightness
    │        └─ adjust_stop_price = favorable_extreme ± stop_distance
    │
    └─→ [OUTPUT] Update trailing stop
            │
            ├─ Check: price vs stop → exit?
            ├─ OR saturation streak ≥ 5 bars → exit?
            └─ Loop back next bar...
```

---

## Risk & Lambda Parameters (Position Sizing)

**Lambda = Portfolio Risk Budget Limit**

```
Capital Structure:
├─ Starting Equity: 1,000,000
│   │
│   ├─ Buffer Fraction: 10% (100,000) ← never touches
│   │
│   └─ Usable Equity: 900,000
│        │
│        ├─ Capital Fraction: 20% (180,000) ← max risk per trade
│        │   │
│        │   ├─ IF drawdown < normal: 100% usage
│        │   ├─ IF drawdown in derated band: 50% usage
│        │   └─ IF drawdown > halt threshold: 0% (no entries)
│        │
│        └─ Lambda Risk Limit: 5% of usable (45,000)
│            │
│            ├─ Checks sum of all open positions
│            ├─ If portfolio risk > lambda: reduce sizing
│            └─ Prevents concentration blow-up
```

**Current Values:**
```
parameter_name              value   range    purpose
─────────────────────────────────────────────────────────
capital_fraction            0.20   0.05-0.40  max risk per trade
buffer_fraction             0.10   0.05-0.20  emergency buffer
drawdown_normal_threshold   0.05   0.01-0.10  before sizing cut
drawdown_derated_threshold  0.10   0.05-0.20  50% size cut here
drawdown_halt_threshold     0.20   0.10-0.30  no new entries
portfolio_lambda_risk_limit 0.05   0.02-0.10  portfolio risk cap
max_live_positions          10     1-20       max open trades
max_per_symbol              2      1-5        max per stock
concentration_cap           0.15   0.05-0.25  max position size
sector_cap                  0.25   0.10-0.50  max sector exposure
```

---

## Who Is Where (Component Hierarchy)

```
┌─────────────────────────────────────────────────────────┐
│ ORCHESTRATOR (Revision2ExternalEngineOrchestrator)       │
│ ├─ Manages position lifecycle                            │
│ ├─ Keeps per-symbol exit controller states               │
│ └─ Calls each component in sequence                      │
└─────────────────────────────────────────────────────────┘
         │
         ├─→ ┌──────────────────────────────────┐
         │   │ BOX 1: DataIngestionBox          │
         │   │ Entry: Load bar, check universe  │
         │   └──────────────────────────────────┘
         │
         ├─→ ┌──────────────────────────────────┐
         │   │ BOX 4: PredictiveAnalyticsBox    │
         │   │ ├─ PA confidence (0.0-1.0)       │
         │   │ ├─ Features: momentum, volume    │
         │   │ └─ Scale factors: dp, dv, vol    │
         │   └──────────────────────────────────┘
         │
         ├─→ ┌──────────────────────────────────┐
         │   │ BOX 5: IDBox (Acceptance Gate)   │
         │   │ ├─ Threshold vs PA confidence    │
         │   │ ├─ Rejects weak signals          │
         │   │ └─ Routes to MPC if approved     │
         │   └──────────────────────────────────┘
         │
         ├─→ ┌──────────────────────────────────┐
         │   │ BOX 3: ModelPredictiveControl    │
         │   │ ├─ Entry PID controller          │
         │   │ ├─ Exit PID controller           │
         │   ├─ Entry timing multiplier         │
         │   └─ Stop/Target prices             │
         │   └──────────────────────────────────┘
         │
         ├─→ ┌──────────────────────────────────┐
         │   │ BOX 6: ContinuousExitController  │
         │   │ ├─ Track 1: PA confidence PID    │
         │   │ ├─ Track 2: Chart studies PID    │
         │   ├─ Input #3: Favorable extreme    │
         │   ├─ Input #4: Time decay           │
         │   ├─ Combines all 4 inputs          │
         │   └─ Outputs: Updated stop price    │
         │   └──────────────────────────────────┘
         │
         ├─→ ┌──────────────────────────────────┐
         │   │ BOX 7: SafetyGatesTargetBox      │
         │   │ ├─ Pre-sizing: Drawdown checks   │
         │   │ ├─ Position sizing (lambda limit)│
         │   │ └─ Post-sizing: Loss caps        │
         │   └──────────────────────────────────┘
         │
         └─→ ┌──────────────────────────────────┐
             │ BOX 8: ExecutionGate             │
             │ ├─ Broker order validation       │
             │ ├─ Slippage checks               │
             │ └─ Actual fill execution         │
             └──────────────────────────────────┘
```

---

## Critical Observation: What's MISSING

The PID controller has **4 inputs** but is **missing 1 crucial input:**

```
CURRENT INPUTS (4):
  ✓ PA Confidence (signal strength)
  ✓ Chart Studies Confidence (technical agreement)
  ✓ Price Curve (favorable extreme)
  ✓ Time Held (bars open)

MISSING INPUT (1):
  ✗ GRID STATE (market regime)
     - Nifty 50 trend (voltage)
     - VIX level (frequency)
     - Stock/Index phase sync (phase angle)
```

**Why This Matters:**
- Current PID optimizes for individual trade quality
- But can't detect market regime mismatch
- Result: "Excellent trade entry" in "terrible market"
- → All 62 trades lose despite perfect signal mechanics

---

## The Fix: Add 5th Input

```
NEW ARCHITECTURE:

[ContinuousExitController.update()]
    │
    ├─ Input #1: PA confidence [existing]
    ├─ Input #2: Chart studies confidence [existing]
    ├─ Input #3: Favorable extreme [existing]
    ├─ Input #4: Time held [existing]
    │
    └─ Input #5: GRID SYNCHRONIZATION STATE [NEW]
         ├─ Voltage: Nifty 50 trend alignment
         ├─ Frequency: VIX in safe band?
         ├─ Phase Angle: Δϕ ≤ 15°?
         │
         └─ Effect on exit:
            ├─ Grid strong → relax exit (let winners run)
            ├─ Grid weak → tighten exit (exit faster)
            └─ Grid rejected → DON'T ENTER (gating at PA/ID)
```

---

## Summary: Current State

| Component | Has? | What It Does |
|-----------|------|--------------|
| **PA Confidence PID** | ✅ | Measures if signal faded vs recent |
| **Chart Studies PID** | ✅ | Measures if technicals turned bearish |
| **Favorable Extreme** | ✅ | Anchors trailing stop to price progress |
| **Time Decay** | ✅ | Forces exit as hold time increases |
| **ATR Droop** | ✅ | Scales stop to current volatility |
| **Lambda Risk Budget** | ✅ | Limits portfolio concentration |
| **Grid Synchronization** | ❌ | **MISSING: Market regime filter** |

**What works:** Individual trade mechanics (entry signal, sizing, stop placement)

**What doesn't work:** Regime detection (when NOT to trade at all)

**Result:** 100% of 62 trades entered in bad regime, all losing

---

## Next Step

**Wire grid synchronization as 5th input to PID controller:**
- Feeds directly into exit tightness calculation
- Also gates entry at PA/ID boundary (don't start if grid rejects)
- Converts from "100% trades, 0% win" to "filtered entries, high win rate"

Ready to implement?
