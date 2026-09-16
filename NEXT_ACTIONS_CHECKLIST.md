# Next Actions Checklist: Orchestrator Integration

**Status:** Phase 1 ✅ | Phase 2 ✅ | Ready for → Orchestrator Integration  
**Time Estimate:** 3-4 hours total  
**Success Metric:** Shadow mode runs without errors, P&L improves vs baseline

---

## STEP 1: Orchestrator Initialization (10 minutes)

**File:** `revision2_external/orchestrator.py`

**Action 1a: Add import at top**
```python
from revision2_external.preentry_deferral_controller import PreEntryDeferralController
```

**Action 1b: Initialize in __init__ (around line 64)**
```python
# Add in __init__ after existing initializations:
self.preentry_deferral = PreEntryDeferralController(
    expected_r_progress_pct=0.077,  # Configurable threshold
    enable_logging=True,
)
self._pending_deferred_signals = {}  # Track pending deferred candidates
```

**Checklist:**
- [ ] Import added
- [ ] Controller initialized
- [ ] Pending signals dict created
- [ ] Test: No import errors

---

## STEP 2: Signal Processing Pipeline (20 minutes)

**File:** `revision2_external/orchestrator.py`  
**Location:** Around line 765 (where `build_plan` is called)

**Current code:**
```python
plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
```

**Replace with:**
```python
# PHASE 1: Pre-Entry Deferral Gate
if self.preentry_deferral is not None and signal.symbol not in self._pending_deferred_signals:
    # First time seeing this signal: arm it for evaluation
    self.preentry_deferral.arm_candidate(
        symbol=signal.symbol,
        current_bar_index=current_bar_num,
        entry_px=next_open,
        atr=atr,
        direction=signal.direction,
    )
    self._pending_deferred_signals[signal.symbol] = True
    # Don't build plan yet; wait for next bar evaluation
    return None  # Skip this bar, evaluate next
else:
    # Previously deferred; check if it was admitted
    if signal.symbol in self._pending_deferred_signals:
        # It was; remove from pending
        del self._pending_deferred_signals[signal.symbol]
    # Proceed to Phase 2 (build plan with PID)

# PHASE 2: PID Entry Price Control (already integrated in build_plan)
plan, pid_info, trace = self.mpc.build_plan(signal, decision, next_open, atr, self.config)
```

**Checklist:**
- [ ] Phase 1 gate logic added
- [ ] Deferred signals tracked
- [ ] return None inserted
- [ ] Test: Single signal defers

---

## STEP 3: Next-Bar Evaluation Loop (30 minutes)

**File:** `revision2_external/orchestrator.py`  
**Location:** Where next bar is processed (around line 800+)

**Add evaluation logic:**
```python
# After bar t+1 closes, evaluate deferred candidates
for pending_symbol in list(self.preentry_deferral.get_pending_symbols()):
    # Fetch bar data for this symbol
    bar_data = ... # (get from your data source)
    
    decision = self.preentry_deferral.evaluate_provisional(
        symbol=pending_symbol,
        next_bar_index=current_bar_num,
        next_bar_open=bar_data['open'],
        next_bar_high=bar_data['high'],
        next_bar_low=bar_data['low'],
        next_bar_close=bar_data['close'],
    )
    
    # Log decision
    if decision == "ADMIT_NEXT_OPEN":
        # Signal the orchestrator to create a plan for this at next open
        self._signals_to_admit_next_open.append(pending_symbol)
        logger.info(f"[DEFERRAL] {pending_symbol} ADMITTED")
    elif decision == "CANCEL_CANDIDATE":
        logger.info(f"[DEFERRAL] {pending_symbol} CANCELLED")
        if pending_symbol in self._pending_deferred_signals:
            del self._pending_deferred_signals[pending_symbol]
    elif decision == "DEFER":
        logger.info(f"[DEFERRAL] {pending_symbol} DEFERRED (re-evaluate)")
```

**Checklist:**
- [ ] Evaluation loop added
- [ ] Decision logging implemented
- [ ] Admitted signals queued for next open
- [ ] Test: Evaluation runs at bar close

---

## STEP 4: Single Signal Test (20 minutes)

**Test:** MARUTI up-trend from transcript

**Setup:**
1. Load MARUTI OHLCV data
2. Identify the up-trend signal bar
3. Set orchestrator to shadow mode
4. Set preentry_deferral_enabled = True

