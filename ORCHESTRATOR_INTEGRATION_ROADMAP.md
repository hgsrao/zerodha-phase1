# Orchestrator Integration Roadmap: Phase 1 + Phase 2

**Status:** Phase 1 and Phase 2 implementation complete. Ready for orchestrator integration.

---

## What Needs to Happen Next

The orchestrator currently:
- ✅ Runs signal detection
- ✅ Builds trade plans via SimplePIDModelPredictiveControlBox
- ✅ Runs exit controller on open positions
- ✅ Submits paper orders

What needs to be added:
1. **Phase 1 Gate:** PreEntryDeferralController in signal processing pipeline
2. **Phase 2 Authority:** PID-controlled entry prices already implemented in pid_controller.py
3. **Integration Test:** Run orchestrator in shadow mode with both phases active

---

## Integration Points

### Location 1: Signal Processing Pipeline
**File:** `revision2_external/orchestrator.py`
**Around line:** 750-780 (where build_plan is called)

**Current flow:**
```python
# Line 765
plan, pid_info, trace = self.mpc.build_plan(
    signal, decision, next_open, atr, self.config
)
```

**New flow (Phase 1 insertion point):**
```python
# BEFORE build_plan, insert Phase 1 deferral gate
if self.preentry_deferral is not None:
    # Arm candidate for evaluation at next bar
    deferred = self.preentry_deferral.arm_candidate(
        symbol=signal.symbol,
        current_bar_index=current_bar_num,
        entry_px=next_open,
        atr=atr,
        direction=signal.direction,
    )
    
    if not deferred:
        # Return None; don't build plan yet
        return None  # Signal deferred, observe next bar
    
    # If we get here, previous bar was evaluated and ADMITTED
    # Proceed to Phase 2

# THEN build_plan (Phase 2 already controls entry price)
plan, pid_info, trace = self.mpc.build_plan(
    signal, decision, next_open, atr, self.config
)
```

### Location 2: Orchestrator Initialization
**File:** `revision2_external/orchestrator.py`
**Around line:** 64 (class __init__)

**Add:**
```python
from revision2_external.preentry_deferral_controller import PreEntryDeferralController

class Orchestrator:
    def __init__(self, ...):
        # ... existing initialization ...
        
        # Phase 1: Pre-entry deferral controller
        self.preentry_deferral = PreEntryDeferralController(
            expected_r_progress_pct=0.077,  # Configurable threshold
            enable_logging=True,
        )
        
        # Phase 2: Already integrated into SimplePIDModelPredictiveControlBox
        # (pid_controller.py lines 195-254)
```

### Location 3: Next-Bar Evaluation Loop
**File:** `revision2_external/orchestrator.py`
**Around line:** 800+ (where bars are processed)

**Add evaluation at bar close:**
```python
# After bar t+1 closes:
for symbol in self.preentry_deferral.get_pending_symbols():
    # Fetch the bar data for symbol
    bar_data = ... # (next_open, next_high, next_low, next_close)
    
    # Evaluate provisional candidate
    decision = self.preentry_deferral.evaluate_provisional(
        symbol=symbol,
        next_bar_index=current_bar_num,
        next_bar_open=bar_data['open'],
        next_bar_high=bar_data['high'],
        next_bar_low=bar_data['low'],
        next_bar_close=bar_data['close'],
    )
    
    # Handle decision
    if decision == "ADMIT_NEXT_OPEN":
        # Signal the orchestrator to build plan for this symbol at next open
        self._signals_admitted_for_next_bar.append(symbol)
    elif decision == "CANCEL_CANDIDATE":
        # Skip; candidate rejected
        pass
    elif decision == "DEFER":
        # Candidate still pending; re-evaluate next bar
        pass
```

---

## Integration Testing Checklist

### Test 1: Single Signal (MARUTI)
- [ ] Load one MARUTI up-trend signal
- [ ] Run orchestrator in shadow mode
- [ ] Verify:
  - Bar t: Signal detected, Phase 1 arms candidate, no plan built
  - Bar t+1: Phase 1 evaluates, ADMITS candidate
  - Bar t+2 open: Phase 2 builds plan with PID-controlled entry
  - Telemetry: pid_intent_bps and slippage_bps populated

### Test 2: Full Signal Set (48 Symbols)
- [ ] Load all 48 liquid large-cap stocks
- [ ] Run orchestrator in shadow mode across full day
- [ ] Collect statistics:
  - Total signals detected
  - % armed for deferral
  - % admitted vs deferred vs cancelled
  - % with pid_intent_bps < 0 (tight entries)
  - % with pid_intent_bps > 0 (loose entries)

