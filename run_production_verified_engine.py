import sys
import calendar
from pathlib import Path
from datetime import time as dtime
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

ENGINE_UNIVERSE = ["TCS", "LT", "TATAMOTORS", "HDFCBANK", "BAJFINANCE"]
SECTOR_MAP = {
    "TCS": "IT",
    "LT": "INFRA",
    "TATAMOTORS": "AUTO",
    "HDFCBANK": "BANK",
    "BAJFINANCE": "FIN"
}

BASE_SLOT_CAPITAL = 333333.33
BASE_R_TARGET = 2.25
BASE_Z_TARGET = 0.60
STOP_ATR_MULT = 1.00

def calculate_zerodha_friction(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)
    stt = 0.00025 * exit_val
    exchange = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp = 0.00003 * entry_val
    gst = 0.18 * (brokerage + exchange + sebi)
    return brokerage + stt + exchange + sebi + stamp + gst

def locate_symbol_csv(data_dir: Path, symbol: str) -> Path | None:
    sym_clean = symbol.replace("&", "").upper()
    for f in data_dir.glob("*.csv"):
        stem_upper = f.stem.replace("&", "").upper()
        tokens = stem_upper.split("_")
        if sym_clean in tokens or symbol.upper() in stem_upper:
            return f
    return None

def resample_15min(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    t_candidates = [c for c in df.columns if any(k in c for k in ['date', 'time', 'timestamp', 'dt'])]
    t_col = t_candidates[0] if t_candidates else df.columns[0]
    df['dt'] = pd.to_datetime(df[t_col]).dt.tz_localize(None)
    df = df.sort_values('dt').set_index('dt')
    
    ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in df.columns:
        ohlc['volume'] = 'sum'
    res = df.resample('15min').agg(ohlc).dropna().reset_index()
    if 'volume' not in res.columns:
        res['volume'] = 50000.0
    res['date_only'] = res['dt'].dt.date
    res['time_only'] = res['dt'].dt.time
    return res

def compute_features(df: pd.DataFrame) -> pd.DataFrame:
    df['ema_50'] = df['close'].ewm(span=50, adjust=False).mean()
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))

    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr'] = tr.rolling(14).mean()
    df['atr_base'] = df['atr'].rolling(40).mean().replace(0, 1e-5)
    df['vol_ratio'] = (df['atr'] / df['atr_base']).replace(0, 1.0)
    df['typ_price'] = (df['high'] + df['low'] + df['close']) / 3.0

    vwap_arr, std_arr = np.zeros(len(df)), np.zeros(len(df))
    for _, idxs in df.groupby('date_only').groups.items():
        w_sum, mean, M2 = 0.0, 0.0, 0.0
        for i in idxs:
            p = df.at[i, 'typ_price']
            w = max(1.0, df.at[i, 'volume'])
            w_old = w_sum
            w_sum += w
            delta_p = p - mean
            R = delta_p * w / w_sum
            mean += R
            M2 += w_old * delta_p * R
            vwap_arr[i] = mean
            std_arr[i] = np.sqrt(M2 / w_sum) if w_sum > 0 and M2 > 0 else 1.0

    df['vwap'] = vwap_arr
    df['vwap_std'] = np.where(std_arr < 1e-4, 1.0, std_arr)
    df['vwap_z'] = (df['close'] - df['vwap']) / df['vwap_std']
    return df.dropna().set_index('dt')

def compute_calibrated_targets(vol_ratio: float, bar_time: dtime) -> tuple[float, float]:
    curr_m = bar_time.hour * 60 + bar_time.minute
    decay_start = 12 * 60 + 30
    cutoff = 15 * 60 + 15
    if curr_m < decay_start:
        decay = 1.0
    else:
        rem = max(0, cutoff - curr_m)
        decay = float(np.clip(rem / (cutoff - decay_start), 0.30, 1.0))
    r_target = max(0.60, BASE_R_TARGET * np.clip(vol_ratio, 0.9, 1.4) * decay)
    z_target = -0.05 if decay < 0.50 else BASE_Z_TARGET * decay
    return r_target, z_target

