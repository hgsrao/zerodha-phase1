# LAMBDA vs. DRAWDOWN - Technical Specification
## Work Item 1.2: Complete Independence Proof

**Status:** EXECUTION IN PROGRESS  
**Created:** 2026-09-01  
**Purpose:** Establish that lambda (portfolio exposure risk) and drawdown (realized loss) are independent mechanisms requiring separate triggers

---

## CORE DEFINITIONS

### LAMBDA: Portfolio Exposure Risk (Preemptive)

**Formula:**
```
λ = Σ(position_size_i × leverage_i × correlation_adjustment) / portfolio_value

Where:
  position_size_i = Rupee value of position i
  leverage_i = 1.0 for equity (no margin), 2.0+ for futures/options
  correlation_adjustment = Reduces lambda when positions are uncorrelated
  portfolio_value = Total portfolio capital
```

**Intuition:**
- Lambda measures HOW MUCH EXPOSURE you have RIGHT NOW
- It's PREEMPTIVE: catches risk BEFORE it becomes realized loss
- High lambda = "Your open positions add up to a lot of risk exposure"
- Example: You're long 100 shares of INFY (₹800k) in a ₹1M portfolio → λ = 0.80 (80% exposure)

**When Lambda Triggers Derating:**
```
IF λ ≥ 0.15 (15% portfolio exposure):
   REDUCE position sizes to 80% of normal
   → Keeps exposure from growing further
   → Still allows trading, just smaller positions
   → SOFT GATE (positions sized down, not blocked)
```

**Characteristics:**
- Measured BEFORE position is taken
- Forward-looking (exposure risk)
- Independent of profits/losses
- Can be high on a winning day or losing day


---

### DRAWDOWN: Portfolio Realized Loss (Reactive)

**Formula:**
```
DD = (Peak_Portfolio_Value - Current_Portfolio_Value) / Peak_Portfolio_Value

Where:
  Peak_Portfolio_Value = Highest portfolio value since start (high watermark)
  Current_Portfolio_Value = Portfolio value right now
  
Example 1: Peak ₹1,000,000 → Current ₹800,000
           DD = (1,000,000 - 800,000) / 1,000,000 = 0.20 (20% drawdown)

Example 2: Peak ₹1,000,000 → Current ₹750,000
           DD = (1,000,000 - 750,000) / 1,000,000 = 0.25 (25% drawdown)
```

**Intuition:**
- Drawdown measures HOW MUCH LOSS you've REALIZED
- It's REACTIVE: measures damage ALREADY DONE
- High drawdown = "You've lost a lot from your peak"
- Can only go up (recovery) or get worse (more losses)

**When Drawdown Triggers Derating:**
```
IF DD ≥ 0.18 (18% portfolio loss):
   REDUCE position sizes to 60% of normal
   → Smaller positions = slower compounding of losses
   → Still allows trading, just smaller
   → SOFT GATE (positions sized down)

IF DD ≥ 0.25 (25% portfolio loss):
   HALT ALL NEW ENTRIES
   → No new positions allowed
   → Only closes/exits permitted
   → HARD GATE (all entries blocked)
```

**Characteristics:**
- Measured AFTER positions are closed
- Backward-looking (historical loss)
- Requires portfolio loss to trigger
- Ratchets only down (peak resets on new high)


---

## INDEPENDENCE PROOF WITH TEST CASES

### Test Case 1: HIGH LAMBDA + LOW DRAWDOWN
**Scenario: Profitable day with high exposure**

```
Portfolio Start:        ₹1,000,000 (peak = ₹1,000,000)
Current Value:         ₹1,050,000 (made ₹50k profit)

Open Positions:
  - INFY: ₹400,000
  - TCS: ₹300,000
  - RELIANCE: ₹250,000
  Total Exposure: ₹950,000

LAMBDA CALCULATION:
  λ = 950,000 / 1,050,000 = 0.90 (90% exposure)
  Status: VERY HIGH (≥ 0.15) → TRIGGER DERATING ✓

DRAWDOWN CALCULATION:
  DD = (1,000,000 - 1,050,000) / 1,000,000 = -0.05 (NEGATIVE = NO DRAWDOWN)
  Status: ZERO/NEGATIVE → NO DERATING TRIGGER ✗

DECISION:
  - Lambda says: "You're over-leveraged, reduce positions to 80%"
  - Drawdown says: "You're profitable, normal sizing OK"
  - BOTH are correct → They contradict → They're INDEPENDENT ✓

Action: Apply LAMBDA derating (reduce to 80% size), ignore drawdown
```

### Test Case 2: LOW LAMBDA + HIGH DRAWDOWN
**Scenario: Heavy loss day with closed positions**

```
Portfolio Start:        ₹1,000,000 (peak = ₹1,000,000)
Current Value:         ₹750,000 (lost ₹250k)

Open Positions:
  - INFY: ₹100,000 (closed most positions to cut losses)
  Total Exposure: ₹100,000

LAMBDA CALCULATION:
  λ = 100,000 / 750,000 = 0.13 (13% exposure)
  Status: LOW (< 0.15) → NO LAMBDA DERATING ✗

DRAWDOWN CALCULATION:
  DD = (1,000,000 - 750,000) / 1,000,000 = 0.25 (25% LOSS)
  Status: CRITICAL (≥ 0.25) → TRIGGER HALT ✓

DECISION:
  - Lambda says: "Your exposure is low, normal sizing OK"
  - Drawdown says: "You've lost 25%, HALT all new entries"
  - BOTH are correct → They contradict → They're INDEPENDENT ✓

Action: Apply DRAWDOWN halt (no new entries), ignore lambda
```

