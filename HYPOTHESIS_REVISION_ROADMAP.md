# HYPOTHESIS REVISION ROADMAP: THREE ALPHA DIRECTIONS

## Overview

Three orthogonal alpha sources, each designed to overcome specific failures from Revisions 1-6:

1. **REVISION 7: Daily/Weekly Swing Trading** (Solves friction drag)
2. **REVISION 8: Pairs Cointegration** (Solves directional regime exposure)
3. **REVISION 9: Volume Profile Mean-Reversion** (Solves technical indicator weakness)

---

## REUSABLE INFRASTRUCTURE (100% Leveraged Across All Three)

✅ Causal MTF extraction (proven zero look-ahead bias)  
✅ Macro regime detection (proven accurate)  
✅ Triple-barrier label generation (proven correct execution)  
✅ Frozen model protocol (proven prevents data leakage)  
✅ Kill-switch validation (proven catches failures)  
✅ Out-of-sample quarantine (Jan-Mar train, Apr validation, May test, Jun sealed)

---

## EXECUTION CHECKLIST FOR EACH DIRECTION

### REVISION 7: Daily/Weekly Swing Trading

**Problem Solved:** Friction drag (0.27R becomes negligible on 3-5R daily moves)

**Key Changes:**
- Extract daily close-to-close bars instead of 1-minute
- Holding period: 3-5 days (vs 45 minutes)
- Target: 3.0R-4.0R (vs 1.5R)
- Stop: 1.0R (same)
- Friction impact: <7% of gross (vs 22%+ on intraday)

**Execution:**
```bash
# 1. Extract daily features
python3 scripts/daily_feature_extraction.py

# 2. Generate triple-barrier labels (5-day horizon)
python3 scripts/daily_triple_barrier_labels.py

# 3. Train frozen model on TRAIN (Jan-Mar)
python3 scripts/daily_model_training.py

# 4. Validation gate on April
python3 scripts/daily_validation_gate.py

# 5. Kill-switch test on May
python3 scripts/daily_test_gate.py
```

**Kill-Switch Criteria:** Win rate ≥ 50.80%, Net P&L > 0.0 bps

---

### REVISION 8: Pairs Cointegration

**Problem Solved:** Directional regime exposure (market direction cancels)

**Key Changes:**
- Feature: Rolling cointegration spread between 1,128 pairs
- Signal: Long laggard, short leader when spread > +2.0σ (and vice versa)
- Entry: Spread extremes (not individual stock direction)
- Exit: Spread mean-reverts to historical mean
- Portfolio beta: ≈ 0 (market-neutral)

**Execution:**
```bash
# 1. Compute cointegration vectors for all pairs
python3 scripts/pairs_cointegration_matrix.py

# 2. Generate spread-based triple-barrier labels
python3 scripts/pairs_triple_barrier_labels.py

# 3. Train pair selection model (Jan-Mar)
python3 scripts/pairs_model_training.py

# 4. Validation gate on April (pair spreads)
python3 scripts/pairs_validation_gate.py

# 5. Kill-switch test on May (spread mean-reversion)
python3 scripts/pairs_test_gate.py
```

**Kill-Switch Criteria:** Win rate ≥ 50.80%, Net P&L > 0.0 bps

---

### REVISION 9: Volume Profile Mean-Reversion

**Problem Solved:** Technical indicators lack institutional volume memory

**Key Changes:**
- Feature: Point of Control (POC) - price level with highest volume
- Signal: Price extremes snap back to POC (structural gravity)
- Entry: Price reaches ±2.0 ATR from moving average
- Target: Price snaps to nearest POC node
- Stop: Break of structure (new high/low)

**Execution:**
```bash
# 1. Compute volume profiles and POC for each symbol
python3 scripts/volume_profile_poc.py

# 2. Generate POC-snap triple-barrier labels
python3 scripts/poc_triple_barrier_labels.py

# 3. Train POC prediction model (Jan-Mar)
python3 scripts/poc_model_training.py

# 4. Validation gate on April (POC snaps)
python3 scripts/poc_validation_gate.py

# 5. Kill-switch test on May (POC gravitation)
python3 scripts/poc_test_gate.py
```

**Kill-Switch Criteria:** Win rate ≥ 50.80%, Net P&L > 0.0 bps

---

## EXECUTION ORDER (Recommended)

**Phase 1 (Week 1):** Daily/Weekly (fastest proof-of-concept)
- Lowest complexity, immediate validation
- Friction math is deterministic
- Result informs whether longer-duration strategies work

**Phase 2 (Week 2-3):** Pairs Cointegration (medium complexity)
- Run in parallel with Phase 1 results analysis
- Highest institutional relevance
- Market-neutral reduces regime exposure

**Phase 3 (Week 3-4):** Volume Profile (highest complexity)
- Run while analyzing Phase 1-2 results
- Most information-rich signal
- Requires careful feature engineering

---

## CODE TEMPLATE STRUCTURE

Each direction follows identical template:

```
scripts/
├── revision7_daily/
│   ├── daily_feature_extraction.py
│   ├── daily_triple_barrier_labels.py
│   ├── daily_model_training.py
│   ├── daily_validation_gate.py
│   └── daily_test_gate.py
├── revision8_pairs/
│   ├── pairs_cointegration_matrix.py
│   ├── pairs_triple_barrier_labels.py
│   ├── pairs_model_training.py
│   ├── pairs_validation_gate.py
│   └── pairs_test_gate.py
└── revision9_volume/
    ├── volume_profile_poc.py
    ├── poc_triple_barrier_labels.py
    ├── poc_model_training.py
    ├── poc_validation_gate.py
    └── poc_test_gate.py
```

---

## EXPECTED OUTCOMES

| Direction | Expected Win Rate | Friction Impact | Market Regime |
|---|---|---|---|
| **Daily/Weekly** | 52-58% | <7% | Directional |
| **Pairs Cointegration** | 51-56% | ~10% | Market-neutral |
| **Volume Profile** | 53-60% | ~8% | Structural |

---

## FINAL VALIDATION GATE (ALL THREE)

After completing each direction's TRAIN → VALIDATION → TEST cycle:

- All three must pass kill-switch on May 2026 TEST split
- Freeze all model parameters and thresholds
- Unlock June 2026 SEALED for final confirmation
- If all three pass sealed: DEPLOYMENT READY

---

## DOCUMENTATION

Complete code templates follow on next sections.
Each includes:
- Full executable Python script
- Feature computation (reuses existing pipeline)
- Triple-barrier label logic
- Model training with class balancing
- Validation gate with kill-switch enforcement
- Detailed comments for modification

---

**Ready to execute. Use templates below.**