def replay_window(data_dir: Path, start_date: str, end_date: str) -> pd.DataFrame:
    frames = {}
    for sym in ENGINE_UNIVERSE:
        csv_p = locate_symbol_csv(data_dir, sym)
        if not csv_p: continue
        raw = pd.read_csv(csv_p)
        t_col = [c for c in raw.columns if any(k in c.lower() for k in ['date', 'time', 'timestamp', 'dt'])][0]
        raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
        m = raw[(raw['dt'] >= start_date) & (raw['dt'] <= end_date)].copy()
        if len(m) > 100:
            frames[sym] = compute_features(resample_15min(m))

    all_times = sorted(list(set().union(*[set(df.index) for df in frames.values()])))
    active_pos, armed, closed = {}, {}, []

    for t in all_times:
        bar_time = t.time()
        
        # 1. Active Position Management
        to_close = []
        for sym, pos in active_pos.items():
            if sym not in frames or t not in frames[sym].index: continue
            bar = frames[sym].loc[t]

            if bar_time >= dtime(15, 15):
                to_close.append((sym, bar['close'], "1515_SQUAREOFF"))
                continue
            if bar['low'] <= pos['stop_price']:
                fill = min(bar['open'], pos['stop_price']) - (0.05 * pos['risk_ticks'])
                to_close.append((sym, fill, "STOP_TRIGGERED"))
                continue

            curr_peak_r = (bar['high'] - pos['entry_price']) / pos['risk_ticks']
            curr_r = (bar['close'] - pos['entry_price']) / pos['risk_ticks']
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)

            target_r, target_z = compute_calibrated_targets(bar['vol_ratio'], bar_time)
            
            if curr_peak_r >= target_r:
                to_close.append((sym, pos['entry_price'] + (target_r * pos['risk_ticks']), "DYNAMIC_R_TARGET"))
                continue
            if bar['vwap_z'] >= target_z and curr_r >= 0.35:
                to_close.append((sym, bar['close'], "DYNAMIC_Z_TARGET"))
                continue
            if pos['peak_r'] >= 1.00:
                pos['stop_price'] = max(pos['stop_price'], pos['entry_price'] + (pos['peak_r'] * 0.50 * pos['risk_ticks']))

        for sym, fill_p, reason in to_close:
            p = active_pos.pop(sym)
            gross = (fill_p - p['entry_price']) * p['shares']
            fric = calculate_zerodha_friction(p['entry_price'] * p['shares'], fill_p * p['shares'])
            closed.append({
                'symbol': sym,
                'entry_time': p['entry_time'],
                'exit_time': t,
                'gross_pnl': gross,
                'friction': fric,
                'net_pnl': gross - fric,
                'exit_reason': reason
            })

        # 2. Market Breadth Interlock: If composite market Z < -1.1, market is in liquidation
        available_slots = 3 - len(active_pos)
        if available_slots <= 0 or bar_time >= dtime(13, 30):
            continue

        curr_market_zs = [frames[s].loc[t, 'vwap_z'] for s in frames if t in frames[s].index]
        if curr_market_zs and np.mean(curr_market_zs) < -1.10:
            armed.clear()
            continue

        # 3. Entry Dispatcher
        active_sec = {SECTOR_MAP.get(s) for s in active_pos.keys()}
        for sym in frames.keys():
            if sym in active_pos or SECTOR_MAP.get(sym) in active_sec: continue
            df = frames[sym]
            if t not in df.index: continue
            loc = df.index.get_loc(t)
            if loc < 2 or loc + 1 >= len(df): continue

            curr_b, prev_b = df.iloc[loc], df.iloc[loc - 1]
            
            # Arming state: oversold check
            if curr_b['vwap_z'] < -2.20 and curr_b['rsi_14'] < 32:
                armed[sym] = min(curr_b['low'], prev_b['low'])

            if sym in armed:
                armed[sym] = min(armed[sym], curr_b['low'])
                # Confirmation candle: green and breaking previous high
                if curr_b['close'] > prev_b['high'] and curr_b['close'] > curr_b['open']:
                    # 50-EMA Trend Guard: stock is not collapsing > 1.5% below 50 EMA
                    if (curr_b['close'] - curr_b['ema_50']) / curr_b['ema_50'] > -0.015:
                        nxt = df.iloc[loc + 1]['open']
                        risk_t = max(nxt - armed[sym], STOP_ATR_MULT * curr_b['atr'])
                        shares = int(BASE_SLOT_CAPITAL / nxt)
                        if shares >= 1:
                            active_pos[sym] = {
                                'entry_time': df.iloc[loc + 1].name,
                                'entry_price': nxt,
                                'stop_price': nxt - risk_t,
                                'risk_ticks': risk_t,
                                'shares': shares,
                                'peak_r': 0.0
                            }
                            del armed[sym]
                            active_sec.add(SECTOR_MAP.get(sym))
                            available_slots -= 1
                            if available_slots <= 0: break

    return pd.DataFrame(closed)