### Test 3: Compare Against Baseline
- [ ] Run orchestrator WITHOUT Phase 1+2 (existing behavior)
- [ ] Run orchestrator WITH Phase 1+2 (new behavior)
- [ ] Compare:
  - Total paper orders submitted
  - Average entry price vs planned
  - Win rates (target before stop)
  - Net P&L after friction

### Test 4: Telemetry Validation
- [ ] Verify all pid_info fields populated
  - entry_price_planned
  - execution_market_price
  - pid_intent_bps (should be ±0 to ±10 range)
  - slippage_bps (should be ±0.5 to ±2.0 range)
- [ ] Verify deferral_history JSON exports correctly
  - Decision trail for each symbol
  - Actual vs expected R-progress
  - Error calculations

---

## Configuration Parameters to Add

Add to orchestrator config (or pass via __init__):

```python
{
    # Phase 1: Pre-Entry Deferral
    "preentry_deferral_enabled": True,
    "preentry_deferral_expected_r_progress": 0.077,  # 7.7%
    
    # Phase 2: PID Entry Price Control
    # (Already using existing pid_kp_entry, pid_ki_entry, etc.)
    # No new params needed; Phase 2 scales entry_adjustment to bps
}
```

---

## Success Criteria

| Criterion | Metric | Target |
|---|---|---|
| Phase 1 functionality | Deferral decisions logged | 100% correct logic |
| Phase 2 functionality | pid_intent_bps populated | Non-zero for non-neutral signals |
| Integration success | Shadow mode runs to completion | 0 exceptions |
| Decision accuracy | Phase 1 ADMIT vs CANCEL logic | ≥ 90% hits target before stop |
| Telemetry accuracy | pid_intent_bps vs actual price | Correlation ≥ 0.95 |
| Comparison vs baseline | Net P&L (Phase 1+2) vs baseline | Positive delta |

---

## Implementation Time Estimate

| Task | Time | Dependencies |
|---|---|---|
| Add PreEntryDeferralController import | 5 min | None |
| Add __init__ integration | 10 min | Import done |
| Insert Phase 1 gate in signal processing | 20 min | __init__ done |
| Add next-bar evaluation loop | 30 min | Gate done |
| Test single signal (MARUTI) | 20 min | Integration done |
| Test full 48-symbol shadow | 30 min | Single signal passes |
| Baseline comparison | 30 min | Full shadow passes |
| Debug and fix issues | 60 min | Empirical |
| **Total** | **3-4 hours** | **Sequential** |

---

## Risk Factors & Mitigation

| Risk | Impact | Mitigation |
|---|---|---|
| Orchestrator doesn't have pending deferred signals storage | Medium | Add `_pending_deferred_symbols` dict to track pending candidates |
| Signal routing complexity (which bar, which symbol) | Medium | Use PreEntryDeferralController.get_pending_symbols() to iterate |
| Telemetry overflow | Low | Only log when pid_intent_bps != 0 (non-neutral) |
| Phase 1+2 slows orchestrator loop | Low | Deferral gate is O(1) per signal; worth the tradeoff |
| Baseline comparison unfair (different market conditions) | Medium | Run both in same shadow period, identical market data |

---

## Next Steps (In Order)

1. **Today:** Add PreEntryDeferralController to orchestrator imports
2. **Today:** Initialize Phase 1 controller in __init__
3. **Tomorrow:** Insert Phase 1 gate in signal processing (lines ~750-780)
4. **Tomorrow:** Add next-bar evaluation loop (lines ~800+)
5. **Tomorrow:** Test single MARUTI signal end-to-end
6. **Tomorrow:** Run full 48-symbol shadow test
7. **Tomorrow:** Compare Phase 1+2 vs baseline
8. **Next day:** Debug findings, prepare for Phase 3 implementation

---

## Phase 3 Preview

After Phase 1+2 integration is complete and validated, Phase 3 (Scheduled Exit on Path Breach) can be integrated:

**File to modify:** `revision2_external/continuous_exit_controller.py`
**Integration point:** After entry, track expected vs actual R-progress
**Decision:** If error > threshold, arm scheduled exit for next bar

This will complete the closed-loop control system:
- **Phase 1:** Gate before entry (prevent false breakouts)
- **Phase 2:** Control entry price (tight vs loose)
- **Phase 3:** Exit on path breach (catch reversals early)

---

Generated: 2026-09-13 11:30 UTC  
Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
