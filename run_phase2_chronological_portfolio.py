import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

MACHINE_DROOP_MAP = {
    "HDFCBANK": 0.04, "ICICIBANK": 0.04, "SBIN": 0.04, "AXISBANK": 0.04, "KOTAKBANK": 0.04,
    "BAJFINANCE": 0.04, "BAJAJFINSV": 0.04, "TCS": 0.04, "INFY": 0.04, "TECHM": 0.04,
    "WIPRO": 0.04, "HCLTECH": 0.04, "TATASTEEL": 0.04, "JSWSTEEL": 0.04, "HINDALCO": 0.04,
    "RELIANCE": 0.055, "LT": 0.055, "MARUTI": 0.055, "M&M": 0.055, "TATACONSUM": 0.055,
    "EICHERMOT": 0.055, "BAJAJ-AUTO": 0.055, "ADANIENT": 0.055, "ADANIPORTS": 0.055,
    "GRASIM": 0.055, "ULTRACEMCO": 0.055, "TITAN": 0.055,
    "ITC": 0.075, "HINDUNILVR": 0.075, "NESTLEIND": 0.075, "BRITANNIA": 0.075,
    "CIPLA": 0.075, "DRREDDY": 0.075, "SUNPHARMA": 0.075, "APOLLOHOSP": 0.075,
    "NTPC": 0.075, "POWERGRID": 0.075, "COALINDIA": 0.075, "ONGC": 0.075, "BPCL": 0.075
}
DEFAULT_DROOP = 0.055

def calculate_zerodha_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)
    stt = 0.00025 * exit_val
    exchange = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp = 0.00003 * entry_val
    gst = 0.18 * (brokerage + exchange + sebi)
    return float(brokerage + stt + exchange + sebi + stamp + gst)

