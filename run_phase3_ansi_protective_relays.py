import pandas as pd
import numpy as np
from pathlib import Path
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

def calculate_zerodha_equity_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)
    stt = 0.00025 * exit_val
    exchange_txn = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp_duty = 0.00003 * entry_val
    gst = 0.18 * (brokerage + exchange_txn + sebi)
    return brokerage + stt + exchange_txn + sebi + stamp_duty + gst

def compute_causal_features_welford(df: pd.DataFrame) -> pd.DataFrame:
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
    
    # Numerically Stable Weighted Welford Algorithm
    vwap_arr = np.zeros(len(df))
    std_arr = np.zeros(len(df))
    
    for _, idxs in df.groupby('date_only').groups.items():
        w_sum = 0.0
        mean = 0.0
        M2 = 0.0
        for i in idxs:
            p = df.at[i, 'typ_price']
            w = max(1.0, df.at[i, 'volume'])
            w_sum_old = w_sum
            w_sum += w
            delta = p - mean
            R = delta * w / w_sum
            mean += R
            M2 += w_sum_old * delta * R
            vwap_arr[i] = mean
            std_arr[i] = np.sqrt(M2 / w_sum) if w_sum > 0 and M2 > 0 else 1.0
            
    df['vwap'] = vwap_arr
    df['vwap_std'] = np.where(std_arr < 1e-4, 1.0, std_arr)
    df['vwap_zscore'] = (df['close'] - df['vwap']) / df['vwap_std']
    
    return df.dropna().reset_index(drop=True)

class ANSI_ProtectiveSupervisor:
    """
    Substation and Generator ANSI Protection Matrix:
      - ANSI 27 : Under-Voltage (Daily Portfolio Drawdown Breaker Trip)
      - ANSI 32R: Reverse Power (Motoring Decouple on Adverse Cascades)
      - ANSI 40 : Loss of Field (Low Liquidity Flow Inhibit)
    """
    def __init__(self, max_daily_loss_inr: float = 7500.0):
        self.max_daily_loss_inr = max_daily_loss_inr
        self.daily_realized_pnl = 0.0
        self.current_day = None
        self.ansi_27_tripped = False

    def roll_day_if_needed(self, current_date):
        if self.current_day != current_date:
            self.current_day = current_date
            self.daily_realized_pnl = 0.0
            self.ansi_27_tripped = False

    def record_closed_pnl(self, net_pnl_inr: float):
        self.daily_realized_pnl += net_pnl_inr
        if self.daily_realized_pnl <= -self.max_daily_loss_inr:
            self.ansi_27_tripped = True

    def check_entry_permissive(self, vol_flow_ratio: float) -> tuple[bool, str]:
        # ANSI 27 Check
        if self.ansi_27_tripped:
            return False, "BLOCKED_ANSI_27_UNDERVOLTAGE_TRIP"
        # ANSI 40 Check: Loss of Excitation / Liquidity Flow Collapse
        if vol_flow_ratio < 1.15:
            return False, "BLOCKED_ANSI_40_LOSS_OF_FIELD"
        return True, "PERMISSIVE_ALL_RELAYS_HEALTHY"

