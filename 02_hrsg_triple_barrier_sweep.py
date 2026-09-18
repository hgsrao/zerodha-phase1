#!/usr/bin/env python3
"""
Phase 2: HRSG Triple Barrier Calibration Engine
Numba-accelerated parameter sweep for Deadband, Upper/Lower Barriers, Holding Horizon
Outputs: Sharpe ratio, Win Rate, P&L, Drawdown per parameter combo
"""

import os
import sys
from pathlib import Path
from itertools import product
import numpy as np
import pandas as pd
from numba import jit, prange
import warnings
warnings.filterwarnings('ignore')

# ============================================================================
# 1. CONFIGURATION: Parameter Ranges
# ============================================================================
DEADBAND_RANGE = np.arange(-3.0, -1.5 + 0.01, 0.25)  # -3.0, -2.75, -2.5, -2.25, -2.0, -1.75, -1.5
ATR_UPPER_RANGE = np.array([1.0, 1.5, 2.0, 2.5, 3.0, 3.5])  # Upper barrier: 1-3.5x ATR
ATR_LOWER_RANGE = np.array([0.75, 1.0, 1.25, 1.5, 1.75, 2.0])  # Lower barrier: 0.75-2.0x ATR
HORIZON_RANGE = np.array([15, 30, 60, 120], dtype=np.int32)  # Bars until forced exit

RISK_PER_TRADE = 500.0  # ₹500 fixed risk
COMMISSION = 0.003  # 0.3% round-trip
SLIPPAGE = 0.001  # 0.1% bid-ask

DATA_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\data\raw")
RESULTS_DIR = Path(r"C:\Users\Dishan\P03_institutional_quant\results")
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

print("\n" + "="*80)
print("🔧 HRSG TRIPLE BARRIER CALIBRATION ENGINE")
print("="*80)
print(f"\nParameter ranges:")
print(f"  Deadband (Z_entry):        {DEADBAND_RANGE[0]:.2f} → {DEADBAND_RANGE[-1]:.2f} (step 0.25)")
print(f"  Upper Barrier (M_profit):  {ATR_UPPER_RANGE[0]:.1f}x → {ATR_UPPER_RANGE[-1]:.1f}x ATR")
print(f"  Lower Barrier (M_stop):    {ATR_LOWER_RANGE[0]:.2f}x → {ATR_LOWER_RANGE[-1]:.2f}x ATR")
print(f"  Holding Horizon (T_max):   {HORIZON_RANGE.min()} → {HORIZON_RANGE.max()} bars")
print(f"\nTotal combinations: {len(DEADBAND_RANGE)} × {len(ATR_UPPER_RANGE)} × {len(ATR_LOWER_RANGE)} × {len(HORIZON_RANGE)}")
print(f"                  = {len(DEADBAND_RANGE) * len(ATR_UPPER_RANGE) * len(ATR_LOWER_RANGE) * len(HORIZON_RANGE)} sweeps")
print("="*80)

