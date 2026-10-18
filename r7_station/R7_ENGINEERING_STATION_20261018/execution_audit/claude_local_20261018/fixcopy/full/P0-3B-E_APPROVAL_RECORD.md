# P0-3B-E Release Candidate Approval Record

**Generated:** 2026-08-12 (Asia/Calcutta)  
**Status:** APPROVED FOR STAGING SOAK (`LIVE_TRADING_ENABLED = False`)  
**Gate 4:** LOCKED

## 1. Implementation Scope

- Durable `ENTRY_SUBMITTING` and `ENTRY_UNKNOWN` states.
- Store-first immutable entry-submission fingerprinting.
- Broker-authoritative recovery after ambiguous transport outcomes.
- Automatic BUY resubmission prohibited after the durable side-effect boundary.
- Fail-closed handling for malformed observations, duplicate exact matches,
  conflicting intent, and exhausted reconciliation budgets.

## 2. Validation Evidence

- Active candidate regression suite: **115 passed in 1.55s**.
- P0-3B-E crash/restart simulation matrix: **6 passed in 0.03s**.
- Mandatory matrix coverage:
  1. Timeout before broker acceptance.
  2. Timeout after broker acceptance.
  3. Delayed broker-order visibility.
  4. Duplicate exact matching orders.
  5. Malformed broker observations.
  6. Reconciliation-budget exhaustion.
- Dedicated `pytest.ini` discovery excludes the superseded uncorrected P0-3
  duplicate while retaining the corrected active safety suite.

## 3. Cryptographic Manifest (SHA-256)

- `institutional_engine_v34_p01d_candidate.py`  
  `97319F7688D36A5424083CB595C08A06E145CDCBE61125CF9A1E1E7D7FF130DA`
- `test_v34_entry_submit_restart_recovery.py`  
  `43749A22615C774D63BA03286080C1FECCFB6DB5D3596706CF23DA72E4DA7002`
- `test_v34_entry_crash_restart_matrix.py`  
  `52637B1FE282D1F33C1C6FAA65E9FD4B63E880A2F73A91380537ED102A65E756`
- `pytest.ini`  
  `3742FB483D80492E0A6E97F0154AB5A375D873471B20EF708A90E69982641C61`
- `nontrading_entry_soak.py`  
  `F0BA54DC5008FDA5F898FDEBF2FF4F96F16D56886228204F60D829FB16A27EE7`
- `test_v34_entry_nontrading_soak.py`  
  `A90659DC3CAEE89B9669A4695E85829B30E09A3D3E321AFC10AC50E2E337EB7B`
- `P0-3B-E_NONTRADING_SOAK_REPORT.md`  
  `C51E878D99DF88D0ADA362E3A6DBB93CBC81E35930BBCE4E558796215E0CB90D`

## 4. Safety Invariants

- `LIVE_TRADING_ENABLED = False` is explicitly configured in
  `run_production_p01d_candidate.py`.
- Gate 4 remains locked.
- Validation used local fake brokers only.
- No production runner was started.
- No broker write operation or trade was initiated during build or validation.

## 5. Release Boundary

This approval authorizes only a non-trading staging soak. It does not authorize
enabling live trading, unlocking Gate 4, connecting a production execution
runner, or placing, modifying, or cancelling broker orders.

## 6. Non-Trading Staging Soak

- Deterministic campaign: **10,000 cycles passed**.
- Timed randomized campaign: **93,318,600 cycles passed in 30 minutes**.
- Active regression suite after soak integration: **116 passed**.
- Automatic duplicate submissions observed: **0**.
- Full evidence is recorded in `P0-3B-E_NONTRADING_SOAK_REPORT.md`.
