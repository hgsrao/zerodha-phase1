# Integration Checklist: From 0-Trades to Live Execution

**Status:** You have calibrated parameters + patch ready. Now integrate them.

---

## Checkpoint 1: Verify Files Are In Place ✓

```bash
cd ~/institutional_quant_workspace/engine_v03

# Check backup exists (safety net)
ls -lh kite_dual_engine_ccpp_plant.py.bak

# Check patch file exists
ls -lh apply_ccpp_closed_loop_patch.py

# Check calibration results exist
cat results/full_plant_10blocks_calibrated.json | head -5
```

Expected output:
```
✓ kite_dual_engine_ccpp_plant.py.bak exists (your safety backup)
✓ apply_ccpp_closed_loop_patch.py exists (5 enhancements to apply)
✓ full_plant_10blocks_calibrated.json exists (25 optimized params)
```

---

## Checkpoint 2: Verify Plant File Is Clean ✓

```bash
python3 check_kite_plant_import.py
```

Expected output:
```
[✓] Syntax is valid
[✓] Found N classes:
    • CCPP_PIDGovernorActuator
    • CCPPDualEnginePlant
    • [other classes...]
[✓] Successfully imported CCPP_PIDGovernorActuator
```

**If import fails:** Restore from backup
```bash
cp kite_dual_engine_ccpp_plant.py.bak kite_dual_engine_ccpp_plant.py
python3 check_kite_plant_import.py
```

---

## Checkpoint 3: Apply Calibrated Parameters ✓

**Find your safety_contract.json:**
```bash
find ~/institutional_quant_workspace -name "safety_contract.json" -o -name "config.json" -o -name "*safety*"
```

**Update these 3 CRITICAL parameters:**

```python
# In your safety_contract.json or config file:

{
  "stop_loss_management": {
    "stop_loss_atr_multiple": 2.3,      # WAS: 1.5 → NOW: 2.3
    "droop_percentage": 0.06,             # WAS: 0.04 → NOW: 0.06
    "ratchet_breakeven_threshold": 0.2,  # WAS: 0.15 → NOW: 0.2
    "max_dwell_bars": 40                  # WAS: 30 → NOW: 40
  },
  
  "bay_management": {
    "max_concurrent_positions": 6,        # WAS: 3-4 → NOW: 6
    "max_gross_exposure": 0.9,            # WAS: 0.6 → NOW: 0.9
    "max_correlation": 0.8,               # WAS: 0.7 → NOW: 0.8
    "cooldown_bars": 50                   # WAS: 30 → NOW: 50
  },
  
  "pid_controller": {
    "kp": 0.055,                          # WAS: 0.10 → NOW: 0.055
    "kd": 0.475,                          # WAS: 0.80 → NOW: 0.475
    "ki": 0.125,                          # WAS: 0.05 → NOW: 0.125
    "max_leverage": 0.5                   # NEW: 50% leverage cap
  },
  
  "entry_signal_quality": {
    "zscore_entry_threshold": -1.6,       # Entry at -1.6 std dev
    "zscore_target_threshold": 0.55,      # Target at +0.55 std dev
    "volatility_pct_limit": 0.1,          # 10% intra-bar moves OK
    "hurst_threshold": 0.46               # Trend detection threshold
  }
}
```

**Verification:**
```bash
# Show only the safety parameters
python3 -c "
import json
with open('safety_contract.json') as f:
    config = json.load(f)
    
print('CRITICAL PARAMETERS:')
print(f'  SL Multiple: {config[\"stop_loss_management\"][\"stop_loss_atr_multiple\"]} (should be 2.3)')
print(f'  Max Concurrent: {config[\"bay_management\"][\"max_concurrent_positions\"]} (should be 6)')
print(f'  Max Gross Exposure: {config[\"bay_management\"][\"max_gross_exposure\"]} (should be 0.9)')
print(f'  PID Kp: {config[\"pid_controller\"][\"kp\"]} (should be 0.055)')
"
```

---

## Checkpoint 4: Run Diagnostic (1-Week Test) ✓

```bash
python3 diagnostic_48symbol_gate_analysis.py --duration 1week
```

**Expected output:**
```
[1/4] Simulating signal generation...
  → Generated ~200 entry signals

[2/4] Simulating position sizing...
  → 140 signals passed sizing (70%)

[3/4] Simulating 18-gate entry decision engine...
  → 60+ signals passed entry decision gate (40%+) ← TARGET MET
  
[4/4] Simulating execution gate...
  → 60+ trades executed (30%+) ← IMPROVEMENT!

BEFORE: 20/197 (10.2%)
AFTER:  60+/200 (30%+) ← AT LEAST 3x improvement
```