**Expected Flow:**
```
Bar t (signal detected):
  → Phase 1 arms candidate
  → NO plan created
  → return None

Bar t+1 (next bar closes):
  → Phase 1 evaluates
  → Actual R > Expected R
  → Decision: ADMIT_NEXT_OPEN
  → Signal queued for next open

Bar t+2 (next open):
  → Phase 2 builds plan (PID controls entry price)
  → Order submitted at execution_market_price
  → pid_intent_bps and slippage_bps logged
```

**Validation:**
```python
# Check telemetry
assert pid_info['pid_intent_bps'] != 0  # PID had opinion
assert pid_info['execution_market_price'] != entry_price_planned or error == 0
assert plan.entry_price == pid_info['execution_market_price'] + slippage
print("✅ Single signal test PASSED")
```

**Checklist:**
- [ ] Load MARUTI data
- [ ] Run orchestrator shadow mode
- [ ] Verify deferral decision logged
- [ ] Verify plan created at t+2 open
- [ ] Verify pid_info fields populated
- [ ] Assert entry prices match expectations

---

## STEP 5: Full 48-Symbol Shadow Test (30 minutes)

**Test:** All 48 liquid large-cap stocks, one full trading day

**Setup:**
1. Load all 48 symbols OHLCV data
2. Set orchestrator to shadow mode
3. preentry_deferral_enabled = True
4. Collect statistics

**Expected Outcomes:**
```
Total signals detected: ~100-200
  Armed for deferral: ~80% (call it 80-160)
  
Deferral decisions:
  ADMIT_NEXT_OPEN: ~70% (56-112)
  CANCEL_CANDIDATE: ~20% (16-32)
  DEFER: ~10% (8-16)

PID adjustments (for admitted):
  pid_intent_bps < 0 (tight): ~50-60%
  pid_intent_bps > 0 (loose): ~30-40%
  pid_intent_bps ≈ 0 (neutral): ~5-10%
```

**Validation:**
```python
results = {
    'total_signals': len(signal_log),
    'armed_for_deferral': sum(1 for s in signal_log if deferred),
    'admitted': sum(1 for s in signal_log if admitted),
    'cancelled': sum(1 for s in signal_log if cancelled),
    'deferred': sum(1 for s in signal_log if deferred and not decided),
    'avg_pid_intent_bps': np.mean([p['pid_intent_bps'] for p in plans]),
}
assert results['total_signals'] > 0
assert results['admitted'] + results['cancelled'] + results['deferred'] > 0
print("✅ Full 48-symbol test PASSED")
```

**Checklist:**
- [ ] Load all 48 symbols
- [ ] Run full orchestrator shadow
- [ ] Collect decision statistics
- [ ] Log to JSON: `phase2_integration_48symbol_results.json`
- [ ] Assert no exceptions
- [ ] Assert statistics reasonable

---

## STEP 6: Baseline Comparison (30 minutes)

**Test:** Phase 1+2 vs Baseline (immediate-entry)

**Setup Two Runs:**

**Run A (Baseline):**
- Load same 48 symbols
- Set preentry_deferral_enabled = False
- Run orchestrator shadow mode
- Collect: orders submitted, entry prices, P&L

**Run B (Phase 1+2):**
- Load same 48 symbols
- Set preentry_deferral_enabled = True
- Run orchestrator shadow mode
- Collect: orders submitted, entry prices, P&L

**Comparison Metrics:**
```python
metrics = {
    'baseline': {
        'total_orders': len(run_a_orders),
        'avg_win_rate': sum(1 for o in run_a_orders if won) / len(run_a_orders),
        'net_pnl_bps': sum(o['pnl_bps'] for o in run_a_orders),
        'target_before_stop_rate': sum(1 for o in run_a_orders if target_first) / len(run_a_orders),
        'avg_entry_slippage': mean([abs(o['execution'] - o['planned']) for o in run_a_orders]),
    },
    'phase1_2': {
        'total_orders': len(run_b_orders),
        'avg_win_rate': sum(1 for o in run_b_orders if won) / len(run_b_orders),
        'net_pnl_bps': sum(o['pnl_bps'] for o in run_b_orders),
        'target_before_stop_rate': sum(1 for o in run_b_orders if target_first) / len(run_b_orders),
        'avg_entry_slippage': mean([abs(o['execution'] - o['planned']) for o in run_b_orders]),
    },
}

# Expected improvements
assert metrics['phase1_2']['avg_win_rate'] > metrics['baseline']['avg_win_rate']
assert metrics['phase1_2']['target_before_stop_rate'] > metrics['baseline']['target_before_stop_rate']
assert metrics['phase1_2']['net_pnl_bps'] > metrics['baseline']['net_pnl_bps']
print("✅ Phase 1+2 outperforms baseline")
```

