#!/usr/bin/env python3
"""
Stage 1 vs Stage 2 Parameter Comparison
Shows all 33 parameters before/after calibration
"""

import json
import pandas as pd

# Stage 1 Baseline (51.75% win rate)
stage1_baseline = {
    'profit_target_atr_mult': 1.6171,
    'stop_loss_atr_mult': 0.7246,
    'entry_pid_kp': 0.1587,
    'exit_pid_kp': 0.1327,
    'min_hold_bars': 1.6471,
    'max_hold_bars': 55.7863,
    'vwap_weight': 0.25,
    'confirmation_2bar_weight': 0.30,
    'momentum_weight': 0.25,
    'volatility_weight': 0.20,
    'green_threshold': 0.60,
    'amber_threshold_lower': 0.40,
    'red_threshold': 0.30,
    'slippage_guard_threshold': 0.05,
    'volatility_regime_multiplier': 1.0,
    'low_vol_regime_multiplier': 1.0,
    'medium_vol_regime_multiplier': 1.0,
    'high_vol_regime_multiplier': 1.0,
    'atr_calculation_period': 20,
    'entry_signal_smoothing_window': 1,
    'exit_signal_smoothing_window': 1,
    'slippage_cost_multiplier': 1.0,
    'minimum_absolute_profit_rupees': 0,
    'momentum_calculation_period': 20,
    'vwap_calculation_period': 20,
    'signal_persistence_requirement': 1,
    'phase1_exploration_intensity': 50,
    'phase2_optimization_intensity': 200,
    'learning_rate_exploration_factor': 0.05,
    'lambda_risk_trigger_level': 0.10,
    'lambda_reduction_factor': 0.9,
    'recalibration_frequency_days': 30,
    'sync_score_confidence_threshold': 1.0,
    'entry_confidence_threshold': 0.50,
    'exit_confidence_threshold': 0.50,
    'min_risk_reward_ratio': 1.5,
    'profit_target_margin_buffer': 0.0,
    'base_dp_dt_multiplier': 0.21,
    'base_dv_dt_multiplier': 4877,
}

# Load Stage 2 Optimized (59.05% win rate)
with open('stage2_calibration_results.json') as f:
    stage2_data = json.load(f)
    stage2_optimized = stage2_data['best_parameters']

print("\n" + "="*120)
print("STAGE 1 vs STAGE 2: COMPLETE 33-PARAMETER COMPARISON")
print("="*120)
print()
print("BASELINE PERFORMANCE:")
print("  Stage 1 (Original):       51.75% win rate")
print("  Stage 2 (Optimized):      {:.2f}% win rate".format(stage2_data['best_win_rate']*100))
print("  Improvement:              +{:.2f}% ({:.1f}% relative gain)".format(
    stage2_data['summary']['improvement_percent'],
    stage2_data['summary']['improvement_percent'] / 0.5175 * 100
))
print()

# Create comparison
comparison_data = []
for param in sorted(stage1_baseline.keys()):
    stage1_val = stage1_baseline.get(param, 'N/A')
    stage2_val = stage2_optimized.get(param, 'N/A')

    if isinstance(stage1_val, (int, float)) and isinstance(stage2_val, (int, float)):
        change = stage2_val - stage1_val
        if stage1_val != 0 and stage1_val != 1:
            pct_change = (change / abs(stage1_val)) * 100
        else:
            pct_change = 0

        comparison_data.append({
            'Parameter': param,
            'Stage 1': round(stage1_val, 6),
            'Stage 2': round(stage2_val, 6),
            'Change': round(change, 6),
            'Change %': "{:+.1f}%".format(pct_change) if pct_change != 0 else "No change"
        })

df = pd.DataFrame(comparison_data)
print(df.to_string(index=False))

print()
print("="*120)
print("MOST IMPORTANT CHANGES (Impact on Win Rate)")
print("="*120)
print()

print("CHART STUDIES WEIGHTS (Tier 1 - Critical):")
print("  VWAP Weight:                  0.2500 → 0.0717  (DOWN 71%) - Less important!")
print("  2-Bar Confirmation Weight:    0.3000 → 0.3768  (UP 26%)  - MORE IMPORTANT!")
print("  Momentum Weight:              0.2500 → 0.2549  (stable)")
print("  Volatility Weight:            0.2000 → 0.2965  (UP 49%)  - Risk control crucial!")
print()

print("ENTRY/EXIT DISCIPLINE (Tier 1 - Critical):")
print("  Entry Confidence Threshold:   0.5000 → 0.4198  (DOWN 16%) - Accept weaker signals")
print("  Exit Confidence Threshold:    0.5000 → 0.7731  (UP 55%)   - Hold longer, exit carefully")
print()

print("SYNCHRONIZATION (Tier 1 - Critical):")
print("  Sync Score Confidence:        1.0000 → 2.9924  (3x stricter!) - Only strong syncs")
print()

print("MARKET RESPONSIVENESS (Tier 2 - High Value):")
print("  Momentum Calculation Period:  20 bars → 5.5 bars (3.6x FASTER!) - More responsive!")
print("  ATR Calculation Period:       20 bars → 23.9 bars (slight slowdown)")
print()

print("VOLATILITY REGIMES (Tier 2 - High Value):")
print("  Overall Volatility Regime:    1.0000 → 1.4413  (44% higher thresholds)")
print("  Low Vol Regime Multiplier:    1.0000 → 1.1619  (more aggressive in quiet markets)")
print("  High Vol Regime Multiplier:   1.0000 → 1.0583  (slightly tighter in volatile)")
print()

print("RISK MANAGEMENT (Tier 3 - Optional):")
print("  Lambda Risk Trigger Level:    0.1000 → 0.1635  (16.35% drawdown trigger)")
print("  Lambda Reduction Factor:      0.9000 → 0.8171  (reduce position size more aggressively)")
print()

print("="*120)
print("INTERPRETATION")
print("="*120)
print()
print("Stage 2 learned that:")
print("  1. VWAP alone is OVERRATED (dropped 71%)")
print("  2. Two-bar confirmation is the REAL workhorse (increased 26%)")
print("  3. Volatility control matters MORE than we thought (increased 49%)")
print("  4. Synchronization must be VERY STRICT (3x threshold increase)")
print("  5. Market moves FAST - momentum should be responsive (5.5 vs 20 bars)")
print("  6. Entry signals can be weaker, but exits must be disciplined")
print()

print("="*120)
print()
