# L2 DATASET CERTIFIER V2 — FROZEN 2026-08-26

**Status:** IMMUTABLE SEALED  
**Scope:** L2 Dataset Certifier V2 ONLY (not Pipeline V2)  
**Date:** 2026-08-26

---

## CERTIFIER IDENTITY

**Source File:** `l2_dataset_certifier_v2.py`  
**SHA256:** `0FBB4E8000D18F951C508DF179DD4A27D9A31F4D5A36C77F62A0D4DEAC7F0568`

**Decision Logic:** Corrected 2026-08-26  
**Frozen Rule Set:** `decision_precedence_deterministic_20260826`

---

## TEST RESULTS (FROZEN)

| Metric | Value |
|--------|-------|
| Collected | 36 |
| Passed | 36 |
| Failed | 0 |
| Skipped | 0 |
| Status | ✅ GREEN |

### Test Files

- `test_l2_dataset_certifier_v2.py` (6 tests)  
  SHA256: `B0DE7690274249399092C0AD76546C718253E25D6DFCFB1483C10B2ACFCE3014`

- `test_l2_dataset_certifier_v2_expanded.py` (16 tests)  
  SHA256: `36C5B59455D9B4A92B1968B5C1A0320BD4946ADDF170335D754AFA03F1A48E24`

- `test_b1_2_v2_decision_precedence_and_binding.py` (14 tests)  
  SHA256: `E1CB34D3BC274BEFB76DF45DA00B08F6D4A5AE19E1EC28E37E11AB024624B72A`

---

## UPSTREAM CONTRACTS (FROZEN)

| Contract | File | SHA256 | Status |
|----------|------|--------|--------|
| Raw Recorder | `raw_l2_microstructure_recorder_v1.py` | `D1CB2E2D95A3EECE39C9F54CFDF500F19F0128A9EAEF99DAF83E9CF205914B7B` | FROZEN |
| Collection Contract | `RAW_L2_MICROSTRUCTURE_V1_COLLECTION_FREEZE_20260825.json` | `DAF7D9AA9E8C5CABABF618B3F4C14E39FA211EAB16C1CB9D81E9AF5A1004A303` | FROZEN |

---

## SESSION GEOMETRY (FROZEN)

- **Session Start:** 2026-08-26 09:15:00 IST = 03:45:00 UTC
- **Session End:** 2026-08-26 15:15:00 IST = 09:45:00 UTC
- **Cadence:** 15 seconds
- **Theoretical Cycle Slots:** 1,440
- **Maximum Symbol Observations:** 69,120 (only when every cycle contains all 48 symbols)

### Authoritative Universe

48 NSE symbols frozen:

```
ADANIENT, ADANIPORTS, APOLLOHOSP, ASIANPAINT, AXISBANK,
BAJAJ-AUTO, BAJAJFINSV, BAJFINANCE, BEL, BHARTIARTL,
CIPLA, COALINDIA, DRREDDY, EICHERMOT, ETERNAL,
GRASIM, HCLTECH, HDFCBANK, HDFCLIFE, HINDALCO,
HINDUNILVR, ICICIBANK, INDIGO, INFY, ITC,
JIOFIN, JSWSTEEL, KOTAKBANK, LT, M&M,
MARUTI, MAXHEALTH, NTPC, ONGC, POWERGRID,
RELIANCE, SBILIFE, SBIN, SHRIRAMFIN, SUNPHARMA,
TATACONSUM, TATASTEEL, TCS, TECHM, TITAN,
TRENT, ULTRACEMCO, WIPRO
```

---

## RAW DATA GEOMETRY (FROZEN)

**One JSONL Line = One Collection Cycle**

### Cycle Structure

```json
{
  "observed_at_utc": "<ISO 8601 UTC timestamp>",
  "observed_at_ist": "<ISO 8601 +05:30 timestamp>",
  "cycle": <integer>,
  "source": "<source identifier>",
  "snapshots": {
    "<symbol>": {
      "valid": <boolean>,
      "last_price": <float>,
      "depth": {"buy": [...], "sell": [...]},
      ...
    },
    ...
  }
}
```

### Snapshots Dictionary

