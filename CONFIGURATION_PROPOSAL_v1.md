# Configuration Proposal: minimum_absolute_profit_rupees

**Date:** 2026-09-09  
**Status:** Ready for Review (Do Not Apply)  
**Evidence Base:** SUNPHARMA Enhanced Diagnostic Report

---

## Problem Statement

Current parameter setting (`minimum_absolute_profit_rupees = ₹50.00`) rejects **100% of 2,631 MPC-approved plans** on real NSE SUNPHARMA data.

**Blocking Reason:** All observed plan profits (₹10.00–₹49.51) fall below ₹50 threshold.

**Impact:** Zero order execution, preventing testing of:
- Gate16 remediation lifecycle
- Cross-session rejection logic
- Order queueing and fill execution
- Reconciliation enforcement

---

## Parameter Classification

✅ **CALIBRATABLE** (not immutable safety)
- Registry: `canonical_parameter_registry.py:101`
- Calibratable flag: `True`
- Category: MPC (economic target)
- Range: 0.0–200.0

---

## Diagnostic Data

**Observed profit distribution (2,631 real plans):**

| Metric | Value |
|--------|-------|
| Minimum | ₹10.00 |
| 10th percentile | ₹10.52 |
| 25th percentile | ₹11.50 |
| Median (50th) | ₹13.46 |
| 75th percentile | ₹17.16 |
| 90th percentile | ₹21.56 |
| 95th percentile | ₹27.63 |
| 99th percentile | ₹39.97 |
| Maximum | ₹49.51 |

**All values < ₹50 (current threshold)**

---

## Proposed Options

### Option A: Conservative (₹20.00)
- **Approval rate:** ~15% (top tier only)
- **Threshold logic:** Approve only highest-expectation plans
- **Trade-off:** Minimal execution, maximum selectivity
- **Use case:** Risk-averse execution testing

### Option B: Moderate (₹15.00)
- **Approval rate:** ~25% (upper-middle tier)
- **Threshold logic:** Approve solid plans, reject marginal
- **Trade-off:** Balanced execution + risk control
- **Use case:** Stage 2 replay (only after ₹20 proves clean behavior)

### Option C: Aggressive (₹12.00)
- **Approval rate:** ~45% (broad tier)
- **Threshold logic:** Light filtering only
- **Trade-off:** Maximum execution visibility
- **Use case:** Diagnostic investigation only

---

## Approved Staged Approach

### Stage 1 (APPROVED): ₹20.00
- **Scope:** Conservative upper-tail sample only
- **Expected approval:** ~10-15% of plans (profit > ₹20)
- **Purpose:** First sealed paper-replay with clean order/fill/reconciliation
- **All other parameters:** Frozen at defaults
- **Versioning:** `SUNPHARMA_v20_sealed_replay`

### Stage 2 (Conditional): ₹15.00
- **Approved only if:** Stage 1 produces safe executions and exact reconciliation
- **Scope:** Broader sample (~25% approval)
- **Purpose:** Verify execution at increased volume
- **Versioning:** `SUNPHARMA_v15_sealed_replay` (separate, never overwrites ₹20)

### Stage 3 (Diagnostic): ₹12.00
- **Reserved for:** Investigation only if earlier stages fail
- **Never for:** Staged execution testing

## Implementation (Stage 1)

### Change Location
File: `calibration_config.py` (override, not registry defaults)

```python
{
    "minimum_absolute_profit_rupees": 20.0,  # From 50.0
    # ALL OTHER PARAMETERS: UNCHANGED
}
```

### Versioning
- Stage 1 report: `inhouse_sunpharma_sealed_report_v20.json`
- Comparison baseline: `inhouse_sunpharma_sealed_report_v50.json` (current)

### Safety Verification
- ✅ Kill switch: locked to `True` (unchanged)
- ✅ Drawdown halt: locked to 25% (unchanged)
- ✅ Daily loss cap: locked to ₹50,000 (unchanged)
- ✅ Other safety gates: operational and monitoring

**Lowering profit floor does NOT bypass immutable safety protections.**

---

## Next Steps (After Review)

1. ✅ Review this proposal with stakeholder
2. ⏳ Select option (A, B, C, or alternative value)
3. ⏳ Apply to configuration
4. ⏳ Rerun SUNPHARMA diagnostic
5. ⏳ Verify orders and fills appear
6. ⏳ Proceed to Gate16 remediation validation

---

## Verification Checklist

- [x] Parameter identified: `minimum_absolute_profit_rupees`
- [x] Classification confirmed: calibratable economic parameter
- [x] Safety nature verified: NOT part of immutable safety contract
- [x] Diagnostic evidence collected: real profit distribution
- [x] Options analyzed: conservative, moderate, aggressive
- [x] Safety gates verified: separate and unaffected
- [ ] Proposal approved (pending review)
- [ ] Configuration applied (pending approval)

---

## Approval Status

✅ **Stage 1 (₹20) APPROVED**
- Conservative upper-tail sample
- Sealed paper-replay with all other parameters frozen
- Proceed to implementation

⏳ **Stage 2 (₹15) CONDITIONAL**
- Approved only after Stage 1 shows clean behavior
- Separate versioned run

❌ **Stage 3 (₹12) RESERVED**
- Diagnostic only; not for staged execution

