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
    consecutive_adverse_bars: int = 0

class ANSIProtectiveRelaySupervisor:
    def __init__(self, daily_dd_limit_rs: float = -7500.0):
        self.daily_dd_limit_rs = daily_dd_limit_rs
        self.current_day = None
        self.daily_realized_pnl = 0.0
        self.tripped_27 = False

    def on_new_minute(self, current_dt):
        day = current_dt.date()
        if day != self.current_day:
            self.current_day = day
            self.daily_realized_pnl = 0.0
            self.tripped_27 = False

    def check_entry_permissive(self) -> bool:
        return not self.tripped_27

    def register_trade_close(self, net_pnl_rs: float):
        self.daily_realized_pnl += net_pnl_rs
        if self.daily_realized_pnl <= self.daily_dd_limit_rs:
            self.tripped_27 = True

    def evaluate_in_trade_relays(self, pos: ActivePosition, bar_close: float, vol_ratio: float, volume_flow: float) -> tuple[bool, str]:
        # ANSI 32R - Reverse Power Protection (Decouple motoring positions)
        if bar_close < pos.entry_price:
            pos.consecutive_adverse_bars += 1
        else:
            pos.consecutive_adverse_bars = 0

        curr_r = (bar_close - pos.entry_price) / pos.risk_ticks
        if curr_r < -0.35 and pos.consecutive_adverse_bars >= 3:
            return True, "ANSI_32R_REVERSE_POWER_DECOUPLE"

        # ANSI 40 - Loss of Field / Excitation (Volume collapse during expansion)
        if volume_flow < 0.20 and vol_ratio > 2.0 and curr_r < 0:
            return True, "ANSI_40_LOSS_OF_FIELD_COLLAPSE"

        return False, "ALL_CLEAR"

