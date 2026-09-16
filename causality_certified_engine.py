"""
Causality-Certified 1-Minute Microstructure Backtest Simulator
-------------------------------------------------------------
Audit Remediations Applied:
1. True 1-Minute Micro-Execution: 15m signals, 1m bar-by-bar chronological fills.
2. Dual-Universe Decoupling: BREADTH_UNIVERSE (48) vs TRADE_UNIVERSE (17).
3. Prior-Bar Target Freezing: Targets for [T, T+15m) frozen at T-15m close.
4. Next-Open Execution on Z-Exits: Bar close signals fill at next 1m open.
5. Dual-Constraint Sizing: Min(Slot Capital / Price, Risk Capital / Stop Distance).
6. Continuous History: No monthly resets; continuous rolling warmup.
7. Unconditional Terminal Liquidation: Asserts open_positions == 0 at EOD.
8. Adversarial Slippage Grid: 0, 2, 5, and 10 bps per leg.
"""

import sys
from pathlib import Path
from dataclasses import dataclass
import numpy as np
import pandas as pd

from alpha_engine_core import ZerodhaFeeCalculator, TimeDecayGovernor, EngineConfig

DATA_DIR = Path('/home/shrinivas/ECS_Complete/P01D_CHART_STUDIES_V10_HISTORICAL_REPLAY_20260825/DATA_1MIN_48_20230703_20260824')

TRADE_UNIVERSE = [
    "AXISBANK", "ULTRACEMCO", "M&M", "JSWSTEEL", "BAJAJ-AUTO",
    "EICHERMOT", "HINDUNILVR", "BAJFINANCE", "ITC", "GRASIM",
    "MARUTI", "COALINDIA", "SBILIFE", "NTPC", "HDFCLIFE", "CIPLA", "ETERNAL"
]

SECTOR_MAP = {
    "AXISBANK": "BANK", "BAJFINANCE": "FIN", "SBILIFE": "FIN", "HDFCLIFE": "FIN",
    "M&M": "AUTO", "BAJAJ-AUTO": "AUTO", "EICHERMOT": "AUTO", "MARUTI": "AUTO",
    "ULTRACEMCO": "CEMENT", "GRASIM": "CEMENT", "JSWSTEEL": "METALS",
    "HINDUNILVR": "FMCG", "ITC": "FMCG", "COALINDIA": "ENERGY", "NTPC": "ENERGY",
    "CIPLA": "PHARMA", "ETERNAL": "OTHER"
}

@dataclass
class CausalPosition:
    symbol: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    risk_ticks: float
    shares: int
    peak_r: float
    frozen_r_target: float
    frozen_z_target: float
    pending_exit: str = None  # Signals scheduled for next 1m open

def compute_variance_ratio(log_returns: np.ndarray, k: int = 5) -> float:
    if len(log_returns) < k * 5:
        return 1.0
    var_1 = np.var(log_returns, ddof=1)
    k_returns = pd.Series(log_returns).rolling(k).sum().dropna().values
    var_k = np.var(k_returns, ddof=1)
    if var_1 == 0:
        return 1.0
    return float(var_k / (k * var_1))

def precompute_15m_stream(df_1m: pd.DataFrame) -> pd.DataFrame:
    """Resamples continuous 1-minute bars to 15-minute bars and calculates features."""
    df_15 = df_1m.resample('15min').agg({
        'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
    }).dropna()
    
    # VWAP and Z-score
    df_15['date'] = df_15.index.date
    df_15['typical'] = (df_15['high'] + df_15['low'] + df_15['close']) / 3.0
    df_15['cum_vol'] = df_15.groupby('date')['volume'].cumsum()
    df_15['cum_pv'] = df_15.groupby('date').apply(lambda g: (g['typical'] * g['volume']).cumsum()).reset_index(level=0, drop=True)
    df_15['vwap'] = df_15['cum_pv'] / np.maximum(df_15['cum_vol'], 1.0)
    
    # 20-period rolling std of typical price for Z-score
    df_15['rolling_std'] = df_15.groupby('date')['typical'].transform(lambda s: s.expanding().std()).fillna(1.0)
    df_15['vwap_zscore'] = (df_15['close'] - df_15['vwap']) / np.maximum(df_15['rolling_std'], 0.1)
    
    # RSI 14
    delta = df_15['close'].diff()
    gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
    loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
    rs = gain / np.maximum(loss, 1e-6)
    df_15['rsi_14'] = 100.0 - (100.0 / (1.0 + rs))
    
    # ATR 14
    h_l = df_15['high'] - df_15['low']
    h_pc = (df_15['high'] - df_15['close'].shift(1)).abs()
    l_pc = (df_15['low'] - df_15['close'].shift(1)).abs()
    tr = pd.concat([h_l, h_pc, l_pc], axis=1).max(axis=1)
    df_15['atr'] = tr.rolling(14).mean()
    
    # Volume ratio (vol / 20-SMA vol)
    df_15['vol_ratio'] = df_15['volume'] / np.maximum(df_15['volume'].rolling(20).mean(), 1.0)
    
    return df_15.dropna()