**Checklist:**
- [ ] Run baseline (preentry_deferral = False)
- [ ] Run Phase 1+2 (preentry_deferral = True)
- [ ] Collect identical metrics from both
- [ ] Export to JSON: `phase1_2_vs_baseline_comparison.json`
- [ ] Verify Phase 1+2 improvements:
  - [ ] Higher win rate
  - [ ] Better target-before-stop
  - [ ] Higher net P&L
  - [ ] Lower avg slippage

---

## STEP 7: Validation & Debug (60 minutes, buffer)

**If tests pass:** ✅ Proceed to Phase 3
**If tests fail:** Debug using checklist below

**Debug Checklist:**
- [ ] Check deferral gate logic (return None on first bar?)
- [ ] Check next-bar evaluation (are bar prices loaded correctly?)
- [ ] Check PID telemetry (are pid_intent_bps populated?)
- [ ] Check orchestrator config (required parameters present?)
- [ ] Check signal flow (does admitted signal reach build_plan?)
- [ ] Review logs for exceptions

**Common Issues:**
| Issue | Solution |
|---|---|
| No signals deferred | Check if arm_candidate is called |
| Deferred signals not evaluated | Check if next-bar evaluation loop runs |
| pid_intent_bps = 0 always | Check if confidence > baseline condition triggers |
| Exception: "symbol already armed" | Clear _pending_deferred_signals dict on error |
| Baseline missing orders | Check if preentry_deferral = False disables gate |

---

## STEP 8: Ready for Phase 3 (After Steps 1-7 Complete)

**Do NOT start Phase 3 until:**
- ✅ Single signal test passes (MARUTI)
- ✅ Full 48-symbol shadow test passes
- ✅ Phase 1+2 outperforms baseline
- ✅ All telemetry validated

**Phase 3 Next Steps:**
1. Modify `continuous_exit_controller.py` for path tracking
2. Add scheduled exit on path breach logic
3. Integrate into orchestrator
4. Test all three phases (1+2+3) end-to-end

---

## SUMMARY CHECKLIST

```
STEP 1: Initialization (10 min)
  [ ] Import added
  [ ] Controller initialized
  [ ] No errors

STEP 2: Signal Processing (20 min)
  [ ] Phase 1 gate inserted
  [ ] return None on deferral
  [ ] No errors

STEP 3: Evaluation Loop (30 min)
  [ ] Next-bar evaluation added
  [ ] Decisions logged
  [ ] No errors

STEP 4: Single Signal Test (20 min)
  [ ] MARUTI test passes
  [ ] Telemetry populated
  [ ] Assertions pass

STEP 5: 48-Symbol Shadow (30 min)
  [ ] All 48 symbols run
  [ ] Statistics collected
  [ ] Results saved to JSON

STEP 6: Baseline Comparison (30 min)
  [ ] Baseline run complete
  [ ] Phase 1+2 run complete
  [ ] Phase 1+2 outperforms baseline

STEP 7: Debug (if needed, 60 min)
  [ ] Issues resolved
  [ ] Tests re-run and pass

STEP 8: Ready for Phase 3
  [ ] All above steps complete
  [ ] Proceeding to Phase 3 implementation
```

---

## TIME ESTIMATE

| Step | Time | Cumulative |
|---|---|---|
| 1. Initialization | 10 min | 10 min |
| 2. Signal Processing | 20 min | 30 min |
| 3. Evaluation Loop | 30 min | 60 min |
| 4. Single Signal Test | 20 min | 80 min |
| 5. 48-Symbol Shadow | 30 min | 110 min |
| 6. Baseline Comparison | 30 min | 140 min |
| 7. Debug (buffer) | 60 min | 200 min |
| **TOTAL** | **~3.5 hours** | |

---

## SUCCESS CRITERIA

✅ **All of the following must be true:**
1. Orchestrator integration compiles without errors
2. Single MARUTI signal test completes end-to-end
3. 48-symbol shadow mode runs without exceptions
4. Phase 1+2 cohort outperforms baseline (higher win rate, P&L, target-before-stop)
5. Telemetry fields populated correctly (pid_intent_bps, slippage_bps)
6. Decision logs show correct ADMIT/CANCEL/DEFER logic

✅ **If all above pass:**
→ **Ready to implement Phase 3**

---

Generated: 2026-09-13 11:50 UTC  
Ready for: Orchestrator integration  
Co-Authored-By: Claude Haiku 4.5 <noreply@anthropic.com>
