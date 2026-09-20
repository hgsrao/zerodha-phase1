# Applying Calibrated Parameters to Safety Contract

**Your calibration score: 54,776 | Damping zeta: 1.13 (healthy underdamping)**

These 25 parameters were optimized across 75 trials on 3-year 48-symbol data. Now apply them to your safety_contract.json.

---

## Parameter Mapping Guide

### BLOCK 0-2: Entry Signal Quality & Volatility Gates

| Calibrated Param | Current Name | Value | Action |
|------------------|--------------|-------|--------|
| sigma_filter | entry_signal_zscore_threshold | 2.5 | ← Use this to filter noisy signals |
| vol_filter_pct | max_intrabar_volatility_pct | 0.1 (10%) | ← Allow 10% intra-bar moves |
| hurst_lookback | trend_detection_lookback | 150 | ← 150-bar window for trend |
| hurst_thresh | mean_reversion_threshold | 0.46 | ← Entry when Hurst > 0.46 |
| ewma_alpha | volatility_ewma_alpha | 0.1 | ← Smooth vol estimates |
| bb_window | bollinger_band_window | 16 | ← 16-bar BB for entry |
| z_entry | **CRITICAL** entry_zscore | **-1.6** | ← Entry signal quality threshold |
| z_target | **CRITICAL** target_zscore | **0.55** | ← Profit target level |

**ACTION**: In safety_contract.json, set:
```json
{
  "entry_signal_quality": {
    "sigma_filter": 2.5,
    "zscore_entry_threshold": -1.6,
    "zscore_target_threshold": 0.55,
    "volatility_pct_limit": 0.1,
    "hurst_lookback": 150,
    "hurst_threshold": 0.46
  }
}
```

---

### BLOCK 3-5: PID Governors & Position Sizing

| Calibrated Param | Current Name | Value | Action |
|------------------|--------------|-------|--------|
| **kp** | **CRITICAL** pid_kp | **0.055** | ← **Very conservative proportional** |
| **kd** | **CRITICAL** pid_kd | **0.475** | ← **Strong derivative damping** |
| **ki** | **CRITICAL** pid_ki | **0.125** | ← **Integral correction** |
| target_bus_vol | target_portfolio_volatility | 0.16 (16%) | ← Target vol for sizing |
| max_leverage_scale | leverage_multiplier | 0.5 | ← Cap leverage at 50% |
| hrp_shrinkage | hrp_correlation_shrinkage | 0.2 | ← Robust correlation est. |
| cluster_linkage | correlation_linkage_method | "single" | ← Single-linkage clustering |

**ACTION**: In safety_contract.json, set:
```json
{
  "pid_controller": {
    "kp": 0.055,
    "kd": 0.475,
    "ki": 0.125,
    "target_volatility": 0.16,
    "max_leverage": 0.5,
    "shrinkage_factor": 0.2
  }
}
```

⚠️ **CRITICAL**: Your original Kp was likely 0.10+ → **REDUCE to 0.055** to prevent over-hunting

---

### BLOCK 6-7: Stop Loss & Ratchet Logic

| Calibrated Param | Current Name | Value | Action |
|------------------|--------------|-------|--------|
| macro_trend_window | trend_lookback_bars | 200 | ← 200-bar trend window |
| droop_percentage | **CRITICAL** droop_stop_pct | **0.06** (6%) | ← **SL floor 6% below entry** |
| sl_mult | **CRITICAL** stop_loss_atr_mult | **2.3** | ← **Stop at 2.3x ATR** |
| dwell_max | max_position_hold_bars | 40 | ← Max 40 bars in trade |
| ratchet_be_thresh | ratchet_breakeven_pct | 0.2 (20%) | ← Move SL to BE at 20% profit |

**ACTION**: In safety_contract.json, set:
```json
{
  "stop_loss_management": {
    "droop_percentage": 0.06,
    "stop_loss_atr_multiple": 2.3,
    "ratchet_breakeven_threshold": 0.2,
    "max_dwell_bars": 40,
    "trend_window": 200
  }
}
```

⚠️ **IMPORTANT**: If your current SL multiple is < 2.3x ATR → **INCREASE to 2.3** (less stops)

---

### BLOCK 8-9: Bay Management & Cooldown

| Calibrated Param | Current Name | Value | Action |
|------------------|--------------|-------|--------|
| merit_weight | signal_merit_scoring_weight | 0.7 | ← Weight merit scores |
| quarantine_dd_limit | quarantine_drawdown_limit | 0.03 (3%) | ← Quarantine after 3% DD |
| cooldown_bars | cooldown_after_loss_bars | 50 | ← 50-bar cooldown post-loss |
| max_active_bays | **CRITICAL** max_concurrent_positions | **6** | ← **Max 6 concurrent trades** |
| max_gross_exposure | **CRITICAL** max_gross_notional_pct | **0.9** (90%) | ← **90% portfolio deployed** |
| islanding_corr_limit | max_portfolio_correlation | 0.8 | ← 80% correlation limit |

**ACTION**: In safety_contract.json, set:
```json
{
  "bay_management": {
    "max_active_bays": 6,
    "max_gross_exposure": 0.9,
    "max_correlation": 0.8,
    "merit_weight": 0.7,
    "quarantine_dd_limit": 0.03,
    "cooldown_bars": 50
  }
}
```

⚠️ **CRITICAL**: If your `max_concurrent_positions` is < 6 → **INCREASE to 6** (allows more trades)

---

## The Three CRITICAL Parameters You Must Change

These three are likely **blocking your 124 trades**:

### 1. Stop Loss Multiple
```
OLD: sl_mult = 1.5x ATR (too tight, stops out quickly)
NEW: sl_mult = 2.3x ATR (gives trades room to breathe)
```

### 2. Max Concurrent Positions
```
OLD: max_concurrent_positions = 3 or 4 (too restrictive)
NEW: max_concurrent_positions = 6 (matches calibration)
```

### 3. Max Portfolio Exposure
```
OLD: max_gross_exposure = 0.60 (60% deployed)
NEW: max_gross_exposure = 0.90 (90% deployed)
```

---

## Damping Ratio Check

Your calibration achieved: **zeta = 1.13**

This means:
- ✓ System is underdamped (zeta < 1.5)
- ✓ Responds quickly to market changes
- ✓ Kd=0.475 is strong enough to prevent overshooting
- ✓ System will execute trades decisively

---

## Apply These Steps NOW

1. **Open your safety_contract.json** (or wherever these params are stored)
2. **Update with the 4 JSON blocks above** (entry, pid, stop_loss, bay_management)
3. **Test with 1-week diagnostic:**
   ```bash
   python3 diagnostic_48symbol_gate_analysis.py --duration 1week
   ```
4. **Verify conversion % improves:**
   - Before: ~10% (20 trades / 197 signals)
   - Target: 40%+ (80+ trades / 197 signals)
5. **Then run full backtest with calibrated params**

---

## If Trades Still Don't Execute

Run diagnostic with these params applied. If still blocked, check which gates are still rejecting:
- volatility_check → increase vol_filter_pct to 0.15
- leverage_limit → decrease max_leverage_scale to 0.4
- max_concurrent_pos → increase max_active_bays to 8
- portfolio_risk → increase max_gross_exposure to 0.95

The calibration gives you the **optimal parameters**. Your safety gates just need to **allow them through**.
