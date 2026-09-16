# PHASE 5: THREE-HEAD ASSEMBLY (PA → ID → MPC → P01D)
## Architecture Design & Configuration Roadmap
**Date:** August 28, 2026  
**Status:** READY FOR DISCUSSION  
**Timeline:** Sep 1-30, 2026 (30 days)

---

## EXECUTIVE SUMMARY

Phase 5 integrates trained Model 0/1 with existing PA/ID/MPC black box architecture. The architecture is **structurally frozen** (serial design with immutable provenance), but key **economic parameters remain unfrozen** for empirical validation.

**What's Frozen:**
- Serial pipeline: Model 0/1 → PA → ID → ID→MPC Packet → MPC → P01D
- No direct PA→MPC shortcuts
- P01D sovereignty (can refuse even if all other heads say "trade")
- 13 existing PA/ID/MPC components (production-ready)

**What's Unfrozen (For Discussion):**
- PA calibration method and prediction horizon
- ID reliability thresholds and barrier parameters
- Expected-return bridge formula
- MPC risk penalty coefficients and constraints
- NSE-specific cost model calibration

---

## ARCHITECTURE OVERVIEW

```
┌─────────────────────────────────────────────────────────────────┐
│                    PHASE 5: THREE-HEAD ASSEMBLY                 │
└─────────────────────────────────────────────────────────────────┘

   Model 0 (Ridge)                Model 1 (XGBoost)
         │                              │
         └──────────────┬───────────────┘
                        │
                   Features (48-dim)
                        │
                        v
    ╔═════════════════════════════════════╗
    ║  PA: Predictive Analytics Black Box ║
    ║                                     ║
    ║  Input: Model 0/1 forecasts        ║
    ║  Output: P(DOWN/FLAT/UP)           ║
    ║  Authority: Prediction only        ║
    ╚═════════════════════════════════════╝
                        │
                        v
    ╔═════════════════════════════════════╗
    ║  ID: Intelligent Discrimination    ║
    ║                                     ║
    ║  Input: PA output + market state   ║
    ║  Output: TAKE/PASS decision        ║
    ║  Authority: Reliability assessment ║
    ╚═════════════════════════════════════╝
                        │
            ┌───────────┴───────────┐
            │  If PASS: return      │
            │  NO_TRADE             │
            │                       │
            │  If TAKE: continue    │
            v                       
    ╔═════════════════════════════════════╗
    ║  Expected-Return Bridge (Unfrozen)  ║
    ║                                     ║
    ║  Input: Calibrated PA + ID         ║
    ║  Output: Expected return (bps)     ║
    ║  Objective: Economic translation   ║
    ╚═════════════════════════════════════╝
                        │
                        v
    ╔═════════════════════════════════════╗
    ║  MPC: Model Predictive Control     ║
    ║                                     ║
    ║  Inputs:                            ║
    ║    - ID→MPC packet (w/ provenance) ║
    ║    - Constraint state (positions,  ║
    ║      risk, turnover, halt status)  ║
    ║  Output: Optimal position sizing   ║
    ║  Authority: Constrained optim.     ║
    ╚═════════════════════════════════════╝
                        │
                        v
    ╔═════════════════════════════════════╗
    ║  P01D: Sovereign Safety Boundary   ║
    ║                                     ║
    ║  Can REFUSE even if PA/ID/MPC all  ║
    ║  said "trade"                      ║
    ║  Authority: FINAL EXECUTION only   ║
    ╚═════════════════════════════════════╝
                        │
                        v
                  Order Execution
                  (or NO_TRADE)
```

---

## FROZEN ELEMENTS

### 1. Serial Architecture (Immutable)

**Rule 1: No shortcuts**
```
FROZEN:  Model → PA → ID → ID→MPC Packet → MPC → P01D
FORBIDDEN: Model → PA → MPC (direct path)
```

**Rule 2: Immutable provenance**
- Every component validates exact input format
- SHA256 hashes track input state through pipeline
- Mismatch = HALT (fail-closed)

**Rule 3: No execution authority at PA/ID/MPC**
- PA: can predict, cannot authorize
- ID: can assess, cannot authorize
- MPC: can optimize, cannot authorize
- **Only P01D can authorize execution**

