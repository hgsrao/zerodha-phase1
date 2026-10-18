# Implementation Guide: Loosen ATR Mechanical Stop

## Objective
Increase `trailing_stop_atr_mult` from 4.0 to 4.5 (or 5.0) to give PID controller time to accumulate exit signals.

---

## Step 1: Locate the Registry Parameter

**File:** `/home/shrinivas/ECS_Project_external_engine/canonical_parameter_registry.py`

**Search for:**
```python
"trailing_stop_atr_mult": {
```

---

## Step 2: Current Configuration (Before)

```python
"trailing_stop_atr_mult": {
    "default": 4.0,        # ← CHANGE THIS
    "min": 3.0,
    "max": 6.0,
    "calibratable": True,
    "calibration_range": [3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
},
```

---

## Step 3: Apply the Fix

### Option A: Conservative Increase (Recommended)

```python
"trailing_stop_atr_mult": {
    "default": 4.5,        # ← INCREASED from 4.0
    "min": 3.0,
    "max": 6.0,
    "calibratable": True,
    "calibration_range": [3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
},
```

**Rationale:** 
- 12.5% increase (4.0 → 4.5)
- Supported by prior art (P02_QUANT_LAB = 4.0, we go slightly higher)
- Low risk, high confidence

---

### Option B: Aggressive Increase

```python
"trailing_stop_atr_mult": {
    "default": 5.0,        # ← INCREASED from 4.0
    "min": 3.0,
    "max": 6.0,
    "calibratable": True,
    "calibration_range": [3.5, 4.0, 4.5, 5.0, 5.5, 6.0],
},
```

**Rationale:**
- 25% increase (4.0 → 5.0)
- Gives PID more room to accumulate
- May allow 5-10 bars instead of 1 bar

---

## Step 4: Verify the Change

```bash
cd /home/shrinivas/ECS_Project_external_engine

# Search for the value in the registry
grep -n "trailing_stop_atr_mult" canonical_parameter_registry.py

# Expected output:
#   123:    "trailing_stop_atr_mult": {
#   124:        "default": 4.5,  ← Verify this changed
```

---

## Step 5: Re-run the 62-Trade Experiment

After making the change, run the same test that produced the 62 trades:

```bash
cd /home/shrinivas/ECS_Project_external_engine

# Clean old traces
rm -f trace_granular_external_*.log pid_trace_trade_*.log

# Run the granular tracer with NEW ATR multiplier
python3 scripts/baseline_granular_stage_tracer.py INFY 5000
```

---

## Step 6: Compare Results

### Before (ATR = 4.0):
```
Total trades:        62
0-bar exits:         49 (79%)
1-3 bar exits:       10 (16%)
4+ bar exits:         3 (5%)
Net P&L:            -₹11,350.66
Win rate:           0%
```

### After (ATR = 4.5 or 5.0):
```
Expected:
Total trades:        50-55 (fewer, but longer-lived)
0-bar exits:         20-25 (40-50%)
1-3 bar exits:       15-20 (30-40%)
4+ bar exits:        10-15 (20-30%)
Net P&L:            -₹5,000 to +₹10,000 (projected)
Win rate:           15-25% (projected)
```

---

## Step 7: Interpret the Results

### Success Indicators ✓

- [ ] Average bars held increased to 2-3 (was 0.3)
- [ ] 0-bar exits dropped to 40-50% (was 79%)
- [ ] At least 1 trade lasting 5+ bars
- [ ] Win rate > 0% (at least some profitable trades)
- [ ] Net P&L loss reduced or turned positive

### Red Flags ✗

- [ ] No change in 0-bar exits (ATR change didn't work)
- [ ] More trades, but more losses (market environment worse)
- [ ] PID still producing weak output (Ki also needs increase)

---

## Step 8: Next Iteration (Optional)

If results are positive but not strong enough, try:

### Option 1: Increase Ki (Integral Gain)

```python
"exit_ki": {
    "default": 0.04,       # ← INCREASED from 0.02
    "min": 0.01,
    "max": 0.10,
    "calibratable": True,
},
```

**Effect:** Integral term accumulates 2x faster, stronger signals by bar 5-7.

### Option 2: Expose saturation_exit_bars

```python
"saturation_exit_bars": {
    "default": 5,
    "min": 2,
    "max": 10,
    "calibratable": True,  # ← Add this
    "calibration_range": [2, 3, 4, 5, 6, 7, 8],
},
```

**Effect:** Calibration engine can tune when price-at-extreme triggers exit.

---

## File Locations Reference

| Component | File | Parameter |
|-----------|------|-----------|
| Registry | `canonical_parameter_registry.py` | `trailing_stop_atr_mult` |
| External Engine | `revision2_external/orchestrator.py` | Uses registry |
| In-House Engine | `revision2/portfolio_orchestrator.py` | Uses registry |
| PID Controller | `revision2/boxes.py` line 400+ | BoundedPID class |
| ATR Calculation | `revision2/boxes.py` line ~470 | `_calculate_atr()` |

---

## Rollback Plan (If Something Breaks)

```bash
# If results are worse, revert to original
cd /home/shrinivas/ECS_Project_external_engine

# Edit canonical_parameter_registry.py and change back:
# "trailing_stop_atr_mult": {"default": 4.0}

# Re-run test
python3 scripts/baseline_granular_stage_tracer.py INFY 5000

# Verify we're back to 62 trades, 0-bar exits
```

---

## Success Criteria

| Criterion | Threshold | Success |
|-----------|-----------|---------|
| Avg bars held | > 1.0 (was 0.3) | ✓ |
| 0-bar exits | < 50% (was 79%) | ✓ |
| Win rate | > 5% (was 0%) | ✓ |
| Net P&L | > -₹5,000 (was -₹11,350) | ✓ |

**Overall success:** All 4 thresholds met.

---

## Timeline

| Action | Expected Duration |
|--------|-------------------|
| Locate parameter | < 2 minutes |
| Make change | < 1 minute |
| Run backtest | 60-90 seconds |
| Analyze results | 5 minutes |
| **Total** | **10-15 minutes** |

---

## Expected Impact

**Conservative estimate (ATR = 4.5):**
- Trades lasting 1-2 bars instead of 0 bars
- P&L improves by ~30-40%
- Win rate improves to 10-15%

**Aggressive estimate (ATR = 5.0):**
- Trades lasting 2-4 bars
- P&L improves by 50%+
- Win rate improves to 20%+

---

## Confidence Level

**HIGH** — This fix is based on:
1. Empirical data (62 actual trades analyzed)
2. Prior art (P02_QUANT_LAB used similar values)
3. Control-systems theory (give PID time to accumulate)
4. Industry standard (Chandelier exit uses 3-5x ATR multipliers)

---

## Questions?

If the results are unexpected, check:
1. **Were the trades actually processed?** Run `grep -c "TRADE" trace_granular_external_INFY.log`
2. **Did the parameter change load?** Add logging to verify registry value used
3. **Is ATR being calculated correctly?** Check `_calculate_atr()` in boxes.py

---

**Ready to proceed?** Answer: **YES** 

The fix is low-risk, well-understood, and supported by empirical evidence. Let's loosen the ATR stop and give the PID controller time to work.
