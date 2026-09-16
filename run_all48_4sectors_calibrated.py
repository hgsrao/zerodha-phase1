from pathlib import Path
from dataclasses import dataclass
import numpy as np
import pandas as pd
import warnings
warnings.filterwarnings('ignore')

from alpha_engine_core import ZerodhaFeeCalculator

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

SECTOR_CLUSTERS = {
    "1_GTG1_FINANCIALS": {
        "name": "GTG-1: Banking & Financial Services (R=4% Droop)",
        "symbols": [
            "AXISBANK", "BAJAJFINSV", "BAJFINANCE", "HDFCBANK", "HDFCLIFE",
            "ICICIBANK", "JIOFIN", "KOTAKBANK", "SBILIFE", "SBIN", "SHRIRAMFIN"
        ],
        "target_r": 2.00,
        "harvest_floor_r": 1.40,
        "trailing_lock_r": 1.20,
        "vr_max": 1.02,
        "breadth_gate_z": -0.40,
        "max_concurrent": 4
    },
    "2_GTG2_AUTO_MOMENTUM": {
        "name": "GTG-2: Autos, Mobility & Retail Dynamic (R=4% Droop)",
        "symbols": [
            "BAJAJ-AUTO", "EICHERMOT", "M&M", "MARUTI", "ADANIENT",
            "ADANIPORTS", "BHARTIARTL", "INDIGO", "RELIANCE", "TITAN", "TRENT"
        ],
        "target_r": 2.00,
        "harvest_floor_r": 1.50,
        "trailing_lock_r": 1.30,
        "vr_max": 1.05,
        "breadth_gate_z": -0.45,
        "max_concurrent": 4
    },
    "3_CSTG_MATERIALS_CAPEX": {
        "name": "CSTG: Metals, Materials, Capex & Power (R=5.5% Droop)",
        "symbols": [
            "COALINDIA", "GRASIM", "HINDALCO", "JSWSTEEL", "LT",
            "NTPC", "ONGC", "POWERGRID", "TATASTEEL", "ULTRACEMCO", "BEL", "ASIANPAINT"
        ],
        "target_r": 2.00,
        "harvest_floor_r": 1.40,
        "trailing_lock_r": 1.20,
        "vr_max": 1.02,
        "breadth_gate_z": -0.40,
        "max_concurrent": 3
    },
    "4_BPSTG_TECH_PHARMA": {
        "name": "BPSTG: IT, Pharma & FMCG Process (R=7.5% Droop)",
        "symbols": [
            "HCLTECH", "INFY", "TCS", "TECHM", "WIPRO",
            "APOLLOHOSP", "CIPLA", "DRREDDY", "MAXHEALTH", "SUNPHARMA",
            "HINDUNILVR", "ITC", "TATACONSUM", "ETERNAL"
        ],
        "target_r": 2.00,
        "harvest_floor_r": 1.50,
        "trailing_lock_r": 1.30,
        "vr_max": 0.98,
        "breadth_gate_z": -0.30,
        "max_concurrent": 3
    }
}

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

def compute_vr(log_returns: np.ndarray, k: int = 5) -> float:
    if len(log_returns) < k * 5:
        return 1.0
    var_1 = np.var(log_returns, ddof=1)
    k_returns = pd.Series(log_returns).rolling(k).sum().dropna().values
    var_k = np.var(k_returns, ddof=1)
    return float(var_k / (k * var_1)) if var_1 > 0 else 1.0

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

