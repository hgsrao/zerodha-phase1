#!/usr/bin/env python3
"""
DCS OPERATOR PANEL - Real-time visualization of 6-stage system
Shows input/output of each black box for INFY (or any symbol)
"""

import pandas as pd
import numpy as np
from pathlib import Path
import json
from datetime import datetime

# Import our system
from COMPLETE_TRADING_SYSTEM_INTEGRATED_20260829 import (
    CompleteIntegratedTradingSystem,
    UnifiedP01DGovernor,
    RelativeSynchronizationThresholds,
    SmartParameterInitializer,
    PredictiveAnalyticsEngine,
    IntelligentDiscriminator,
    EconomicBridgeValidator,
    ModelPredictiveController,
    TradingPIDController
)

# ============================================================================
# LOAD DATA
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
SYMBOL = 'INFY'

print("\n" + "="*100)
print("DCS OPERATOR PANEL - REAL-TIME 6-STAGE SYSTEM VISUALIZATION")
print("="*100)
print(f"Symbol: {SYMBOL}")
print(f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")

# Load data
files = list(DATA_DIR.glob(f"NSE_{SYMBOL}_15minute_*.csv"))
if not files:
    print(f"❌ No data found for {SYMBOL}")
    exit(1)

df = pd.read_csv(files[0])
df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
df = df.sort_values('timestamp').reset_index(drop=True)

print(f"✓ Loaded {len(df)} bars ({df['timestamp'].min()} to {df['timestamp'].max()})")
print()

# ============================================================================
# STAGE 1: SYNCHRONIZATION THRESHOLDS
# ============================================================================

print("\n" + "─"*100)
print("STAGE 1: SYNCHRONIZATION THRESHOLDS (Symbol-Specific)")
print("─"*100)

sync_calc = RelativeSynchronizationThresholds(verbose=False)
thresholds = sync_calc.calculate_from_data(df, SYMBOL)

print(f"\nINPUT:")
print(f"  DataFrame: {len(df)} bars of OHLCV")
print(f"  Symbol: {SYMBOL}")

print(f"\nOUTPUT (Thresholds):")
print(f"  dp_dt_threshold (price momentum): ₹{thresholds['dp_dt_threshold']:.4f}")
print(f"  dv_dt_threshold (volume momentum): {thresholds['dv_dt_threshold']:,.0f} shares")
print(f"  Volatility class: {thresholds['volatility_class']}")
print(f"  ATR (volatility): ₹{thresholds['atr']:.4f}")
print(f"  Signal quality min: {thresholds['signal_quality_min']}")

# ============================================================================
# STAGE 2: PARAMETER INITIALIZATION
# ============================================================================

print("\n" + "─"*100)
print("STAGE 2: SMART PARAMETER INITIALIZATION (Data-Driven)")
print("─"*100)

param_init = SmartParameterInitializer(verbose=False)
params = param_init.initialize_from_data(df, SYMBOL)

print(f"\nINPUT:")
print(f"  DataFrame: Last 50 bars")
print(f"  ATR: ₹{thresholds['atr']:.4f}")

print(f"\nOUTPUT (Learnable Parameters):")
print(f"  profit_target: ₹{params['profit_target']['current']:.4f} (range: {params['profit_target']['min']:.2f}-{params['profit_target']['max']:.2f})")
print(f"  stop_loss: ₹{params['stop_loss']['current']:.4f} (range: {params['stop_loss']['min']:.2f}-{params['stop_loss']['max']:.2f})")
print(f"  entry_pid_kp: {params['entry_pid_kp']['current']:.4f} (range: {params['entry_pid_kp']['min']:.2f}-{params['entry_pid_kp']['max']:.2f})")
print(f"  exit_pid_kp: {params['exit_pid_kp']['current']:.4f} (range: {params['exit_pid_kp']['min']:.2f}-{params['exit_pid_kp']['max']:.2f})")
print(f"  min_hold_bars: {int(params['min_hold_bars']['current'])} (range: {params['min_hold_bars']['min']}-{params['min_hold_bars']['max']})")
print(f"  max_hold_bars: {int(params['max_hold_bars']['current'])} (range: {params['max_hold_bars']['min']}-{params['max_hold_bars']['max']})")

# Extract current values for downstream stages
params_current = {
    'profit_target': params['profit_target']['current'],
    'stop_loss': params['stop_loss']['current'],
    'entry_pid_kp': params['entry_pid_kp']['current'],
    'exit_pid_kp': params['exit_pid_kp']['current'],
    'min_hold_bars': int(params['min_hold_bars']['current']),
    'max_hold_bars': int(params['max_hold_bars']['current'])
}

# ============================================================================
# TEST ON SAMPLE BAR (near end of data)
# ============================================================================

print("\n" + "="*100)
print("REAL-TIME EXECUTION: Test on bar 500 (sample midpoint)")
print("="*100)

test_bar_idx = 500
test_bar = df.iloc[test_bar_idx]
prev_bar = df.iloc[test_bar_idx - 1]

print(f"\nCurrent bar {test_bar_idx}:")
print(f"  Timestamp: {test_bar['timestamp']}")
print(f"  OHLC: {test_bar['open']:.2f} / {test_bar['high']:.2f} / {test_bar['low']:.2f} / {test_bar['close']:.2f}")
print(f"  Volume: {test_bar['volume']:,.0f}")

# ============================================================================
# STAGE 3: PA ENGINE (Predictive Analytics)
# ============================================================================

print("\n" + "─"*100)
print("STAGE 3: PA ENGINE (Predictive Analytics - Signal Quality)")
print("─"*100)

pa_engine = PredictiveAnalyticsEngine(verbose=False)
pa_score = pa_engine.calculate_pa_score(df, test_bar_idx, lookback=20)

print(f"\nINPUT:")
print(f"  Current bar index: {test_bar_idx}")
print(f"  Lookback window: 20 bars")
print(f"  Price data: Available")

print(f"\nCALCULATION COMPONENTS:")
window = df.iloc[max(0, test_bar_idx - 20):test_bar_idx + 1]
close = window['close'].values
sma_20 = close.mean()
trend_strength = abs(close[-1] - sma_20) / sma_20 if sma_20 > 0 else 0
print(f"  1. Trend strength: {trend_strength:.4f} (|price - SMA20| / SMA20)")

volume = window['volume'].values
avg_vol = volume[:-1].mean()
vol_ratio = volume[-1] / avg_vol if avg_vol > 0 else 1
print(f"  2. Volume confirmation: {vol_ratio:.4f} (current_vol / avg_vol)")

returns = np.diff(close) / close[:-1]
volatility = np.std(returns) if len(returns) > 0 else 0.01
vol_regime = 1.0 / (1.0 + volatility * 100)
print(f"  3. Volatility regime: {vol_regime:.4f} (stability score)")

momentum = (close[-1] - close[-5]) / close[-5] if len(close) > 5 else 0
print(f"  4. Momentum: {momentum:.4f} (5-bar price change)")

high = window['high'].values
low = window['low'].values
recent_high = high[-10:].max()
recent_low = low[-10:].min()
range_pct = (close[-1] - recent_low) / (recent_high - recent_low + 0.01) if recent_high > recent_low else 0.5
print(f"  5. Range position: {range_pct:.4f} (position in recent range)")

print(f"\nOUTPUT:")
print(f"  PA Score: {pa_score:.4f} (0.0 = weak, 1.0 = strong)")
if pa_score >= 0.70:
    print(f"  Signal Quality: ✅ STRONG")
elif pa_score >= 0.60:
    print(f"  Signal Quality: ⚠️ MODERATE")
else:
    print(f"  Signal Quality: ❌ WEAK")

# ============================================================================
# STAGE 4: ID FILTER (Intelligent Discrimination)
# ============================================================================

print("\n" + "─"*100)
print("STAGE 4: ID FILTER (Intelligent Discrimination - Entry Validation)")
print("─"*100)

# Calculate timing signal
sma_20_series = df['close'].rolling(window=20).mean()
sma_20 = sma_20_series.iloc[test_bar_idx]

high = df['high'].tail(50)
low = df['low'].tail(50)
close_series = df['close'].tail(50)
tr1 = high - low
tr2 = np.abs(high - close_series.shift(1))
tr3 = np.abs(low - close_series.shift(1))
tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
atr = tr.mean()

# Calculate timing signals
price_deviation = test_bar['close'] - sma_20
p_term = np.clip(price_deviation / 100, -1, 1)
dp_dt = test_bar['close'] - prev_bar['close']
d_term = np.clip(dp_dt / 10, -1, 1)
dv_dt = test_bar['volume'] - prev_bar['volume']
avg_volume = (test_bar['volume'] + prev_bar['volume']) / 2.0
v_term = np.clip(dv_dt / max(avg_volume, 1), -1, 1)
total_signal = (p_term + d_term + v_term) / 3.0

# Sync gate
price_direction = 1.0 if dp_dt > 0 else -1.0
volume_direction = 1.0 if dv_dt > 0 else -1.0
phase_alignment = price_direction * volume_direction

sync_checks = {
    'dp_dt_magnitude': abs(dp_dt) > thresholds['dp_dt_threshold'],
    'dv_dt_magnitude': abs(dv_dt) > thresholds['dv_dt_threshold'],
    'phase_aligned': phase_alignment > 0.0,
    'signal_quality': pa_score >= 0.6
}

id_filter = IntelligentDiscriminator(verbose=False)
id_approved = id_filter.should_accept_trade(
    pa_score=pa_score,
    sync_checks=sync_checks,
    price_momentum=dp_dt / 100.0,
    vol_momentum=dv_dt / max(test_bar['volume'], 1)
)

print(f"\nINPUT:")
print(f"  PA score: {pa_score:.4f}")
print(f"  Sync checks (all must pass):")
print(f"    ✓ dp_dt magnitude {abs(dp_dt):.4f} > {thresholds['dp_dt_threshold']:.4f}: {sync_checks['dp_dt_magnitude']}")
print(f"    ✓ dv_dt magnitude {abs(dv_dt):,.0f} > {thresholds['dv_dt_threshold']:,.0f}: {sync_checks['dv_dt_magnitude']}")
print(f"    ✓ Phase aligned (price·volume direction): {sync_checks['phase_aligned']}")
print(f"    ✓ Signal quality (PA >= 0.6): {sync_checks['signal_quality']}")

print(f"\nOUTPUT:")
print(f"  ID Filter Result: {'✅ PASS - Entry allowed' if id_approved else '❌ FAIL - Entry blocked'}")
if not id_approved:
    failed = [k for k, v in sync_checks.items() if not v]
    print(f"  Reasons for rejection: {failed}")

# ============================================================================
# STAGE 5: BRIDGE VALIDATOR (Economic Viability)
# ============================================================================

print("\n" + "─"*100)
print("STAGE 5: BRIDGE (Economic Viability Check)")
print("─"*100)

bridge = EconomicBridgeValidator(verbose=False)
bridge_result = bridge.validate_trade_economics(
    entry_price=test_bar['close'],
    profit_target=params_current['profit_target'],
    stop_loss=params_current['stop_loss'],
    entry_cost_bps=2.0,
    exit_cost_bps=2.0
)

print(f"\nINPUT:")
print(f"  Entry price: ₹{test_bar['close']:.2f}")
print(f"  Profit target: ₹{params_current['profit_target']:.4f}")
print(f"  Stop loss: ₹{params_current['stop_loss']:.4f}")
print(f"  Entry cost: 2 bps")
print(f"  Exit cost: 2 bps")

print(f"\nOUTPUT:")
print(f"  Total round-trip cost: ₹{bridge_result['total_cost']:.4f}")
print(f"  Risk/Reward ratio: {bridge_result['risk_reward_ratio']:.2f}:1")
print(f"  Economically viable: {'✅ YES' if bridge_result['viable'] else '❌ NO'}")
if bridge_result['issues']:
    print(f"  Issues: {', '.join(bridge_result['issues'])}")

# ============================================================================
# STAGE 6: MPC (Model Predictive Control)
# ============================================================================

print("\n" + "─"*100)
print("STAGE 6: MPC (Model Predictive Control - Position Sizing)")
print("─"*100)

mpc = ModelPredictiveController(verbose=False)
position_size = mpc.calculate_position_size(
    capital=100000,
    stop_loss_amount=abs(params_current['stop_loss']),
    current_drawdown=0.0,
    recent_win_rate=0.52,
    lambda_control=1.0
)

print(f"\nINPUT:")
print(f"  Capital: ₹100,000")
print(f"  Stop loss amount: ₹{abs(params_current['stop_loss']):.4f}")
print(f"  Current drawdown: 0.0%")
print(f"  Recent win rate: 52%")
print(f"  Lambda (Kelly fraction): 1.0x")

print(f"\nCALCULATION:")
risk_per_trade = 100000 * 0.01 * 1.0
print(f"  Risk per trade: ₹{risk_per_trade:.2f} (1% of capital × lambda)")
print(f"  Position size: {risk_per_trade:.0f} / ₹{abs(params_current['stop_loss']):.4f} = {position_size} shares")

print(f"\nOUTPUT:")
print(f"  Position size: {position_size} shares")
print(f"  Capital at risk: ₹{position_size * abs(params_current['stop_loss']):.2f}")

# ============================================================================
# P01D GOVERNOR - PID CONTROLLERS
# ============================================================================

print("\n" + "─"*100)
print("P01D GOVERNOR - TIMING OPTIMIZATION (PID Controllers)")
print("─"*100)

entry_pid = TradingPIDController(
    kp=params_current['entry_pid_kp'],
    ki=0.01,
    kd=0.01,
    target=0.75,
    name=f"Entry_{SYMBOL}"
)

pid_result = entry_pid.calculate(current_value=total_signal)

print(f"\nENTRY PID CONTROLLER:")
print(f"  Target signal: 0.75")
print(f"  Current signal: {total_signal:.4f}")
print(f"  Error (target - current): {pid_result['error']:.4f}")
print(f"  P-term (Kp × error): {pid_result['p_term']:.6f}")
print(f"  I-term (Ki × integral): {pid_result['i_term']:.6f}")
print(f"  D-term (Kd × derivative): {pid_result['d_term']:.6f}")
print(f"  Total adjustment: {pid_result['adjustment']:.6f}")
print(f"  PID Ready (adjustment < 0): {pid_result['adjustment'] < 0.0}")

# ============================================================================
# SUMMARY & DECISION
# ============================================================================

print("\n" + "="*100)
print("SYSTEM DECISION SUMMARY")
print("="*100)

entry_ready = (
    id_approved and
    bridge_result['viable'] and
    (pid_result['adjustment'] < 0.0)
)

print(f"\nAll Gates Status:")
print(f"  1. PA Engine: PA score = {pa_score:.4f} {'✅' if pa_score >= 0.60 else '❌'}")
print(f"  2. Sync Gate: Phase aligned = {sync_checks['phase_aligned']} {'✅' if sync_checks['phase_aligned'] else '❌'}")
print(f"  3. ID Filter: Approved = {id_approved} {'✅' if id_approved else '❌'}")
print(f"  4. Bridge: Viable = {bridge_result['viable']} {'✅' if bridge_result['viable'] else '❌'}")
print(f"  5. MPC: Position = {position_size} shares {'✅' if position_size > 0 else '❌'}")
print(f"  6. Entry PID: Ready = {pid_result['adjustment'] < 0.0} {'✅' if pid_result['adjustment'] < 0.0 else '❌'}")

print(f"\n{'='*100}")
if entry_ready:
    print(f"✅ ENTRY SIGNAL: All gates passed - READY TO ENTER")
    print(f"   Entry: {position_size} shares @ ₹{test_bar['close']:.2f}")
    print(f"   Target: ₹{test_bar['close'] + params_current['profit_target']:.2f}")
    print(f"   Stop: ₹{test_bar['close'] + params_current['stop_loss']:.2f}")
else:
    print(f"❌ NO ENTRY: One or more gates failed")

print(f"{'='*100}\n")

# Save to JSON for dashboard
output = {
    'timestamp': datetime.now().isoformat(),
    'symbol': SYMBOL,
    'bar_index': test_bar_idx,
    'bar_ohlcv': {
        'open': float(test_bar['open']),
        'high': float(test_bar['high']),
        'low': float(test_bar['low']),
        'close': float(test_bar['close']),
        'volume': int(test_bar['volume'])
    },
    'stage_1_thresholds': {
        'dp_dt_threshold': float(thresholds['dp_dt_threshold']),
        'dv_dt_threshold': float(thresholds['dv_dt_threshold']),
        'volatility_class': thresholds['volatility_class'],
        'atr': float(thresholds['atr'])
    },
    'stage_2_parameters': params_current,
    'stage_3_pa': {
        'pa_score': float(pa_score),
        'quality': 'STRONG' if pa_score >= 0.70 else ('MODERATE' if pa_score >= 0.60 else 'WEAK')
    },
    'stage_4_id': {
        'approved': bool(id_approved),
        'sync_checks': {k: bool(v) for k, v in sync_checks.items()}
    },
    'stage_5_bridge': {
        'viable': bool(bridge_result['viable']),
        'total_cost': float(bridge_result['total_cost']),
        'rr_ratio': float(bridge_result['risk_reward_ratio'])
    },
    'stage_6_mpc': {
        'position_size': int(position_size),
        'capital_at_risk': float(position_size * abs(params_current['stop_loss']))
    },
    'entry_ready': bool(entry_ready)
}

with open('DCS_OPERATOR_PANEL_OUTPUT.json', 'w') as f:
    json.dump(output, f, indent=2)

print(f"✓ Results saved to: DCS_OPERATOR_PANEL_OUTPUT.json")