def compute_causal_features(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    df['rsi_percentile'] = df['rsi_14'].rolling(100).apply(lambda x: (x.argsort().argsort()[-1] + 1.0) / 100.0, raw=True)

    tr = np.maximum(df['high'] - df['low'], np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1))))
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

    def calc_vwap_stats(g):
        cum_w = g['volume'].cumsum()
        cum_wp = (g['typ_price'] * g['volume']).cumsum()
        vwap = cum_wp / cum_w.replace(0, 1e-5)
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
    print("Loading NIFTY Grid reference features...")
    grid_df = pd.read_parquet('nifty_grid_features.parquet').set_index('dt')
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    csv_files = sorted(list(data_dir.glob('*.csv')))

    print("Preprocessing July 2023 universe...")
    asset_bars = {}
    for fpath in csv_files:
        sym = fpath.name.split('_')[1]
        try:
            raw = pd.read_csv(fpath)
            t_col = [c for c in raw.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            month = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-04')].copy()
            if len(month) > 300:
                asset_bars[sym] = compute_causal_features(month).set_index('dt')
        except Exception:
            continue

    all_timestamps = sorted(list(set.union(*[set(df.index) for df in asset_bars.values()])))
    
    # Portfolio Limits & Protective Relay Setup
    MAX_CONCURRENT_POSITIONS = 5
    FIXED_RUPEE_ALLOCATION = 100000.0
    active_positions: dict[str, ActivePosition] = {}
    completed_trades = []
    
    supervisor = ANSIProtectiveRelaySupervisor(daily_dd_limit_rs=-7500.0)

    print("Running Unified Portfolio with ANSI Protection Package...")

    for t in all_timestamps:
        b_time = t.time()
        supervisor.on_new_minute(t)

        # 1. Evaluate Active Positions
        to_remove = []
        for sym, pos in active_positions.items():
            if t not in asset_bars[sym].index:
                continue
            bar = asset_bars[sym].loc[t]

            # A. Session EOD Square-Off
            if b_time >= pd.to_datetime('15:15:00').time():
                fill_p = bar['close']
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                net = gross - friction
                supervisor.register_trade_close(net)
                completed_trades.append({
                    'symbol': sym, 'entry_price': pos.entry_price, 'exit_price': fill_p,
                    'gross_pnl_rs': gross, 'friction_rs': friction, 'net_pnl_rs': net,
                    'gross_r': (fill_p - pos.entry_price) / pos.risk_ticks,
                    'exit_reason': 'SESSION_1515_SQUAREOFF'
                })
                to_remove.append(sym)
                continue

            # B. Worst-Case Conservative Stop Check
            if bar['low'] <= pos.active_stop_price:
                fill_p = min(bar['open'], pos.active_stop_price) - (0.05 * pos.risk_ticks)
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                net = gross - friction
                supervisor.register_trade_close(net)
                completed_trades.append({
                    'symbol': sym, 'entry_price': pos.entry_price, 'exit_price': fill_p,
                    'gross_pnl_rs': gross, 'friction_rs': friction, 'net_pnl_rs': net,
                    'gross_r': (fill_p - pos.entry_price) / pos.risk_ticks,
                    'exit_reason': 'STOP_TRIGGERED'
                })
                to_remove.append(sym)
                continue

            # C. ANSI Protective Relays (Reverse Power & Field Loss)
            tripped, trip_reason = supervisor.evaluate_in_trade_relays(
                pos, bar['close'], bar['vol_ratio'], bar['vol_flow_ratio']
            )
            if tripped:
                fill_p = bar['close']
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                net = gross - friction
                supervisor.register_trade_close(net)
                completed_trades.append({
                    'symbol': sym, 'entry_price': pos.entry_price, 'exit_price': fill_p,
                    'gross_pnl_rs': gross, 'friction_rs': friction, 'net_pnl_rs': net,
                    'gross_r': (fill_p - pos.entry_price) / pos.risk_ticks,
                    'exit_reason': trip_reason
                })
                to_remove.append(sym)
                continue

            # D. Dynamic Asymmetric Take-Profit Target (1.80R)
            curr_r = (bar['close'] - pos.entry_price) / pos.risk_ticks
            if curr_r >= 1.80:
                fill_p = bar['close']
                gross = (fill_p - pos.entry_price) * pos.shares
                friction = calculate_zerodha_friction(pos.entry_price * pos.shares, fill_p * pos.shares)
                net = gross - friction
                supervisor.register_trade_close(net)
                completed_trades.append({
                    'symbol': sym, 'entry_price': pos.entry_price, 'exit_price': fill_p,
                    'gross_pnl_rs': gross, 'friction_rs': friction, 'net_pnl_rs': net,
                    'gross_r': curr_r,
                    'exit_reason': 'TARGET_HARVEST_1.80R'
                })
                to_remove.append(sym)
                continue

            # E. Ratchet stops for next bar
            curr_peak_r = (bar['high'] - pos.entry_price) / pos.risk_ticks
            pos.peak_r = max(pos.peak_r, curr_peak_r)
            if pos.peak_r >= 1.0:
                new_stop = pos.entry_price + (pos.peak_r * 0.45 * pos.risk_ticks)
                pos.active_stop_price = max(pos.active_stop_price, new_stop)

        for s in to_remove:
            del active_positions[s]

        # 2. Candidate Admission Gate (Supervised by ANSI 27)
        if len(active_positions) >= MAX_CONCURRENT_POSITIONS or b_time >= pd.to_datetime('14:45:00').time():
            continue

        if not supervisor.check_entry_permissive():
            continue

        if t not in grid_df.index:
            continue
        grid_row = grid_df.loc[t]

        for sym, df_sym in asset_bars.items():
            if sym in active_positions or t not in df_sym.index:
                continue
            row = df_sym.loc[t]

            # Require confirmation and depth
            if row['vwap_zscore'] < -2.8 and row['rsi_percentile'] < 0.04 and row['vol_flow_ratio'] >= 1.10:
                price_dir = 1.0 if row['dp_dt'] > 0 else -1.0
                volume_dir = 1.0 if row['dv_dt'] > 0 else -1.0
                if row['dp_dt'] < 0 and (price_dir * volume_dir) < 0:
                    continue

                droop_r = MACHINE_DROOP_MAP.get(sym, DEFAULT_DROOP)
                droop_atten = (1.0 / droop_r) * max(0.0, -grid_row['f_grid'])
                raw_u = 0.50 * (-row['vwap_zscore']) * (1.0 + 0.3 * max(0.0, row['vol_ratio'] - 1.0))
                u_eff = float(np.clip(raw_u - droop_atten, 0.0, 1.25))
                if u_eff < 0.75:
                    continue

                loc_idx = df_sym.index.get_loc(t)
                if loc_idx + 1 >= len(df_sym):
                    continue
                next_bar = df_sym.iloc[loc_idx + 1]
                entry_p = next_bar['open']
                risk_ticks = 1.4 * row['atr']  # Wider noise buffer
                shares = max(1, int(FIXED_RUPEE_ALLOCATION / entry_p))

                active_positions[sym] = ActivePosition(
                    symbol=sym,
                    entry_time=next_bar.name,
                    entry_price=entry_p,
                    active_stop_price=entry_p - risk_ticks,
                    risk_ticks=risk_ticks,
                    shares=shares,
                    peak_r=0.0
                )

                if len(active_positions) >= MAX_CONCURRENT_POSITIONS:
                    break

    tdf = pd.DataFrame(completed_trades)
    print("\n" + "=" * 80)
    print("PHASE 3: ANSI PROTECTIVE RELAY SUPERVISOR RESULTS (JULY 2023)")
    print("=" * 80)
    print(f"Total Trades Completed : {len(tdf):,}")
    if not tdf.empty:
        wr = (tdf['net_pnl_rs'] > 0).mean() * 100
        gross = tdf['gross_pnl_rs'].sum()
        friction = tdf['friction_rs'].sum()
        net = tdf['net_pnl_rs'].sum()
        print(f"Net Win Rate           : {wr:.2f}%")
        print(f"Gross Portfolio Alpha  : ₹{gross:+,.2f}")
        print(f"Total Zerodha Charges  : ₹{friction:,.2f}")
        print(f"Net Portfolio Realized : ₹{net:+,.2f}")
        print(f"Net Expectancy / Trade : ₹{net/len(tdf):+,.2f}")
        print("-" * 80)
        print("Exit & Trip Reason Distribution:")
        for r, cnt in tdf['exit_reason'].value_counts().items():
            print(f"  • {r:<33}: {cnt:<5} trades ({cnt/len(tdf)*100:.1f}%)")
    print("=" * 80)

if __name__ == '__main__':
    run()