def run_single_sector_engine(sector_id: str, cfg: dict, raw_data: dict, slip_bps: float = 2.0) -> pd.DataFrame:
    print(f"\n[{sector_id}] Initializing {cfg['name']}...")
    slip_mult = slip_bps / 10000.0
    
    valid_symbols = [s for s in cfg['symbols'] if s in raw_data]
    print(f"  • Verified Data Files: {len(valid_symbols)} / {len(cfg['symbols'])}")
    
    matrices_15m = {}
    z_list = []
    daily_gaps = set()
    
    for s in valid_symbols:
        df_1m = raw_data[s]
        feat_15 = resample_15m(df_1m)
        matrices_15m[s] = feat_15
        z_list.append(feat_15['vwap_zscore'].rename(s))
        
        daily = df_1m.resample('D').agg({'open': 'first', 'close': 'last'}).dropna()
        daily['prev_close'] = daily['close'].shift(1)
        daily['gap_pct'] = (daily['open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        for d in daily[daily['gap_pct'] <= -1.25].index.date:
            daily_gaps.add((s, d))

    sector_breadth = pd.concat(z_list, axis=1).mean(axis=1).rename('sector_z')
    
    sector_1m = {s: raw_data[s] for s in valid_symbols}
    master_timeline = sorted(list(set.union(*[set(df.index) for df in sector_1m.values()])))
    
    active_positions: dict[str, Position] = {}
    armed_symbols: dict[str, dict] = {}
    closed_trades = []

    for t_1m in master_timeline:
        b_time = t_1m.time()
        b_date = t_1m.date()
        is_15m = (t_1m.minute % 15 == 0)

        # 1. Execute Pending Exits at Next 1m Open
        to_del = []
        for sym, pos in active_positions.items():
            if pos.pending_exit and t_1m in sector_1m[sym].index:
                open_p = sector_1m[sym].loc[t_1m, 'open']
                fill_p = open_p * (1.0 - slip_mult)
                gross = (fill_p - pos.entry_price) * pos.shares
                notional_in = pos.entry_price * pos.shares
                notional_out = fill_p * pos.shares
                fees = ZerodhaFeeCalculator.calculate_round_trip(notional_in, notional_out)
                closed_trades.append({
                    'sector': cfg['name'],
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

            if b1['low'] <= pos.stop_price:
                fill_p = min(b1['open'], pos.stop_price) * (1.0 - slip_mult)
                gross = (fill_p - pos.entry_price) * pos.shares
                fees = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_p * pos.shares)
                closed_trades.append({
                    'sector': cfg['name'],
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

            if curr_peak_r >= pos.r_target:
                fill_p = (pos.entry_price + (pos.r_target * pos.risk_ticks)) * (1.0 - slip_mult)
                gross = (fill_p - pos.entry_price) * pos.shares
                fees = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_p * pos.shares)
                closed_trades.append({
                    'sector': cfg['name'],
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

            if pos.peak_r >= cfg['trailing_lock_r']:
                ratchet = pos.entry_price + (pos.peak_r * 0.40 * pos.risk_ticks)
                pos.stop_price = max(pos.stop_price, ratchet)

        # 3. 15-Minute Macro State Evaluation
        if is_15m:
            t_eval = t_1m - pd.Timedelta(minutes=15)
            
            for sym, pos in active_positions.items():
                if sym in matrices_15m and t_eval in matrices_15m[sym].index:
                    m15 = matrices_15m[sym].loc[t_eval]
                    curr_r = (m15['close'] - pos.entry_price) / pos.risk_ticks
                    if m15['vwap_zscore'] >= -0.20 and curr_r >= cfg['harvest_floor_r']:
                        pos.pending_exit = "DYNAMIC_Z_15M_CLOSE_EXIT"

            if t_eval in sector_breadth.index and sector_breadth.loc[t_eval] < cfg['breadth_gate_z']:
                armed_symbols.clear()
                continue

            if len(active_positions) < cfg['max_concurrent'] and b_time < pd.Timestamp("14:30:00").time():
                for sym in valid_symbols:
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

                    w_closes = m_df['close'].iloc[loc-25:loc].values
                    log_rets = np.diff(np.log(w_closes))
                    if compute_vr(log_rets, k=5) > cfg['vr_max']:
                        continue

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
                                        r_target=cfg['target_r']
                                    )
                                    del armed_symbols[sym]
                                    if len(active_positions) >= cfg['max_concurrent']:
                                        break

    print(f"  • Completed {len(closed_trades)} trades.")
    return pd.DataFrame(closed_trades)

def main():
    print("=" * 85)
    print("ALL 48 SYMBOLS: 4 COMBINED-CYCLE SECTOR ENGINES (38-MONTH CONTINUOUS)")
    print("Execution Regime: Causal 1-Minute | 2.0 bps Slippage | Full Zerodha Fees")
    print("=" * 85)

    all_files = list(DATA_DIR.glob("NSE_*_minute_*.csv"))
    raw_data = {}
    print(f"Loading raw datasets for {len(all_files)} files...")
    for f in all_files:
        sym = f.name.split('_')[1].upper()
        df = pd.read_csv(f)
        t_col = [c for c in df.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
        df['dt'] = pd.to_datetime(df[t_col]).dt.tz_localize(None)
        raw_data[sym] = df.set_index('dt').sort_index()

    all_results = []
    sector_audits = []

    for sec_id, sec_cfg in SECTOR_CLUSTERS.items():
        tdf = run_single_sector_engine(sec_id, sec_cfg, raw_data, slip_bps=2.0)
        if not tdf.empty:
            all_results.append(tdf)
            tot = len(tdf)
            wr = (tdf['net_pnl'] > 0).mean() * 100.0
            gross = tdf['gross_pnl'].sum()
            fees = tdf['friction'].sum()
            net = tdf['net_pnl'].sum()
            avg_t = net / tot
            
            sector_audits.append({
                'Machine Unit': sec_cfg['name'].split(':')[0],
                'Sector Cluster': sec_cfg['name'].split(':')[1].strip(),
                'Trades': tot,
                'Win Rate': f"{wr:.1f}%",
                'Gross Alpha': f"₹{gross:+10,.2f}",
                'Fees Paid': f"₹{fees:9,.2f}",
                'Net P&L': f"₹{net:+10,.2f}",
                'Net / Trade': f"₹{avg_t:+6.2f}"
            })

    print("\n" + "=" * 95)
    print("COMBINED-CYCLE POWER PLANT AUDIT (38 MONTHS: JUL 2023 - AUG 2026)")
    print("=" * 95)
    audit_df = pd.DataFrame(sector_audits)
    print(audit_df.to_string(index=False))
    print("=" * 95)

    if all_results:
        master_df = pd.concat(all_results, ignore_index=True)
        tot_all = len(master_df)
        wr_all = (master_df['net_pnl'] > 0).mean() * 100.0
        gross_all = master_df['gross_pnl'].sum()
        fees_all = master_df['friction'].sum()
        net_all = master_df['net_pnl'].sum()

        print("\n" + "=" * 95)
        print("CONSOLIDATED 48-ASSET COMBINED-CYCLE PORTFOLIO")
        print("=" * 95)
        print(f"Total Portfolio Trades Closed : {tot_all}")
        print(f"Consolidated Net Win Rate     : {wr_all:.1f}%")
        print(f"Consolidated Gross Alpha      : ₹{gross_all:+,.2f}")
        print(f"Total Statutory Fees & Slip   : ₹{fees_all:,.2f}")
        print(f"Net Realized Portfolio P&L    : ₹{net_all:+,.2f}")
        print("=" * 95)

if __name__ == '__main__':
    main()
