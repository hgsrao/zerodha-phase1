from pathlib import Path
from dataclasses import dataclass
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

from alpha_engine_core import ZerodhaFeeCalculator

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

def compute_hurst(series: np.ndarray, max_lags: int = 40) -> float:
    if len(series) < 150:
        return 0.50
    lags = range(8, max_lags)
    tau = []
    for lag in lags:
        subsets = [series[i:i + lag] for i in range(0, len(series) - lag, lag)]
        rs = []
        for sub in subsets:
            mean_adj = sub - np.mean(sub)
            cum_dev = np.cumsum(mean_adj)
            r = np.max(cum_dev) - np.min(cum_dev)
            s = np.std(sub, ddof=1)
            if s > 0:
                rs.append(r / s)
        if rs:
            tau.append(np.mean(rs))
        else:
            tau.append(1.0)
    poly = np.polyfit(np.log(list(lags)), np.log(tau), 1)
    return float(poly[0])

@dataclass
class Position:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    risk_ticks: float
    shares: int
    peak_r: float
    r_target: float
    pending_exit: str = None

def resample_15m(df_1m: pd.DataFrame) -> pd.DataFrame:
    df_15 = df_1m.resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    
    df_15['date'] = df_15.index.date
    df_15['typical'] = (df_15['high'] + df_15['low'] + df_15['close']) / 3.0
    df_15['cum_vol'] = df_15.groupby('date')['volume'].cumsum()
    df_15['cum_pv'] = df_15.groupby('date', group_keys=False).apply(
        lambda g: (g['typical'] * g['volume']).cumsum()
    )
    df_15['vwap'] = df_15['cum_pv'] / np.maximum(df_15['cum_vol'], 1.0)
    
    df_15['rolling_std'] = df_15.groupby('date')['typical'].transform(lambda s: s.expanding().std()).fillna(1.0)
    df_15['vwap_zscore'] = (df_15['close'] - df_15['vwap']) / np.maximum(df_15['rolling_std'], 0.1)
    
    delta = df_15['close'].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / np.maximum(loss, 1e-6)
    df_15['rsi_14'] = 100.0 - (100.0 / (1.0 + rs))
    
    h_l = df_15['high'] - df_15['low']
    h_pc = (df_15['high'] - df_15['close'].shift(1)).abs()
    l_pc = (df_15['low'] - df_15['close'].shift(1)).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    df_15['atr'] = tr.rolling(14).mean()
    df_15['vol_ratio'] = df_15['volume'] / np.maximum(df_15['volume'].rolling(20).mean(), 1.0)
    
    return df_15.dropna()