# ============================================================================
# 2. NUMBA-ACCELERATED BACKTEST ENGINE
# ============================================================================
@jit(nopython=True, parallel=True)
def backtest_signal_vector(
    prices,
    volumes,
    deadband_threshold,
    atr_upper_mult,
    atr_lower_mult,
    holding_bars,
    risk_per_trade,
    commission,
    slippage
):
    """
    Triple Barrier backtest (Numba JIT)
    Returns: pnl_trades (array of P&L per trade), max_dd, sharpe
    """
    n_bars = len(prices)
    trades = np.empty(n_bars, dtype=np.float64)
    trades[:] = 0.0
    trade_count = 0

    # MA20 for entry signal
    ma20 = np.empty(n_bars, dtype=np.float64)
    ma20[:20] = prices[0]
    for i in range(20, n_bars):
        ma20[i] = np.mean(prices[i-20:i])

    # ATR (14-period)
    atr = np.empty(n_bars, dtype=np.float64)
    atr[:14] = 0.0
    for i in range(14, n_bars):
        tr = np.maximum(
            prices[i] - prices[i-1],
            np.maximum(
                prices[i] - np.min(prices[i-14:i]),
                np.max(prices[i-14:i]) - prices[i]
            )
        )
        atr[i] = np.mean(np.abs(np.diff(prices[i-14:i])))

    in_position = False
    entry_price = 0.0
    entry_bar = 0
    position_qty = 0

    for i in range(21, n_bars):
        if volumes[i] == 0:
            continue

        # ====================================================================
        # ENTRY: Deadband score + MA crossover
        # ====================================================================
        if not in_position and i >= 20:
            pct_dist = (prices[i] - ma20[i]) / ma20[i]

            # Entry signal: price crosses below MA20 with deadband filter
            if pct_dist < deadband_threshold and prices[i-1] >= ma20[i-1]:
                entry_price = prices[i] * (1 + slippage)
                position_qty = int(risk_per_trade / (entry_price * 0.02))  # 2% risk

                in_position = True
                entry_bar = i

        # ====================================================================
        # EXIT: Triple Barrier (Upper + Lower + Horizon)
        # ====================================================================
        if in_position:
            bars_held = i - entry_bar
            current_atr = atr[i] if atr[i] > 0 else 1.0

            upper_target = entry_price + (atr_upper_mult * current_atr)
            lower_stop = entry_price - (atr_lower_mult * current_atr)

            # Exit conditions (in order of priority)
            exit_triggered = False
            exit_price = 0.0

            # 1. Upper barrier (profit target)
            if prices[i] >= upper_target:
                exit_price = upper_target * (1 - slippage)
                exit_triggered = True

            # 2. Lower barrier (stop loss)
            elif prices[i] <= lower_stop:
                exit_price = lower_stop * (1 + slippage)
                exit_triggered = True

            # 3. Horizontal barrier (time-based expiration)
            elif bars_held >= holding_bars:
                exit_price = prices[i] * (1 - slippage)
                exit_triggered = True

            if exit_triggered:
                # Gross P&L
                pnl_gross = (exit_price - entry_price) * position_qty
                # Costs
                pnl_cost = -commission * entry_price * position_qty
                pnl_cost -= commission * exit_price * position_qty
                # Net
                pnl_net = pnl_gross + pnl_cost

                trades[trade_count] = pnl_net
                trade_count += 1
                in_position = False

    # ========================================================================
    # 3. METRICS CALCULATION
    # ========================================================================
    pnl_trades = trades[:trade_count]

    if trade_count == 0:
        return np.array([0.0]), 0.0, 0.0, 0.0, 0.0, 0.0

    # Cumulative P&L
    cumsum = np.cumsum(pnl_trades)
    running_max = np.maximum.accumulate(cumsum)
    drawdown = cumsum - running_max
    max_dd = np.min(drawdown)

    # Sharpe Ratio
    total_return = np.sum(pnl_trades)
    n_trades = len(pnl_trades)

    if n_trades > 1:
        std_return = np.std(pnl_trades)
        sharpe = (total_return / n_trades) / (std_return + 1e-6) * np.sqrt(252 * 6.5 * 60)  # Annualized
    else:
        sharpe = 0.0

    # Win rate
    wins = np.sum(pnl_trades > 0)
    win_rate = wins / n_trades if n_trades > 0 else 0.0

    # Profit factor
    gross_profit = np.sum(pnl_trades[pnl_trades > 0])
    gross_loss = np.abs(np.sum(pnl_trades[pnl_trades < 0]))
    profit_factor = (gross_profit / (gross_loss + 1e-6)) if gross_loss > 0 else 0.0

    return pnl_trades, total_return, sharpe, win_rate, profit_factor, max_dd


# ============================================================================
# 4. LOAD & PREPROCESS DATA
# ============================================================================
def load_symbol_data(symbol: str, data_dir: Path):
    """Load Parquet file for a symbol"""
    parquet_file = data_dir / f"{symbol}_1min.parquet"

    if not parquet_file.exists():
        print(f"⚠ {symbol}: Parquet file not found → {parquet_file}")
        return None

    try:
        df = pd.read_parquet(parquet_file)
        print(f"✓ Loaded {symbol:12s}: {len(df):>8,} bars | {df.index.min().date()} → {df.index.max().date()}")

        # Extract OHLCV
        prices = df['close'].values.astype(np.float64)
        volumes = df['volume'].values.astype(np.float64)

        return prices, volumes
    except Exception as e:
        print(f"✗ Error loading {symbol}: {e}")
        return None