def run_causal_replay(slippage_bps_per_leg: float = 2.0):
    print("=" * 80)
    print(f"STARTING CAUSAL REPLAY | SLIPPAGE: {slippage_bps_per_leg:.1f} BPS PER LEG")
    print("=" * 80)
    
    # 1. Load entire universe manifest
    all_files = list(DATA_DIR.glob("NSE_*_minute_*.csv"))
    assert len(all_files) >= 48, f"Manifest check failed: Expected >= 48 symbols, found {len(all_files)}"
    
    raw_1m_data = {}
    print(f"[1/4] Loading continuous 1-minute datasets for breadth and execution...")
    for f in all_files:
        sym = f.name.split('_')[1].upper()
        df = pd.read_csv(f)
        t_col = [c for c in df.columns if c.lower() in ['date', 'datetime', 'time', 'timestamp']][0]
        df['dt'] = pd.to_datetime(df[t_col]).dt.tz_localize(None)
        raw_1m_data[sym] = df.set_index('dt').sort_index()

    # Verify trade universe presence
    for sym in TRADE_UNIVERSE:
        assert sym in raw_1m_data, f"Required trading asset {sym} missing from dataset manifest!"

    # 2. Build 15m feature matrices and calculate global 48-symbol breadth
    print(f"[2/4] Pre-computing 15m continuous feature matrices and macro breadth...")
    matrices_15m = {}
    all_15m_z = []
    for sym, df_1m in raw_1m_data.items():
        feat_15 = precompute_15m_stream(df_1m)
        matrices_15m[sym] = feat_15
        all_15m_z.append(feat_15['vwap_zscore'].rename(sym))

    macro_breadth_df = pd.concat(all_15m_z, axis=1).mean(axis=1).rename('breadth_z')
    print(f"[INFO] 48-Symbol Breadth initialized across {len(macro_breadth_df)} 15-minute frames.")

    # 3. Microstructure Execution Loop (Chronological 1-Minute Steps)
    print(f"[3/4] Running 1-minute chronological simulation across 38 months...")
    
    # Extract chronological 1-minute timeline across trading assets
    trade_1m_dfs = {s: raw_1m_data[s] for s in TRADE_UNIVERSE}
    master_timeline_1m = sorted(list(set.union(*[set(df.index) for df in trade_1m_dfs.values()])))
    
    active_positions: dict[str, CausalPosition] = {}
    armed_symbols: dict[str, dict] = {}
    closed_trades = []
    
    slip_mult = slippage_bps_per_leg / 10000.0
    config = EngineConfig()
    governor = TimeDecayGovernor(config)

    # Pre-calculate daily gap-downs strictly on prior session close
    daily_gap_flags = set()
    for sym, df_1m in trade_1m_dfs.items():
        daily = df_1m.resample('D').agg({'open': 'first', 'close': 'last'}).dropna()
        daily['prev_close'] = daily['close'].shift(1)
        daily['gap_pct'] = (daily['open'] - daily['prev_close']) / daily['prev_close'] * 100.0
        bad_days = daily[daily['gap_pct'] <= -1.25].index.date
        for d in bad_days:
            daily_gap_flags.add((sym, d))

    current_15m_bar_time = None

    for t_1m in master_timeline_1m:
        bar_time = t_1m.time()
        bar_date = t_1m.date()
        is_15m_boundary = (t_1m.minute % 15 == 0)

        # ----------------------------------------------------
        # A. Execute Pending Orders at Current 1m Open
        # ----------------------------------------------------
        symbols_to_remove = []
        for sym, pos in active_positions.items():
            if pos.pending_exit and t_1m in trade_1m_dfs[sym].index:
                open_p = trade_1m_dfs[sym].loc[t_1m, 'open']
                # Adverse slippage on exit fill
                fill_price = open_p * (1.0 - slip_mult)
                gross_pnl = (fill_price - pos.entry_price) * pos.shares
                notional_in = pos.entry_price * pos.shares
                notional_out = fill_price * pos.shares
                friction = ZerodhaFeeCalculator.calculate_round_trip(notional_in, notional_out)
                net_pnl = gross_pnl - friction

                closed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t_1m,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_price,
                    'shares': pos.shares,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': net_pnl,
                    'reason': pos.pending_exit
                })
                symbols_to_remove.append(sym)

        for sym in symbols_to_remove:
            del active_positions[sym]

        # ----------------------------------------------------
        # B. 1-Minute Microstructure Risk & Target Checks
        # ----------------------------------------------------
        for sym, pos in list(active_positions.items()):
            if t_1m not in trade_1m_dfs[sym].index:
                continue
            bar_1m = trade_1m_dfs[sym].loc[t_1m]
            
            # 1. Mandatory 15:15 Session Square-off
            if bar_time >= pd.Timestamp("15:15:00").time():
                pos.pending_exit = "MANDATORY_1515_SQUAREOFF"
                continue

            # 2. Hard Stop Loss Evaluation inside the 1-minute bar
            if bar_1m['low'] <= pos.stop_price:
                # Stop triggers intraday at exact stop level minus 1-tick / slippage
                fill_price = min(bar_1m['open'], pos.stop_price) * (1.0 - slip_mult)
                gross_pnl = (fill_price - pos.entry_price) * pos.shares
                friction = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_price * pos.shares)
                closed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t_1m,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_price,
                    'shares': pos.shares,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': gross_pnl - friction,
                    'reason': "STOP_LOSS_1M_HIT"
                })
                del active_positions[sym]
                continue

            # 3. Dynamic R Target Evaluation inside the 1-minute bar
            curr_peak_r = (bar_1m['high'] - pos.entry_price) / pos.risk_ticks
            pos.peak_r = max(pos.peak_r, curr_peak_r)

            if curr_peak_r >= pos.frozen_r_target:
                target_p = pos.entry_price + (pos.frozen_r_target * pos.risk_ticks)
                fill_price = target_p * (1.0 - slip_mult)
                gross_pnl = (fill_price - pos.entry_price) * pos.shares
                friction = ZerodhaFeeCalculator.calculate_round_trip(pos.entry_price * pos.shares, fill_price * pos.shares)
                closed_trades.append({
                    'symbol': sym,
                    'entry_time': pos.entry_time,
                    'exit_time': t_1m,
                    'entry_price': pos.entry_price,
                    'exit_price': fill_price,
                    'shares': pos.shares,
                    'gross_pnl': gross_pnl,
                    'friction': friction,
                    'net_pnl': gross_pnl - friction,
                    'reason': "DYNAMIC_R_TARGET_1M_HIT"
                })
                del active_positions[sym]
                continue

            # 4. Trailing Profit Ratchet
            if pos.peak_r >= 1.20:
                ratchet_stop = pos.entry_price + (pos.peak_r * 0.40 * pos.risk_ticks)
                pos.stop_price = max(pos.stop_price, ratchet_stop)

        # ----------------------------------------------------
        # C. 15-Minute Macro State Evaluation (At Completed Closes)
        # ----------------------------------------------------
        if is_15m_boundary:
            t_15m_eval = t_1m - pd.Timedelta(minutes=15)
            
            # 1. Update/Freeze Targets for active positions from prior 15m close
            for sym, pos in active_positions.items():
                if sym in matrices_15m and t_15m_eval in matrices_15m[sym].index:
                    m_bar = matrices_15m[sym].loc[t_15m_eval]
                    r_t, z_t = governor.compute_targets(m_bar['vol_ratio'], t_15m_eval.time())
                    pos.frozen_r_target = max(r_t, 1.80)
                    pos.frozen_z_target = z_t
                    
                    # Schedule Z-Score exit for next 1m open if satisfied at 15m close
                    curr_r = (m_bar['close'] - pos.entry_price) / pos.risk_ticks
                    if m_bar['vwap_zscore'] >= pos.frozen_z_target and curr_r >= 1.40:
                        pos.pending_exit = "DYNAMIC_Z_15M_CLOSE_EXIT"

            # 2. Macro Breadth Filter across ALL 48 symbols
            if t_15m_eval in macro_breadth_df.index:
                macro_breadth = macro_breadth_df.loc[t_15m_eval]
                if macro_breadth < -0.45:
                    armed_symbols.clear()
                    continue

            # 3. Evaluate New Signals across Trade Universe
            if len(active_positions) < config.max_concurrent_positions and bar_time < pd.Timestamp("14:30:00").time():
                active_sectors = [SECTOR_MAP.get(s, "OTHER") for s in active_positions]

                for sym in TRADE_UNIVERSE:
                    if sym in active_positions or (sym, bar_date) in daily_gap_flags:
                        continue
                    if SECTOR_MAP.get(sym, "OTHER") in active_sectors:
                        continue
                    if sym not in matrices_15m or t_15m_eval not in matrices_15m[sym].index:
                        continue

                    m_df = matrices_15m[sym]
                    loc = m_df.index.get_loc(t_15m_eval)
                    if loc < 30:
                        continue

                    curr_15 = m_df.iloc[loc]
                    prev_15 = m_df.iloc[loc - 1]

                    # Rolling Variance Ratio Check (trailing 25 bars)
                    window_closes = m_df['close'].iloc[loc-25:loc].values
                    log_rets = np.diff(np.log(window_closes))
                    if compute_variance_ratio(log_rets, k=5) > 1.05:
                        continue

                    # Arm condition: Z < -2.5 and RSI < 28
                    if curr_15['vwap_zscore'] < -2.5 and curr_15['rsi_14'] < 28.0:
                        armed_symbols[sym] = {
                            'armed_time': t_15m_eval,
                            'swing_low': min(curr_15['low'], prev_15['low']),
                            'atr': curr_15['atr'],
                            'vol_ratio': curr_15['vol_ratio']
                        }

                    # Trigger check: Close > Prev High & Close > Open
                    if sym in armed_symbols:
                        arm = armed_symbols[sym]
                        arm['swing_low'] = min(arm['swing_low'], curr_15['low'])

                        if curr_15['close'] > prev_15['high'] and curr_15['close'] > curr_15['open']:
                            # Entry confirmed at current 1m bar open with adverse slippage
                            if t_1m in trade_1m_dfs[sym].index:
                                entry_p = trade_1m_dfs[sym].loc[t_1m, 'open'] * (1.0 + slip_mult)
                                risk_distance = max(entry_p - arm['swing_low'], config.stop_atr_multiplier * arm['atr'])
                                
                                # Dual-Constraint Sizing
                                shares_by_capital = int(config.slot_capital / entry_p)
                                shares_by_risk = int(1500.0 / max(risk_distance, 0.5))  # Hard ₹1,500 risk cap
                                final_shares = min(shares_by_capital, shares_by_risk)

                                if final_shares >= 1:
                                    r_t, z_t = governor.compute_targets(arm['vol_ratio'], t_15m_eval.time())
                                    active_positions[sym] = CausalPosition(
                                        symbol=sym,
                                        entry_time=t_1m,
                                        entry_price=entry_p,
                                        stop_price=entry_p - risk_distance,
                                        risk_ticks=risk_distance,
                                        shares=final_shares,
                                        peak_r=0.0,
                                        frozen_r_target=max(r_t, 1.80),
                                        frozen_z_target=z_t
                                    )
                                    del armed_symbols[sym]
                                    active_sectors.append(SECTOR_MAP.get(sym, "OTHER"))
                                    if len(active_positions) >= config.max_concurrent_positions:
                                        break

    # 4. Mandatory Terminal Reconciliation
    print(f"[4/4] Reconciling terminal state. Open positions: {len(active_positions)}")
    assert len(active_positions) == 0, f"Critical Leak: {len(active_positions)} positions left open at end of data!"
    
    return pd.DataFrame(closed_trades)

