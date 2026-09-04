# Complete Remediation Plan - All Critical Issues

**Date**: 2026-09-04  
**Scope**: Fix all 10 critical failures identified in user correction  
**Target**: Production-ready, Backtrader-equivalent results, GitHub push  
**Deadline**: This session

---

## Critical Issues to Fix (In Order)

### 1. Date Range Filtering (August 14 Excluded)
**Issue**: `end_date="2026-08-14"` interpreted as midnight, excludes intraday bars  
**Fix**: Change to `end_date="2026-08-15"` or use inclusive filtering  
**Impact**: Adds ~315 bars back into analysis  
**Test**: Verify August 14 bars present in data

### 2. Duplicate Timestamp Handling in Signal
**Issue**: Loop deduplicates events but indicator calculation still sees dupes  
**Fix**: Deduplicate data BEFORE passing to signal calculation  
**Impact**: Signal values will match Backtrader more closely  
**Test**: Verify no duplicate timestamps in signal input

### 3. Session-Aware Exit Timing
**Issue**: Closes using yesterday's price, records tomorrow's timestamp  
**Fix**: Detect session end (market close time), close at that bar, use correct timestamp  
**Impact**: All positions properly MIS-closed same day  
**Test**: Verify all trades have entry_date == exit_date

### 4. Entry Cost Calculation (Use Next-Bar Open)
**Issue**: Costs calculated on current-bar open, should use next-bar actual fill  
**Fix**: Calculate costs using actual next-bar open (the fill price)  
**Impact**: Costs match actual execution  
**Test**: Verify entry prices match filled next-bar open

### 5. Daily Loss Accounting (Sign Error)
**Issue**: Losses stored as negative, gate tests for positive magnitude  
**Fix**: Store as positive, adjust gate comparison logic  
**Impact**: Daily loss gate works correctly  
**Test**: Pass deterministic gate test for daily loss

### 6. Open Positions Field Names
**Issue**: Supplies `position_value`, gates expect `notional`  
**Fix**: Add/rename field to match gate expectations  
**Impact**: Exposure gates can properly aggregate positions  
**Test**: Verify gate sees all open positions

### 7. Event-Time Gates (Not Wall-Clock)
**Issue**: Gates use wall-clock time for historical backtests  
**Fix**: Use event timestamp, not current time  
**Impact**: Gates are deterministic in backtest  
**Test**: Same engine run produces identical results

### 8. Ledger Accounting Gap (₹1,020.09)
**Issue**: P&L -₹1,348 vs ledger -₹328 = -₹1,020 gap  
**Fix**: Trace all cash movements, find missing entries  
**Impact**: Perfect reconciliation  
**Test**: portfolio.get_equity() == sum(closed_trades P&L) + cash

### 9. Deterministic Gate Tests (0/36 Passing)
**Issue**: All gate unit tests fail  
**Fix**: Fix inputs, fix gate logic, all should pass  
**Impact**: Gates verified correct  
**Test**: Run gate test suite, all 36 pass

### 10. Results Reproducibility
**Issue**: JSON modified after commit  
**Fix**: Don't modify results, save once only  
**Impact**: Results match committed version  
**Test**: Verify JSON unchanged from git

---

## Implementation Order

**Phase A: Data & Timing (Issues 1, 2)**
- Fix date range filtering
- Deduplicate timestamps before signal
- Re-run test, verify data quality

**Phase B: Execution & Costs (Issues 3, 4)**
- Implement session-aware close
- Fix entry cost calculation
- Verify MIS compliance

**Phase C: Accounting & Reconciliation (Issues 5, 6, 8)**
- Fix daily loss sign
- Fix open positions fields
- Trace accounting gap
- Achieve perfect reconciliation

**Phase D: Gates & Tests (Issues 7, 9)**
- Fix event-time logic
- Run deterministic gate tests
- Get to 36/36 passing

**Phase E: Verification (Issue 10)**
- Lock results
- Commit to git
- Push to GitHub

---

## Success Criteria

### Before GitHub Push
- [  ] All 10 issues fixed
- [  ] 5-symbol test produces honest results
- [  ] Results reconcile perfectly (₹0 gap)
- [  ] All 776+ trades are same-day MIS
- [  ] Gate tests: 36/36 passing
- [  ] Drawdown calculation correct
- [  ] No modifications after save
- [  ] All daily closes at session end

### GitHub Status
- [  ] Clean git history
- [  ] All fixes committed with messages
- [  ] Results JSON locked
- [  ] README with instructions
- [  ] Cloud access from laptop verified

---

## Timeline

Expected completion: 3-4 hours for all fixes + testing + GitHub push

