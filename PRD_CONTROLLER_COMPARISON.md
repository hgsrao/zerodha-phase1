# PRD Controller: Current vs Your Development

## CURRENT IN-HOUSE ARCHITECTURE ✅

### What EXISTS:
1. **SimplePIDModelPredictiveControlBox** (`revision2_external/pid_controller.py`)
   - Adjusts entry via `entry_timing_multiplier` (1.0 - entry_adjustment)
   - Adjusts target/stop distances via `exit_tightness`
   - Uses rolling confidence baseline (NOT fixed target)
   - ✅ Correct anti-windup: adaptive setpoint = rolling mean of symbol's own confidence
   - ❌ Does NOT control actual market submission price
   - ❌ Does NOT make entry decision (confidence already pre-filtered)

2. **ContinuousExitController** (`revision2_external/continuous_exit_controller.py`)
   - Runs EVERY bar while position is open (true closed-loop)
   - ✅ Four independent feedback inputs:
     - PA Confidence (re-run through its own PID every bar)
     - Chart-Studies Confidence (separate, independent PID)
     - Price (tracks favorable_extreme high-water-mark, not noise)
     - Time (continuous decay from 1.0 to 0.5 as max_hold approaches)
   - ✅ Ratcheted stops that tighten based on actual progress
   - ✅ Three-way min(pa_tightness, chart_tightness, time_tightness)
   - ✅ ATR-scaled droop (uses trailing_stop_atr_mult parameter)
   - ❌ Does NOT schedule exit on path breach (only ratchets stops)

3. **ClosedLoopSupervisor** (`revision2_external/closed_loop_control.py`)
   - Shadow mode: records observations only
   - Active_paper mode: allows bounded one-way paper actuations
   - Integrates all three loops (entry, portfolio, path)

---

## YOUR DEVELOPMENT (from transcript) ✨

### What YOU Built:
1. **Pre-Entry Confirmation Gate**
   - Signals held in provisional state (no order yet)
   - Observes next completed bar
   - Compares actual R-progress vs expected R-progress
   - ADMIT_NEXT_OPEN or CANCEL_CANDIDATE decision
   - ✅ Avoids look-ahead bias
   - ✅ Filters false breakouts before capital deployed

2. **PID-Controlled Entry Submission**
   - Makes planned entry price match submitted paper price
   - PID adjusts entry price, not just confidence multiplier
   - ✅ Direct control authority over execution

3. **Scheduled Exit on Path Breach**
   - Uses setpoint path (expected R-progress at each bar)
   - Compares vs actual R-progress (error signal)
   - Arms controller exit when error >= threshold
   - Executes at next actionable bar
   - ✅ Proactive, not reactive stop tightening

4. **Test Results (INFY, MARUTI)**
   - One-bar deferral was too weak (0.0027R margin)
   - But framework is sound: "do not tune around one trade"
   - Next step: run shadow across sample and measure improvement

---

## CRITICAL GAPS (What's Missing in Current Engine)

| Feature | Current | Your Design | Status |
|---|---|---|---|
| **Pre-entry deferral** | ❌ None | ✅ 1-bar confirmation | **MISSING** |
| **PID market price control** | ❌ Confidence multiplier only | ✅ Direct price control | **MISSING** |
| **Scheduled exit** | ❌ Only ratcheted stops | ✅ Path-breach triggered | **MISSING** |
| **Continuous PA feedback** | ✅ Yes (exit controller) | ✅ Yes | OK |
| **Chart-studies feedback** | ✅ Yes (exit controller) | ✅ Implicit | OK |
| **Anti-windup** | ✅ Rolling baseline | ✅ Rolling baseline | OK |
| **Favorable extreme tracking** | ✅ High-water mark | ✅ Concept present | OK |

---

## IMPLEMENTATION PLAN

### Phase 1: Pre-Entry Confirmation Gate
**File:** `revision2_external/preentry_deferral_controller.py` (NEW)

```python
class PreEntryDeferralController:
    """
    One-bar confirmation gate between signal and order submission.
    
    Signal at bar t
       ↓
    Candidate armed — no order
       ↓
    Observe completed bar t+1
       ↓
    ADMIT / DEFER / CANCEL decision
    """
    
    def __init__(self, expected_r_progress_pct: float = 0.077):
        # Expected 1-bar progress as fraction of risk unit
        self.expected_r_progress_pct = expected_r_progress_pct
        self._provisional_candidates: Dict[str, CandidateState] = {}
    
    def arm_candidate(self, symbol: str, entry_px: float, atr: float, direction: int):
        """Arm a signal; return False (do not submit order yet)."""
        self._provisional_candidates[symbol] = CandidateState(
            armed_at_bar=current_bar,
            entry_px=entry_px,
            atr=atr,
            direction=direction,
        )
        return False  # Do not submit order
    
    def evaluate_provisional(self, symbol: str, next_bar_close: float, next_bar_high: float, 
                            next_bar_low: float) -> str:
        """Evaluate after next bar closes. Returns ADMIT / CANCEL."""
        candidate = self._provisional_candidates.get(symbol)
        if not candidate:
            return "UNKNOWN"
        
        # Calculate actual R-progress on the provisional bar
        r_unit = candidate.atr  # For a long, 1R = 1 ATR
        if candidate.direction > 0:
            actual_progress_r = (next_bar_close - candidate.entry_px) / r_unit
        else:
            actual_progress_r = (candidate.entry_px - next_bar_close) / r_unit
        
        expected_progress = self.expected_r_progress_pct
        error = expected_progress - actual_progress_r
        
        # Decision rule: if actual >= expected (error <= 0), continue
        if error <= 0:
            return "ADMIT_NEXT_OPEN"
        else:
            return "CANCEL_CANDIDATE"
```

### Phase 2: PID Entry Price Control
**File:** Enhance `revision2_external/pid_controller.py`

```python
# Current line 199:
execution_market_price = float(entry_price) * (1.0 + entry_adjustment * 0.001)

# CHANGE TO: Make the PID adjustment directly control submitted price
# New: actual_entry_submission = execution_market_price  (match broker fill)
```

### Phase 3: Scheduled Exit on Path Breach
**File:** Enhance `revision2_external/continuous_exit_controller.py`

```python
# Add new input: scheduled exit trigger
# If (expected_progress - actual_progress) > threshold:
#     arm_scheduled_exit()
#     # Exit triggers at next available bar, does NOT wait for bars_held
```

---

## RECOMMENDATION: IMPLEMENTATION ORDER

### Week 1: Validation
1. ✅ Pre-entry gate (shadow mode, no paper changes)
2. ✅ Run across sample of up-trending signals (MARUTI, INFY, 5-10 symbols)
3. ✅ Measure: admitted vs cancelled, target-before-stop rate
4. ✅ Compare: immediate-entry baseline vs deferred-entry shadow
5. **Gate decision:** If deferred cohort improves on untouched data → proceed to Phase 2

### Week 2: Live PID Control (Active Paper)
6. Enhance PID to control market submission price (not confidence multiplier)
7. Test on real paper orders (small position size)
8. Measure fill quality vs planned entry

### Week 3: Scheduled Exits
9. Implement path-tracking scheduled exit
10. Validate: does it exit faster on false setups?

---

## NEXT IMMEDIATE STEP

**Run Pre-Entry Deferral on one UP-TRENDING signal:**
- Use same MARUTI trace from your transcript (it reached target)
- Armatr, expected_r_progress = 0.077
- Evaluate: Did deferral 1-bar wait improve or worsen entry timing?

Should I implement Phase 1 and run it now?