def run():
    print("Loading NIFTY Grid features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))
    
    print("Precomputing Welford features (July 2023)...")
    symbol_frames = {}
    for fpath in csv_files:
        sym = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            time_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[time_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(m) > 300:
                symbol_frames[sym] = compute_causal_features_welford(m).set_index('dt')
        except Exception:
            continue
            
    all_timestamps = sorted(list(set.union(*[set(df.index) for df in symbol_frames.values()])))
    
    MAX_CONCURRENT_POSITIONS = 5
    PORTFOLIO_EQUITY = 500000.0
    ALLOCATION_PER_TRADE = PORTFOLIO_EQUITY / MAX_CONCURRENT_POSITIONS
    
    supervisor = ANSI_ProtectiveSupervisor(max_daily_loss_inr=7500.0)
    active_positions = {}
    closed_trades = []
    blocked_counts = {"ANSI_27": 0, "ANSI_40": 0}
    
    print("Running Chronological Simulation with ANSI Protection Matrix...")
    for t in all_timestamps:
        supervisor.roll_day_if_needed(t.date())
        
        # 1. EVALUATE ACTIVE POSITIONS
        symbols_to_close = []
        for sym, pos in active_positions.items():
            if t not in symbol_frames[sym].index:
                continue
            bar = symbol_frames[sym].loc[t]
            bar_open = bar['open']
            bar_high = bar['high']
            bar_low = bar['low']
            bar_close = bar['close']
            bar_time = bar['time_only']
            risk_ticks = pos['risk_ticks']
            
            # SESSION 15:15 IST SQUARE-OFF
            if bar_time >= pd.to_datetime('15:15:00').time():
                symbols_to_close.append((sym, bar_close, "SESSION_1515_SQUAREOFF"))
                continue
                
            # WORST-CASE STOP CHECK
            if bar_low <= pos['stop_price']:
                fill = min(bar_open, pos['stop_price']) - (0.05 * risk_ticks)
                symbols_to_close.append((sym, fill, "STOP_TRIGGERED"))
                continue
                
            # ANSI 32R: REVERSE POWER TRIP (Controlled Decouple)
            # If trade has negative return and volume cascade is bleeding downward for 3 consecutive bars
            if bar_close < pos['entry_price'] and bar['dp_dt'] < 0 and bar['dv_dt'] > 0:
                pos['adverse_burn_bars'] += 1
                if pos['adverse_burn_bars'] >= 3:
                    symbols_to_close.append((sym, bar_close, "TRIP_ANSI_32R_REVERSE_POWER"))
                    continue
            else:
                pos['adverse_burn_bars'] = 0
                
            # VWAP EQUILIBRIUM TARGET
            if bar['vwap_zscore'] >= -0.20:
                symbols_to_close.append((sym, bar_close, "VWAP_EQUILIBRIUM"))
                continue
                
            # TRAILING STOPS (Effective next bar)
            curr_peak_r = (bar_high - pos['entry_price']) / risk_ticks
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)
            
            if pos['peak_r'] >= 1.0:
                new_stop = pos['entry_price'] + (pos['peak_r'] * 0.60 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
            elif pos['peak_r'] >= 0.50:
                new_stop = pos['entry_price'] + (0.18 * risk_ticks)
                pos['stop_price'] = max(pos['stop_price'], new_stop)
                
        for sym, fill_p, reason in symbols_to_close:
            p = active_positions.pop(sym)
            gross_pnl = (fill_p - p['entry_price']) * p['shares']
            friction = calculate_zerodha_equity_friction(p['entry_price'] * p['shares'], fill_p * p['shares'])
            net_pnl = gross_pnl - friction
            risk_inr = p['risk_ticks'] * p['shares']
            
            supervisor.record_closed_pnl(net_pnl)
            
            closed_trades.append({
                'symbol': sym,
                'gross_pnl': gross_pnl,
                'friction': friction,
                'net_pnl': net_pnl,
                'net_r': net_pnl / max(1.0, risk_inr),
                'exit_reason': reason
            })
            
        # 2. CHECK CANDIDATE ENTRIES
        available_slots = MAX_CONCURRENT_POSITIONS - len(active_positions)
        if available_slots <= 0:
            continue
            
        for sym, df in symbol_frames.items():
            if sym in active_positions or t not in df.index:
                continue
            row = df.loc[t]
            
            if row['time_only'] >= pd.to_datetime('14:45:00').time():
                continue
                
            # Filter entries: z-score deeper oversold (-2.8) to give wider noise margin
            if row['vwap_zscore'] < -2.8 and row['rsi_percentile'] < 0.05:
                # Phase alignment check
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    continue
                
                # ANSI Protection Gate Check
                permissive, reason = supervisor.check_entry_permissive(row['vol_flow_ratio'])
                if not permissive:
                    if "ANSI_27" in reason:
                        blocked_counts["ANSI_27"] += 1
                    elif "ANSI_40" in reason:
                        blocked_counts["ANSI_40"] += 1
                    continue
                    
                loc_idx = df.index.get_loc(t)
                if loc_idx + 1 >= len(df):
                    continue
                next_bar = df.iloc[loc_idx + 1]
                entry_p = next_bar['open']
                # Increased noise clearance: 1.5x ATR instead of 1.2x
                risk_t = 1.5 * row['atr']
                shares = int(ALLOCATION_PER_TRADE / entry_p)
                if shares < 1:
                    continue
                    
                active_positions[sym] = {
                    'entry_price': entry_p,
                    'stop_price': entry_p - risk_t,
                    'risk_ticks': risk_t,
                    'shares': shares,
                    'peak_r': 0.0,
                    'adverse_burn_bars': 0
                }
                available_slots -= 1
                if available_slots <= 0:
                    break

    print("\n" + "=" * 75)
    print("PHASE 3: ANSI PROTECTIVE RELAY SUPERVISOR RESULTS")
    print("=" * 75)
    print(f"Total Closed Trades           : {len(closed_trades):,}")
    print(f"Blocked - ANSI 27 (Max DD)   : {blocked_counts['ANSI_27']}")
    print(f"Blocked - ANSI 40 (Low Vol)   : {blocked_counts['ANSI_40']}")
    
    if closed_trades:
        tdf = pd.DataFrame(closed_trades)
        total = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100
        gross_pnl = tdf['gross_pnl'].sum()
        friction = tdf['friction'].sum()
        net_pnl = tdf['net_pnl'].sum()
        net_exp = tdf['net_r'].mean()
        
        print("-" * 75)
        print(f"Win Rate                      : {wr:.2f}%")
        print(f"Total Gross P&L               : ₹{gross_pnl:+,.2f}")
        print(f"Total Statutory Fees          : ₹{friction:,.2f}")
        print(f"Total Net Portfolio P&L       : ₹{net_pnl:+,.2f}")
        print(f"Average Net Expectancy        : {net_exp:+.3f}R / trade")
        print("-" * 75)
        print("Exit Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<30}: {cnt:<5} trades ({cnt/total*100:.1f}%)")
    print("=" * 75)

if __name__ == '__main__':
    run()