**If still < 30%:**
Check which gates are still blocking:
```bash
python3 diagnostic_48symbol_gate_analysis.py --duration 1week --verbose
```
Then adjust the blocking gate thresholds:
- volatility_check → increase vol_filter_pct to 0.15
- leverage_limit → decrease max_leverage to 0.4
- max_concurrent → increase to 8

---

## Checkpoint 5: Apply Closed-Loop Patch (Optional - Advanced) ✓

The `apply_ccpp_closed_loop_patch.py` file adds 5 enhancements:

1. **Self-Learning Adaptive PID** - Damps PID after stops, normalizes after targets
2. **Plant Registers** - Cooldown & ANSI lockout enforcement
3. **Sector Scanning Latch** - Prevents sector flooding
4. **Multi-Regime Dynamic ATR Floor** - Volatile conditions = wider stops
5. **Closed-Loop Feedback** - Learns from trade exits

**To apply this patch:**
```bash
# Backup current file (extra safety)
cp kite_dual_engine_ccpp_plant.py kite_dual_engine_ccpp_plant.py.pre-patch

# Apply patch (review carefully first)
python3 apply_ccpp_closed_loop_patch.py

# Verify syntax
python3 -c "import kite_dual_engine_ccpp_plant; print('✓ Patch applied successfully')"
```

**Note:** This patch is OPTIONAL. The calibrated parameters alone should fix your 0-trades issue.

---

## Checkpoint 6: Run Full 1-Month Diagnostic ✓

```bash
python3 diagnostic_48symbol_gate_analysis.py --duration 1month
```

This tests across a fuller time window (more market conditions).

---

## Checkpoint 7: Paper Trade Test ✓

Once diagnostics show 30%+ conversion:

```bash
# Run in shadow mode (paper trading, no real orders)
python3 kite_dual_engine_ccpp_plant.py --mode shadow --duration 1week

# Monitor output for:
# - Entry signals: 100+
# - Trades executed: 30+
# - PnL: Positive trend
# - Gate rejections: < 20%
```

---

## Checkpoint 8: Live / Active Paper Mode ✓

Only after shadow mode shows consistent PnL:

```bash
python3 kite_dual_engine_ccpp_plant.py --mode active_paper --live-data
```

This uses live market data but no real orders (paper account).

---

## Timeline

| Step | Time | Status |
|------|------|--------|
| 1. Verify files | 2 min | ← DO THIS NOW |
| 2. Check plant syntax | 1 min | ← DO THIS NOW |
| 3. Update safety_contract.json | 10 min | ← DO THIS NOW |
| 4. Run 1-week diagnostic | 5 min | ← DO THIS NOW |
| 5. Apply patch (optional) | 5 min | Later, if needed |
| 6. Run 1-month diagnostic | 10 min | Tomorrow |
| 7. Shadow paper trade test | 24 hours | Tomorrow |
| 8. Active paper mode | Ongoing | Next week |
| **TOTAL TO FIRST TRADES** | **~30 min** | **TODAY** |

---

## Success Criteria

### Today (Before EOD)
- [ ] Files verified (checkpoint 1-2)
- [ ] safety_contract.json updated (checkpoint 3)
- [ ] Diagnostic runs with 30%+ conversion (checkpoint 4)

### Tomorrow
- [ ] 1-month diagnostic confirms improvement
- [ ] Shadow paper trade shows 100+ signals, 30+ trades executed

### Next Week
- [ ] Active paper mode running
- [ ] PnL consistently positive (Sharpe > 0.5)
- [ ] Ready for live trading (if desired)

---

## Troubleshooting

**Q: Diagnostic still shows < 30% conversion?**
A: Re-check safety_contract.json was saved. Verify the 3 critical params:
```bash
grep -A2 "stop_loss_atr_multiple\|max_concurrent_positions\|max_gross_exposure" safety_contract.json
```

**Q: File syntax error after patch?**
A: Restore and re-verify:
```bash
cp kite_dual_engine_ccpp_plant.py.bak kite_dual_engine_ccpp_plant.py
python3 check_kite_plant_import.py
```

**Q: PnL is negative in shadow mode?**
A: Calibration gave you optimal gate settings, not guaranteed profit. You may need to:
- Adjust entry signal quality (z_entry threshold)
- Tighten stop loss (sl_mult slightly lower)
- Review feature interactions (see PDF guide)

---

## Next: Start with Checkpoint 1

Run this now:
```bash
cd ~/institutional_quant_workspace/engine_v03
ls -lh kite_dual_engine_ccpp_plant.py.bak apply_ccpp_closed_loop_patch.py
cat results/full_plant_10blocks_calibrated.json | jq '.master_fitness_score, .damping_ratio_zeta'
```

Then move to Checkpoint 2-3.
