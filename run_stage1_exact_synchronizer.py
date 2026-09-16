import pandas as pd
import numpy as np
from pathlib import Path

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
    df['vol_ratio'] = df['atr'] / df['atr_baseline']
    
    df['vol_median'] = df['volume'].rolling(50).median()
    df['vol_flow_ratio'] = df['volume'] / df['vol_median']
    
    # Differential variables (from COMPLETE_DCS_CLOSED_LOOP_V1_FIXED.py lines 311-312)
    df['dp_dt'] = df['close'].diff()
    df['dv_dt'] = df['volume'].diff()
    
    # Anchored VWAP
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    df['date_only'] = df['dt'].dt.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    df['cum_vp'] = df.groupby('date_only').apply(lambda g: (g['typ_price'] * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['cum_v'] = df.groupby('date_only')['volume'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_v'].replace(0, 1e-5)
    
    df['cum_sq_diff'] = df.groupby('date_only').apply(lambda g: ((g['typ_price'] - g['vwap'])**2 * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['vwap_std'] = np.sqrt(df['cum_sq_diff'] / df['cum_v'].replace(0, 1e-5)).replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def run():
    print("Loading NIFTY Grid features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    trades = []
    blocked_out_of_phase = 0
    blocked_grid_stress = 0
    
    print("Running Stage 1 using exact ECS Phase Alignment + Grid Supervision...")
    
    for i, fpath in enumerate(csv_files, 1):
        symbol = fpath.name.split('_')[1]
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
            
            # Base Oversold Trigger
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                
                # 1. EXACT PHASE ALIGNMENT (from your file lines 315-326)
                price_direction = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_direction = 1.0 if row['dv_dt'] > 0 else -1.0
                phase_alignment = price_direction * volume_direction
                
                # Permissive: Must show buyer absorption (phase_alignment > 0)
                # or price stabilizing (dp_dt >= 0)
                if row['dp_dt'] < 0 and phase_alignment < 0:
                    # Free-fall on expanding volume -> Out of phase
                    blocked_out_of_phase += 1
                    continue
                
                # 2. EXTERNAL GRID FREQUENCY SUPERVISION (NIFTY not in high-velocity dump)
                if grid_row['f_grid'] < -0.0025:  # NIFTY falling > 0.25% in 1-min
                    blocked_grid_stress += 1
                    continue
                    
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                risk_ticks = 1.2 * row['atr']
                future = df.iloc[pos + 1 : pos + 42]
                
                exit_r = 0.0
                bars_held = 40
                exit_reason = 'TIME_HORIZON'
                
                for offset, (_, fut_row) in enumerate(future.iterrows(), start=1):
                    if (entry_price - fut_row['low']) >= risk_ticks:
                        exit_r = -1.0
                        exit_reason = 'STOP_LOSS'
                        bars_held = offset
                        break
                    if fut_row['vwap_zscore'] >= -0.3:
                        exit_r = (fut_row['close'] - entry_price) / risk_ticks
                        exit_reason = 'VWAP_Z_TARGET'
                        bars_held = offset
                        break
                else:
                    exit_r = (future.iloc[-1]['close'] - entry_price) / risk_ticks
                    
                last_exit = pos + bars_held
                gross_r = exit_r
                net_r = gross_r - 0.18
                trades.append({'symbol': symbol, 'gross_r': gross_r, 'net_r': net_r, 'exit_reason': exit_reason})

    print("\n" + "=" * 75)
    print("STAGE 1 WITH EXACT CODEBASE SYNCHRONIZER & GRID RESULTS")
    print("=" * 75)
    print(f"Permitted Trades Closed : {len(trades)}")
    print(f"Blocked - Out of Phase  : {blocked_out_of_phase}")
    print(f"Blocked - Grid Inhibit  : {blocked_grid_stress}")
    
    if trades:
        tdf = pd.DataFrame(trades)
        total = len(tdf)
        wr = (tdf['net_r'] > 0).mean() * 100
        gross_exp = tdf['gross_r'].mean()
        net_exp = tdf['net_r'].mean()
        total_pnl = tdf['net_r'].sum()
        
        print("-" * 75)
        print(f"Win Rate         : {wr:.2f}%")
        print(f"Gross Expectancy : {gross_exp:+.3f}R")
        print(f"Net Expectancy   : {net_exp:+.3f}R")
        print(f"Total Net P&L    : {total_pnl:+.2f}R")
        print("Exit Breakdown:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<15}: {cnt} trades ({cnt/total*100:.1f}%)")
    print("=" * 75)

if __name__ == '__main__':
    run()