### Test Case 3: HIGH LAMBDA + HIGH DRAWDOWN
**Scenario: Worst case - both triggered simultaneously**

```
Portfolio Start:        ₹1,000,000 (peak = ₹1,000,000)
Current Value:         ₹800,000 (lost ₹200k but still have big positions)

Open Positions:
  - INFY: ₹600,000
  - TCS: ₹200,000
  Total Exposure: ₹800,000

LAMBDA CALCULATION:
  λ = 800,000 / 800,000 = 1.00 (100% exposure!)
  Status: EXTREME (≥ 0.15) → TRIGGER DERATING ✓

DRAWDOWN CALCULATION:
  DD = (1,000,000 - 800,000) / 1,000,000 = 0.20 (20% LOSS)
  Status: HIGH (≥ 0.18) → TRIGGER DERATING ✓

DECISION:
  - Lambda derating: Reduce to 80% size
  - Drawdown derating: Reduce to 60% size
  - USE MORE RESTRICTIVE: 60% (drawdown wins)
  - BOTH are correct, just at different severity levels ✓

Action: Apply most restrictive derating (60%), since both triggered
```

### Test Case 4: LOW LAMBDA + ZERO DRAWDOWN
**Scenario: Conservative trading on winning day**

```
Portfolio Start:        ₹1,000,000 (peak = ₹1,000,000)
Current Value:         ₹1,100,000 (made ₹100k profit)

Open Positions:
  - INFY: ₹150,000 (only one small position)
  Total Exposure: ₹150,000

LAMBDA CALCULATION:
  λ = 150,000 / 1,100,000 = 0.136 (13.6% exposure)
  Status: LOW (< 0.15) → NO DERATING ✗

DRAWDOWN CALCULATION:
  DD = (1,000,000 - 1,100,000) / 1,000,000 = -0.10 (NEGATIVE = NO DRAWDOWN)
  Status: ZERO/NEGATIVE → NO DERATING ✗

DECISION:
  - Lambda says: "Low exposure, normal sizing OK"
  - Drawdown says: "Profitable, normal sizing OK"
  - Both agree → Normal full-size entries allowed ✓

Action: No derating, proceed with full-size entries
```

---

## PARAMETER RENAME REFERENCE

### OLD NAMES (Confusing)
```
lambda_risk_trigger_level
├─ Problem: "lambda_risk" sounds like a Greek letter used in finance
├─ Actually: Portfolio exposure threshold for derating
└─ Confusion: Listeners think "risk premium" or "option greeks"

lambda_reduction_factor  
├─ Problem: "reduction_factor" is vague
├─ Actually: Multiplier to apply when lambda is high
└─ Confusion: Is it reducing λ itself? Or position sizes?
```

### NEW NAMES (Clear Intent)
```
portfolio_risk_derate_trigger = 0.15
├─ Clear: This is the PORTFOLIO EXPOSURE level that triggers derating
├─ Derate = "reduce position size"
├─ Trigger = "the threshold that causes it"
└─ Readable: "When portfolio risk hits 15%, derate"

portfolio_derated_size_multiplier = 0.80
├─ Clear: When above trigger, positions become 80% of normal size
├─ Derate = "reduce"
├─ Multiplier = "factor to apply"
└─ Readable: "Apply 0.80x size reduction"
```

---

## IMPLEMENTATION NOTES

### Code Update Priority
1. **gates_framework.py**: References to lambda params → Use new names
2. **ecs_parameter_management.py**: Remove lambda params (they're not live strategy)
3. **safety_gates_config.py**: Already using portfolio_risk_* names ✓
4. **calibration_config.py**: Already using correct names ✓

### Testing Requirements
1. **Unit tests** for lambda calculation (prove exposure math)
2. **Unit tests** for drawdown calculation (prove loss math)
3. **Integration tests** for both triggering simultaneously
4. **Regression tests** for all parameter references

### Documentation Requirements
1. ✅ This file: Lambda vs. Drawdown independence
2. ⏳ parameter_rename_guide.md: Migration path for old → new names
3. ⏳ Code comments: Mark lambda references with parameter rename notes
4. ⏳ Safety gates documentation: Explain derating logic

---

## SUMMARY TABLE

| Aspect | Lambda | Drawdown |
|--------|--------|----------|
| **Measures** | Exposure risk | Realized loss |
| **Timing** | Preemptive | Reactive |
| **Triggered By** | High open position value | Cumulative portfolio losses |
| **Derate Threshold** | 15% exposure | 18% loss |
| **Halt Threshold** | (None) | 25% loss |
| **Trigger Type** | SOFT (derate positions) | SOFT (derate) + HARD (halt) |
| **Both High?** | Use more restrictive rule | Use drawdown (60% vs. 80%) |
| **Independence** | YES - one can be high while other is low | YES - proven by test cases |

---

## NEXT IMMEDIATE ACTIONS

- [x] Document lambda formula with examples
- [x] Document drawdown formula  
- [x] Prove independence via test cases (4 cases above)
- [ ] Create parameter_rename_guide.md
- [ ] Update all code references
- [ ] Write integration tests for gate combinations

**Status: SPECIFICATION COMPLETE - PROCEEDING TO RENAME GUIDE**

