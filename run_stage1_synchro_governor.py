from pathlib import Path
import pandas as pd
import numpy as np

class SynchroCheckRelay:
    def __init__(self, max_vol_ratio: float = 2.2):
        self.max_vol_ratio = max_vol_ratio

    def evaluate_permissive(self, price_delta: float, vol_flow: float, vol_ratio: float) -> tuple[bool, float, str]:
        # 1. Voltage / Stress Check
        if vol_ratio > self.max_vol_ratio:
            return False, 0.0, "REJECT_OVERVOLTAGE"
        size_factor = 0.5 if vol_ratio > 1.4 else 1.0

        # 2. Phase Angle / Flow Divergence Check
        # Reject waterfall cascades where price is dumping on heavy selling volume
        if price_delta < -0.012 and vol_flow > 1.6:
            return False, 0.0, "REJECT_PHASE_DIVERGENCE"
            
        return True, size_factor, "PERMISSIVE_GRANTED"

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
    df['ret_1m'] = df['close'].pct_change()
    
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['date_only'] = pd.to_datetime(df[time_col]).dt.date
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    df['cum_vp'] = df.groupby('date_only').apply(lambda g: (g['typ_price'] * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['cum_v'] = df.groupby('date_only')['volume'].cumsum()
    df['vwap'] = df['cum_vp'] / df['cum_v'].replace(0, 1e-5)
    
    df['cum_sq_diff'] = df.groupby('date_only').apply(lambda g: ((g['typ_price'] - g['vwap'])**2 * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df['vwap_std'] = np.sqrt(df['cum_sq_diff'] / df['cum_v'].replace(0, 1e-5)).replace(0, 1e-5)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

def run_simulation():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    relay = SynchroCheckRelay()
    
    print("=== STAGE 1: SYNCHROCHECK + DYNAMIC BE LATCH (July 2023) ===")
    trades = []
    blocked_count = 0
    be_latched_count = 0
    
    for file_path in csv_files:
        symbol = file_path.name.split('_')[1]
        try:
            raw_df = pd.read_csv(file_path)
            time_col = [c for c in raw_df.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw_df['dt'] = pd.to_datetime(raw_df[time_col])
            month_df = raw_df[(raw_df['dt'] >= '2023-07-03') & (raw_df['dt'] <= '2023-08-04')].copy()
            if len(month_df) < 300:
                continue
            df = compute_causal_features(month_df)
        except Exception:
            continue
            
        last_exit_pos = -999
        
        for pos in range(0, len(df) - 45):
            if pos < last_exit_pos + 15:
                continue
            row = df.iloc[pos]
            
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                permissive, size_factor, _ = relay.evaluate_permissive(
                    price_delta=row['ret_1m'],
                    vol_flow=row['vol_flow_ratio'],
                    vol_ratio=row['vol_ratio']
                )
                
                if not permissive:
                    blocked_count += 1
                    continue
                    
                entry_bar = df.iloc[pos + 1]
                entry_price = entry_bar['open']
                initial_risk = 1.2 * row['atr']
                current_stop = entry_price - initial_risk
                future_window = df.iloc[pos + 1 : pos + 42]
                
                exit_r = 0.0
                bars_held = 40
                exit_reason = 'TIME_HORIZON'
                be_latched = False
                
                for offset, (_, fut_row) in enumerate(future_window.iterrows(), start=1):
                    # Active Governor Check: Trailing Breakeven Latch at +0.40R MFE
                    unrealized_mfe = (fut_row['high'] - entry_price) / initial_risk
                    if not be_latched and unrealized_mfe >= 0.40:
                        # Latch stop to Breakeven (+0.05R to absorb execution drag)
                        current_stop = entry_price + (0.05 * initial_risk)
                        be_latched = True
                        be_latched_count += 1
                    
                    # Stop Evaluation
                    if fut_row['low'] <= current_stop:
                        if be_latched:
                            exit_r = +0.05
                            exit_reason = 'STOP_BREAKEVEN'
                        else:
                            exit_r = -1.0
                            exit_reason = 'STOP_LOSS'
                        bars_held = offset
                        break
                        
                    # Target Evaluation
                    if fut_row['vwap_zscore'] >= -0.3:
                        exit_r = (fut_row['close'] - entry_price) / initial_risk
                        exit_reason = 'VWAP_Z_TARGET'
                        bars_held = offset
                        break
                else:
                    exit_r = (future_window.iloc[-1]['close'] - entry_price) / initial_risk
                    
                last_exit_pos = pos + bars_held
                gross_r = exit_r * size_factor
                net_r = gross_r - 0.18
                trades.append({
                    'symbol': symbol,
                    'gross_r': gross_r,
                    'net_r': net_r,
                    'exit_reason': exit_reason
                })

    print("-" * 70)
    print(f"Operational Diagnostics:")
    print(f"  • Total Trades Executed : {len(trades)}")
    print(f"  • Synchrocheck Blocked  : {blocked_count}")
    print(f"  • Stops Latched to B/E  : {be_latched_count}")
    
    if trades:
        tdf = pd.DataFrame(trades)
        total_t = len(tdf)
        wr = (tdf['net_r'] > 0).mean() * 100
        gross_exp = tdf['gross_r'].mean()
        net_exp = tdf['net_r'].mean()
        total_pnl = tdf['net_r'].sum()
        
        print("-" * 70)
        print(f"Win Rate         : {wr:.2f}%")
        print(f"Gross Expectancy : {gross_exp:+.3f}R")
        print(f"Net Expectancy   : {net_exp:+.3f}R")
        print(f"Total Net P&L    : {total_pnl:+.2f}R")
        print("-" * 70)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<16}: {cnt} trades ({cnt/total_t*100:.1f}%)")
    print("=" * 70)

if __name__ == '__main__':
    run_simulation()
