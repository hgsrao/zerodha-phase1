# L2_DATASET_CERTIFIER_V1 — SUPERSEDED BEFORE REAL USE

**Date:** 2026-08-26  
**Status:** SUPERSEDED_BEFORE_REAL_USE  
**Reason:** Fundamental schema mismatch with actual frozen recorder output

---

## **The Defect**

L2_DATASET_CERTIFIER_V1 assumes a **flat record structure**:

```
ONE JSONL LINE = ONE SYMBOL RECORD
```

With top-level `symbol` field and per-symbol market/depth data.

**Actual frozen recorder output is NESTED:**

```
ONE JSONL LINE = ONE COLLECTION CYCLE
  ├─ cycle-level fields (observed_at_utc, observed_at_ist, cycle number)
  └─ snapshots dict (48 per-symbol nested records)
```

---

## **Impact**

V1 certifier cannot correctly parse real raw L2 data:

- Treats entire cycle object as a single symbol record
- Looks for top-level `symbol` field (finds none)
- `symbol = raw.get("symbol", "UNKNOWN")` → always `"UNKNOWN"`
- Misses observation timestamps (observed_at_utc, observed_at_ist)
- Cannot understand cycle structure
- Cannot count cycles vs symbol observations separately
- Cannot audit collection cadence (1,440 cycles/day)

---

## **Authority**

- Frozen Recorder: `raw_l2_microstructure_recorder_v1.py` (lines 950–978)
- Frozen Contract: `RAW_L2_MICROSTRUCTURE_V1_COLLECTION_FREEZE_20260825.json`
- Both define NESTED cycle structure explicitly

V1 certifier was built against a misunderstood schema and is not repairable in-place.

---

## **Path Forward**

**L2_DATASET_CERTIFIER_V2** (in progress):
- Understands nested cycle structure
- Parses cycle-level observation timestamps
- Flattens snapshots to per-symbol analysis
- Tracks cycle coverage (1,440 slots) vs symbol coverage (48 per cycle)
- Preserves cycle provenance
- Adds comprehensive quality accounting

---

## **Legacy Note**

This file is sealed as-is. Do not modify.

L2_DATASET_CERTIFIER_V1.py remains frozen in source tree as a historical artifact.

Never use V1 on real L2 data.
