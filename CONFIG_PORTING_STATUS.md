# Configuration Porting Status: External → In-House

## ✅ FIXED

### 1. Stop Loss Multiplier
**Issue:** In-house had 1.2, external used 1.0  
**Fix:** Changed canonical_parameter_registry.py line 127  
```python
# BEFORE:
ParameterSpec("stop_loss_atr_mult", "MPC", "float", 1.2, ...)

# AFTER:
ParameterSpec("stop_loss_atr_mult", "MPC", "float", 1.0, ...)
```
**Impact:** Stop price now ₹10,162.18 (matches external +₹108.01 trade)

---

## ✅ VERIFIED (Already Correct)

### 2. Entry PID Kp
- External: 0.150000 ✅
- In-house: 0.15 ✅
- Status: MATCH

### 3. Profit Target Multiplier
- External: 1.5R ✅
- In-house: 1.50 ✅
- Status: MATCH

### 4. Minimum Hold Bars
- External: 2 bars ✅
- In-house: 2 bars ✅
- Status: MATCH

### 5. Maximum Hold Bars
- External: 60 bars ✅
- In-house: 60 bars ✅
- Status: MATCH

---

## ⏳ LEARNED (Not Fixed, Will Learn from Seed)

### Study Weights
**External result:** [0.40, 0.10, 0.1817, 0.40]  
**How it works:** These weights are LEARNED from seed period (July-Aug 2023)

**Process:**
1. Orchestrator loads July-Aug 2023 data
2. For each bar, studies vote + get graded (correct/incorrect)
3. Hit rates accumulated over _HIT_RATE_WINDOW (20 bars)
4. Study PIDs adjust weights based on hit rates
5. By end of seed, weights converge to [0.40, 0.10, 0.1817, 0.40]

**Requirement:** Orchestrator must seed from EXACT same period:
- Start: 2023-07-03
- End: 2023-08-31 (before Sept test)
- Must process all bars through composite_study_signal.py

**Status:** Will be learned automatically IF orchestrator uses same seed period

---

## ACTION REQUIRED

To reproduce +₹108.01 result in orchestrator:

1. ✅ **DONE:** Fixed stop_loss_atr_mult (1.2 → 1.0)

2. ⏳ **TODO:** Run orchestrator with matching seed period:
   ```
   seed_start: 2023-07-03
   seed_end:   2023-08-31
   test_date:  2023-09-01
   ```

3. ⏳ **TODO:** Verify orchestrator learns same study weights:
   - Ichimoku: 0.40 (should converge here)
   - Bollinger: 0.10 (should converge here)
   - Stochastic: 0.1817 (should converge here)
   - Session VWAP: 0.40 (should converge here)

4. ⏳ **TODO:** Run orchestrator on 2023-09-01 and compare:
   - Entry: ₹10,175.34 ✓
   - Stop: ₹10,162.18 ✓
   - Target: ₹10,195.07 ✓
   - Exit: ₹10,192.60 ✓
   - Net P&L: +₹108.01 ✓

---

## EXPECTED OUTCOME

If orchestrator uses:
- ✅ Seed: July-Aug 2023
- ✅ Test: 2023-09-01
- ✅ Constraints: stop_loss_atr_mult = 1.0 (NOW FIXED)
- ✅ All other params matching

**Then:** Orchestrator should produce **+₹108.01 net P&L** on primary trade

---

## Configuration Summary Table

| Parameter | External | In-House | Status |
|---|---|---|---|
| stop_loss_atr_mult | 1.0R | 1.0R | ✅ FIXED |
| profit_target_atr_mult | 1.5R | 1.5R | ✅ OK |
| pid_kp_entry | 0.15 | 0.15 | ✅ OK |
| min_hold_bars | 2 | 2 | ✅ OK |
| max_hold_bars | 60 | 60 | ✅ OK |
| seed_period | 2023-07-03 → 2023-08-31 | TBD | ⏳ CHECK |
| ichimoku_weight (learned) | 0.40 | Will learn | ⏳ AUTO |
| bollinger_weight (learned) | 0.10 | Will learn | ⏳ AUTO |
| stochastic_weight (learned) | 0.1817 | Will learn | ⏳ AUTO |
| session_vwap_weight (learned) | 0.40 | Will learn | ⏳ AUTO |

---

Generated: 2026-09-13  
Next: Run orchestrator with corrected config