- **Key:** Authoritative symbol or unknown string
- **Value:** Dictionary or malformed
- **Cardinality:** 0 to N symbols per cycle (never fabricated to 48)

---

## ACCOUNTING INVARIANTS (FROZEN)

### File Level
```
nonempty_lines = parseable_cycles + malformed_json_lines
```

### Cycle Level
```
total_snapshot_entries = canonical_entries + rejected_entries
```

**Enforcement:** Must pass or session status becomes HOLD.

---

## DECISION PRECEDENCE (FROZEN)

### Precedence Order

#### 1. FAIL (Highest Priority)

**Condition:** Unrecoverable structural or provenance corruption

**Examples:**
- `malformed_json_lines > 0`
- `parseable_cycles == 0`
- Missing cycle-level observation timestamps
- Snapshots container not a dictionary
- Any condition preventing trustworthy interpretation

---

#### 2. HOLD

**Condition:** Incomplete or insufficient session for intended certification

**Includes:**
- `parseable_cycles < 95% of frozen 1,440 slots`
- Missing opening cycles
- Missing closing cycles
- Internal gaps in cycle coverage

**Critical Rule:** Source anomalies CANNOT upgrade HOLD to PASS_WITH_SOURCE_FLAGS.

---

#### 3. PASS_WITH_SOURCE_FLAGS

**Condition:** Complete session with legitimate non-fatal source anomalies

**Requirements:**
- All 1,440 theoretical cycle slots present
- All timestamps valid and reconstructible deterministically
- No data loss or ambiguity
- Anomalies: reversed cycles OR missing per-cycle symbols

**Allowed Anomaly Types:**

1. **Reversed Cycles**
   - Definition: Cycles appear in non-chronological order in raw JSONL
   - Requirement: All 1,440 unique cycle times exist and are valid
   - Requirement 2: No duplicate timestamps or missing observations
   - Requirement 3: Canonical output may sort a derived copy; raw is immutable
   - Condition for Approval: Complete timestamp set is recoverable deterministically

2. **Missing Authoritative Symbol**
   - Definition: One or more authoritative symbols absent from one or more cycles
   - Requirement: Session is otherwise complete (all 1,440 cycles present)
   - Condition for Approval: Sparse symbol coverage does not prevent analysis

**Forbidden Under PASS_WITH_SOURCE_FLAGS:**
- Data loss or ambiguity
- Incomplete cycle coverage
- Unreconstructible timestamps
- Duplicate observations
- Structural corruption with "flags" workaround

---

#### 4. PASS (Lowest Priority)

**Condition:** Complete and clean session

**Requirements:**
- All 1,440 cycles present
- No reversed/out-of-order cycles
- No missing authoritative symbols in any cycle
- Raw ordering matches chronological order

---

## RAW IMMUTABILITY (FROZEN)

- **Requirement:** Raw JSONL must not be modified during certification
- **Verification:** SHA256 before == SHA256 after processing
- **Enforcement:** FAIL if immutability violated

---

## BROKER AND EXECUTION CONSTRAINTS (FROZEN)

- **Broker Imports:** NONE
- **Broker Calls:** ZERO
- **Execution Authority:** FALSE
- **Production Runner Dependency:** NONE
- **Certifier Authority:** Read-only audit only

---

## FREEZE COVENANT

**DO NOT MODIFY THIS FILE.**

Any modification nullifies freeze. Re-freeze required.

- Do not append clauses (semantic freeze is closed)
- Do not weaken decision rules (precedence is immutable)
- Do not create workarounds (all exceptions → new freeze iteration)
- Refuse overwrite on restart (freeze file is append-only; existing = sealed)

---

## NEXT PHASE

**Pipeline V2 Binding**

Certifier V2 freeze binds the identity:

```
L2_DATASET_CERTIFIER_V2_FREEZE_20260826.json SHA256
```

Pipeline V2 must require this exact freeze artifact before canonicalization.

Blocked conditions:
- Missing certification artifact
- Raw JSONL SHA mismatch
- Tampered certification artifact
- Superseded Certifier V1 identity
- Wrong/missing Certifier V2 freeze reference

---

**Certifier V2 is now SEALED for production use.**

*Pipeline V2 freeze awaits binding verification.*