---

## UNFROZEN CONFIGURATION POINTS
### (12 Critical Questions for Discussion)

---

## TOPIC 1: PA CALIBRATION METHOD

**Current State:**
- Model 0/1 output raw prediction values
- PA must convert to calibrated P(DOWN/FLAT/UP) probabilities
- Calibration is mandatory per architecture freeze

**Options:**

**A. Platt Scaling (Recommended for simplicity)**
```python
p(class) = 1 / (1 + exp(A*score + B))
# Requires validation data to fit A, B
# Can become stale if distribution shifts
```

**B. Isotonic Regression (Recommended for robustness)**
```python
# Non-parametric calibration
# More flexible than Platt
# Handles multi-modal distributions better
```

**C. Temperature Scaling (Modern, simple)**
```python
p(class) = softmax(score / T)
# Single parameter T to learn
# Works well for deep neural networks
```

**Discussion Points:**
- Do we have enough validation data to fit calibration curve?
- Should calibration be symbol-specific or universal?
- How often should calibration curve be updated? (never / quarterly / data-driven drift?)
- What metric to optimize for calibration? (log-loss / Brier score / ECE?)

**Recommendation for Discussion:**
Start with **Isotonic Regression** (most robust), validate on holdout, freeze for 6 months.

---

## TOPIC 2: PA PREDICTION HORIZON

**Current State:**
- Models trained on price changes over 1-minute interval
- PA must specify what future state it's predicting

**Options:**

**A. Ultra-short (next bar, 1-minute)**
- Pros: Matches training data frequency
- Cons: Noisy, high transaction costs
- Market impact: Immediate (spreads, impact)

**B. Short-term (5-minute to 30-minute)**
- Pros: Reduced noise, more actionable
- Cons: Longer latency before signal becomes clear
- Market impact: Moderate (book walk)

**C. Intra-day (end-of-day)**
- Pros: Clear signal, sufficient time to execute
- Cons: Misses intra-day alpha
- Market impact: Predictable (EOD volume)

**Discussion Points:**
- What time horizon makes sense for NSE liquidity?
- What execution latency can we achieve (P01D)?
- Should PA output multi-horizon forecasts (1-min AND 5-min AND EOD)?
- Does prediction horizon change by symbol liquidity?

**Recommendation for Discussion:**
Start with **1-minute horizon** (matches training data), prepare 5-minute version for Phase 6.

---

## TOPIC 3: ID RELIABILITY ASSESSMENT

**Current State:**
- ID receives PA forecast + market state
- Must decide whether PA's forecast is reliable enough to act on

**Options:**

**A. Recent PA Performance (Adaptive)**
- Track recent PA accuracy on validation window
- TAKE if recent accuracy > threshold
- PASS if accuracy falling
- Risk: Can be whippy; may chase performance

**B. Forecast Confidence Score (Static)**
- Use PA's confidence output directly
- TAKE if confidence > threshold (e.g., 70%)
- PASS otherwise
- Risk: Confidence ≠ accuracy

**C. Regime Stability Check (Contextual)**
- Check if current regime matches training regime
- TAKE if regime "stable"
- PASS if regime "uncertain" or "changing"
- Risk: Regime detection is hard; can be lagged

**D. Hybrid (Recommended)**
- Combine: recent accuracy + confidence + regime stability
- Weight each component
- TAKE if weighted score > threshold
- Risk: More complex, more parameters to tune

**Discussion Points:**
- How to measure "recent accuracy"? (last N trades / last N minutes / rolling window?)
- Should different symbols have different thresholds?
- What happens when ID rejects everything? (too conservative)
- What happens when ID accepts everything? (too aggressive)
- How often should ID threshold be updated?

**Recommendation for Discussion:**
Start with **hybrid approach** (confidence + recent accuracy + regime), freeze thresholds for 6 months.

---

## TOPIC 4: ID TAKE/PASS THRESHOLD

**Current State:**
- Architecture allows TAKE/PASS (binary decision)
- No generic 0.90 threshold authorized (must be data-derived)

**Options:**

**A. Conservative (Pass frequently, trade rarely)**
- Threshold: 80% reliability
- Pros: Fewer false positives, higher precision
- Cons: Miss many opportunities, low recall
- Best for: Capital preservation mode

