# INDEPENDENT 10X MODE AUDIT - IN-HOUSE ENGINE

**Date**: 2026-09-04  
**Purpose**: Comprehensive verification of all audit findings + additional issues  
**Status**: AUDIT COMPLETE - 11 Total Issues Found

---

## AUDIT SCOPE

1. Verify all 9 audit findings from Backtrader benchmark audit
2. Identify additional issues not yet catalogued
3. Classify by severity and dependencies
4. Plan fixes in optimal order
5. Execute all fixes
6. Re-validate against Backtrader reference

---

## FINDINGS SUMMARY

### Critical Issues (5 - Block Backtrader Parity)

**BUG-001: Drawdown Unit Mismatch** ✓ VERIFIED
- **Location**: timestamp_aligned_backtest.py:188 vs gates_framework.py:268
- **Root Cause**: Drawdown calculated as 0-100 percentage scale, gates expect 0-1 fraction scale
  * Code returns: `(peak - current) / peak * 100` = 25.0
  * Gates check: `25.0 >= 0.25` (ALWAYS TRUE)
  * Result: Drawdown halt triggers on first minor loss
- **Impact**: Portfolio risk management completely broken
- **Audit Finding**: YES (Item 9 - "Drawdown multiplied by 100")
- **Fix**: Return as fraction `(peak - current) / peak` = 0.25

**BUG-002: Entry Costs Calculated Before Gate Sizing** ✓ VERIFIED
- **Location**: timestamp_aligned_backtest.py:168-170, 202-211
- **Root Cause**: Costs calculated on 100 shares, but gates reduce actual fill to 1-5 shares
  * Requested qty: 100 shares → Entry cost: Rs XXX (on 100 shares)
  * Gate decision: size = 5 shares → Entry cost deducted on 100, entered on 5
  * Trade ledger: Only sees 5 shares entered, full cost charged to cash
  * Reconciliation: Cash down by (100*price + costs) but trade only has (5*price + costs/20)
  * Missing: Rs (95*price + costs*19/20)
- **Impact**: Rs -3,844.77 unreconciled per run
- **Audit Finding**: YES (Item 4 - "Entry fees calculated on 100 before gates cap")
- **Fix**: Calculate costs AFTER gate sizing, on actual `(size * price)`

**BUG-003: Zero-Quantity Orders Not Rejected** ✓ VERIFIED
- **Location**: gates_framework.py (can return size=0), timestamp_aligned_backtest.py:204-211
- **Root Cause**: Gates may reduce size to 0 if all gates pass but constraints cap at 0
  * Gate 08 checks concentration on 100 shares, blocks if > 20%
  * Gate 09 caps actual position to remaining available
  * If remaining = 0, gate returns size=0
  * Code still enters position with qty=0
- **Impact**: 70 of 129 reported trades have qty=0 (not economic trades)
- **Audit Finding**: YES (Item 1 - "70 of 129 have quantity zero")
- **Fix**: Reject entries where `size <= 0` before portfolio entry

**BUG-004: Next-Bar Open Read Before Gate Approval** ✓ VERIFIED
- **Location**: timestamp_aligned_backtest.py:162-164 (executed), then gates at line 202
- **Root Cause**: Code structure reads next bar before checking gates
  ```python
  # Line 162-164: Read next bar's open (FUTURE DATA)
  next_bar = df.iloc[bar_idx + 1]
  entry_price = next_bar['open']
  
  # Line 202: THEN check gates (can see future data)
  can_enter, size, reason = self.entry_engine.can_enter(signal, state)
  ```
- **Impact**: Gates can inspect future bar open before approving order
- **Audit Finding**: YES (Item 8 - "Next bar open read before order decision")
- **Fix**: Gates must use only current-bar data; next-bar only used for fill price AFTER approval

**BUG-005: Lambda Calculation Incorrect** ✓ VERIFIED
- **Location**: timestamp_aligned_backtest.py:189
- **Root Cause**: `lambda = len(positions) / 5` instead of `gross_exposure / equity`
  * Current: If 3 positions held → λ = 0.6
  * Correct: If 3×Rs100k positions in Rs1M equity → λ = 0.3 (30% exposure)
  * These can diverge wildly (3 small positions vs 1 huge position)
- **Impact**: Exposure gates don't reflect true portfolio risk
- **Audit Finding**: YES (Item 6 - "Lambda uses open_position_count/5")
- **Fix**: `lambda = sum(position_values) / current_equity`

---

### Configuration Issues (4 - Need Single Source of Truth)

**CONFIG-001: Empty Open Positions List** ✓ VERIFIED
- **Location**: timestamp_aligned_backtest.py:193
- **Issue**: `open_positions=[]` hardcoded empty
- **Impact**: Gate 08 (symbol concentration) always sees 0 positions, can't enforce limits
- **Fix**: Populate with actual `[{'symbol': s, 'qty': p.qty, 'value': v} ...]`

