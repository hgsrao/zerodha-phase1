import pandas as pd
import numpy as np
from pathlib import Path
from dataclasses import dataclass
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

def calculate_zerodha_delivery_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = 0.0  # Zero brokerage for equity delivery on Zerodha
    stt = 0.001 * turnover  # 0.1% on buy and sell
    exchange = 0.0000325 * turnover
    dp_charges = 15.93  # DP charges per scrip on sell side (including GST)
    sebi = 0.000001 * turnover
    stamp = 0.00015 * entry_val  # 0.015% stamp duty on delivery buy
    gst = 0.18 * (exchange + sebi)
    return float(stt + exchange + dp_charges + sebi + stamp + gst)

@dataclass
class SwingPosition:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    active_stop_price: float
    risk_ticks: float
    shares: int
    peak_r: float
    bars_held: int = 0

def resample_15min(raw_df: pd.DataFrame) -> pd.DataFrame:
    raw_df.columns = [c.lower() for c in raw_df.columns]
    time_col = [c for c in raw_df.columns if c in ['date', 'datetime', 'time', 'timestamp']][0]
    raw_df['dt'] = pd.to_datetime(raw_df[time_col]).dt.tz_localize(None)
    raw_df = raw_df.sort_values('dt').set_index('dt')
    
    df_15 = raw_df.resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna().reset_index()
    
    df_15['date_only'] = df_15['dt'].dt.date
    df_15['time_only'] = df_15['dt'].dt.time
    
    # Technical Indicators
    delta = df_15['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df_15['rsi_14'] = 100 - (100 / (1 + rs))
    
    tr = np.maximum(df_15['high'] - df_15['low'], np.maximum(abs(df_15['high'] - df_15['close'].shift(1)), abs(df_15['low'] - df_15['close'].shift(1))))
    df_15['atr'] = tr.rolling(14).mean()
    
    # Anchored Multi-Day VWAP
    df_15['typ'] = (df_15['high'] + df_15['low'] + df_15['close']) / 3.0
    cum_v = df_15['volume'].cumsum()
    cum_vp = (df_15['typ'] * df_15['volume']).cumsum()
    df_15['vwap'] = cum_vp / cum_v.replace(0, 1e-5)
    cum_sq = (((df_15['typ'] - df_15['vwap'])**2) * df_15['volume']).cumsum()
    df_15['vwap_std'] = np.sqrt(cum_sq / cum_v.replace(0, 1e-5)).replace(0, 1e-5)
    df_15['vwap_zscore'] = (df_15['close'] - df_15['vwap']) / df_15['vwap_std']
    
    return df_15.dropna().reset_index(drop=True)

def run():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print("Building 15-minute multi-day frames for all available assets (July 2023)...")
    symbol_frames = {}
    for fpath in csv_files:
        sym = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            sub = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(sub) > 300:
                symbol_frames[sym] = resample_15min(sub).set_index('dt')
        except Exception:
            continue
            
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))
    print(f"Loaded {len(symbol_frames)} assets across {len(all_timestamps):,} 15-minute bars.")
    
    MAX_CONCURRENT_SWINGS = 5
    SWING_ALLOCATION = 100000.0  # Rs 1,00,000 per swing position
    active_swings: dict[str, SwingPosition] = {}
    completed_swings = []
    
    # Multi-day holding horizon limit: 60 bars of 15m (~2.5 trading days)
    MAX_HOLD_BARS = 60
    TARGET_R = 2.20
    
    print("Executing Option C: Multi-Day Swing Engine (Overnight Carry Enabled)...")
    
    for t in all_timestamps:
        # 1. Manage open positions
        to_close = []
        for sym, pos in active_swings.items():
            if t not in symbol_frames[sym].index:
                continue
            bar = symbol_frames[sym].loc[t]
            pos.bars_held += 1
            
            # (a) Conservative Stop Check (including overnight gaps)
            if bar['low'] <= pos.active_stop_price:
                fill_p = min(bar['open'], pos.active_stop_price) - (0.05 * pos.risk_ticks)
                to_close.append((sym, fill_p, "STOP_TRIGGERED", t))
                continue
                
            # (b) Asymmetric Profit Target Hit (2.20R)
            curr_high_r = (bar['high'] - pos.entry_price) / pos.risk_ticks
            if curr_high_r >= TARGET_R:
                fill_p = pos.entry_price + (TARGET_R * pos.risk_ticks)
                to_close.append((sym, fill_p, "TARGET_HARVEST_2.20R", t))
                continue
                
            # (c) Max Time Horizon Expiration
            if pos.bars_held >= MAX_HOLD_BARS:
                to_close.append((sym, bar['close'], "SWING_TIME_EXPIRATION", t))
                continue
                
            # (d) Trailing Stop Ratchet
            pos.peak_r = max(pos.peak_r, curr_high_r)
            if pos.peak_r >= 1.20:
                ratchet = pos.entry_price + (pos.peak_r * 0.40 * pos.risk_ticks)
                pos.active_stop_price = max(pos.active_stop_price, ratchet)
                
        for sym, fill_p, reason, exit_t in to_close:
            p = active_swings.pop(sym)
            gross = (fill_p - p.entry_price) * p.shares
            friction = calculate_zerodha_delivery_friction(p.entry_price * p.shares, fill_p * p.shares)
            net = gross - friction
            completed_swings.append({
                'symbol': sym,
                'entry_time': p.entry_time,
                'exit_time': exit_t,
                'entry_price': p.entry_price,
                'exit_price': fill_p,
                'shares': p.shares,
                'gross_pnl': gross,
                'friction': friction,
                'net_pnl': net,
                'net_r': net / (p.risk_ticks * p.shares),
                'bars_held': p.bars_held,
                'exit_reason': reason
            })
            
        # 2. Look for new swing entries if capacity exists
        if len(active_swings) >= MAX_CONCURRENT_SWINGS:
            continue
            
        for sym, df in symbol_frames.items():
            if sym in active_swings or t not in df.index:
                continue
            loc_idx = df.index.get_loc(t)
            if loc_idx < 2 or loc_idx + 1 >= len(df):
                continue
                
            curr_bar = df.iloc[loc_idx]
            prev_bar = df.iloc[loc_idx - 1]
            
            # Swing Setup: Significant Multi-Day VWAP Oversold + Synchrocheck confirmation
            if curr_bar['vwap_zscore'] < -2.0 and curr_bar['rsi_14'] < 35.0:
                if curr_bar['close'] > prev_bar['high'] and curr_bar['close'] > curr_bar['open']:
                    next_bar = df.iloc[loc_idx + 1]
                    entry_p = next_bar['open']
                    swing_low = min(curr_bar['low'], prev_bar['low'])
                    risk_dist = max(entry_p - swing_low, 1.2 * curr_bar['atr'])
                    shares = max(1, int(SWING_ALLOCATION / entry_p))
                    
                    active_swings[sym] = SwingPosition(
                        symbol=sym,
                        entry_time=next_bar.name,
                        entry_price=entry_p,
                        active_stop_price=entry_p - risk_dist,
                        risk_ticks=risk_dist,
                        shares=shares,
                        peak_r=0.0
                    )
                    if len(active_swings) >= MAX_CONCURRENT_SWINGS:
                        break

    tdf = pd.DataFrame(completed_swings)
    print("\n" + "=" * 80)
    print("OPTION C: MULTI-DAY SWING CARRY ENGINE RESULTS (JULY 2023)")
    print("=" * 80)
    print(f"Total Completed Swings : {len(tdf):,}")
    if not tdf.empty:
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross = tdf['gross_pnl'].sum()
        friction = tdf['friction'].sum()
        net = tdf['net_pnl'].sum()
        avg_hold = tdf['bars_held'].mean() * 15 / 60  # in hours
        print(f"Net Win Rate           : {wr:.2f}%")
        print(f"Gross Swing Alpha      : Rs {gross:+,.2f}")
        print(f"Total Statutory Fees   : Rs {friction:,.2f}  (Friction Drag: {friction/abs(gross)*100:.1f}%)")
        print(f"Net Portfolio P&L      : Rs {net:+,.2f}")
        print(f"Average Hold Duration  : {avg_hold:.1f} market hours")
        print("-" * 80)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<25}: {cnt:<4} trades ({cnt/len(tdf)*100:.1f}%)")
        tdf.to_parquet('option_c_swing_ledger.parquet')
        print("\nSaved trade ledger to 'option_c_swing_ledger.parquet'")
    print("=" * 80)

if __name__ == '__main__':
    run()
