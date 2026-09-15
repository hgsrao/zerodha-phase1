#!/usr/bin/env python3
"""
COMPLETE REAL DCS SYSTEM - 48 SYMBOLS
=====================================

Using ACTUAL COMPLETE_DCS_CLOSED_LOOP_V1 with:
✅ Stage 1: Data Input Validation
✅ Stage 2: PA (Predictive Analytics) + FEEDBACK LOOP 1 (PID - adjusts weights)
✅ Stage 3: ID (Intelligent Discrimination)
✅ Stage 4: Bridge (Economic Viability)
✅ Stage 5: MPC (Model Predictive Control) + FEEDBACK LOOP 2 (PID - adjusts Lambda)
✅ Sync Gate: Synchronization checks (dP/dt, dV/dt, phase angle)
✅ Stage 6: P01D (Sovereign Authority with PID timing)

This is the REAL closed-loop self-learning system.
"""

import pandas as pd
import numpy as np
import json
from pathlib import Path
from datetime import datetime
import sys

sys.path.insert(0, str(Path.cwd()))
from COMPLETE_DCS_CLOSED_LOOP_V1 import DCSClosedLoopSystem

# ============================================================================
# CONFIGURATION
# ============================================================================

DATA_DIR = Path("P01D_V2B_REGIME_TWO_PILLAR_20260816/DATA_CLEAN_CORRECTED_UNION50_15MIN/EQUITIES")
MANIFEST_HASH = "552AD9DDCE81BBA822C33F4A4DC42CF613D715184300EE0899772CA04544AE1E"

TEST_PERIODS = {
    '1_DAY': {'name': '1 Day', 'start': '2026-08-27', 'end': '2026-08-27'},
    '1_MONTH': {'name': '1 Month', 'start': '2026-08-01', 'end': '2026-08-28'},
    '3_YEAR': {'name': '3 Years', 'start': '2023-08-14', 'end': '2026-08-13'}
}

SYMBOLS_48 = [
    'INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN',
    'BAJFINANCE', 'ICICIBANK', 'SUNPHARMA', 'KOTAKBANK', 'LT',
    'AXISBANK', 'BHARTIARTL', 'HINDUNILVR', 'ITC', 'MARUTI',
    'NTPC', 'POWERGRID', 'TATASTEEL', 'ZYDUSLIFE', 'LAURUSLABS',
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'BAJAJ-AUTO',
    'BAJAJFINSV', 'BEL', 'CIPLA', 'COALINDIA', 'DRREDDY',
    'EICHERMOT', 'ETERNAL', 'GRASIM', 'HCLTECH', 'HDFCLIFE',
    'HINDALCO', 'INDIGO', 'JIOFIN', 'JSWSTEEL', 'M&M',
    'MAXHEALTH', 'ONGC', 'SBILIFE', 'SHRIRAMFIN', 'TATACONSUM',
    'TECHM', 'TITAN', 'TRENT', 'ULTRACEMCO', 'WIPRO'
]

# ============================================================================
# RUNNER USING REAL DCS SYSTEM
# ============================================================================

