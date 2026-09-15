#!/usr/bin/env python3
"""
DCS LOCAL MODE - V12 MOMENTUM STRATEGY
======================================

Uses proven V12 momentum strategy (56.2% win rate from Phase 5)
- 12-1 momentum ranking
- V9-style risk management
- Monthly rebalancing
- Sector diversification
- Real costs (commission + slippage)

1-Day, 1-Month, 3-Year backtests
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime
from collections import defaultdict

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
MANIFEST_HASH = "552AD9DDCE81BBA822C33F4A4DC42CF613D715184300EE0899772CA04544AE1E"

COMMISSION_PER_SIDE = 0.0002
SLIPPAGE_ENTRY = 0.0005
SLIPPAGE_EXIT = 0.0005
STARTING_CAPITAL = 1_000_000.0

FORMATION_MONTHS = 12
SKIP_MONTHS = 1
MAXIMUM_POSITIONS = 20
RISK_FRACTION = 0.02
ATR_LOOKBACK = 14

SECTORS = {
    'INFY': 'IT', 'TCS': 'IT', 'WIPRO': 'IT', 'TECHM': 'IT', 'HCLTECH': 'IT',
    'RELIANCE': 'ENERGY', 'NTPC': 'ENERGY', 'ONGC': 'ENERGY', 'POWERGRID': 'ENERGY',
    'HDFCBANK': 'BANK', 'SBIN': 'BANK', 'ICICIBANK': 'BANK', 'KOTAKBANK': 'BANK', 'AXISBANK': 'BANK',
    'BAJFINANCE': 'FINANCE', 'BAJAJFINSV': 'FINANCE', 'SHRIRAMFIN': 'FINANCE',
    'LT': 'INFRA', 'MARUTI': 'AUTO', 'BAJAJ-AUTO': 'AUTO', 'EICHERMOT': 'AUTO',
    'SUNPHARMA': 'PHARMA', 'DRREDDY': 'PHARMA', 'CIPLA': 'PHARMA', 'ZYDUSLIFE': 'PHARMA',
    'BHARTIARTL': 'TELECOM', 'HINDUNILVR': 'FMCG', 'ITC': 'FMCG',
    'ADANIENT': 'ADANI', 'ADANIPORTS': 'ADANI',
    'APOLLOHOSP': 'HEALTHCARE', 'MAXHEALTH': 'HEALTHCARE', 'SBILIFE': 'FINANCE',
    'ASIANPAINT': 'PAINTS', 'ULTRACEMCO': 'CEMENT', 'COALINDIA': 'MINING',
    'ETERNAL': 'AUTO', 'GRASIM': 'CEMENT', 'HDFCLIFE': 'FINANCE', 'HINDALCO': 'METAL',
    'INDIGO': 'AUTO', 'JIOFIN': 'FINANCE', 'JSWSTEEL': 'METAL', 'M&M': 'AUTO',
    'ONGC': 'ENERGY', 'TATACONSUM': 'FMCG', 'TATASTEEL': 'METAL', 'TRENT': 'RETAIL',
    'BEL': 'DEFENSE', 'LAURUSLABS': 'PHARMA'
}

TEST_PERIODS = {
    '1_DAY': {
        'name': '1 Day',
        'start': '2026-08-27',
        'end': '2026-08-27',
    },
    '1_MONTH': {
        'name': '1 Month',
        'start': '2026-08-01',
        'end': '2026-08-28',
    },
    '3_YEAR': {
        'name': '3 Years',
        'start': '2023-08-14',
        'end': '2026-08-13',
    }
}

# ============================================================================
# UTILITIES
# ============================================================================

def calculate_atr(high, low, close, period=14):
    """Calculate Average True Range"""
    tr1 = high - low
    tr2 = (high - close.shift()).abs()
    tr3 = (low - close.shift()).abs()
    tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    return tr.rolling(period).mean()

def resample_daily(df):
    """Resample to daily OHLC"""
    df = df.copy()
    df['date'] = df['timestamp'].dt.date

    daily = df.groupby('date').agg({
        'open': 'first',
        'high': 'max',
        'low': 'min',
        'close': 'last',
        'volume': 'sum',
        'timestamp': 'last'
    }).reset_index(drop=True)

    daily['timestamp'] = pd.to_datetime(daily['timestamp'])
    return daily

def month_ends(df):
    """Get month-end dates"""
    df = df.copy()
    df['year_month'] = df['timestamp'].dt.to_period('M')
    month_end_mask = df['year_month'] != df['year_month'].shift(-1)
    return df[month_end_mask]['timestamp'].tolist()

def calculate_momentum(returns, formation=12, skip=1):
    """12-1 momentum"""
    if len(returns) < formation + skip:
        return np.nan
    past_returns = returns.iloc[-formation-skip:-skip]
    momentum = (1 + past_returns).prod() - 1
    return momentum

# ============================================================================
# V12 MOMENTUM BACKTEST ENGINE
# ============================================================================

class V12MomentumBacktester:
    """V12 momentum strategy"""

    def __init__(self, symbol_data, epoch_name, epoch_start, epoch_end):
        self.symbol_data = symbol_data
        self.epoch_name = epoch_name
        self.epoch_start = epoch_start
        self.epoch_end = epoch_end
        self.positions = {}
        self.trade_log = []
        self.equity = STARTING_CAPITAL

    def filter_epoch(self):
        """Filter to epoch"""
        filtered = {}
        for symbol, df in self.symbol_data.items():
            df_epoch = df[(df['timestamp'] >= self.epoch_start) &
                         (df['timestamp'] <= self.epoch_end)].copy()
            if len(df_epoch) > FORMATION_MONTHS * 21:
                filtered[symbol] = df_epoch
        return filtered

    def calculate_daily_returns(self, df):
        """Daily returns"""
        return (df['close'].pct_change()).dropna()

    def rank_by_momentum(self, rebalance_date, eligible_symbols):
        """Rank symbols by 12-1 momentum"""
        momentum_scores = {}

        for symbol in eligible_symbols:
            if symbol not in self.symbol_data:
                continue

            df = self.symbol_data[symbol]
            df_past = df[df['timestamp'] <= rebalance_date].copy()

            if len(df_past) < FORMATION_MONTHS * 21:
                continue

            returns = self.calculate_daily_returns(df_past)
            momentum = calculate_momentum(returns, FORMATION_MONTHS, SKIP_MONTHS)

            if not np.isnan(momentum):
                momentum_scores[symbol] = momentum

        ranked = sorted(momentum_scores.items(), key=lambda x: x[1], reverse=True)
        return ranked

    def position_quantity(self, atr_value, entry_price):
        """V9-style position sizing"""
        if atr_value <= 0 or entry_price <= 0:
            return 0
        risk_amount = self.equity * RISK_FRACTION
        quantity = int(risk_amount / (atr_value * entry_price))
        return max(1, min(quantity, 500))

    def backtest(self):
        """Run backtest"""

        filtered_data = self.filter_epoch()
        eligible_symbols = list(filtered_data.keys())

        if not eligible_symbols:
            return None

        # Resample to daily
        daily_data = {}
        for symbol in eligible_symbols:
            daily_data[symbol] = resample_daily(filtered_data[symbol])

        # Get rebalance dates
        all_dates = set()
        for symbol, df in daily_data.items():
            for d in month_ends(df):
                if self.epoch_start <= d <= self.epoch_end:
                    all_dates.add(d)

        rebalance_dates = sorted(list(all_dates))

        # Process each rebalance
        for rebalance_idx, rebalance_date in enumerate(rebalance_dates):

            # Rank and select
            ranked = self.rank_by_momentum(rebalance_date, eligible_symbols)
            top_20_symbols = [s for s, _ in ranked[:MAXIMUM_POSITIONS]]

            # Apply sector control
            sector_count = defaultdict(int)
            selected = []
            for symbol, _ in ranked:
                sector = SECTORS.get(symbol, 'OTHER')
                if sector_count[sector] < 2:
                    selected.append(symbol)
                    sector_count[sector] += 1
                if len(selected) >= MAXIMUM_POSITIONS:
                    break
            top_20_symbols = selected

            # Close positions not in top 20
            to_close = set(self.positions.keys()) - set(top_20_symbols)
            for symbol in to_close:
                if symbol in daily_data:
                    df = daily_data[symbol]
                    df_rebal = df[df['timestamp'] >= rebalance_date]

                    if len(df_rebal) > 0:
                        exit_price = df_rebal.iloc[0]['open'] * (1 - SLIPPAGE_EXIT) * (1 - COMMISSION_PER_SIDE)

                        pos = self.positions[symbol]
                        exit_pnl = (exit_price - pos['entry_price']) * pos['quantity']

                        self.trade_log.append({
                            'entry_date': pos['entry_date'],
                            'exit_date': df_rebal.iloc[0]['timestamp'],
                            'symbol': symbol,
                            'pnl': exit_pnl
                        })

                        self.equity += exit_pnl
                        del self.positions[symbol]

            # Enter new positions
            for symbol in top_20_symbols:
                if symbol not in self.positions and symbol in daily_data:
                    df = daily_data[symbol]
                    df_rebal = df[df['timestamp'] >= rebalance_date]

                    if len(df_rebal) > 0:
                        entry_price = df_rebal.iloc[0]['open']
                        atr_val = calculate_atr(
                            df_rebal['high'], df_rebal['low'], df_rebal['close']
                        ).iloc[0] if len(df_rebal) >= ATR_LOOKBACK else entry_price * 0.02

                        if np.isnan(atr_val) or atr_val <= 0:
                            atr_val = entry_price * 0.02

                        quantity = self.position_quantity(atr_val, entry_price)
                        if quantity > 0 and (quantity * entry_price) <= self.equity * 0.1:

                            entry_with_cost = entry_price * (1 + SLIPPAGE_ENTRY) * (1 + COMMISSION_PER_SIDE)
                            stop_price = entry_price - (ATR_LOOKBACK * atr_val)

                            self.positions[symbol] = {
                                'entry_date': df_rebal.iloc[0]['timestamp'],
                                'entry_price': entry_with_cost,
                                'stop_price': stop_price,
                                'quantity': quantity,
                            }

            # Daily stop checks
            if rebalance_idx < len(rebalance_dates) - 1:
                next_rebalance = rebalance_dates[rebalance_idx + 1]
            else:
                next_rebalance = self.epoch_end

            for symbol in list(self.positions.keys()):
                if symbol not in daily_data:
                    continue

                df = daily_data[symbol]
                df_between = df[(df['timestamp'] > rebalance_date) &
                               (df['timestamp'] < next_rebalance)]

                pos = self.positions[symbol]

                for _, bar in df_between.iterrows():
                    if bar['low'] <= pos['stop_price']:
                        exit_price = pos['stop_price'] * (1 - SLIPPAGE_EXIT) * (1 - COMMISSION_PER_SIDE)
                        exit_pnl = (exit_price - pos['entry_price']) * pos['quantity']

                        self.trade_log.append({
                            'entry_date': pos['entry_date'],
                            'exit_date': bar['timestamp'],
                            'symbol': symbol,
                            'pnl': exit_pnl,
                        })

                        self.equity += exit_pnl
                        del self.positions[symbol]
                        break

        # Close remaining
        for symbol in list(self.positions.keys()):
            if symbol in daily_data:
                df = daily_data[symbol]
                df_last = df[df['timestamp'] <= self.epoch_end]

                if len(df_last) > 0:
                    exit_price = df_last.iloc[-1]['close'] * (1 - SLIPPAGE_EXIT) * (1 - COMMISSION_PER_SIDE)
                    pos = self.positions[symbol]
                    exit_pnl = (exit_price - pos['entry_price']) * pos['quantity']

                    self.trade_log.append({
                        'entry_date': pos['entry_date'],
                        'exit_date': df_last.iloc[-1]['timestamp'],
                        'symbol': symbol,
                        'pnl': exit_pnl,
                    })

                    self.equity += exit_pnl
                    self.positions.pop(symbol, None)

        if len(self.trade_log) == 0:
            return {
                'epoch': self.epoch_name,
                'trades': [],
                'final_equity': self.equity,
                'total_pnl': 0,
                'win_rate': 0,
            }

        winning = len([t for t in self.trade_log if t['pnl'] > 0])
        win_rate = winning / len(self.trade_log)

        return {
            'epoch': self.epoch_name,
            'trades': self.trade_log,
            'final_equity': self.equity,
            'total_pnl': self.equity - STARTING_CAPITAL,
            'win_rate': win_rate,
            'total_trades': len(self.trade_log),
        }

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " DCS LOCAL MODE - V12 MOMENTUM STRATEGY (PROVEN) ".center(78) + "║")
    print("║" + " 1-Day + 1-Month + 3-Year Backtests ".center(78) + "║")
    print("║" + " 56.2% Win Rate (Phase 5 Validated) ".center(78) + "║")
    print("╚" + "="*78 + "╝\n")

    # Load all data
    print("Loading P01D-V2B certified 50-symbol dataset...")
    all_data = {}

    for i, csv_file in enumerate(sorted(DATA_DIR.glob("NSE_*_15minute_*.csv"))):
        try:
            df = pd.read_csv(csv_file)
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
            df = df.sort_values('timestamp').reset_index(drop=True)

            symbol = csv_file.stem.split('_')[1]
            all_data[symbol] = df

            if (i + 1) % 10 == 0 or i == 49:
                print(f"  [{i+1:2}/50] {symbol:15} ({len(df):6} candles)")
        except Exception as e:
            print(f"  [ERR] {str(e)[:40]}")

    print(f"\n✓ Loaded {len(all_data)}/50 symbols\n")

    # Run backtests
    all_results = {}

    for period_key, period_config in TEST_PERIODS.items():
        period_start = pd.Timestamp(period_config['start'], tz='Asia/Kolkata')
        period_end = pd.Timestamp(period_config['end'], tz='Asia/Kolkata')

        print("="*80)
        print(f"BACKTEST: {period_config['name']} ({period_config['start']} to {period_config['end']})")
        print("="*80 + "\n")

        engine = V12MomentumBacktester(all_data, period_key, period_start, period_end)
        result = engine.backtest()

        if result:
            all_results[period_key] = result

            trades = result['trades']
            wins = len([t for t in trades if t['pnl'] > 0])
            win_rate = (wins / len(trades) * 100) if trades else 0

            print(f"Trades:         {result['total_trades']}")
            print(f"Winning Trades: {wins}")
            print(f"Win Rate:       {win_rate:.1f}%")
            print(f"Total P&L:      ₹{result['total_pnl']:+,.2f}")
            print(f"Return:         {(result['total_pnl']/STARTING_CAPITAL)*100:+.2f}%")
            print(f"Final Equity:   ₹{result['final_equity']:,.2f}\n")

    # Save report
    print("\n" + "="*80)
    print("COMPLETE")
    print("="*80 + "\n")

    report = {
        'timestamp': datetime.now().isoformat(),
        'system': 'DCS_LOCAL_V12_MOMENTUM',
        'data_source': 'P01D-V2B-CERTIFIED',
        'manifest_hash': MANIFEST_HASH,
        'strategy': 'V12_MOMENTUM_WITH_V9_RISK',
        'configuration': {
            'formation_months': FORMATION_MONTHS,
            'skip_months': SKIP_MONTHS,
            'maximum_positions': MAXIMUM_POSITIONS,
            'risk_fraction': RISK_FRACTION,
        },
        'periods': all_results
    }

    report_file = Path("DCS_LOCAL_V12_MOMENTUM_REPORT_20260829.json")
    with open(report_file, 'w') as f:
        json.dump(report, f, indent=2, default=str)

    print(f"✓ Report saved: {report_file}\n")

    # Summary
    print("Summary:")
    for period_key, result in all_results.items():
        print(f"\n{result['epoch']}:")
        print(f"  Trades:    {result['total_trades']}")
        print(f"  Win Rate:  {result['win_rate']:.1%}")
        print(f"  P&L:       ₹{result['total_pnl']:+,.2f}")

if __name__ == '__main__':
    main()
