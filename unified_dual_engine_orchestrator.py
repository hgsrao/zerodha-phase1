import sys
from pathlib import Path
from datetime import time as dtime
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)

ENGINE_A_UNIVERSE = ["TCS", "INFY", "HDFCBANK", "TATAMOTORS", "BAJFINANCE"]
ENGINE_B_UNIVERSE = ["M&M", "TATASTEEL", "BEL", "INDIGO", "HINDALCO"]

SECTOR_MAP = {
    "TCS": "IT", "INFY": "IT",
    "HDFCBANK": "BANK",
    "TATAMOTORS": "AUTO", "M&M": "AUTO",
    "BAJFINANCE": "FIN",
    "TATASTEEL": "METALS", "HINDALCO": "METALS",
    "BEL": "DEFENSE_CAPGOODS",
    "INDIGO": "AVIATION"
}

PORTFOLIO_CAPITAL = 1000000.0
ENGINE_A_CAPITAL  = 400000.0   # 40% Intraday MIS
ENGINE_B_CAPITAL  = 600000.0   # 60% CNC Swings

ENGINE_A_MAX_SLOTS = 2         # ₹2,00,000 / slot
ENGINE_B_MAX_SLOTS = 3         # ₹2,00,000 / slot

def friction_engine_a(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = min(40.0, 0.0003 * turnover)
    stt = 0.00025 * exit_val
    exchange = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp = 0.00003 * entry_val
    gst = 0.18 * (brokerage + exchange + sebi)
    return brokerage + stt + exchange + sebi + stamp + gst

def friction_engine_b(entry_val: float, exit_val: float) -> float:
    turnover = entry_val + exit_val
    brokerage = 0.0
    stt = 0.001 * turnover
    exchange = 0.0000325 * turnover
    sebi = 0.000001 * turnover
    stamp = 0.00015 * entry_val
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

def resample_ohlc(df: pd.DataFrame, freq: str = '15min') -> pd.DataFrame:
    df.columns = [c.lower() for c in df.columns]
    t_candidates = [c for c in df.columns if any(k in c for k in ['date', 'time', 'timestamp', 'dt'])]
    t_col = t_candidates[0] if t_candidates else df.columns[0]
    df['dt'] = pd.to_datetime(df[t_col]).dt.tz_localize(None)
    df = df.sort_values('dt').set_index('dt')
    
    ohlc = {'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last'}
    if 'volume' in df.columns:
        ohlc['volume'] = 'sum'
    res = df.resample(freq).agg(ohlc).dropna().reset_index()
    if 'volume' not in res.columns:
        res['volume'] = 50000.0
    res['date_only'] = res['dt'].dt.date
    res['time_only'] = res['dt'].dt.time
    return res

def compute_engine_a_features(df: pd.DataFrame) -> pd.DataFrame:
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

def compute_engine_b_features(df: pd.DataFrame) -> pd.DataFrame:
    df['ema_20'] = df['close'].ewm(span=20, adjust=False).mean()
    tr = np.maximum(
        df['high'] - df['low'],
        np.maximum(abs(df['high'] - df['close'].shift(1)), abs(df['low'] - df['close'].shift(1)))
    )
    df['atr_daily'] = tr.rolling(14).mean()
    df['vol_sma20'] = df['volume'].rolling(20).mean()
    
    delta = df['close'].diff()
    gain = delta.where(delta > 0, 0.0).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-5)
    df['rsi_14'] = 100 - (100 / (1 + rs))
    df['ema_distance_atr'] = (df['ema_20'] - df['close']) / df['atr_daily'].replace(0, 1e-5)
    return df.dropna().set_index('dt')

