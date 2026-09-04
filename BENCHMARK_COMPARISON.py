#!/usr/bin/env python3
"""
Benchmark comparison: Our engine vs Backtrader
Validates that both produce consistent results on frozen NSE data
"""

import json
import pandas as pd
from pathlib import Path


def compare_results():
    """Compare our results against Backtrader benchmark"""

    print(f"\n{'='*90}")
    print("BENCHMARK COMPARISON: Our Engine vs Backtrader")
    print(f"{'='*90}\n")

    # Load our results
    our_file = Path("TIMESTAMP_ALIGNED_5SYMBOL_RESULTS.json")
    if not our_file.exists():
        print("[ERROR] TIMESTAMP_ALIGNED_5SYMBOL_RESULTS.json not found")
        return

    with open(our_file) as f:
        our_results = json.load(f)

    # Load Backtrader results (when available)
    bt_file = Path(r"C:\Users\Dishan\Documents\Codex\Zerodha_backtrader_benchmark\BACKTRADER_5SYMBOL_RESULTS.json")
    if bt_file.exists():
        with open(bt_file) as f:
            bt_results = json.load(f)
    else:
        bt_results = None
        print("[PENDING] Backtrader results not yet available")

    # Comparison table
    print(f"{'Metric':<30} | {'Our Engine':<20} | {'Backtrader':<20} | {'Match':<10}")
    print("-" * 85)

    # Initial capital
    our_init = our_results.get('initial_capital', 0)
    bt_init = bt_results.get('initial_capital', 0) if bt_results else 0
    match = "[OK]" if our_init == bt_init else "[FAIL]"
    print(f"{'Initial Capital':<30} | {our_init:>18,.0f} | {bt_init:>18,.0f} | {match:<10}")

    # Final equity
    our_final = our_results.get('final_equity', 0)
    bt_final = bt_results.get('final_value', 0) if bt_results else 0
    match = "[OK]" if abs(our_final - bt_final) < 1000 else "[FAIL]"  # Allow ±1000 rounding
    print(f"{'Final Equity':<30} | {our_final:>18,.0f} | {bt_final:>18,.0f} | {match:<10}")

    # Total P&L
    our_pnl = our_results.get('total_pnl', 0)
    bt_pnl = bt_results.get('total_pnl', 0) if bt_results else 0
    match = "[OK]" if abs(our_pnl - bt_pnl) < 1000 else "[FAIL]"
    print(f"{'Total P&L':<30} | {our_pnl:>18,.0f} | {bt_pnl:>18,.0f} | {match:<10}")

    # Return %
    our_ret = our_results.get('return_percent', 0)
    bt_ret = bt_results.get('return_percent', 0) if bt_results else 0
    match = "[OK]" if abs(our_ret - bt_ret) < 0.1 else "[FAIL]"
    print(f"{'Return %':<30} | {our_ret:>17.2f}% | {bt_ret:>17.2f}% | {match:<10}")

    # Total trades
    our_trades = our_results.get('total_trades', 0)
    bt_trades = bt_results.get('total_trades', 0) if bt_results else 0
    match = "[OK]" if our_trades == bt_trades else "[FAIL]"
    print(f"{'Total Trades':<30} | {our_trades:>19} | {bt_trades:>19} | {match:<10}")

    # Max drawdown
    our_dd = our_results.get('max_drawdown', 0)
    bt_dd = bt_results.get('max_drawdown', 0) if bt_results else 0
    match = "[OK]" if abs(our_dd - bt_dd) < 0.5 else "[FAIL]"
    print(f"{'Max Drawdown %':<30} | {our_dd:>18.2f} | {bt_dd:>18.2f} | {match:<10}")

    print("-" * 85)

    # Overall verdict
    if bt_results:
        all_match = (
            abs(our_final - bt_final) < 1000 and
            abs(our_pnl - bt_pnl) < 1000 and
            abs(our_ret - bt_ret) < 0.1 and
            our_trades == bt_trades and
            abs(our_dd - bt_dd) < 0.5
        )

        if all_match:
            print("\n[OK] ENGINES AGREE - Framework is validated [OK]")
            print("Our implementation produces consistent results with Backtrader.")
            print("Data handling, costs, accounting, and timing are correct.")
        else:
            print("\n[DISCREPANCY] Results differ - trade-by-trade analysis needed")
            print("Check:")
            print("  - Cost calculation (buy vs sell, GST, STT)")
            print("  - Entry timing (bar indexing, next-bar rule)")
            print("  - Exit logic (stop loss, profit target)")
            print("  - Portfolio accounting (cash tracking, position values)")

        return {
            'our_results': our_results,
            'backtrader_results': bt_results,
            'match': all_match
        }
    else:
        print("\n[PENDING] Awaiting Backtrader results for full comparison")
        print("Our engine shows:")
        print(f"  - {our_trades} trades")
        print(f"  - {our_ret:.2f}% return")
        print(f"  - {our_dd:.2f}% max drawdown")
        print("\nOnce Backtrader completes, we'll validate these results.")

        return {
            'our_results': our_results,
            'backtrader_results': None,
            'match': None
        }


if __name__ == "__main__":
    comparison = compare_results()