**B. Balanced (Moderate acceptance)**
- Threshold: 60% reliability
- Pros: Reasonable trade-off
- Cons: Still many rejected signals
- Best for: Normal operation

**C. Aggressive (Trade frequently, pass rarely)**
- Threshold: 40% reliability
- Pros: High recall, capture more opportunities
- Cons: More false positives, lower precision
- Best for: High-conviction alpha

**Discussion Points:**
- Should threshold be fixed or dynamic?
- Different thresholds for long vs. short signals?
- Different thresholds for different symbols?
- How does threshold interact with position sizing?

**Recommendation for Discussion:**
Start with **60% reliability threshold (balanced)**, plan to test 40% and 80% variants.

---

## TOPIC 5: EXPECTED-RETURN BRIDGE FORMULA

**Current State:**
- Architecture explicitly rejects P(UP)-P(DOWN) as automatic default
- Bridge must be empirically validated on our data
- Must account for costs

**Options:**

**A. Simple Directional (Baseline)**
```
expected_return = (P(UP) - P(DOWN)) * fixed_magnitude
= Risk: Assumes equal price move for UP vs DOWN
```

**B. Magnitude-Adjusted (Moderate)**
```
expected_return = P(UP) * magnitude_up - P(DOWN) * magnitude_down
# Requires training magnitude predictor
= More realistic, but adds complexity
```

**C. Regime-Conditional (Advanced)**
```
expected_return = regime_model[market_regime](P(dir)) * leverage[regime]
# Different formula for volatile/calm/trending regimes
= Best performance, but most complex
```

**D. Cost-Adjusted (Conservative)**
```
gross_return = P(UP) * upside - P(DOWN) * downside
expected_return = gross_return - spread_cost - impact_cost - holding_cost
= Most realistic, required for MPC
```

**Discussion Points:**
- Do we have magnitude predictions from Model 0/1?
- Should bridge use Model 0 and Model 1 separately or combine them?
- How to validate bridge without consuming holdout? (cross-validation on training data)
- What's the relationship between prediction horizon and return magnitude?

**Recommendation for Discussion:**
Start with **Cost-Adjusted Bridge** (Option D): gross P(dir)-based return minus estimated execution costs.

---

## TOPIC 6: MPC RISK PENALTY COEFFICIENT (λ_risk)

**Current State:**
- MPC optimization: maximize(expected_return) - λ_risk * risk - transaction_costs
- λ_risk weights how much risk aversion MPC should apply

**Options:**

**A. Conservative (High risk aversion)**
- λ_risk = 2.0 (very strong risk penalty)
- Smaller positions, lower volatility
- Fewer trades (lower turnover)
- Best for: Drawdown control

**B. Moderate (Balanced)**
- λ_risk = 1.0 (equal weight to return and risk)
- Medium-sized positions
- Reasonable volatility
- Best for: Normal operation

**C. Aggressive (Low risk aversion)**
- λ_risk = 0.5 (weak risk penalty)
- Larger positions, higher volatility
- More turnover
- Best for: Growth-oriented

**D. Dynamic (Data-driven)**
- Adjust λ_risk based on current drawdown
- Increase λ during losses, decrease during gains
- Risk: Can create whipsaw behavior

**Discussion Points:**
- How does λ_risk interact with position limits?
- Should λ_risk be symbol-specific?
- How to measure "risk" in NSE context? (volatility / max drawdown / VaR?)
- Should λ_risk adjust for intra-day vs. overnight risk?

**Recommendation for Discussion:**
Start with **λ_risk = 1.0 (balanced)**, plan to test 0.5 and 2.0 variants in Phase 6.

---

## TOPIC 7: POSITION LIMITS

**Current State:**
- MPC must respect hard constraints
- Architecture supports per-symbol and portfolio-level limits

**Options:**

**A. Per-Symbol Limits (Diversification)**
```
Max position per symbol = Notional / N_symbols
= Ensures no single symbol dominates
= Example: If capital = 1M and N=8, max per symbol = 125K
```

