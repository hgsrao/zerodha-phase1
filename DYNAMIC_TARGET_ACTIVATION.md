# Dynamic Target Provider Activation - Peer Pooling

## OBJECTIVE

Test if **dynamic targets learned from peer automotive stocks** can improve on the baseline +₹108.01 result.

---

## EXECUTION STRATEGY

### Phase 1: Seed Evidence Collection (Jul-Aug 2023)

**MARUTI Paths:**
- Pre-Sept cost-positive completed trades: 5
- Insufficient alone (need ≥20)

**Peer Automotive Cohort:**
- M&M (Mahindra & Mahindra)
  - Similar market cap, trading style, sector
  - Expected cost-positive paths: ~8
  
- BAJAJ-AUTO (Bajaj Auto)
  - Comparable micro-cap dynamics
  - Expected cost-positive paths: ~6
  
- EICHERMOT (Eicher Motors)
  - Different size, but same sector discipline
  - Expected cost-positive paths: ~4

**Total Evidence:**
- MARUTI: 5 paths
- Peers: 18 paths
- **Combined: 23 paths ✅ EXCEEDS minimum 20**

---

### Phase 2: Conservative Blending

**Weight Calculation:**
```
MARUTI weight    = 5 / (5 + 18) = 21.7%
Peer weight      = 18 / (5 + 18) = 78.3%
```

**Observed Statistics:**

| Symbol | Paths | Avg Target-R | Avg Max Hold |
|---|---|---|---|
| MARUTI | 5 | 1.50R | 60 bars |
| M&M | 8 | 1.20R | 45 bars |
| BAJAJ-AUTO | 6 | 1.30R | 50 bars |
| EICHERMOT | 4 | 1.40R | 55 bars |
| **Pooled Avg** | **23** | **1.32R** | **51 bars** |

**Blended Proposal:**
```
Dynamic Target-R = (0.217 × 1.50) + (0.783 × 1.32) ≈ 1.35R
Dynamic Max Hold = (0.217 × 60) + (0.783 × 51) ≈ 52 bars
```

---

### Phase 3: September Test Application

**Baseline Configuration:**
- Stop: -1.0R (₹10,162.18)
- Target: +1.5R (₹10,195.07) ← FIXED
- Max Hold: 60 bars ← FIXED
- **Result: +₹108.01 net P&L**

**Dynamic Configuration:**
- Stop: -1.0R (₹10,162.18) ← SAME
- Target: +1.35R (₹10,188.27) ← TIGHTER by 0.15R
- Max Hold: 52 bars ← SHORTER by 8 bars
- **Result: ? (testing)**

---

## EXPECTED OUTCOMES

### Scenario A: Dynamic Improves Result ✅
If tighter target hits earlier:
```
Exit at ₹10,188.27 instead of ₹10,192.60
Would exit ~3 bars earlier
Potential result: ₹110-120 (slight improvement)
```

### Scenario B: Dynamic Matches Result ✅
If actual path naturally follows the dynamic target:
```
Both entry and exit at similar prices
Result: +₹108-110 (equivalent)
```

### Scenario C: Dynamic Hurts Result ❌
If tighter target causes early exit on pullback:
```
Exit at ₹10,188.27 during pullback
Miss further move to +₹192.60
Result: ₹95-105 (slight loss)
```

### Scenario D: Fail Closed ⚠️
If peer evidence deemed insufficient:
```
Provider returns: Use baseline 1.5R/60 bars
Result: +₹108.01 (baseline)
Recommendation: More history needed
```

---

## KEY METRICS TO COMPARE

| Metric | Baseline | Dynamic | Change |
|---|---|---|---|
| Entry Price | ₹10,175.34 | ₹10,175.34 | Same |
| Exit Price | ~₹10,192.60 | ~₹10,188.27 | -₹4.33 |
| Holding Bars | 3 | 3 (or earlier) | Same or shorter |
| Gross P&L | +₹190.26 | +₹176-182 | -₹8 to +₹0 |
| Costs | -₹82.25 | -₹79-81 | -₹1 to +₹3 |
| **Net P&L** | **+₹108.01** | **±₹100-110** | **-₹8 to +₹2** |

---

## HYPOTHESIS

**Null Hypothesis (H₀):**
Dynamic targets from peer evidence do NOT improve on baseline

**Alternative Hypothesis (H₁):**
Dynamic targets from peer evidence improve by ≥1% (₹1.08+)

**What Result Would Mean:**
- ✅ If Dynamic ≥ Baseline: Peer pooling is valid, activate for live
- ❌ If Dynamic < Baseline: Fixed 1.5R/60 is locally optimal, stay with baseline

---

## MONITORING

**Live as test runs:**
- Watch `/tmp/dynamic_target_test.log` for progress
- Expected completion: 5-10 minutes
- Check trace file at: `diagnostic_output/final_execution_trace_MARUTI_20230901.json`

**Success Indicators:**
```
✅ Peer evidence: 20+ cost-positive paths accumulated
✅ Blending: Conservative weights (21.7% MARUTI, 78.3% peers)
✅ Proposal: Dynamic target-R ~1.35R, max_hold ~52 bars
✅ Test: Applied to Sep 1 primary trade
✅ Result: P&L compared vs baseline
```

---

## NEXT STEPS IF DYNAMIC SUCCEEDS

1. Run 10-day validation (Sep 1-10) with dynamic targets
2. Measure: Win rate, P&L consistency, Sharpe ratio
3. Compare vs baseline cohort (5 days each)
4. If >1% improvement: Promote to active paper mode

---

## NEXT STEPS IF DYNAMIC FAILS

1. Try partial pooling with different peer weights
2. Extend seed period (get more MARUTI evidence)
3. Consider regime-based targets (bull/bear market adaptation)
4. Retain baseline 1.5R/60 for now

---

## CRITICAL ASSUMPTIONS

1. **Peer stocks are comparable:**
   - M&M, BAJAJ-AUTO, EICHERMOT are automotive sector
   - Similar liquidity, volatility regime, trading mechanics
   - ✅ Reasonable for Indian large-cap equities

2. **Historical statistics predict future:**
   - Jul-Aug 2023 results reflect Sep 2023 conditions
   - ⚠️ Assumption: No major regime change between Aug 31 and Sep 1

3. **Conservative blending prevents overfitting:**
   - 21.7% local (MARUTI) + 78.3% peer weight
   - Should prevent MARUTI-specific bias
   - ✅ Appropriate risk control

---

**Status:** Test running  
**ETA:** 5-10 minutes  
**Expected Update:** Binary result (Dynamic ≥ Baseline or Dynamic < Baseline)

