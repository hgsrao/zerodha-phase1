# Decision Tree: Dynamic Targets vs Baseline

## PRIMARY DECISION: Sep 1 Test Result

### Path A: Dynamic Wins ✅ (Net P&L > ₹108.01)

**Outcome:** Peer-pooled targets improved on baseline

**Immediate Action:**
```
Launch: python3 run_walkforward_validation.py
Runtime: 30-45 minutes
Tests: Sep/Oct/Nov/Dec/Jan (5 months × 2 modes)
```

**What to Monitor:**
- Dynamic wins at least 3 of 5 months
- No catastrophic losses in any month
- Consistent improvement across regimes

**If Walk-Forward Confirms Dynamic:**
```
✅ PROMOTE TO ACTIVE PAPER MODE
├─ 1% position size (risk capital)
├─ Daily monitoring of P&L vs forecast
├─ Weekly rebalancing check
├─ Monthly validation against seed evidence
└─ Quarterly full re-validation with new data
```

**If Walk-Forward Shows Dynamic Inconsistent:**
```
⚠️ HYBRID APPROACH
├─ Use baseline (1.5R/60) as default
├─ Use dynamic (1.35R/52) when confidence high
├─ Regime-based override (bull vs bear targets)
└─ Continue research to improve dynamic model
```

---

### Path B: Dynamic Loses ❌ (Net P&L < ₹108.01)

**Outcome:** Peer-pooled targets underperformed baseline

**Immediate Action (Pick One):**

#### Option B1: Debug & Retry
```
Adjust peer weighting:
  Current: 21.7% MARUTI + 78.3% peers
  Try:     30% MARUTI + 70% peers (more local weight)
  
Expected impact:
  - Shifts dynamic target toward 1.4R (vs 1.35R)
  - Shifts max_hold toward 55 bars (vs 52 bars)
  - Closer to baseline but slightly tighter
  
Retry on Sep 1 test with adjusted weighting
```

#### Option B2: Change Peer Cohort
```
Current peers: M&M, BAJAJ-AUTO, EICHERMOT (all automotive)
Try alternative: TCS, INFY, WIPRO (IT sector instead)

Reasoning:
  - MARUTI may have sector-specific dynamics
  - IT sector has different volatility regime
  - Test if cross-sector pooling weakens or strengthens model
  
Retry walk-forward with new peers
```

#### Option B3: Extend Seed Period
```
Current: Jul 3 - Aug 31 (2 months)
Try:     Jan 1 - Aug 31 (8 months)

Reasoning:
  - More data = more stable statistics
  - Better representation of different regimes within sector
  - Addresses "insufficient evidence" concern
  
Re-run dynamic provider with longer seed
```

#### Option B4: Accept Baseline
```
Keep: 1.5R / 60 bars (proven +₹108.01)

Reasoning:
  - Baseline is validated and safe
  - Dynamic is too speculative without more evidence
  - Live trading capital should not fund continuous optimization
  
Proceed to active paper with baseline configuration
```

---

### Path C: Dynamic Ties (Net P&L ≈ ₹108.01)

**Outcome:** Peer-pooled targets matched baseline

**Immediate Action:**
```
Launch: python3 run_walkforward_validation.py
Purpose: Test stability across different market regimes
Expectation: Determine if tie is consistent or luck
```

**If Walk-Forward Shows Consistent Tie:**
```
✅ EITHER APPROACH ACCEPTABLE
Choose based on risk tolerance:
  Dynamic = more aggressive (tighter target, shorter hold)
  Baseline = more conservative (looser target, longer hold)
  
Proceed with whichever matches risk appetite
```

**If Walk-Forward Shows Dynamic Wins Some, Loses Some:**
```
⚠️ REGIME-BASED APPROACH
├─ Detect market regime (bull/bear/sideways)
├─ Use dynamic in bullish regimes (works better)
├─ Use baseline in bearish regimes (safer)
└─ Switch dynamically based on HMM regime
```

---

## WALK-FORWARD INTERPRETATION GUIDE

### If Dynamic Wins 4-5 Months:
```
✅✅ STRONG SIGNAL
Dynamic is clearly superior
Promote immediately to active paper
No iteration needed
```

