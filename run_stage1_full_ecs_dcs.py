class SectorTurbinePID:
    def __init__(self, target_z: float, kp: float = 0.3, ki: float = 0.02, kd: float = 0.15, droop: float = 0.05, integral_clamp: float = 1.5, trail_activation_u: float = 0.15):
        self.target_z = target_z
        self.kp = kp
        self.ki = ki
        self.kd = kd
        self.droop = droop
        self.integral_clamp = integral_clamp
        self.trail_activation_u = trail_activation_u
        self.integral = 0.0
        self.prev_error = None

    def update(self, current_z: float, grid_velocity: float = 0.0) -> float:
        error = current_z - self.target_z
        self.integral += error
        self.integral = max(-self.integral_clamp, min(self.integral_clamp, self.integral))
        deriv = (error - self.prev_error) if self.prev_error is not None else 0.0
        self.prev_error = error
        u_raw = (self.kp * error) + (self.ki * self.integral) + (self.kd * deriv)
        return u_raw - (self.droop * grid_velocity)


def resolve_sector_params(symbol, config):
    profiles = config.get('sector_governor_profiles', {})
    for sector_id, prof in profiles.items():
        if symbol in prof.get('symbols', []):
            return prof, sector_id
    # Default fallback
    return {
        'engine': 'ENGINE_B',
        'sl_atr_mult': 1.8,
        'target_zscore': 0.5,
        'z_entry_threshold': -2.0,
        'max_bars_held': 40
    }, 'UNASSIGNED'

import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

class ECSTradingSupervisor:
    """
    From ECS_TradingSupervisor_Production.py & COMPLETE_DCS_CLOSED_LOOP_V1_FIXED.py:
    1. Phase Angle: Price-Volume directional agreement check.
    2. Speed Signal: Entry gate adaptation based on NIFTY trend velocity.
    3. Voltage Signal: Dynamic risk-sizing based on relative volatility stress.
    """
    def __init__(self):
        self.speed_ema = 0.0
        self.voltage_ema = 0.0

    def evaluate_signals(self, dp_dt: float, dv_dt: float, vol_ratio: float, nifty_vel: float) -> tuple[bool, float, str]:
        # --- 1. ANSI 25 SYNCHROCHECK & PHASE ANGLE (Grid Back-Charged) ---
        # Allow normal mean-reversion absorption on dips.
        # Trip OUT_OF_PHASE_CASCADE only under extreme dumping stress (vol_ratio > 2.5)
        is_anti_phase_fault = (dp_dt < 0) and (dv_dt > 0) and (vol_ratio > 2.5)
        if is_anti_phase_fault:
            return False, 0.0, "OUT_OF_PHASE_CASCADE"

        # --- 2. SPEED / FREQUENCY SIGNAL (Lines 245-279) ---
        # Base speed in island/normal mode = 0
        stress_adj = (vol_ratio - 1.0) * 40.0
        nifty_drift = nifty_vel * 10000.0  # bps drift
        raw_speed = np.clip(-stress_adj + nifty_drift, -100.0, 100.0)
        self.speed_ema = 0.7 * self.speed_ema + 0.3 * raw_speed
        
        # If speed is deeply negative (market in severe downward shock), raise entry bar
        if self.speed_ema < -45.0:
            return False, 0.0, "GRID_VELOCITY_DEFENSIVE_LOCKOUT"

        # --- 3. VOLTAGE SIGNAL (Lines 281-323: Position Sizing) ---
        # Higher volatility stress reduces generator MW dispatch (position size)
        raw_voltage = np.clip(100.0 - (vol_ratio - 1.0) * 100.0, 25.0, 125.0)
        self.voltage_ema = 0.7 * self.voltage_ema + 0.3 * raw_voltage
        
        # Voltage to sizing multiplier (0.5x to 1.25x)
        size_multiplier = float(np.clip(self.voltage_ema / 100.0, 0.5, 1.25))

        return True, size_multiplier, "SYNCED_PERMISSIVE"