def main():
    slippage_scenarios = [0.0, 2.0, 5.0, 10.0]
    audit_summary = []

    for slip in slippage_scenarios:
        tdf = run_causal_replay(slippage_bps_per_leg=slip)
        if tdf.empty:
            continue
        tot = len(tdf)
        wr = (tdf['net_pnl'] > 0).mean() * 100.0
        gross = tdf['gross_pnl'].sum()
        fees = tdf['friction'].sum()
        net = tdf['net_pnl'].sum()
        cum = tdf['net_pnl'].cumsum()
        max_dd = (cum.cummax() - cum).max()

        audit_summary.append({
            'Slippage (bps/leg)': f"{slip:.1f} bps",
            'Trades': tot,
            'Win Rate': f"{wr:.1f}%",
            'Gross Alpha': f"₹{gross:+10,.2f}",
            'Fees Paid': f"₹{fees:9,.2f}",
            'Net P&L': f"₹{net:+10,.2f}",
            'Max DD': f"₹{max_dd:9,.2f}",
            'Net / Trade': f"₹{net / tot:+6.2f}"
        })

    res_df = pd.DataFrame(audit_summary)
    print("\n" + "=" * 95)
    print("AUDIT GATE 2 & 3: CAUSAL MICRO-SIMULATION & ADVERSARIAL STRESS MATRIX")
    print("=" * 95)
    print(res_df.to_string(index=False))
    print("=" * 95)

if __name__ == '__main__':
    main()