**B. Notional Portfolio Limits (Leverage control)**
```
Max total notional = K × capital
= K=1.0: no leverage
= K=2.0: 2x leverage (borrow cash)
= Risk: Leverage = magnifies both gains and losses
```

**C. VaR-Based Limits (Risk control)**
```
Max position such that 1-day 95% VaR < 2% of capital
= Position sized to risk, not capital
= More sophisticated, less mechanical
```

**Discussion Points:**
- Should position size be fixed (e.g., 1M notional) or adaptive?
- Different limits for different symbols?
- What happens when MPC wants to trade but hits limit?
- Should limits be tight (conservative) or loose (aggressive)?

**Recommendation for Discussion:**
Start with **per-symbol limits**: max 20% of capital per symbol, max 100% total.

---

## TOPIC 8: TURNOVER CONSTRAINTS

**Current State:**
- MPC can be constrained to limit trading frequency
- Prevents over-trading and excessive costs

**Options:**

**A. Daily Turnover Limit (Most common)**
```
Max daily turnover = K × capital
= K=0.5: can trade up to 50% of portfolio per day
= K=1.0: can trade entire portfolio per day
= Prevents excessive trading
```

**B. Per-Trade Limit (Mechanical)**
```
Max size per trade = cap_per_trade (e.g., 10M notional)
= Simple, easy to enforce
= May cut short promising trades
```

**C. Participations Rate Limit (Liquidity-aware)**
```
Max participation = K% of 1-minute volume
= K=5: never take more than 5% of volume in 1 minute
= Prevents market impact
= Requires volume forecasts
```

**D. No Constraint (Free trading)**
```
Let MPC optimize without turnover limit
= Risk: May over-trade, high costs
= Benefit: Captures all alpha
```

**Discussion Points:**
- Is turnover a cost or a risk control?
- Should turnover be daily or intra-day cumulative?
- Different limits for different symbols?
- What if model predicts sudden large move? Override constraint?

**Recommendation for Discussion:**
Start with **0.5x daily turnover limit** (can trade up to 50% of capital per day).

---

## TOPIC 9: COST MODEL CALIBRATION

**Current State:**
- NSE cost model NOT YET DEFINED
- Architecture requires: spread + book walk + impact + volatility adjustment

**Required Components:**

**A. Spread (Fixed cost)**
```
Cost = (ask - bid) / 2 / price
= For NSE, typical: 1-3 bps depending on liquidity
= More liquid symbols: lower spread
= Less liquid symbols: higher spread
```

**B. Book Walk (Partially-liquid cost)**
```
Cost to execute through visible top-5 orders
= Depends on order size vs. available depth
= Can be measured from historical L2 data
= Estimated cost if order walks top-5
```

**C. Impact (Market move due to our order)**
```
Cost = f(size, volatility, volume)
= Larger orders have larger impact
= More volatile: larger impact
= Higher volume days: smaller impact
```

**D. Holding Cost (Opportunity cost over time)**
```
Cost = alpha_per_minute × minutes_held
= If we hold position while market moves against us
= Needs alpha quantification
```

**Discussion Points:**
- Do we have enough L2 history to calibrate? (Sep 1-Aug 2026 data exists)
- Symbol-specific cost models? (Yes, liquidity varies)
- Should cost model update weekly/monthly? (Weekly recommended)
- Impact model: use power law or piecewise linear?

**Recommendation for Discussion:**
Start with **empirical cost calibration** on available L2 data (Aug 2025-Aug 2026).

---

## TOPIC 10: MPC RE-OPTIMIZATION FREQUENCY

**Current State:**
- MPC solves at some cadence, executes first action, re-solves at next update

**Options:**

**A. Every Minute (Real-time)**
- MPC solves every 60 seconds
- Very responsive to new signals
- Risk: May over-trade, high computational cost
- Best for: Intra-day alpha

**B. Every 5 Minutes (Balanced)**
- MPC solves every 5 minutes
- Reasonable responsiveness
- Moderate computational cost
- Best for: Balanced operation

**C. End-of-Day (Batch)**
- MPC solves once per day (e.g., 15:00 IST)
- Very predictable, low cost
- Risk: Misses intra-day alpha
- Best for: Strategic positioning