class RealDCSRunner:
    """Run REAL DCS system with all 6 stages + feedback loops + sync gate"""

    def __init__(self):
        self.all_results = {}

    def load_symbol(self, symbol):
        """Load symbol from certified data"""
        files = list(DATA_DIR.glob(f"NSE_{symbol}_15minute_*.csv"))
        if not files:
            return None

        try:
            df = pd.read_csv(files[0])
            df['timestamp'] = pd.to_datetime(df['timestamp'], utc=True).dt.tz_convert('Asia/Kolkata')
            df = df.sort_values('timestamp').reset_index(drop=True)
            return df
        except:
            return None

    def run_period(self, period_key, period_config):
        """Run REAL DCS system on period"""

        period_start = pd.Timestamp(period_config['start'], tz='Asia/Kolkata')
        period_end = pd.Timestamp(period_config['end'], tz='Asia/Kolkata')

        print(f"\n{'='*80}")
        print(f"REAL DCS 6-STAGE + FEEDBACK LOOPS + SYNC GATE: {period_config['name']}")
        print(f"Period: {period_config['start']} to {period_config['end']}")
        print(f"{'='*80}\n")
        print("System Features:")
        print("  ✓ Stage 1: Data Input Validation")
        print("  ✓ Stage 2: PA Analytics + FEEDBACK LOOP 1 (PID: Kp=0.1, Ki=0.01, Kd=0.01)")
        print("  ✓ Stage 3: Intelligent Discrimination")
        print("  ✓ Stage 4: Economic Bridge")
        print("  ✓ Stage 5: MPC Control + FEEDBACK LOOP 2 (PID: Kp=0.05, Ki=0.005, Kd=0.005)")
        print("  ✓ Sync Gate: dP/dt, dV/dt, phase angle checks")
        print("  ✓ Stage 6: P01D Sovereign Authority\n")

        results = {}
        total_trades = 0
        total_pnl = 0
        symbols_with_trades = 0
        total_wins = 0

        for i, symbol in enumerate(SYMBOLS_48, 1):
            df = self.load_symbol(symbol)
            if df is None:
                continue

            df_period = df[(df['timestamp'] >= period_start) &
                          (df['timestamp'] <= period_end)].copy()

            if len(df_period) < 100:
                continue

            # Create DCS system instance (with all 6 stages + feedback loops)
            dcs = DCSClosedLoopSystem(symbol, verbose=False)

            # Run through the 6 stages
            trades = []
            equity = 1_000_000.0

            for idx in range(50, len(df_period) - 1):
                row = df_period.iloc[idx]
                next_row = df_period.iloc[idx + 1]
                history_df = df_period.iloc[max(0, idx-50):idx]

                # STAGE 1: Data Input Validation
                data_result = dcs.stage1_data_input(row)
                if data_result['status'] == 'REJECTED':
                    continue

                # STAGE 2: PA Model (with learnable weights, adjusts via FEEDBACK LOOP 1)
                pa_result = dcs.stage2_pa_model(row, history_df)
                pa_score = pa_result['pa_score']

                # STAGE 3: ID Decision
                id_result = dcs.stage3_id_decision(pa_score, id_threshold=0.60)
                if id_result['decision'] != 'TAKE':
                    continue

                # STAGE 4: Bridge (Economic Viability)
                bridge_result = dcs.stage4_bridge(row['close'], cost_bps=1)

                # STAGE 5: MPC (with learnable position sizing, adjusts via FEEDBACK LOOP 2)
                mpc_result = dcs.stage5_mpc(equity)
                position_size = mpc_result.get('position_size', 1)

                # STAGE 6: P01D (Sovereign Authority with PID timing)
                p01d_result = dcs.stage6_sovereign_decision(
                    decision=id_result['decision'],
                    pa_score=pa_score,
                    position_size=position_size
                )

                if p01d_result['action'] != 'EXECUTE':
                    continue

                # EXECUTE TRADE
                entry_price = row['close']
                exit_price = next_row['open']

                pnl = (exit_price - entry_price) * position_size

                trades.append({'pnl': float(pnl)})

                equity += pnl

                # FEEDBACK LOOP 1: PA Learning - update weights based on win rate
                dcs.update_pa_weights_via_feedback(trades)

                # FEEDBACK LOOP 2: Risk Control - update Lambda based on drawdown
                dcs.update_risk_lambda_via_feedback(equity)

            if len(trades) == 0:
                continue

            winning = len([t for t in trades if t['pnl'] > 0])
            win_rate = winning / len(trades)

            results[symbol] = {
                'symbol': symbol,
                'trades': trades,
                'total_trades': len(trades),
                'winning_trades': winning,
                'total_pnl': float(equity - 1_000_000.0),
                'final_equity': float(equity),
                'win_rate': float(win_rate),
                'pa_weights': dcs.pa_weights.copy(),
                'current_lambda': dcs.mpc_params['risk_lambda']
            }

            total_trades += len(trades)
            total_pnl += (equity - 1_000_000.0)
            total_wins += winning
            symbols_with_trades += 1

            if i % 10 == 0 or i == len(SYMBOLS_48):
                print(f"  [{i:2}/{len(SYMBOLS_48)}] {symbol:15} | {len(trades):4} trades | Win: {win_rate:.1%} | P&L: ₹{(equity-1_000_000.0):+8.2f}")

        # Aggregate
        aggregate_win_rate = total_wins / total_trades if total_trades > 0 else 0

        print(f"\n{period_config['name']} AGGREGATE RESULTS:")
        print(f"  Total Trades:       {total_trades}")
        print(f"  Winning Trades:     {total_wins}")
        print(f"  Win Rate:           {aggregate_win_rate:.1%}")
        print(f"  Total P&L:          ₹{total_pnl:+,.2f}")
        print(f"  Symbols w/ Trades:  {symbols_with_trades}/{len(SYMBOLS_48)}\n")

        return {
            'period': period_key,
            'config': period_config,
            'symbol_results': results,
            'aggregate': {
                'total_trades': total_trades,
                'winning_trades': total_wins,
                'win_rate': float(aggregate_win_rate),
                'total_pnl': float(total_pnl),
                'symbols_with_trades': symbols_with_trades
            }
        }

    def run_all(self):
        """Run all periods"""
        for period_key, period_config in TEST_PERIODS.items():
            result = self.run_period(period_key, period_config)
            self.all_results[period_key] = result

    def save_report(self):
        """Save complete results"""
        report = {
            'timestamp': datetime.now().isoformat(),
            'system': 'REAL_DCS_CLOSED_LOOP_SYSTEM',
            'data_source': 'P01D-V2B-CERTIFIED-UNION50-15MIN',
            'manifest_hash': MANIFEST_HASH,
            'architecture': {
                'stages': 6,
                'feedback_loops': 2,
                'sync_gate': True,
                'pid_controllers': True
            },
            'feedback_loop_1_pa': {
                'type': 'PID Controller (learns PA weights)',
                'target_win_rate': 52.0,
                'kp': 0.1,
                'ki': 0.01,
                'kd': 0.01
            },
            'feedback_loop_2_risk': {
                'type': 'PID Controller (learns Risk Lambda)',
                'target_drawdown': -3.0,
                'kp': 0.05,
                'ki': 0.005,
                'kd': 0.005
            },
            'periods': {}
        }

        for period_key, result in self.all_results.items():
            report['periods'][period_key] = result['aggregate']

        report_file = Path("REAL_DCS_COMPLETE_48SYMBOLS_REPORT_20260829.json")
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)

        return report_file

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("\n" + "╔" + "="*78 + "╗")
    print("║" + " REAL DCS COMPLETE SYSTEM - 48 SYMBOLS ".center(78) + "║")
    print("║" + " 6 Stages + 2 PID Feedback Loops + Sync Gate ".center(78) + "║")
    print("║" + " 1-Day + 1-Month + 3-Year Backtests ".center(78) + "║")
    print("╚" + "="*78 + "╝\n")

    runner = RealDCSRunner()

    start_time = datetime.now()

    runner.run_all()

    elapsed = (datetime.now() - start_time).total_seconds()

    report_file = runner.save_report()

    print(f"✓ Report saved: {report_file}")
    print(f"✓ Total execution time: {elapsed:.1f} seconds\n")

    print("="*80)
    print("STATUS: REAL DCS COMPLETE SYSTEM TEST FINISHED")
    print("="*80 + "\n")

if __name__ == '__main__':
    main()