def main():
    print("=" * 80)
    print("HURST-OPTIMIZED COMBINED POWER PLANT SIMULATION (38 MONTHS)")
    print("Regime: Mean-Reverting Universe Only (H < 0.48) | Target: 2.50R")
    print("=" * 80)

    all_files = list(DATA_DIR.glob("NSE_*_minute_*.csv"))
    raw_data = {}
    print(f"Loading {len(all_files)} raw asset files...")
    for f in all_files:
        sym = f.name.split('_')[1].upper()
        df = pd.read_csv(f)
        t_col = [c for c in df.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
        df['dt'] = pd.to_datetime(df[t_col]).dt.tz_localize(None)
        raw_data[sym] = df.set_index('dt').sort_index()

    print("Computing Hurst Exponents across all assets to select mean-reverting universe...")
    eligible_symbols = []
    for sym, df_1m in raw_data.items():
        c15 = df_1m['close'].iloc[::15].dropna().values
        h = compute_hurst(c15[-8000:])
        if h < 0.48:
            eligible_symbols.append((sym, h))

    eligible_symbols.sort(key=lambda x: x[1])
    print(f"Verified {len(eligible_symbols)} symbols meeting strict mean-reversion criteria:")
    for s, h in eligible_symbols:
        print(f"  • {s:<12}: H = {h:.3f}")

    target_syms = [s for s, _ in eligible_symbols]
    if not target_syms:
        print("No symbols passed H < 0.48.")
        return

    slip_mult = 2.0 / 10000.0
    matrices_15m = {}
    z_list = []
    daily_gaps = set()

    for s in target_syms:
        df_1m = raw_data[s]
        feat_15 = resample_15m(df_1m)
        matrices_15m[s] = feat_15
        z_list.append(feat_15['vwap_zscore'].rename(s))

        daily = df_1m.resample('D').agg({'open': 'first', 'close': 'last'}).dropna()
        daily['prev_close'] = daily['close'].shift(1)
        daily['gap_pct'] = (daily['open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        for d in daily[daily['gap_pct'] <= -1.25].index.date:
            daily_gaps.add((s, d))

    macro_breadth = pd.concat(z_list, axis=1).mean(axis=1).rename('breadth_z')
    sector_1m = {s: raw_data[s] for s in target_syms}
    master_timeline = sorted(list(set.union(*[set(df.index) for df in sector_1m.values()])))

    active_positions: dict[str, Position] = {}
    armed_symbols: dict[str, dict] = {}
    closed_trades = []

    for t_1m in master_timeline:
        b_time = t_1m.time()
        b_date = t_1m.date()
        is_15m = (t_1m.minute % 15 == 0)

        # 1. Fill Pending Exits on 1m Open
        to_del = []
        for sym, pos in active_positions.items():
            if pos.pending_exit and t_1m in sector_1m[sym].index:
                open_p = sector_1m[sym].loc[t_1m, 'open']
                fill_p = open_p * (1.0 - slip_mult)
                gross = (fill_p - pos.entry_price) * pos.shares
                fees = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_p * pos.shares)
                closed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t_1m,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_p,
                    'shares': pos.shares,
                    'gross_pnl': gross,
                    'friction': fees,
                    'net_pnl': gross - fees,
                    'reason': pos.pending_exit
                })
                to_del.append(sym)
        for s in to_del:
            del active_positions[s]

        # 2. Intraday 1-Minute Microstructure Engine
        for sym, pos in list(active_positions.items()):
            if t_1m not in sector_1m[sym].index:
                continue
            b1 = sector_1m[sym].loc[t_1m]

            if b_time >= pd.Timestamp("15:15:00").time():
                pos.pending_exit = "MANDATORY_1515_SQUAREOFF"
                continue

            # Stop-loss check
            if b1['low'] <= pos.stop_price:
                fill_p = min(b1['open'], pos.stop_price) * (1.0 - slip_mult)
                gross = (fill_p - pos.entry_price) * pos.shares
                fees = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_p * pos.shares)
                closed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t_1m,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_p,
                    'shares': pos.shares,
                    'gross_pnl': gross,
                    'friction': fees,
                    'net_pnl': gross - fees,
                    'reason': "STOP_LOSS_1M_HIT"
                })
                del active_positions[sym]
                continue

            curr_peak_r = (b1['high'] - pos.entry_price) / pos.risk_ticks
            pos.peak_r = max(pos.peak_r, curr_peak_r)

            # High-Yield 2.50R Target
            if curr_peak_r >= pos.r_target:
                fill_p = (pos.entry_price + (pos.r_target * pos.risk_ticks)) * (1.0 - slip_mult)
                gross = (fill_p - pos.entry_price) * pos.shares
                fees = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_p * pos.shares)
                closed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t_1m,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_p,
                    'shares': pos.shares,
                    'gross_pnl': gross,
                    'friction': fees,
                    'net_pnl': gross - fees,
                    'reason': "DYNAMIC_R_TARGET_1M_HIT"
                })
                del active_positions[sym]
                continue

            # Trailing Profit Lock at 1.40R
            if pos.peak_r >= 1.40:
                ratchet = pos.entry_price + (pos.peak_r * 0.45 * pos.risk_ticks)
                pos.stop_price = max(pos.stop_price, ratchet)

        # 3. 15-Minute Macro State Evaluation
        if is_15m:
            t_eval = t_1m - pd.Timedelta(minutes=15)

            for sym, pos in active_positions.items():
                if sym in matrices_15m and t_eval in matrices_15m[sym].index:
                    m15 = matrices_15m[sym].loc[t_eval]
                    curr_r = (m15['close'] - pos.entry_price) / pos.risk_ticks
                    if m15['vwap_zscore'] >= -0.15 and curr_r >= 1.60:
                        pos.pending_exit = "DYNAMIC_Z_15M_CLOSE_EXIT"

            if t_eval in macro_breadth.index and macro_breadth.loc[t_eval] < -0.40:
                armed_symbols.clear()
                continue

            if len(active_positions) < 4 and b_time < pd.Timestamp("14:30:00").time():
                for sym in target_syms:
                    if sym in active_positions or (sym, b_date) in daily_gaps:
                        continue
                    if sym not in matrices_15m or t_eval not in matrices_15m[sym].index:
                        continue

                    m_df = matrices_15m[sym]
                    loc = m_df.index.get_loc(t_eval)
                    if loc < 30:
                        continue

                    c15 = m_df.iloc[loc]
                    p15 = m_df.iloc[loc - 1]

                    if c15['vwap_zscore'] < -2.5 and c15['rsi_14'] < 28.0:
                        armed_symbols[sym] = {
                            'swing_low': min(c15['low'], p15['low']),
                            'atr': c15['atr']
                        }

                    if sym in armed_symbols:
                        arm = armed_symbols[sym]
                        arm['swing_low'] = min(arm['swing_low'], c15['low'])

                        if c15['close'] > p15['high'] and c15['close'] > c15['open']:
                            if t_1m in sector_1m[sym].index:
                                entry_p = sector_1m[sym].loc[t_1m, 'open'] * (1.0 + slip_mult)
                                risk_dist = max(entry_p - arm['swing_low'], 1.2 * arm['atr'])
                                
                                shares = min(int(100000.0 / entry_p), int(1500.0 / max(risk_dist, 0.5)))
                                if shares >= 1:
                                    active_positions[sym] = Position(
                                        symbol=sym,
                                        entry_time=t_1m,
                                        entry_price=entry_p,
                                        stop_price=entry_p - risk_dist,
                                        risk_ticks=risk_dist,
                                        shares=shares,
                                        peak_r=0.0,
                                        r_target=2.50
                                    )
                                    del armed_symbols[sym]
                                    if len(active_positions) >= 4:
                                        break

    tdf = pd.DataFrame(closed_trades)
    print("\n" + "=" * 80)
    print("HURST MEAN-REVERTING FILTERED RUN RESULTS")
    print("=" * 80)
    if not tdf.empty:
        tot = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100.0
        gross = tdf['gross_pnl'].sum()
        fees = tdf['friction'].sum()
        net = tdf['net_pnl'].sum()
        
        print(f"Total Trades Closed       : {tot}")
        print(f"Net Win Rate              : {wr:.2f}%")
        print(f"Total Gross Alpha         : ₹{gross:+,.2f}")
        print(f"Total Statutory Fees      : ₹{fees:,.2f}")
        print(f"Total Net P&L (Realized)  : ₹{net:+,.2f}")
        print(f"Net Expected Value/Trade  : ₹{net / tot:+,.2f}")
        print("-" * 80)
        print("Performance by Symbol:")
        grp = tdf.groupby('symbol').agg(
            trades=('net_pnl', 'count'),
            wr=('net_pnl', lambda x: (x > 0).mean() * 100),
            gross=('gross_pnl', 'sum'),
            net=('net_pnl', 'sum')
        )
        print(grp.to_string())
    print("=" * 80)

if __name__ == '__main__':
    main()
