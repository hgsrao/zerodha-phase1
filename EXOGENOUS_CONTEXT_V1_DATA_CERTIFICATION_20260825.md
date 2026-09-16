# Exogenous Context V1 — Data Certification
## 25 August 2026

**OVERALL STATUS: CERTIFIED WITH DOCUMENTED SOURCE FLAGS**

### INDIA VIX — 15 minute

Classification: **CERTIFIED WITH SOURCE FLAGS**

- Rows: 65,662
- Source OHLC-envelope flags: 56
- Flag window: 2018-12-10 to 2019-01-21
- Missing full session: 2019-07-02
- Raw data remains immutable.
- No interpolation or manual repair permitted.

### NIFTY BANK — 15 minute

Classification: **CERTIFIED WITH DOCUMENTED MISSING BARS**

- Rows: 65,685
- Invalid OHLC: 0
- Two timestamps absent on 2022-03-07 relative to INDIA VIX.
- No interpolation permitted.

### Continuous NIFTY futures — daily OHLCV

Classification: **CERTIFIED**

- Rows: 2,638
- Exact trading-date match with NIFTY BANK.
- Zero-volume rows: 0
- Negative-volume rows: 0

### Continuous NIFTY futures — OI

Classification: **CERTIFIED WITH AVAILABILITY BOUNDARY**

- Historical OI unavailable through 2019-01-21.
- First usable non-zero OI: 2019-01-22.
- Isolated missing OI: 2019-08-28.
- No OI interpolation permitted.
- Modern 2023-07-03 to 2026-08-24 overlap has zero missing/zero-OI rows.

### Research status

No feature definitions, hypotheses, outcome joins, or models have yet been created.

P01D remains unchanged and sovereign.

JSON SHA256: 18e4ab2b0c10af96b4e47ed00c47f2042e02ad16164ab708a848b1b182a14a2f
