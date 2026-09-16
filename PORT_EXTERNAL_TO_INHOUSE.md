# Port External Engine → In-House Engine

## OBJECTIVE
The external engine just proved it can generate +₹108.01 net P&L on 2023-09-01 MARUTI primary trade.  
**Goal:** Reproduce this result in the in-house orchestrator.

---

## CONFIRMED WORKING CONFIGURATION (External)

### 1. Entry PID Parameters
```
Setpoint (rolling):  0.5349 (rolling mean of symbol's own recent confidence)
Measurement:         0.6336 (PA confidence on signal)
Error:              -0.0987 (negative = strong signal, tight entry bias)

Gains Applied:
  Kp:  ? (need to extract from trace)
  Ki:  ? (need to extract from trace)
  Kd:  ? (need to extract from trace)

Actual Outputs:
  P term:  -0.0148
  I term:  -0.0095
  D term:  -0.0125
  Total:   -0.0371 (clamped or raw)
```

### 2. Study Weights (External) ✅
```
Ichimoku:      40.00%
Bollinger:     10.00%
Stochastic:    18.17%
Session VWAP:  40.00%
```

### 3. Study Hit Rates (External) ✅
```
Ichimoku:      75%
Bollinger:     60%
Stochastic:    60%
Session VWAP:  75%
```

### 4. Safety Constraints (External) ✅
```
Stop:          -1.0R  (₹10,162.18)
Target:        +1.5R  (₹10,195.07)
Min Hold:      2 bars
Max Hold:      60 bars
Reward:Risk:   1.29x (target distance / stop distance)
```

### 5. Entry Decision Logic (External) ✅
```
PA confidence:      0.6336 (moderate-strong)
Chart studies vote: 4/4 BUY (unanimous)
Quality band:       green (bullish)
Direction:          +1 (long)

Decision: ADMIT → Submit order at ₹10,175.34
```

### 6. Cost Model (External) ✅
```
Gross P&L:   +₹190.26
Friction:    -₹82.25
Net P&L:     +₹108.01

Implied friction cost: 0.27R ≈ 8 bps (matches documented model)
```

---

## IN-HOUSE ENGINE CONFIGURATION (orchestrator.py)

### Current Entry PID Gains (from canonical_parameter_registry.py)
```
pid_kp_entry:  0.15  (range: 0.05-0.30)  ← Potentially DIFFERENT from external
pid_ki_entry:  0.05  (range: 0.01-0.20)  ← Potentially DIFFERENT from external
pid_kd_entry:  0.08  (range: 0.01-0.20)  ← Potentially DIFFERENT from external
```

### ❌ PROBLEM: We don't know the external engine's exact PID gains
The external engine used different Kp/Ki/Kd values, which produced:
- P: -0.0148 from error = -0.0987
- This implies Kp ≈ 0.15 (similar to in-house)
- But Ki and Kd may differ

---

## ACTION PLAN: Port External → In-House

### STEP 1: Instrument External Engine to Export PID Gains
**File:** run_external_no_pid_one_signal_trace.py
**Action:** Before running 2023-09-01 test, log the actual Kp/Ki/Kd used

```python
# In the final execution trace, add:
"pid_gains_used": {
    "entry_kp": <actual value>,
    "entry_ki": <actual value>,
    "entry_kd": <actual value>,
    "exit_kp": <actual value>,
    "exit_ki": <actual value>,
    "exit_kd": <actual value>,
}
```

### STEP 2: Extract & Match Study Weights
**External weights (confirmed working):**
- Ichimoku: 0.4
- Bollinger: 0.1
- Stochastic: 0.1817
- Session VWAP: 0.4

**In-house check:**
Search orchestrator.py for study weight initialization and set to match.

### STEP 3: Extract & Match Study Hit Rates
**External hit rates (confirmed working):**
- Ichimoku: 0.75
- Bollinger: 0.60
- Stochastic: 0.60
- Session VWAP: 0.75

**In-house check:**
If these are learned from seed data, verify seed period is July-Aug 2023 (same as external).

### STEP 4: Extract & Match Safety Constraints
**External (confirmed working):**
- Stop: -1.0 × ATR
- Target: +1.5 × ATR
- Min hold: 2 bars
- Max hold: 60 bars

**In-house check:**
Verify canonical registry has same values.

### STEP 5: Test Side-by-Side
**Create comparison test:**
```python
# Run external engine: 2023-09-01 (primary signal only)
# Run in-house engine: 2023-09-01 (same signal)
# Compare:
#   - Entry decision (both should be ADMIT)
#   - Entry price (both should be ₹10,175.34)
#   - Exit decision (both should be HOLD→EXIT at target)
#   - Exit price (both should be ~₹10,192.60)
#   - Net P&L (both should be +₹108.01)
```

---

## MAPPING TABLE: External → In-House

| Component | External Value | In-House Current | Status | Action |
|---|---|---|---|---|
| **Entry Kp** | ? | 0.15 | ❓ Unknown if match | Extract from trace |
| **Entry Ki** | ? | 0.05 | ❓ Unknown if match | Extract from trace |
| **Entry Kd** | ? | 0.08 | ❓ Unknown if match | Extract from trace |
| **Ichimoku weight** | 0.40 | ? | ❓ Check | Verify/set to 0.40 |
| **Bollinger weight** | 0.10 | ? | ❓ Check | Verify/set to 0.10 |
| **Stochastic weight** | 0.1817 | ? | ❓ Check | Verify/set to 0.1817 |
| **Session VWAP weight** | 0.40 | ? | ❓ Check | Verify/set to 0.40 |
| **Stop multiplier** | -1.0 ATR | -1.0 ATR | ✅ Likely match | Confirm |
| **Target multiplier** | +1.5 ATR | +1.5 ATR | ✅ Likely match | Confirm |
| **Min hold bars** | 2 | ? | ❓ Check | Verify/set to 2 |
| **Max hold bars** | 60 | 60 | ✅ Likely match | Confirm |
| **Cost model** | 0.27R friction | ? | ❓ Check | Verify same |

---

## CRITICAL UNKNOWNS

1. **What were the exact PID gains used by external engine?**
   - Need to re-run external with instrumentation to export Kp/Ki/Kd

2. **Are orchestrator study weights set to [0.40, 0.10, 0.1817, 0.40]?**
   - Need to search orchestrator.py for initial weight values

3. **Does orchestrator seed from same July-Aug 2023 period?**
   - If seed period differs, hit rates will be different → different weights → different P&L

4. **Are cost model and slippage calculations identical?**
   - External shows -₹82.25 on +₹190.26 gross
   - Need to verify orchestrator uses same friction model

---

## NEXT STEPS

**Immediate (30 minutes):**
1. Instrument external engine to export actual PID gains used
2. Re-run 2023-09-01 test and capture gains in trace
3. Search orchestrator.py for study weight initialization
4. Compare all configuration parameters

**Then (1 hour):**
1. Set orchestrator weights to match external
2. Verify seed period and hit rates match
3. Run orchestrator on 2023-09-01 with same data
4. Compare P&L line-by-line

**Success criteria:**
- In-house orchestrator produces +₹108.01 net P&L on primary trade
- All intermediate values (entry price, stop, target, exit timing) match external

---

Generated: 2026-09-13
Ready for: Configuration porting and side-by-side validation
