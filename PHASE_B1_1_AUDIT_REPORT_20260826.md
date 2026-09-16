# PHASE B1.1 — POLARS AUDIT + ENVIRONMENT RECORD

**Date:** 2026-08-26  
**Status:** ✓ PASS

---

## Environment & Installation

### Python
- **Version:** 3.13.5
- **Executable:** `C:\Users\Dishan\AppData\Local\Programs\Python\Python313\python.exe`

### Polars
- **Version:** 1.44.0
- **License:** MIT (open-source)
- **Installation Path:** `.venv_polars_research\Lib\site-packages\polars`
- **Isolated Environment:** YES (separate venv from production runtime)

### Dependencies
- **PyArrow:** 25.0.1 (for Parquet read/write)
- **polars-runtime-32:** 1.44.0 (compiled accelerators)
- **NumPy:** Not installed (not required; Polars is self-contained)
- **Pandas:** Not installed (not required; avoid Python-level interchange)

### Capabilities Verified
| Feature | Status |
|---------|--------|
| read_parquet | ✓ Enabled |
| write_parquet | ✓ Enabled (via PyArrow 25.0.1) |
| read_json | ✓ Enabled |
| read_ndjson | ✓ Enabled |
| DataFrame creation | ✓ OK |

---

## Raw L2 JSONL Schema (from Frozen Contract)

The raw collector (`raw_l2_microstructure_recorder_v1.py`) produces JSONL with these fields:

### Metadata
- `symbol` (string, required)
- `exchange_timestamp` (ISO 8601, UTC, required)
- `last_trade_time` (ISO 8601, UTC, optional)

### Market Data
- `instrument_token` (integer, optional)
- `last_price` (float, required for validity)
- `last_quantity` (integer, optional)
- `average_price` (float, optional)
- `volume` (integer, optional)
- `buy_quantity` (integer, optional)
- `sell_quantity` (integer, optional)
- `net_change` (float, optional)

### OHLC Intrabar
- `ohlc.open` (float, optional)
- `ohlc.high` (float, optional)
- `ohlc.low` (float, optional)
- `ohlc.close` (float, optional)

### Order Book Depth (up to 5 levels each side)
**Bids (buy side):**
- `depth.buy[0].price`, `depth.buy[0].quantity`, `depth.buy[0].orders`
- ... (repeats for levels 1–4)

**Asks (sell side):**
- `depth.sell[0].price`, `depth.sell[0].quantity`, `depth.sell[0].orders`
- ... (repeats for levels 1–4)

---

## Canonical Columnar Schema (V1)

Designed for deterministic, type-safe transformation of raw JSONL → Parquet.

### Identifiers & Provenance (required)
```
session_date          : Date         (YYYY-MM-DD from directory)
observation_timestamp : Datetime[UTC] (exchange_timestamp, required)
observation_timestamp_ist : Datetime[Asia/Kolkata] (derived, required)
symbol                : String       (required)
instrument_token      : Int64 (nullable)
source_json_line_num  : UInt32       (line number in raw JSONL)
```

### Market Data (market-observed values)
```
last_price           : Float32
last_quantity        : Int32 (nullable)
average_price        : Float32 (nullable)
volume               : Int64 (nullable)
buy_quantity         : Int64 (nullable)
sell_quantity        : Int64 (nullable)
net_change           : Float32 (nullable)
last_trade_timestamp : Datetime[UTC] (nullable)
```

### OHLC Intrabar
```
ohlc_open            : Float32 (nullable)
ohlc_high            : Float32 (nullable)
ohlc_low             : Float32 (nullable)
ohlc_close           : Float32 (nullable)
```

### Bid Levels (1–5, with explicit sparse/empty distinction)
```
bid_1_price   : Float32 (nullable=populated, null=empty level)
bid_1_qty     : Int32 (nullable)
bid_1_orders  : Int32 (nullable)
...
bid_5_price   : Float32 (nullable)
bid_5_qty     : Int32 (nullable)
bid_5_orders  : Int32 (nullable)
bid_populated_count : UInt8 (0–5, how many levels have price+qty)
```

### Ask Levels (1–5, with explicit sparse/empty distinction)
```
ask_1_price   : Float32 (nullable=populated, null=empty level)
ask_1_qty     : Int32 (nullable)
ask_1_orders  : Int32 (nullable)
...
ask_5_price   : Float32 (nullable)
ask_5_qty     : Int32 (nullable)
ask_5_orders  : Int32 (nullable)
ask_populated_count : UInt8 (0–5)
```