# ============================================================================
# 5. SWEEP LOOP
# ============================================================================
def main():
    # Load first available symbol
    symbols = ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]
    prices_arr = None
    volumes_arr = None
    symbol_loaded = None

    for sym in symbols:
        result = load_symbol_data(sym, DATA_DIR)
        if result is not None:
            prices_arr, volumes_arr = result
            symbol_loaded = sym
            break

    if prices_arr is None:
        print(f"\n❌ No Parquet files found in {DATA_DIR}")
        print("   Run 01_download_historical_lake.py first to download data.")
        sys.exit(1)

    print(f"\n✅ Using {symbol_loaded} for calibration ({len(prices_arr):,} bars)")

    # ========================================================================
    # SWEEP
    # ========================================================================
    print("\n" + "="*80)
    print("🔄 PARAMETER SWEEP (Numba JIT)...")
    print("="*80 + "\n")

    results = []
    total_combos = len(DEADBAND_RANGE) * len(ATR_UPPER_RANGE) * len(ATR_LOWER_RANGE) * len(HORIZON_RANGE)
    combo_idx = 0

    for deadband in DEADBAND_RANGE:
        for atr_upper in ATR_UPPER_RANGE:
            for atr_lower in ATR_LOWER_RANGE:
                for horizon in HORIZON_RANGE:
                    pnl_arr, total_pnl, sharpe, win_rate, pf, max_dd = backtest_signal_vector(
                        prices_arr, volumes_arr,
                        deadband, atr_upper, atr_lower, horizon,
                        RISK_PER_TRADE, COMMISSION, SLIPPAGE
                    )

                    n_trades = len(pnl_arr)

                    results.append({
                        'deadband': deadband,
                        'atr_upper_mult': atr_upper,
                        'atr_lower_mult': atr_lower,
                        'holding_bars': horizon,
                        'n_trades': n_trades,
                        'total_pnl': total_pnl,
                        'sharpe': sharpe,
                        'win_rate': win_rate,
                        'profit_factor': pf,
                        'max_drawdown': max_dd,
                    })

                    combo_idx += 1
                    if combo_idx % 50 == 0:
                        print(f"  [{combo_idx:4d}/{total_combos}] Sharpe: {sharpe:6.2f} | WR: {win_rate:5.1%} | P&L: ₹{total_pnl:>9.0f}")

    # ========================================================================
    # SAVE RESULTS
    # ========================================================================
    df_results = pd.DataFrame(results)
    df_results = df_results.sort_values('sharpe', ascending=False)

    # Save full results
    results_csv = RESULTS_DIR / f"hrsg_sweep_{symbol_loaded}.csv"
    df_results.to_csv(results_csv, index=False)
    print(f"\n✅ Full results saved: {results_csv}")

    # Top 10
    print("\n" + "="*80)
    print("🏆 TOP 10 PARAMETER COMBINATIONS (by Sharpe Ratio)")
    print("="*80)
    top_10 = df_results.head(10)
    for idx, row in top_10.iterrows():
        print(
            f"  Deadband: {row['deadband']:6.2f} | "
            f"ATR Upper: {row['atr_upper_mult']:4.2f}x | "
            f"ATR Lower: {row['atr_lower_mult']:4.2f}x | "
            f"Horizon: {row['holding_bars']:3.0f}b | "
            f"Sharpe: {row['sharpe']:7.2f} | "
            f"WR: {row['win_rate']:5.1%} | "
            f"P&L: ₹{row['total_pnl']:>8.0f} | "
            f"Trades: {row['n_trades']:4.0f}"
        )

    print("\n" + "="*80)
    print("✅ CALIBRATION COMPLETE")
    print("="*80)


if __name__ == "__main__":
    main()