def main():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    if not data_dir.exists():
        print(f"[ERROR] Missing data path: {data_dir}", file=sys.stderr)
        return

    month_windows = [
        (m, f"2023-{m:02d}-01", f"2023-{m:02d}-{calendar.monthrange(2023, m)[1]:02d}")
        for m in range(7, 13)
    ]
    all_ledgers, summary_rows = [], []

    print("=" * 82)
    print("STARTING PRODUCTION ENGINE (CALIBRATED PARAMS + BREADTH + EMA INTERLOCK)")
    print("=" * 82)

    for m_num, start_d, end_d in month_windows:
        m_label = f"2023-{m_num:02d} ({calendar.month_abbr[m_num]})"
        df = replay_window(data_dir, start_d, end_d)
        
        if df.empty:
            summary_rows.append({
                'Month': m_label, 'Trades': 0, 'Win_Rate': 0.0,
                'Gross_PNL': 0.0, 'Fees': 0.0, 'Net_PNL': 0.0
            })
            continue

        df['month_period'] = m_label
        all_ledgers.append(df)

        wr = float((df['net_pnl'] > 0).mean() * 100)
        gross = float(df['gross_pnl'].sum())
        fees = float(df['friction'].sum())
        net = float(df['net_pnl'].sum())

        summary_rows.append({
            'Month': m_label, 'Trades': len(df), 'Win_Rate': wr,
            'Gross_PNL': gross, 'Fees': fees, 'Net_PNL': net
        })
        print(f"[{m_label}] Trades: {len(df):<2} | WR: {wr:5.1f}% | Gross: ₹{gross:+10,.2f} | Net: ₹{net:+10,.2f}")

    sdf = pd.DataFrame(summary_rows)
    print("\n" + "=" * 82)
    print("PRODUCTION VERIFIED WALK-FORWARD SUMMARY (2023 JUL - DEC)")
    print("=" * 82)
    print(f"{'Month':<18} | {'Trades':<6} | {'Win %':<6} | {'Gross P&L':<12} | {'Fees':<10} | {'Net P&L':<12}")
    print("-" * 82)
    for _, r in sdf.iterrows():
        print(f"{r['Month']:<18} | {int(r['Trades']):<6} | {float(r['Win_Rate']):5.1f}% | ₹{float(r['Gross_PNL']):+10,.2f} | ₹{float(r['Fees']):8,.2f} | ₹{float(r['Net_PNL']):+10,.2f}")
    print("-" * 82)
    tot_trades = int(sdf['Trades'].sum())
    tot_gross = float(sdf['Gross_PNL'].sum())
    tot_fees = float(sdf['Fees'].sum())
    tot_net = float(sdf['Net_PNL'].sum())
    print(f"{'TOTAL':<18} | {tot_trades:<6} | {'---':<6} | ₹{tot_gross:+10,.2f} | ₹{tot_fees:8,.2f} | ₹{tot_net:+10,.2f}")
    print("=" * 82)

    if all_ledgers:
        master = pd.concat(all_ledgers, ignore_index=True)
        print("\nAsset Breakdown Across Entire Window:")
        for sym, g in master.groupby('symbol'):
            s_wr = (g['net_pnl'] > 0).mean() * 100
            print(f"  • {sym:<12}: {len(g):<2} trades | WR: {s_wr:5.1f}% | Net P&L: ₹{g['net_pnl'].sum():+10,.2f}")
        master.to_parquet("production_verified_2023_ledger.parquet")
        print("\n[+] Detailed ledger saved to 'production_verified_2023_ledger.parquet'")

if __name__ == '__main__':
    main()
