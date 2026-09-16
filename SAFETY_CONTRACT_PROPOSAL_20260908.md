# Safety Contract Proposal: Slippage Tolerance & Cross-Session Orders

**Date:** September 8, 2026  
**Status:** PROPOSAL DRAFT (no code/contract changes)  
**Proposer:** Revision 4 Validation Phase

---

## 1. Evidence Base

### Diagnostic Run
- **Run ID:** `slippage-20260908T120131-a47b2a2fb05c`
- **Commit:** `7ed6dce` (Regenerate stratified slippage diagnostic artifacts)
- **Observations:** 5,935 fills (48 symbols, NSE August 2024, sealed month)
- **Artifacts:**
  - Raw data: `diagnostic_output/raw_observations.csv`
  - Summary: `diagnostic_output/summary_statistics.json`

### Stratified Results

#### Intraday Gaps (5,917 observations)
- **Definition:** decision_date == fill_date (same calendar day)
- **Median slippage:** 0.0%
- **95th percentile:** 0.0534%
- **99th percentile:** 0.1070%
- **Maximum:** 0.5845%
- **Rejections @ 0.10%:** 68 fills (1.15%)
- **Rejections @ 0.15%:** 23 fills (0.39%)
- **Rejections @ 0.20%:** 12 fills (0.20%)

#### Session Gaps (18 observations)
- **Definition:** decision_date ≠ fill_date (cross-calendar-day fill)
- **Median slippage:** 0.6022%
- **95th percentile:** 3.1354%
- **99th percentile:** 3.1354%
- **Maximum:** 3.1354%
- **Rejections @ 0.10%:** 13 of 18 (72.22%)
- **Rejections @ 0.15%:** 11 of 18 (61.11%)
- **Rejections @ 0.20%:** 10 of 18 (55.56%)

---

## 2. Current Safety Contract

**Gate 16 (Slippage Tolerance)** in `revision4/gates_framework.py`:
```
slippage_tolerance_percent = 0.001  (0.10%)
```

**Status:** UNCHANGED by this proposal.

---

## 3. Proposed Changes

### 3A. Cross-Session Order Handling (APPROVED for Testing)

**Current behavior:** Orders submitted at end-of-session may fill after market close or next-day open, triggering session gaps.

**Proposed default:** Cross-session orders are **rejected by default** at pre-submission evaluation (Gate 13 or new gate).

**Criteria for cross-session rejection:**
- Decision timestamp date ≠ next bar timestamp date
- Reject order with message: "Cross-session order not authorized"
- Exception: Only if explicit authorization flag is set on OrderIntent

**Rationale:**
- Session gaps have median 0.602% slippage (>0.10% threshold)
- 72% of session gaps exceed current tolerance
- Cannot apply same tolerance as intraday without separate governance
- Prohibition with override keeps default safe; allows exceptional cases with audit trail

**Implementation location:** `revision4/timestamp_orchestrator.py` pre-submission gate sequence

**Test requirement:** End-to-end tests validating:
1. Orders scheduled near session boundary are rejected
2. Explicit authorization flag bypasses rejection (with audit logging)
3. Rejected orders are reported in CompletedTrade with reason="session_gap_rejected"

---

### 3B. Intraday Threshold Review Path (PROPOSED for Future Testing)

**Current:** 0.10% tolerance rejects 1.15% of normal intraday fills (68 of 5,917).

**Candidate change:** 0.15% tolerance rejects 0.39% of intraday fills (23 of 5,917).

**Status:** Candidate only—no approval yet.

**Before any change to intraday tolerance:**
1. ✅ Formal versioned safety contract (this document)
2. ✅ Lifecycle tests of gate logic with new tolerance
3. ✅ Full 48-symbol replay with new tolerance
4. ✅ Reconciliation and P&L validation
5. ✅ Explicit approval commit with audit trail

**Decision deferred** until after cross-session rejection path is validated.

---

## 4. Contract Maintenance

### Unchanged Items
- Gate 16 threshold: `0.001` (0.10%)
- Gate 16 logic: close[t] vs open[t+1]
- Gate 16 rejection message format
- All other gates (1-15, 17-18): no changes

### New Items (If Approved)
- Cross-session rejection gate (location TBD, pre-submission)
- OrderIntent.authorized_cross_session flag (optional, defaults False)
- CompletedTrade.rejection_reason field must include "session_gap_rejected" type

---

## 5. Next Steps (In Sequence)

### Phase 1: Cross-Session Rejection (Immediate)
1. Implement cross-session rejection gate in orchestrator pre-submission
2. Add OrderIntent.authorized_cross_session flag
3. Add CompletedTrade.rejection_reason="session_gap_rejected" logging
4. Write end-to-end tests (orchestrator + broker + ledger)
5. Validate on SUNPHARMA/August sealed month
6. Validate on 48-symbol sealed month
7. Commit with test evidence

### Phase 2: Intraday Threshold Review (After Phase 1)
1. Formal safety-contract version (e.g., v1.1)
2. Update Gate 16 tolerance in new proposal document
3. Lifecycle and reconciliation tests
4. 48-symbol replay with new tolerance
5. Explicit approval before commit

### Phase 3: Calibration (After Both Phases)
- Only after orchestrator is proven on real data with stable contract

---

## 6. Audit Trail

- **Diagnostic commit:** `7ed6dce`
- **Proposal date:** September 8, 2026
- **Proposal commit:** (TBD after cross-session tests pass)
- **Contract version:** Current = 1.0 (0.10% unchanged)

---

## 7. Risk Assessment

**If cross-session rejection is not implemented:**
- Orders filled on next day will fail Gate 16 (1.36% measured in 18 session observations)
- False rejections of valid fills (session gap is not execution failure)
- Audit visibility into session-gap decisions lost

**If intraday tolerance change is approved without testing:**
- Wider tolerance allows larger slippage without rejection
- Must validate reconciliation and P&L impact
- Must prove no safety regression

**Mitigation:** Staged rollout with tests at each phase.

---

## Approval Status

- [ ] Cross-session rejection gate implementation + tests
- [ ] Intraday threshold review (formal version, TBD)
- [ ] Contract change commit (after both approved)

**Current:** Proposal stage. No code changes. No contract modifications.
