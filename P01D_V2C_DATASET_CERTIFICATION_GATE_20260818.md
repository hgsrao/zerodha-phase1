# V2-C — Dataset Certification Gate

**STATUS: FROZEN SPECIFICATION. No data acquired. No labels generated.
No model trained.** Defines what "certified" means for the historical
15-minute dataset `P01D_V2C_OUTCOME_RESOLVER_D0_20260818.md` requires,
before epoch boundaries can be frozen or any label generated. This is a
data-engineering gate, not a research result — nothing here produces or
implies a performance number.

## Why this gate exists, precisely

`P01D_V2C_MASTER_RESEARCH_RECORD_CORRECTION_20260817.md` states epoch
boundaries are genuinely blocked on acquiring this dataset, and that the
split "must be fixed by DATE, not by row count or performance" and
"BEFORE ANY LABEL IS GENERATED." That sequencing is preserved here:
**acquire and certify first; freeze epoch dates from the certified
coverage second; generate labels third.** Committing to exact dates
before knowing what's actually obtainable would risk quietly picking
dates convenient to a result that doesn't exist yet.

## 1. Coverage target

As broad a historical window as the point-in-time NIFTY 50 membership
infrastructure already supports (per `p01d-v2c-master-record`: 79
membership intervals, 78 acquisition rows, 76 unique tradeable security
keys, 0 count failures) — **not a pre-committed exact date range.** Kite
Connect's actual per-request lookback limit for 15-minute-interval
historical data is not assumed here; it will be discovered empirically
during acquisition (likely requiring many chunked requests per symbol,
stitched together) and the *achieved* coverage, not a wished-for one,
is what gets certified.

## 2. Symbols

The full point-in-time universe already reconstructed for V2-C — not
today's NIFTY 50 constituents projected backward (explicitly prohibited
by the existing preregistration). Includes the already-handled identity/
membership transitions: TATAMTRDVR, HDFC/LTIM, JIOFIN demerger/index-
membership-vs-tradability, GRASIM/VEDL, SSLT/VEDL. Certification confirms
15-minute data was actually acquired for every symbol-interval the
point-in-time membership record says should exist, not just for whichever
symbols happened to be easy to fetch.

## 3. Point-in-time constituent membership — re-verified against the data itself

The existing membership/reconstitution system is trusted infrastructure,
but certification re-checks that the **acquired price data's own date
range** for each symbol is consistent with that symbol's certified
membership interval — a symbol should not have 15-minute bars dated
outside the window the membership record says it was a valid, tradeable
constituent for.

## 4. Timestamp / session integrity

- Every bar's timestamp falls within NSE equity session hours for its
  date (09:15–15:30 IST), on a genuine NSE trading day (weekday, not a
  holiday — cross-checked against the already-corrected V2-C calendar
  database, `V2C_REFERENCE\V2C_CALENDAR_DATABASE_CLOSURE_20260817.md`,
  2,125 sessions, not re-derived from scratch).
- No timestamp appears twice for the same symbol (duplicate-bar check).
- No session is missing bars mid-day in a way inconsistent with a full
  trading session (gap detection within a day, not just at the edges).

## 5. Missing / duplicate bar audit

Explicit count, per symbol, of: expected bars (from session calendar x
interval), actual bars acquired, missing bars, duplicate bars. **A
symbol with an unexplained missing-bar rate above a threshold fixed at
certification time (not decided per-symbol after seeing which ones look
inconvenient) is flagged, not silently interpolated or dropped.**

## 6. Corporate-action treatment

Splits, bonuses, and other corporate actions occurring within the
coverage window must be identified per symbol, and the adjustment
convention (split-adjusted vs. raw) stated explicitly and applied
consistently — an unadjusted split would appear as a dislocation-shaped
price jump and could contaminate label generation with an artifact
indistinguishable from a real extreme move. This is exactly the kind of
defect the resolver's Z-score-based trigger cannot itself detect.

## 7. NIFTY 50 index reference data

A genuine NIFTY 50 index series (not a proxy/ETF) covering the same
certified window, at the same 15-minute resolution, for any V2-C context
feature that needs it — following the same "no proxy substitution
without flagging it" discipline already applied elsewhere in this
project when NIFTYBEES was considered as a stand-in for the real index.

## 8. Immutable hashes

Once acquired and passing §§2–7, the dataset is hashed (per-symbol file
hashes plus one manifest hash covering the set) and treated as frozen
input — the same discipline as every other frozen artifact in this
project. Any later correction to the dataset (a genuinely found data
defect) is a dated, re-hashed correction, never a silent replacement.

## 9. What certification produces

A single `V2C_DATASET_CERTIFICATION_RECORD.md` stating: exact achieved
date coverage per symbol, the full symbol list, pass/fail per §§3–7,
and the dataset's hash manifest. **This record is what epoch boundaries
get frozen from next** — a separate, later step, not automatic once
certification passes.

## What this gate does not do

Does not acquire any data (a separate, explicit step requiring the
user's own Kite session — the same credential boundary as every other
data pull in this project). Does not fix epoch boundaries. Does not
generate any REVERTING/DETERIORATING/UNRESOLVED label. Does not open
TRAIN. `LIVE_TRADING_ENABLED` unaffected throughout.

## Honest scope note

Acquiring years of 15-minute data across ~76 point-in-time symbols,
likely requiring many chunked historical-data requests per symbol given
Kite's per-request lookback limits at this resolution, is a substantial,
multi-session data-engineering effort — not something to start at the
tail end of an already long session. Recommended as its own dedicated
next session, with the acquisition script built and reviewed before any
live Kite calls are made, same pattern as every other historical pull in
this project.