### Validity Flags (step-2 feature engine input)
```
record_valid         : Boolean (all required fields non-null)
has_bids             : Boolean (any valid bid level)
has_asks             : Boolean (any valid ask level)
```

---

## Adversarial Test Fixture Plan

### Test Suite: `test_l2_canonical_columnar_pipeline_v1.py`

#### Category A: Valid Data

1. **test_valid_complete_record**
   - All fields populated
   - 5 bid levels populated
   - 5 ask levels populated
   - Session-bound timestamps
   - Expected: PASS, all fields present

2. **test_sparse_depth_legitimate**
   - 2 bid levels only
   - 1 ask level only
   - Remaining levels null (not zero)
   - Expected: PASS, populated counts = 2/1

#### Category B: Missing/Malformed Data

3. **test_missing_required_field**
   - No exchange_timestamp
   - Expected: record_valid = false

4. **test_missing_price_level**
   - Bid has quantity but no price
   - Expected: bid_populated_count excludes that level

5. **test_nan_infinity**
   - last_price = NaN, Infinity
   - Expected: null (not converted to 0)

6. **test_negative_values**
   - Negative quantity/price
   - Expected: Preserved in raw column; step-2 flags as anomaly

#### Category C: Depth Schema

7. **test_no_depth_container**
   - depth field missing entirely
   - Expected: all bid/ask columns null

8. **test_depth_not_list**
   - depth.buy is string instead of list
   - Expected: parsing error OR all bid columns null

9. **test_mixed_populated_empty**
   - bid[0] = valid, bid[1] = {}, bid[2] = valid, bid[3..4] = null
   - Expected: bid_populated_count = 2, values at indices 0 and 2 only

#### Category D: Timestamps

10. **test_timestamp_parsing**
    - ISO 8601 with timezone
    - Expected: observation_timestamp_ist correctly converted to IST

11. **test_out_of_session**
    - Timestamp before 09:15 or after 15:15 IST
    - Expected: Preserved in row; step-2 layer flags

#### Category E: Source Immutability

12. **test_raw_jsonl_unchanged**
    - Read raw JSONL
    - Transform to Parquet
    - Re-read raw JSONL
    - Expected: byte-for-byte identical, SHA256 unchanged

13. **test_parquet_deterministic**
    - Same input → same output Parquet
    - Expected: Output Parquet SHA256 stable across runs

#### Category F: Type Safety

14. **test_dtype_preservation**
    - Write Parquet with strict types
    - Re-read Parquet
    - Expected: Types unchanged (no float32→float64 drift)

15. **test_null_handling**
    - Nullable fields properly encoded
    - Expected: Polars null preserves as Parquet NULL

---

## Constraints Verification

| Constraint | Status |
|-----------|--------|
| Zero broker calls | ✓ Confirmed |
| Zero order writes | ✓ Confirmed |
| LIVE_TRADING_ENABLED = False | ✓ Confirmed |
| P01D untouched | ✓ Confirmed |
| No PA training | ✓ Confirmed |
| No feature selection | ✓ Confirmed |
| Raw JSONL immutable | ✓ Confirmed (read-only pipeline) |
| Isolated environment | ✓ Confirmed (.venv_polars_research) |
| Polars licensed | ✓ MIT (permissive open-source) |

---

## Key Design Decisions

1. **Nullable depth levels:** NULL represents an empty/missing level, not zero. This preserves the distinction between "no liquidity" (NULL) and "zero quantity" (0).

2. **Populated counts:** `bid_populated_count` / `ask_populated_count` explicitly track how many levels have valid price+qty, enabling efficient step-2 feature computation.

3. **Timestamp dualization:** Both UTC and IST preserved for different use cases (data integrity audits use UTC; session-bound analysis uses IST).

4. **Parquet as derived:** Raw JSONL is sovereign; Parquet is deterministically reproducible and lives in a separate directory tree.

5. **No Pandas interchange:** Polars works natively; avoid Python-level conversions that lose metadata/precision.

---

## Next Steps: PHASE B1.2

Build the candidate:
```
l2_canonical_columnar_pipeline_v1.py
test_l2_canonical_columnar_pipeline_v1.py
```

This pipeline will:
1. Accept a certified raw L2 JSONL path
2. Read and validate against the frozen schema
3. Transform to canonical columnar format (above)
4. Write to Parquet in `L2_MICROSTRUCTURE_COLUMNAR/YYYY-MM-DD/l2_top5.parquet`
5. Create manifest with provenance and hashes
6. Verify source JSONL SHA256 unchanged

---

## Unresolved Issues

None at this stage. Ready to proceed to B1.2.

---

**Report:** PHASE B1.1: PASS ✓