### If Dynamic Wins 2-3 Months:
```
⚠️ MIXED SIGNAL
Dynamic works in some regimes, not others
Implement regime-based switching
Or: Fine-tune peer weights and retry
```

### If Dynamic Wins 0-1 Months:
```
❌❌ WEAK SIGNAL
Baseline is consistently better
Abandon dynamic approach (for now)
Keep baseline 1.5R/60 for live trading
Research alternative methods
```

---

## FULL DECISION TREE FLOWCHART

```
START: Sep 1 Dynamic Test
  │
  ├─ Result > +₹108.01 ✅
  │   └─ Launch Walk-Forward Validator
  │       ├─ Dynamic Wins 4-5 months → PROMOTE TO PAPER ✅
  │       ├─ Dynamic Wins 2-3 months → REGIME-BASED SWITCHING ⚠️
  │       └─ Dynamic Wins 0-1 months → KEEP BASELINE ❌
  │
  ├─ Result ≈ +₹108.01 =
  │   └─ Launch Walk-Forward Validator
  │       ├─ Consistent tie → EITHER ACCEPTABLE =
  │       ├─ Regime-dependent → REGIME-BASED SWITCHING ⚠️
  │       └─ Highly variable → KEEP BASELINE ❌
  │
  └─ Result < +₹108.01 ❌
      ├─ Option B1: Adjust peer weights (21.7% → 30% MARUTI)
      ├─ Option B2: Change peer cohort (IT sector instead)
      ├─ Option B3: Extend seed period (2 mo → 8 months)
      └─ Option B4: Accept baseline (1.5R/60 proven safe)
```

---

## FINAL DECISION MATRIX

| Sep 1 Result | Walk-Forward | Action | Confidence |
|---|---|---|---|
| Dynamic Wins | 4-5 mo wins | Promote to paper | Very High ✅✅ |
| Dynamic Wins | 2-3 mo wins | Regime switching | Medium ⚠️ |
| Dynamic Wins | 0-1 mo wins | Debug & retry | Low ❌ |
| Dynamic Ties | Consistent | Either acceptable | High ✅ |
| Dynamic Ties | Variable | Regime switching | Medium ⚠️ |
| Dynamic Loses | (Any) | Debug option B1-4 | Low-Med ⚠️ |

---

## TIMELINE UNTIL DECISION

```
Now:          Sep 1 dynamic test completing (5-15 min)
         ↓
T+0 min:      Get Sep 1 result
         ↓
T+5 min:      Decision: Which path (A/B/C)?
         ↓
T+10 min:     If Path A or C: Launch walk-forward
              If Path B: Choose debug option
         ↓
T+50 min:     Walk-forward complete
         ↓
T+55 min:     Final verdict: PROMOTE / ITERATE / KEEP BASELINE
         ↓
T+60 min:     Either: Active paper (Path A success)
              Or: Debug cycle (Path B)
              Or: Regime switching setup (Path C mixed)
```

---

## LIVE TRADING READINESS CHECKLIST

### Before Active Paper ✅
- [ ] Sep 1 baseline validated (+₹108.01)
- [ ] Configuration ported to in-house
- [ ] Walk-forward test shows consistent wins
- [ ] No catastrophic losses in any month
- [ ] Position sizing determined (risk ≤ 1%)
- [ ] Broker connection tested
- [ ] Daily P&L monitoring configured
- [ ] Stop-loss enforcement verified

### Before Regime Switching ⚠️
- [ ] HMM regime detection running
- [ ] Bull/bear thresholds calibrated
- [ ] Dynamic targets frozen (Sep-Jan evidence)
- [ ] Baseline ready as fallback
- [ ] Switching logic tested in paper

### Before Rejecting Dynamic ❌
- [ ] All debug options attempted (B1-B4)
- [ ] Walk-forward confirms consistent underperformance
- [ ] Baseline clearly superior (3+ month wins)
- [ ] Research directions identified for future

---

**Status:** Awaiting Sep 1 dynamic test completion (should be within next 10 minutes)

**Next Message:** Will show Sep 1 result → Determine which path → Execute accordingly