@dataclass
class ActivePosition:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    active_stop_price: float
    risk_ticks: float
    shares: int
    peak_r: float
    entry_idx: int

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
    
    time_col = [c for c in df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    df['dt'] = pd.to_datetime(df[time_col]).dt.tz_localize(None)
    df['date_only'] = df['dt'].dt.date
    df['time_only'] = df['dt'].dt.time
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0
    
    # Audit Fix: Consistent Day-Reset Weighted VWAP Dispersion
    def calc_vwap_stats(g):
        cum_w = g['volume'].cumsum()
        cum_wp = (g['typ_price'] * g['volume']).cumsum()
        vwap = cum_wp / cum_w.replace(0, 1e-5)
        # Accurate weighted variance: sum(w * (p - vwap)^2) / sum(w)
        cum_w_diff2 = (((g['typ_price'] - vwap)**2) * g['volume']).cumsum()
        vwap_std = np.sqrt(cum_w_diff2 / cum_w.replace(0, 1e-5)).replace(0, 1e-5)
        z = (g['close'] - vwap) / vwap_std
        return pd.DataFrame({'vwap': vwap, 'vwap_std': vwap_std, 'vwap_zscore': z}, index=g.index)
    
    stats = df.groupby('date_only', group_keys=False).apply(calc_vwap_stats)
    df['vwap'] = stats['vwap']
    df['vwap_std'] = stats['vwap_std']
    df['vwap_zscore'] = stats['vwap_zscore']
    
    return df.dropna().reset_index(drop=True)

def run():
    print("Loading NIFTY Grid features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print("Preprocessing July 2023 1-minute datasets across all symbols...")
    asset_bars = {}
    
    for fpath in csv_files:
        symbol = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            month = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(month) < 300:
                continue
            df = compute_causal_features(month)
            asset_bars[symbol] = df.set_index('dt')
        except Exception:
            continue
            
    print(f"Loaded {len(asset_bars)} symbols. Assembling chronological timeline...")
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in asset_bars.values()])))
    
    # Portfolio State
    MAX_CONCURRENT_POSITIONS = 5
    FIXED_RUPEE_ALLOCATION = 100000.0  # ₹1,00,000 capital per machine slot
    active_positions: dict[str, ActivePosition] = {}
    completed_trades = []
    
    print(f"Running Unified Chronological Ledger (Max Concurrent: {MAX_CONCURRENT_POSITIONS})...")
    
    for t in all_timestamps:
        b_time = t.time()
        
        # 1. Intra-bar stop check & position maintenance on open trades
        to_remove = []
        for sym, pos in active_positions.items():
            if t not in asset_bars[sym].index:
                continue
            bar = asset_bars[sym].loc[t]
            
            # (a) Hard Session Square-Off at 15:15 IST
            if b_time >= pd.to_datetime('15:15:00').time():
                fill_p = bar['close']
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                completed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_p,
                    'shares': pos.shares,
                    'gross_pnl_rs': gross,
                    'friction_rs': friction,
                    'net_pnl_rs': gross - friction,
                    'gross_r': (fill_p - pos.entry_price) / pos.risk_ticks,
                    'exit_reason': 'SESSION_1515_SQUAREOFF'
                })
                to_remove.append(sym)
                continue
                
            # (b) Worst-case stop check: bar low against active stop
            if bar['low'] <= pos.active_stop_price:
                fill_p = min(bar['open'], pos.active_stop_price) - (0.05 * pos.risk_ticks)
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                completed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_p,
                    'shares': pos.shares,
                    'gross_pnl_rs': gross,
                    'friction_rs': friction,
                    'net_pnl_rs': gross - friction,
                    'gross_r': (fill_p - pos.entry_price) / pos.risk_ticks,
                    'exit_reason': 'STOP_TRIGGERED'
                })
                to_remove.append(sym)
                continue
                
            # (c) Equilibrium target on close
            if bar['vwap_zscore'] >= -0.20:
                fill_p = bar['close']
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                completed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_p,
                    'shares': pos.shares,
                    'gross_pnl_rs': gross,
                    'friction_rs': friction,
                    'net_pnl_rs': gross - friction,
                    'gross_r': (fill_p - pos.entry_price) / pos.risk_ticks,
                    'exit_reason': 'VWAP_EQUILIBRIUM'
                })
                to_remove.append(sym)
                continue
                
            # (d) Update peak & arm trailing ratchets for next bar
            curr_peak_r = (bar['high'] - pos.entry_price) / pos.risk_ticks
            pos.peak_r = max(pos.peak_r, curr_peak_r)
            if pos.peak_r >= 1.0:
                new_stop = pos.entry_price + (pos.peak_r * 0.60 * pos.risk_ticks)
                pos.active_stop_price = max(pos.active_stop_price, new_stop)
            elif pos.peak_r >= 0.50:
                new_stop = pos.entry_price + (0.18 * pos.risk_ticks)
                pos.active_stop_price = max(pos.active_stop_price, new_stop)
                
        for s in to_remove:
            del active_positions[s]
            
        # 2. Portfolio Admission Gate (Check available slots & intraday cutoff)
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS or b_time >= pd.to_datetime('14:45:00').time():
            continue
            
        if t not in grid_df.index:
            continue
        grid_row = grid_df.loc[t]
        
        # Scan for admitted machines
        for sym, df_sym in asset_bars.items():
            if sym in active_positions or t not in df_sym.index:
                continue
                
            row = df_sym.loc[t]
            if row['vwap_zscore'] < -2.5 and row['rsi_percentile'] < 0.05 and row['vol_flow_ratio'] >= 1.05:
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    continue
                    
                droop_r = MACHINE_DROOP_MAP.get(sym, DEFAULT_DROOP)
                droop_atten = (1.0 / droop_r) * max(0.0, -grid_row['f_grid'])
                raw_u = 0.50 * (-row['vwap_zscore']) * (1.0 + 0.3 * max(0.0, row['vol_ratio'] - 1.0))
                u_eff = float(np.clip(raw_u - droop_atten, 0.0, 1.25))
                if u_eff < 0.70:
                    continue
                    
                # Look up next-bar open for true entry fill
                loc_idx = df_sym.index.get_loc(t)
                if loc_idx + 1 >= len(df_sym):
                    continue
                next_bar = df_sym.iloc[loc_idx + 1]
                entry_p = next_bar['open']
                risk_ticks = 1.2 * row['atr']
                shares = max(1, int(FIXED_RUPEE_ALLOCATION / entry_p))
                
                active_positions[sym] = ActivePosition(
                    symbol=sym,
                    entry_time=next_bar.name,
                    entry_price=entry_p,
                    active_stop_price=entry_p - risk_ticks,
                    risk_ticks=risk_ticks,
                    shares=shares,
                    peak_r=0.0,
                    entry_idx=loc_idx + 1
                )
                
                if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
                    break

    tdf = pd.DataFrame(completed_trades)
    print("\n" + "=" * 80)
    print("PHASE 2: UNIFIED CHRONOLOGICAL PORTFOLIO & RUPEE LEDGER RESULTS")
    print("=" * 80)
    print(f"Total Completed Trades : {len(tdf):,}")
    
    if not tdf.empty:
        wr = (tdf['net_pnl_rs'] > 0).mean() * 100
        gross_pnl = tdf['gross_pnl_rs'].sum()
        total_fees = tdf['friction_rs'].sum()
        net_pnl = tdf['net_pnl_rs'].sum()
        avg_gross_r = tdf['gross_r'].mean()
        
        print(f"Net Win Rate           : {wr:.2f}%")
        print(f"Average Gross Trade R  : {avg_gross_r:+.3f}R")
        print(f"Gross Portfolio Alpha  : ₹{gross_pnl:+,.2f}")
        print(f"Total Zerodha Charges  : ₹{total_fees:,.2f}")
        print(f"Net Portfolio P&L      : ₹{net_pnl:+,.2f}")
        print("-" * 80)
        print("Exit Reason Breakdown:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<25}: {cnt:<5} trades ({cnt/len(tdf)*100:.1f}%)")
            
        tdf.to_parquet('phase2_chronological_ledger.parquet')
        print(f"\nSaved trade ledger to 'phase2_chronological_ledger.parquet'")
    print("=" * 80)

if __name__ == '__main__':
    run()
