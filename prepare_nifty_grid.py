import pandas as pd
import numpy as np

def compute_grid_features():
    print("Pre-processing NIFTY 50 grid data...")
    df = pd.read_csv('NIFTY_50_1min_true.csv')
    df.columns = [c.lower() for c in df.columns]
    
    # Standardize timestamp
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    
    # 1. Grid Voltage: Normalized Volatility Stress (ATR14 / ATR50 baseline)
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    df['grid_atr'] = tr.rolling(14).mean()
    df['grid_atr_base'] = df['grid_atr'].rolling(50).mean()
    df['v_grid'] = (df['grid_atr'] / df['grid_atr_base']).replace(0, 1.0)
    
    # 2. Grid Frequency: 1-minute velocity/drift
    df['f_grid'] = df['close'].pct_change().fillna(0)
    
    # 3. Grid Volume Flow & Phase Angle
    df['grid_vol_med'] = df['volume'].rolling(50).median().replace(0, 1.0)
    df['grid_vol_flow'] = (df['volume'] / df['grid_vol_med']).fillna(1.0)
    
    # Phase angle delta: atan2(velocity, volume displacement)
    df['delta_grid_rad'] = np.arctan2(df['f_grid'], np.maximum(0.01, df['grid_vol_flow'] - 1.0))
    
    out_cols = ['dt', 'close', 'v_grid', 'f_grid', 'grid_vol_flow', 'delta_grid_rad']
    clean_grid = df[out_cols].dropna().reset_index(drop=True)
    clean_grid.to_parquet('nifty_grid_features.parquet', index=False)
    print(f"✅ Grid features saved: {len(clean_grid):,} rows in nifty_grid_features.parquet")

if __name__ == '__main__':
    compute_grid_features()
