# P0-3B-E Non-Trading Staging Soak Report

**Date:** 2026-08-12 (Asia/Calcutta)  
**Result:** PASS  
**Release boundary:** Non-trading staging only  
**Gate 4:** LOCKED  
**LIVE_TRADING_ENABLED:** `False`

## Deterministic Campaign

- Seed: `34035`
- Cycles: **10,000**
- Elapsed time: **0.203 seconds**
- Accepted/recovered: **2,467**
- Duplicate exact-order observations: **2,565**
- Malformed observations: **2,458**
- Submission not accepted: **2,510**
- Automatic duplicate submissions: **0**

## Timed Randomized Campaign

- Seed: `34035`
- Duration: **1,800.000 seconds (30 minutes)**
- Cycles: **93,318,600**
- Accepted/recovered: **23,326,634**
- Duplicate exact-order observations: **23,332,628**
- Malformed observations: **23,331,917**
- Submission not accepted: **23,327,421**
- Automatic duplicate submissions: **0**

## Regression Evidence

- Active candidate suite after soak integration: **116 passed in 1.74s**.

## Safety Evidence

- The soak harness used in-memory fake brokers only.
- The production runner was not imported or started.
- No broker network connection was used.
- No credentials were read.
- No real order was placed, modified, or cancelled.
- Ambiguous entry submission was never automatically retried.
- Duplicate and malformed broker evidence failed closed.
- Reconciliation-budget exhaustion failed closed.

## Conclusion

P0-3B-E passed deterministic and timed non-trading staging soak validation.
This report does not authorize live trading or unlocking Gate 4.