**Discussion Points:**
- What latency can we achieve? (depends on P01D, exchange)
- Does cadence depend on model horizon? (1-min predictions → 1-min re-opt?)
- Should cadence be fixed or adaptive?
- Computational cost: how many times per day can MPC solve?

**Recommendation for Discussion:**
Start with **5-minute re-optimization** (balance of responsiveness and cost).

---

## TOPIC 11: MULTI-POSITION SUPPORT

**Current State:**
- Current architecture allows one position at a time
- Question: Should Phase 5 support multiple concurrent positions?

**Options:**

**A. Single Position (Current, Simpler)**
- One trade at a time
- Close old, open new
- Risk: Can be forced to exit good positions to enter new ones
- Benefit: Simple, less coordination needed

**B. Multi-Position (More complex, higher alpha potential)**
- Up to N concurrent positions
- Can maintain multiple trades
- Requires multi-dimensional covariance matrix
- Benefit: Richer alpha capture

**Discussion Points:**
- Does NSE data support multi-position modeling? (Need 48×48 covariance)
- Should each symbol have max 1 position or could have long+short?
- What's the maximum number of concurrent positions?
- How to handle portfolio-level risk across positions?

**Recommendation for Discussion:**
Keep **single-position architecture for Phase 5**, plan multi-position for Phase 6+.

---

## TOPIC 12: P01D INTEGRATION

**Current State:**
- P01D remains sovereign safety boundary
- MPC→P01D handoff is normalization only

**Discussion Points:**

**A. Any new safety rules for Phase 5?**
- Should P01D have stronger/weaker constraints?
- New halt conditions? (e.g., "MPC recommended same action 3 times in a row"?)
- New cooldown periods? (e.g., "wait 5 min after any trade")
- New drawdown limits?

**B. Should P01D understand PA/ID/MPC internals?**
- Current: P01D gets normalized position + provenance hash
- Future: Should P01D inspect PA confidence / ID reliability / MPC optimization quality?
- Risk: Tighter coupling, harder to maintain separation of concerns

**C. Any live-trading credentials or safety switches needed?**
- Current: LIVE_TRADING_ENABLED hardcoded False
- Question: Should this flag live in Phase 5 or stay in P01D?
- Current plan: Keep False until Oct 31, 2026

**Recommendation for Discussion:**
Keep P01D unchanged for Phase 5, maintain sovereign authority, add monitoring hooks.

---

## SUMMARY OF CONFIGURATION DECISIONS

### Required Before Phase 5B Coding (40-50 hours)

| Topic | Option | Status | Notes |
|-------|--------|--------|-------|
| PA Calibration | Isotonic Regression | Recommended | Robust, non-parametric |
| Horizon | 1-minute | Recommended | Matches training data |
| ID Reliability | Hybrid (confidence + accuracy + regime) | Recommended | Balanced approach |
| ID Threshold | 60% | Recommended | Start balanced, test 40%/80% |
| Expected Return | Cost-Adjusted (P(dir) - costs) | Recommended | Most realistic |
| λ_risk | 1.0 | Recommended | Balanced risk aversion |
| Position Limits | 20% per symbol, 100% total | Recommended | Diversified |
| Turnover | 0.5x daily | Recommended | Prevents over-trading |
| Cost Model | Empirical calibration (NSE) | Required | Use Aug 2025-2026 L2 data |
| Re-opt Frequency | 5-minute | Recommended | Balance responsiveness/cost |
| Multi-position | NO (single position) | Recommended | Simpler for Phase 5 |
| P01D Changes | None (maintain sovereignty) | Recommended | Keep separation of concerns |

---

## TIMELINE

**Sep 1-2:** User discussion + configuration decision (4 hours)
**Sep 3-20:** Phase 5B coding + testing (40-50 hours machine work)
**Sep 21-30:** Integration validation + freeze (10 hours)

---

## NEXT ACTION

**User to decide:**
1. For each of the 12 topics above, approve the "Recommended" option or specify alternative
2. Once configuration is locked, Phase 5B development begins immediately
3. All configuration decisions are preregistered and frozen until Phase 6

---

**Prepared:** Aug 28, 2026  
**Author:** Claude Haiku 4.5  
**Status:** AWAITING USER DECISION