class UnifiedDualEngineOrchestrator:
    def __init__(self, data_root: Path):
        self.data_root = data_root
        self.slot_cap_a = ENGINE_A_CAPITAL / ENGINE_A_MAX_SLOTS
        self.slot_cap_b = ENGINE_B_CAPITAL / ENGINE_B_MAX_SLOTS
        self.active_a = {}
        self.active_b = {}
        self.armed_a = {}
        self.closed_trades = []

    def get_active_sectors(self) -> set:
        sec_a = {SECTOR_MAP.get(s, "UNKNOWN") for s in self.active_a.keys()}
        sec_b = {SECTOR_MAP.get(s, "UNKNOWN") for s in self.active_b.keys()}
        return sec_a.union(sec_b)

    def time_decay_targets(self, vol_ratio: float, bar_time: dtime) -> tuple[float, float]:
        curr_m = bar_time.hour * 60 + bar_time.minute
        decay_start = 12 * 60 + 30
        cutoff = 15 * 60 + 15
        if curr_m < decay_start:
            decay = 1.0
        else:
            rem = max(0, cutoff - curr_m)
            decay = float(np.clip(rem / (cutoff - decay_start), 0.30, 1.0))
        r_target = max(0.45, 1.30 * np.clip(vol_ratio, 0.9, 1.4) * decay)
        z_target = -0.05 if decay < 0.50 else 0.40 * decay
        return r_target, z_target

    def run_replay(self, start_date: str, end_date: str):
        print("=" * 85)
        print(f"STARTING UNIFIED DUAL-ENGINE ORCHESTRATION [{start_date} -> {end_date}]")
        print(f"Allocations: Engine A (15m MIS): ₹{ENGINE_A_CAPITAL:,.0f} | Engine B (CNC): ₹{ENGINE_B_CAPITAL:,.0f}")
        print("=" * 85)

        frames_15m = {}
        frames_daily = {}

        for sym in ENGINE_A_UNIVERSE:
            csv_path = locate_symbol_csv(self.data_root, sym)
            if not csv_path: continue
            raw = pd.read_csv(csv_path)
            t_candidates = [c for c in raw.columns if any(k in c.lower() for k in ['date', 'time', 'timestamp', 'dt'])]
            t_col = t_candidates[0] if t_candidates else raw.columns[0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= start_date) & (raw['dt'] <= end_date)].copy()
            if len(m) > 100:
                frames_15m[sym] = compute_engine_a_features(resample_ohlc(m, '15min'))

        for sym in ENGINE_B_UNIVERSE:
            csv_path = locate_symbol_csv(self.data_root, sym)
            if not csv_path: continue
            raw = pd.read_csv(csv_path)
            t_candidates = [c for c in raw.columns if any(k in c.lower() for k in ['date', 'time', 'timestamp', 'dt'])]
            t_col = t_candidates[0] if t_candidates else raw.columns[0]
            raw['dt'] = pd.to_datetime(raw[t_col]).dt.tz_localize(None)
            m = raw[(raw['dt'] >= start_date) & (raw['dt'] <= end_date)].copy()
            if len(m) > 100:
                frames_daily[sym] = compute_engine_b_features(resample_ohlc(m, '1D'))

        if not frames_15m and not frames_daily:
            print("[ERROR] No data loaded for either engine. Exiting.")
            return

        # Safe union without unbound set.union() crash
        all_sets = [set(df.index) for df in frames_15m.values()]
        all_15m_times = sorted(list(set().union(*all_sets))) if all_sets else []

        print(f"[+] Loaded {len(frames_15m)} Intraday feeds ({list(frames_15m.keys())})")
        print(f"[+] Loaded {len(frames_daily)} Swing feeds ({list(frames_daily.keys())})")
        print(f"[+] Streaming across {len(all_15m_times):,} discrete 15-minute bars...\n")

        for t in all_15m_times:
            bar_date = t.date()
            bar_time = t.time()

            # --- A. MANAGE ACTIVE INTRADAY (ENGINE A) ---
            a_exits = []
            for sym, pos in self.active_a.items():
                if sym not in frames_15m or t not in frames_15m[sym].index: continue
                bar = frames_15m[sym].loc[t]

                if bar_time >= dtime(15, 15):
                    a_exits.append((sym, bar['close'], "A_1515_HARD_CLOSE"))
                    continue
                if bar['low'] <= pos['stop_price']:
                    fill = min(bar['open'], pos['stop_price']) - (0.05 * pos['risk_ticks'])
                    a_exits.append((sym, fill, "A_STOP_TRIGGERED"))
                    continue
                curr_peak_r = (bar['high'] - pos['entry_price']) / pos['risk_ticks']
                curr_r = (bar['close'] - pos['entry_price']) / pos['risk_ticks']
                pos['peak_r'] = max(pos['peak_r'], curr_peak_r)

                target_r, target_z = self.time_decay_targets(bar['vol_ratio'], bar_time)
                if curr_peak_r >= target_r:
                    fill = pos['entry_price'] + (target_r * pos['risk_ticks'])
                    a_exits.append((sym, fill, "A_DYNAMIC_R_TARGET"))
                    continue
                if bar['vwap_z'] >= target_z and curr_r >= 0.35:
                    a_exits.append((sym, bar['close'], "A_DYNAMIC_Z_TARGET"))
                    continue
                if pos['peak_r'] >= 1.0:
                    pos['stop_price'] = max(pos['stop_price'], pos['entry_price'] + (pos['peak_r'] * 0.5 * pos['risk_ticks']))

            for sym, fill_p, reason in a_exits:
                p = self.active_a.pop(sym)
                gross = (fill_p - p['entry_price']) * p['shares']
                fric = friction_engine_a(p['entry_price'] * p['shares'], fill_p * p['shares'])
                self.closed_trades.append({
                    'engine': 'ENGINE_A_INTRADAY',
                    'symbol': sym, 'entry_time': p['entry_time'], 'exit_time': t,
                    'gross_pnl': gross, 'friction': fric, 'net_pnl': gross - fric,
                    'exit_reason': reason
                })

            # --- B. MANAGE ACTIVE SWINGS (ENGINE B) ---
            if bar_time == dtime(15, 15):
                b_exits = []
                for sym, pos in self.active_b.items():
                    if sym not in frames_daily: continue
                    d_matches = frames_daily[sym][frames_daily[sym].index.date == bar_date]
                    if d_matches.empty: continue
                    d_bar = d_matches.iloc[0]

                    pos['holding_days'] += 1
                    if d_bar['low'] <= pos['stop_price']:
                        fill = min(d_bar['open'], pos['stop_price'])
                        b_exits.append((sym, fill, "B_DAILY_STOP_TRIGGERED"))
                        continue
                    if d_bar['high'] >= d_bar['ema_20']:
                        fill = d_bar['ema_20']
                        b_exits.append((sym, fill, "B_20EMA_TARGET_HIT"))
                        continue
                    if pos['holding_days'] >= 7:
                        b_exits.append((sym, d_bar['close'], "B_MAX_HORIZON_CLOSE"))
                        continue

                for sym, fill_p, reason in b_exits:
                    p = self.active_b.pop(sym)
                    gross = (fill_p - p['entry_price']) * p['shares']
                    fric = friction_engine_b(p['entry_price'] * p['shares'], fill_p * p['shares'])
                    self.closed_trades.append({
                        'engine': 'ENGINE_B_SWING',
                        'symbol': sym, 'entry_time': p['entry_time'], 'exit_time': t,
                        'gross_pnl': gross, 'friction': fric, 'net_pnl': gross - fric,
                        'exit_reason': reason
                    })

            # --- C. SCAN ENTRIES WITH SECTOR INTERLOCK ---
            active_sectors = self.get_active_sectors()

            # Engine A Entry
            if len(self.active_a) < ENGINE_A_MAX_SLOTS and bar_time < dtime(13, 30):
                for sym in frames_15m.keys():
                    if sym in self.active_a or SECTOR_MAP.get(sym) in active_sectors: continue
                    df_sym = frames_15m[sym]
                    if t not in df_sym.index: continue
                    loc = df_sym.index.get_loc(t)
                    if loc < 2 or loc + 1 >= len(df_sym): continue

                    curr_b = df_sym.iloc[loc]
                    prev_b = df_sym.iloc[loc - 1]

                    if curr_b['vwap_z'] < -2.20 and curr_b['rsi_14'] < 32:
                        self.armed_a[sym] = min(curr_b['low'], prev_b['low'])

                    if sym in self.armed_a:
                        self.armed_a[sym] = min(self.armed_a[sym], curr_b['low'])
                        if curr_b['close'] > prev_b['high'] and curr_b['close'] > curr_b['open']:
                            next_open = df_sym.iloc[loc + 1]['open']
                            risk_t = max(next_open - self.armed_a[sym], 0.80 * curr_b['atr'])
                            shares = int(self.slot_cap_a / next_open)
                            if shares >= 1:
                                self.active_a[sym] = {
                                    'entry_time': df_sym.iloc[loc + 1].name,
                                    'entry_price': next_open,
                                    'stop_price': next_open - risk_t,
                                    'risk_ticks': risk_t,
                                    'shares': shares,
                                    'peak_r': 0.0
                                }
                                del self.armed_a[sym]
                                active_sectors.add(SECTOR_MAP.get(sym))
                                if len(self.active_a) >= ENGINE_A_MAX_SLOTS: break

            # Engine B Entry (15:00 IST for next-day entry)
            if len(self.active_b) < ENGINE_B_MAX_SLOTS and bar_time == dtime(15, 0):
                for sym in frames_daily.keys():
                    if sym in self.active_b or SECTOR_MAP.get(sym) in active_sectors: continue
                    df_d = frames_daily[sym]
                    d_matches = df_d[df_d.index.date == bar_date]
                    if d_matches.empty: continue
                    d_loc = df_d.index.get_loc(d_matches.index[0])
                    if d_loc < 1 or d_loc + 1 >= len(df_d): continue

                    d_curr = df_d.iloc[d_loc]
                    d_prev = df_d.iloc[d_loc - 1]

                    if d_curr['ema_distance_atr'] >= 2.50 and d_curr['rsi_14'] < 36:
                        if d_curr['close'] > d_prev['high'] and d_curr['volume'] > d_curr['vol_sma20']:
                            next_d_open = df_d.iloc[d_loc + 1]['open']
                            stop_dist = 1.25 * d_curr['atr_daily']
                            shares = int(self.slot_cap_b / next_d_open)
                            if shares >= 1:
                                self.active_b[sym] = {
                                    'entry_time': df_d.iloc[d_loc + 1].name,
                                    'entry_price': next_d_open,
                                    'stop_price': next_d_open - stop_dist,
                                    'shares': shares,
                                    'holding_days': 0
                                }
                                active_sectors.add(SECTOR_MAP.get(sym))
                                if len(self.active_b) >= ENGINE_B_MAX_SLOTS: break

        self.report_performance()

    def report_performance(self):
        print("\n" + "=" * 85)
        print("UNIFIED DUAL-ENGINE PORTFOLIO LEDGER RESULTS")
        print("=" * 85)
        if not self.closed_trades:
            print("[-] No trades triggered in the simulated window.")
            return

        tdf = pd.DataFrame(self.closed_trades)
        total_gross = tdf['gross_pnl'].sum()
        total_fric = tdf['friction'].sum()
        total_net = tdf['net_pnl'].sum()
        total_wr = (tdf['net_pnl'] > 0).mean() * 100

        print(f"Total Portfolio Completed Trades : {len(tdf)}")
        print(f"Overall Net Win Rate             : {total_wr:.2f}%")
        print(f"Total Gross Portfolio Edge       : ₹{total_gross:+,.2f}")
        print(f"Total Statutory Fees & STT       : ₹{total_fric:,.2f}")
        print(f"TOTAL NET PORTFOLIO PROFIT       : ₹{total_net:+,.2f}")
        print("-" * 85)

        print("Breakdown By Operating Engine:")
        for eng, g in tdf.groupby('engine'):
            wr = (g['net_pnl'] > 0).mean() * 100
            print(f"  • {eng:<20}: {len(g):<2} trades | WR: {wr:5.1f}% | Gross: ₹{g['gross_pnl'].sum():+9.2f} | Fees: ₹{g['friction'].sum():7.2f} | Net: ₹{g['net_pnl'].sum():+9.2f}")

        print("-" * 85)
        print("Breakdown By Sector Allocation:")
        tdf['sector'] = tdf['symbol'].map(SECTOR_MAP)
        for sec, g in tdf.groupby('sector'):
            print(f"  • {sec:<18}: {len(g):<2} trades | Net: ₹{g['net_pnl'].sum():+9.2f}")

        tdf.to_parquet('unified_dual_engine_ledger.parquet')
        print("\n[+] Detailed portfolio ledger persisted to 'unified_dual_engine_ledger.parquet'")
        print("=" * 85)

if __name__ == '__main__':
    data_dir = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')
    orchestrator = UnifiedDualEngineOrchestrator(data_dir)
    orchestrator.run_replay('2023-07-03', '2023-08-31')
