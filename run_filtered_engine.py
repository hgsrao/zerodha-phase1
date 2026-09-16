import sys
from pathlib import Path
from datetime import time as dtime
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

# Focused Universe: High-fidelity mean reverters
ENGINE_A_UNIVERSE = ["TCS", "LT", "TATAMOTORS", "HDFCBANK"]

SECTOR_MAP = {
    "TCS": "IT", "LT": "INFRA",
    "TATAMOTORS": "AUTO", "HDFCBANK": "BANK"
}

BASE_SLOT_CAPITAL = 333333.3  # ₹3.33L per slot

def friction_engine_a(entry_val: float, exit_val: float) -> float:
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
    # 50-EMA Trend Filter (Inhibits knife catching in fundamental downgrades)
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

def time_decay_targets(vol_ratio: float, bar_time: dtime) -> tuple[float, float]:
    curr_m = bar_time.hour * 60 + bar_time.minute
    decay_start = 12 * 60 + 30
    cutoff = 15 * 60 + 15
    if curr_m < decay_start:
        decay = 1.0
    else:
        rem = max(0, cutoff - curr_m)
        decay = float(np.clip(rem / (cutoff - decay_start), 0.30, 1.0))
    r_target = max(0.50, 1.30 * np.clip(vol_ratio, 0.9, 1.4) * decay)
    z_target = -0.05 if decay < 0.50 else 0.40 * decay
    return r_target, z_target

def run():
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    frames = {}
    for sym in ENGINE_A_UNIVERSE:
        csv_p = locate_symbol_csv(data_dir, sym)
        if not csv_p: continue
        raw = pd.read_csv(csv_p)
        t_col = [c for c in raw.columns if any(k in c.lower() for k in ['date', 'time', 'timestamp', 'dt'])][0]
        raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
        m = raw[(raw['dt'] >= '2023-07-03') & (raw['dt'] <= '2023-08-31')].copy()
        if len(m) > 100:
            frames[sym] = compute_features(resample_15min(m))

    all_times = sorted(list(set().union(*[set(df.index) for df in frames.values()])))
    active_pos = {}
    armed = {}
    closed = []

    for t in all_times:
        bar_time = t.time()
        
        # 1. Exit Management
        to_close = []
        for sym, pos in active_pos.items():
            if sym not in frames or t not in frames[sym].index: continue
            bar = frames[sym].loc[t]

            if bar_time >= dtime(15, 15):
                to_close.append((sym, bar['close'], "A_1515_CLOSE"))
                continue
            if bar['low'] <= pos['stop_price']:
                fill = min(bar['open'], pos['stop_price']) - (0.05 * pos['risk_ticks'])
                to_close.append((sym, fill, "A_STOP"))
                continue

            curr_peak_r = (bar['high'] - pos['entry_price']) / pos['risk_ticks']
            curr_r = (bar['close'] - pos['entry_price']) / pos['risk_ticks']
            pos['peak_r'] = max(pos['peak_r'], curr_peak_r)

            target_r, target_z = time_decay_targets(bar['vol_ratio'], bar_time)
            if curr_peak_r >= target_r:
                to_close.append((sym, pos['entry_price'] + (target_r * pos['risk_ticks']), "A_R_TARGET"))
                continue
            if bar['vwap_z'] >= target_z and curr_r >= 0.35:
                to_close.append((sym, bar['close'], "A_Z_TARGET"))
                continue
            if pos['peak_r'] >= 1.0:
                pos['stop_price'] = max(pos['stop_price'], pos['entry_price'] + (pos['peak_r'] * 0.5 * pos['risk_ticks']))

        for sym, fill_p, reason in to_close:
            p = active_pos.pop(sym)
            gross = (fill_p - p['entry_price']) * p['shares']
            fric = friction_engine_a(p['entry_price'] * p['shares'], fill_p * p['shares'])
            closed.append({
                'symbol': sym, 'gross_pnl': gross, 'friction': fric,
                'net_pnl': gross - fric, 'exit_reason': reason
            })

        # 2. Entry Management with EMA-50 Inhibit
        if len(active_pos) < 3 and bar_time < dtime(13, 30):
            active_sec = {SECTOR_MAP.get(s) for s in active_pos.keys()}
            for sym in frames.keys():
                if sym in active_pos or SECTOR_MAP.get(sym) in active_sec: continue
                df = frames[sym]
                if t not in df.index: continue
                loc = df.index.get_loc(t)
                if loc < 2 or loc + 1 >= len(df): continue

                curr_b, prev_b = df.iloc[loc], df.iloc[loc - 1]
                
                # Arming condition
                if curr_b['vwap_z'] < -2.20 and curr_b['rsi_14'] < 32:
                    armed[sym] = min(curr_b['low'], prev_b['low'])

                if sym in armed:
                    armed[sym] = min(armed[sym], curr_b['low'])
                    
                    # Synchrocheck confirmation + EMA 50 trend sanity check
                    if curr_b['close'] > prev_b['high'] and curr_b['close'] > curr_b['open']:
                        # Avoid buying when price is collapsed > 1.5% below 50 EMA
                        if (curr_b['close'] - curr_b['ema_50']) / curr_b['ema_50'] > -0.015:
                            nxt = df.iloc[loc + 1]['open']
                            risk_t = max(nxt - armed[sym], 0.80 * curr_b['atr'])
                            shares = int(BASE_SLOT_CAPITAL / nxt)
                            if shares >= 1:
                                active_pos[sym] = {
                                    'entry_price': nxt,
                                    'stop_price': nxt - risk_t,
                                    'risk_ticks': risk_t,
                                    'shares': shares,
                                    'peak_r': 0.0
                                }
                                del armed[sym]
                                active_sec.add(SECTOR_MAP.get(sym))
                                if len(active_pos) >= 3: break

    print("\n" + "=" * 75)
    print("TREND-FILTERED ALPHA ENGINE (JULY - AUGUST 2023)")
    print("=" * 75)
    tdf = pd.DataFrame(closed)
    if tdf.empty:
        print("No trades generated.")
        return
    
    wr = (tdf['net_pnl'] > 0).mean() * 100
    gross = tdf['gross_pnl'].sum()
    fric = tdf['friction'].sum()
    net = tdf['net_pnl'].sum()

    print(f"Total Completed Trades    : {len(tdf)}")
    print(f"Overall Win Rate          : {wr:.2f}%")
    print(f"Total Gross P&L           : ₹{gross:+,.2f}")
    print(f"Total Statutory Fees      : ₹{fric:,.2f}")
    print(f"TOTAL NET PORTFOLIO PROFIT: ₹{net:+,.2f}")
    print("-" * 75)
    print("Performance by Symbol:")
    for sym, g in tdf.groupby('symbol'):
        s_wr = (g['net_pnl'] > 0).mean() * 100
        print(f"  • {sym:<12}: {len(g):<2} trades | WR: {s_wr:5.1f}% | Net: ₹{g['net_pnl'].sum():+9.2f}")
    print("=" * 75)

if __name__ == '__main__':
    run()
