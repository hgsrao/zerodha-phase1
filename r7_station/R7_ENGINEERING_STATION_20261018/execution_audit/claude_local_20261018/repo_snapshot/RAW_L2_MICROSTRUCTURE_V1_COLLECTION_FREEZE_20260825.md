# Raw L2 Microstructure V1 — Collection Freeze
## 25 August 2026

**STATUS: FROZEN BEFORE FIRST PROSPECTIVE L2 OBSERVATION**

### Recorder

- File: raw_l2_microstructure_recorder_v1.py
- SHA256: d1cb2e2d95a3eece39c9f54cfdf500f19f0128a9eaef99daf83e9cf205914b7b
- Self-test: PASS
- Broker operation when running: quote only
- Broker-write authority: NONE

### Universe

48 authoritative equities.

### Collection schedule

- Interval: 15 seconds
- Timezone: Asia/Kolkata
- Continuous-session window: 09:15 <= time < 15:15
- Storage: append-only JSONL
- Raw order book: up to five bid and five ask levels

### Phase 1 purpose

Collect and certify genuine raw market-depth observations.

There is currently:

- no OBI formula,
- no OBI threshold,
- no microstructure score,
- no trading interpretation,
- no entry/exit rule,
- no predictive model.

The previous experimental obi >= 0.2 threshold is **not**
adopted by this research branch.

### Research boundary

Feature definitions will be created only in a later,
separately frozen research phase.

Raw collection itself cannot authorize trading.

P01D remains unchanged and sovereign.

### Freeze JSON SHA256

daf7d9aa9e8c5cababf618b3f4c14e39fa211eab16c1cb9d81e9af5a1004a303