def compute_causal_features(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    
    df['rsi_percentile'] = df['rsi_14'].rolling(100).apply(
        lambda x: (x.argsort().argsort()[-1] + 1.0) / 100.0, raw=True
    )

    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['atr'] = tr.rolling(14).mean()
    df['atr_baseline'] = df['atr'].rolling(100).mean()
    df['vol_ratio'] = (df['atr'] / df['atr_baseline']).replace(0, 1.0)
    
    df['vol_median'] = df['volume'].rolling(50).median()
    df['vol_flow_ratio'] = df['volume'] / df['vol_median']
    
    df['dp_dt'] = df['close'].diff()
    df['dv_dt'] = df['volume'].diff()
    
    # Vectorized daily VWAP
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    df['date_only'] = df['dt'].dt.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    df['vp'] = df['typ_price'] * df['volume']
    df['cum_vp'] = df.groupby('date_only')['vp'].cumsum()
    df['cum_v'] = df.groupby('date_only')['volume'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_v'].replace(0, 1e-5)
    
    df['sq_diff_v'] = ((df['typ_price'] - df['vwap'])**2) * df['volume']
    df['cum_sq_diff'] = df.groupby('date_only')['sq_diff_v'].cumsum()
    df['vwap_std'] = np.sqrt(df['cum_sq_diff'] / df['cum_v'].replace(0, 1e-5)).replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)


import json
from pathlib import Path

def load_fleet_config():
    cfg_path = Path('results/fleet_config.json')
    if cfg_path.exists():
        try:
            return json.loads(cfg_path.read_text())
        except Exception as e:
            print(f'[WARN] Error loading fleet config: {e}')
    return {}

def resolve_sector_params(symbol, config):
    profiles = config.get('sector_governor_profiles', {})
    for sector_id, prof in profiles.items():
        if symbol in prof.get('symbols', []):
            return prof, sector_id
    return {
        'engine': 'ENGINE_B',
        'sl_atr_mult': 1.8,
        'target_zscore': 0.5,
        'z_entry_threshold': -2.0,
        'max_bars_held': 40
    }, 'UNASSIGNED'

def run():
    config = load_fleet_config()
    print("Loading NIFTY Grid reference...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    supervisor = ECSTradingSupervisor()
    trades = []
    blocked_phase = 0
    blocked_speed = 0
    
    print("Running Stage 1 (1 Month) with Full Closed-Loop ECS Supervisor...")
    
    for i, fpath in enumerate(csv_files, 1):
        symbol = fpath.name.split('_')[1]
        sec_prof, sector_id = resolve_sector_params(symbol, config)
        if sector_id == 'UNASSIGNED':
            continue
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            month = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(month) < 300:
                continue
            df = compute_causal_features(month)
        except Exception:
            continue
            
        last_exit = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit + 15:
                continue
            row = df.iloc[pos]
            t = row['dt']
            
            if t not in grid_df.index:
                continue
            grid_row = grid_df.loc[t]
            
            # Oversold Core Candidate Gate
            if row['vwap_zscore'] < sec_prof['z_entry_threshold'] and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                
                # Supervisory Synchronizer Check
                permissive, size_multiplier, reason = supervisor.evaluate_signals(
                    dp_dt=row['dp_dt'],
                    dv_dt=row['dv_dt'],
                    vol_ratio=row['vol_ratio'],
                    nifty_vel=grid_row['f_grid']
                )
                
                if not permissive:
                    if reason == "OUT_OF_PHASE_CASCADE":
                        blocked_phase += 1
                    elif reason == "GRID_VELOCITY_DEFENSIVE_LOCKOUT":
                        blocked_speed += 1
                    continue
                    
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                risk_ticks = sec_prof['sl_atr_mult'] * row['atr']
                if risk_ticks <= 0:
                    continue

                pid_cfg = sec_prof.get('pid', {})
                controller = SectorTurbinePID(
                    target_z=sec_prof['target_zscore'],
                    kp=pid_cfg.get('kp', 0.25),
                    ki=pid_cfg.get('ki', 0.02),
                    kd=pid_cfg.get('kd', 0.15),
                    droop=pid_cfg.get('droop', 0.05),
                    integral_clamp=pid_cfg.get('integral_clamp', 1.5),
                    trail_activation_u=pid_cfg.get('trail_activation_u', 0.15)
                )

                current_stop = entry_price - risk_ticks
                max_b = sec_prof['max_bars_held']
                future = df.iloc[pos + 1 : pos + 1 + max_b]
                
                exit_r = 0.0
                bars_held = max_b
                exit_reason = 'TIME_HORIZON'

                for bar_idx, (_, fut_row) in enumerate(future.iterrows(), 1):
                    fut_t = fut_row['dt']
                    fut_grid_vel = grid_df.loc[fut_t]['f_grid'] if (fut_t in grid_df.index and 'f_grid' in grid_df.columns) else 0.0

                    u_control = controller.update(fut_row['vwap_zscore'], grid_velocity=fut_grid_vel)

                    if u_control > -0.30:
                        ratchet_pct = min(1.0, max(0.0, (u_control + 0.30) / 0.60))
                        dynamic_stop = entry_price - (risk_ticks * (1.0 - (0.80 * ratchet_pct)))
                        current_stop = max(current_stop, dynamic_stop)

                    if fut_row['low'] <= current_stop:
                        exit_r = (current_stop - entry_price) / risk_ticks
                        exit_reason = 'STOP_LOSS'
                        bars_held = bar_idx
                        break

                    if fut_row['vwap_zscore'] >= sec_prof['target_zscore']:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_reason = 'VWAP_Z_TARGET'
                        bars_held = bar_idx
                        break
                else:
                    exit_r = (future.iloc[-1]['close'] - entry_price) / risk_ticks
                    exit_reason = 'TIME_HORIZON'
                    bars_held = max_b

                last_exit = pos + bars_held
                gross_r = exit_r * size_multiplier
                net_r = gross_r - (0.05 * size_multiplier)
                
                trades.append({
                    'symbol': symbol,
                    'gross_r': round(gross_r, 4),
                    'net_r': round(net_r, 4),
                    'exit_reason': exit_reason,
                    'bars_held': bars_held,
                    'size_mult': size_multiplier
                })

    print("\n" + "=" * 75)
    print("STAGE 1 WITH COMPLETE CLOSED-LOOP ECS SUPERVISOR RESULTS")
    print("=" * 75)
    print(f"Permitted Trades Executed: {len(trades)}")
    print(f"Blocked - Phase Divergence : {blocked_phase}")
    print(f"Blocked - Speed / Velocity : {blocked_speed}")
    
    if trades:
        tdf = pd.DataFrame(trades)
        Path("results").mkdir(parents=True, exist_ok=True)
        tdf.to_csv("results/stage1_trades.csv", index=False)
        print("[INFO] Saved results/stage1_trades.csv successfully")
        total = len(tdf)
        wr = (tdf['net_r'] > 0).mean() * 100
        gross_exp = tdf['gross_r'].mean()
        net_exp = tdf['net_r'].mean()
        total_pnl = tdf['net_r'].sum()
        
        print("-" * 75)
        print(f"Win Rate         : {wr:.2f}%")
        print(f"Gross Expectancy : {gross_exp:+.3f}R / trade")
        print(f"Net Expectancy   : {net_exp:+.3f}R / trade")
        print(f"Total Net P&L    : {total_pnl:+.2f}R")
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<15}: {cnt} trades ({cnt/total*100:.1f}%)")
    print("=" * 75)

if __name__ == '__main__':
    run()