**CONFIG-002: Overnight Positions Allowed** ✓ VERIFIED
- **Location**: timestamp_aligned_backtest.py:226-242 (force-close at end of sample)
- **Issue**: Positions held until end of 3-year dataset, not closed at daily session end
- **Impact**: 56 of 59 trades span multiple dates (violates MIS same-day close rule)
- **Fix**: Identify session end for each trading day, force-close all MIS positions

**CONFIG-003: Signal Formula Mismatch** ✓ VERIFIED
- **Location**: PHASE3_CONFIG_FROZEN_20260904.json vs signal_confidence_formula.py
- **Discrepancy**: JSON declares EMA(9) for trend, code uses SMA(9)
- **Fix**: Update config or code to agree (recommend SMA per code, update config)

**CONFIG-004: Multiple Gate Parameter Conflicts** ✓ VERIFIED
- **6 Conflicts Found**:
  1. Concentration: JSON says 20%, code uses 15%
  2. Exposure: JSON says 100%, code uses 50%
  3. Daily Loss: JSON says 2% of equity, code uses fixed Rs 50,000
  4. Slippage: JSON says 0.10%, code interprets 0.10 as 10%
  5. Holdout: JSON says 2026-09-01, data ends 2026-08-14
  6. Duplicate timestamps: Validator says VALID, but 204 exist per file
- **Fix**: Create canonical execution contract, update both config and code

---

### Data Issues (1 - Preparation Step Needed)

**DATA-001: Duplicate Timestamps** ✓ VERIFIED
- **Location**: historical_data_zerodha_nifty48/ CSVs + data_loader_frozen.py
- **Issue**: 204 exact duplicate timestamp rows in each selected file
- **Validator**: Reports VALID despite duplicates
- **Impact**: Undefined behavior (depends on DataFrame iteration order)
- **Fix**: Document deduplication step in data-prep, keep first occurrence

---

## AUDIT VERIFICATION CHECKLIST

### Audit Finding Verification
- [x] 70 zero-quantity trades (BUG-003)
- [x] 56 overnight trades (CONFIG-002)  
- [x] P&L unreconciled -Rs 3,844.77 (BUG-002)
- [x] Entry costs wrong (BUG-002)
- [x] Drawdown unit wrong (BUG-001)
- [x] Lambda calculation wrong (BUG-005)
- [x] Empty position list (CONFIG-001)
- [x] Future bar inspection (BUG-004)
- [x] Duplicate timestamps (DATA-001)

### Additional Issues Found (Not in Original Audit)
- [x] Configuration conflicts not resolved (CONFIG-004)
- [x] Signal formula mismatch (CONFIG-003)

---

## FIX IMPLEMENTATION PLAN

### Phase 1: Critical Fixes (Unblock Backtrader Parity)
1. **FIX-001**: Drawdown unit mismatch
   - Expected impact: Restore portfolio risk management
   - Dependencies: None
   - Complexity: Low (1-line change)

2. **FIX-002**: Calculate costs after gate sizing
   - Expected impact: Reconcile P&L ledger
   - Dependencies: FIX-003 (zero quantity rejection)
   - Complexity: Medium (reorder calculation flow)

3. **FIX-003**: Reject zero-quantity orders
   - Expected impact: Eliminate 70 invalid trades
   - Dependencies: None
   - Complexity: Low (1 conditional check)

4. **FIX-004**: Gates use only current-bar data
   - Expected impact: Restore execution causality
   - Dependencies: None
   - Complexity: Medium (refactor signal/gate order)

5. **FIX-005**: Correct lambda calculation
   - Expected impact: Proper exposure derating
   - Dependencies: CONFIG-001 (populate positions)
   - Complexity: Low (change formula)

### Phase 2: Configuration Fixes (Enable 48-Symbol Run)
6. **CONFIG-FIX-001**: Populate open positions list
7. **CONFIG-FIX-002**: Daily MIS close enforcement
8. **CONFIG-FIX-003**: Resolve all 6 parameter conflicts
9. **DATA-FIX-001**: Document deduplication

---

## BACKTRADER REFERENCE (TRUTH)
- 665 trades (vs our 129)
- 42.41% win rate (vs our broken 28.68%)
- -0.0764% net return (vs our -0.25% broken)
- 0.111% max drawdown (proper scale)
- Perfect reconciliation

**Target**: In-house engine produces identical 665 trades, 42.41% win rate, -0.0764% return on 5-symbol test

---

## STATUS

**Audit Complete**: All 11 issues verified and documented  
**Next Step**: Begin Phase 1 critical fixes (FIX-001 through FIX-005)  
**Validation**: Re-run 5-symbol test against Backtrader reference after each fix

