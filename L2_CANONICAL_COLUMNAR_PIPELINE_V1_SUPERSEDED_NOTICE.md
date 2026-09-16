# L2 CANONICAL COLUMNAR PIPELINE V1 — SUPERSEDED BEFORE FREEZE

**Date:** 2026-08-26  
**Status:** SUPERSEDED_CANDIDATE_BEFORE_FREEZE / BEFORE REAL DATA USE  
**Reason:** Fundamental schema mismatch with actual frozen recorder output

---

## **The Defect**

V1 pipeline assumed a **flat per-symbol record structure**:

```
ONE JSONL LINE = ONE SYMBOL OBSERVATION
```

With top-level `symbol` field and per-symbol market/depth data.

**Actual frozen recorder output is NESTED:**

```
ONE JSONL LINE = ONE COLLECTION CYCLE
  ├─ cycle-level fields (observed_at_utc, observed_at_ist, cycle number)
  └─ snapshots dictionary (0 to N per-symbol nested records)
```

---

## **Impact on V1 Candidate**

The pipeline V1 candidate in this session:
- Assumed flat per-symbol row structure
- Did not extract cycle-level observation timestamps correctly
- Would not properly flatten nested snapshots dictionary
- Could not preserve cycle identity across per-symbol canonical rows
- Assumed fabrication of missing symbols to fill 48-symbol grid

---

## **Path Forward**

**L2 CANONICAL COLUMNAR PIPELINE V2** (in progress):
- Understands nested cycle structure
- Flattens snapshots dict to per-symbol canonical rows (zero to N, never fabricated)
- Preserves cycle-level observation timestamps
- Keeps three timestamp domains separate
- Implements field-specific quality provenance

---

## **Legacy**

This file is sealed as-is. Do not modify.

V1 pipeline candidate remains frozen in source tree as historical artifact.

Never use V1 on real L2 data.
