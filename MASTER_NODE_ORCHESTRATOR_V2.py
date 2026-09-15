#!/usr/bin/env python3
"""
MASTER NODE ORCHESTRATOR V2
Coordinates deterministic distributed backtest across worker nodes

Status: OFFLINE RESEARCH ONLY (not for production)
"""

import requests
import json
import time
from datetime import datetime
from pathlib import Path

# ============================================================================
# CONFIGURATION
# ============================================================================

MASTER_IP = '192.168.0.47'
MASTER_PORT = 5000

WORKER_NODES = [
    {
        'name': 'Research-Worker-1',
        'ip': '192.168.0.17',
        'port': 5000,
        'status': 'pending'
    }
]

# All 48 NIFTY50 certified symbols (from P02 study)
NIFTY50_SYMBOLS = [
    'ADANIENT', 'ADANIPORTS', 'APOLLOHOSP', 'ASIANPAINT', 'AXISBANK',
    'BAJAJ-AUTO', 'BAJAJFINSV', 'BAJFINANCE', 'BHARTIARTL', 'BPCL',
    'BRITANNIA', 'CIPLA', 'COALINDIA', 'DIVISLAB', 'DRREDDY',
    'EICHERMOT', 'GRASIM', 'HCLTECH', 'HDFCBANK', 'HDFCLIFE',
    'HEROMOTOCO', 'HINDALCO', 'HINDUNILVR', 'ICICIBANK', 'INDUSINDBK',
    'INFY', 'ITC', 'JSWSTEEL', 'KOTAKBANK', 'LT',
    'M&M', 'MARUTI', 'NESTLEIND', 'NTPC', 'ONGC',
    'POWERGRID', 'RELIANCE', 'SBILIFE', 'SBIN', 'SUNPHARMA',
    'TATACONSUM', 'TATASTEEL', 'TCS', 'TECHM', 'TITAN',
    'ULTRACEMCO', 'UPL', 'WIPRO'
]

# ============================================================================
# COLORS
# ============================================================================

class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    YELLOW = '\033[93m'
    RED = '\033[91m'
    BOLD = '\033[1m'
    RESET = '\033[0m'

# ============================================================================
# FUNCTIONS
# ============================================================================

def print_header():
    print(f"\n{Colors.BOLD}{Colors.CYAN}")
    print("╔" + "═"*100 + "╗")
    print("║" + " MASTER NODE ORCHESTRATOR V2 - DETERMINISTIC RESEARCH BACKTEST ".center(100) + "║")
    print("║" + f" Status: OFFLINE RESEARCH ONLY | Mode: DETERMINISTIC & REPRODUCIBLE ".center(100) + "║")
    print("╚" + "═"*100 + "╝")
    print(f"{Colors.RESET}\n")


def check_worker_health():
    """Verify worker nodes are online and certified"""

    print(f"{Colors.BOLD}{Colors.YELLOW}[MASTER] Checking worker health...{Colors.RESET}\n")

    healthy_count = 0
    for node in WORKER_NODES:
        try:
            url = f"http://{node['ip']}:{node['port']}/health"
            response = requests.get(url, timeout=5)

            if response.status_code == 200:
                health_data = response.json()
                mode = health_data.get('mode', 'unknown')
                available_symbols = health_data.get('certified_symbols_available', 0)
                required_symbols = health_data.get('certified_symbols_required', 0)

                print(f"{Colors.GREEN}[MASTER] ✓ {node['name']:25} HEALTHY{Colors.RESET}")
                print(f"         Mode: {mode}")
                print(f"         Symbols: {available_symbols}/{required_symbols}")

                if available_symbols < required_symbols:
                    print(f"{Colors.RED}         WARNING: Only {available_symbols}/{required_symbols} symbols available{Colors.RESET}")
                    node['status'] = 'incomplete_data'
                else:
                    node['status'] = 'healthy'
                    healthy_count += 1
            else:
                print(f"{Colors.RED}[MASTER] ✗ {node['name']:25} returned {response.status_code}{Colors.RESET}")
                node['status'] = 'unhealthy'

        except Exception as e:
            print(f"{Colors.RED}[MASTER] ✗ {node['name']:25} {str(e)[:40]}{Colors.RESET}")
            node['status'] = 'offline'

    print()

    if healthy_count == 0:
        print(f"{Colors.RED}{Colors.BOLD}[MASTER] ERROR: No healthy workers!{Colors.RESET}")
        return False

    return True


def submit_to_worker(worker_ip, worker_port, symbols, epoch='VALIDATION'):
    """Submit backtest request to worker"""

    url = f"http://{worker_ip}:{worker_port}/backtest"

    try:
        print(f"{Colors.CYAN}[MASTER] → Submitting {len(symbols)} symbols (epoch: {epoch})...{Colors.RESET}")

        response = requests.post(
            url,
            json={'symbols': symbols, 'epoch': epoch},
            timeout=600
        )

        if response.status_code == 200:
            data = response.json()
            results = data.get('results', [])
            agg = data.get('aggregate_stats', {})

            print(f"{Colors.GREEN}[MASTER] ✓ Received results{Colors.RESET}")
            print(f"         Trades: {agg.get('total_trades')}")
            print(f"         Win Rate: {agg.get('aggregate_win_rate')*100:.1f}%")
            print(f"         Total P&L: ₹{agg.get('total_pnl', 0):.2f}")

            return data
        else:
            print(f"{Colors.RED}[MASTER] ✗ Worker error: {response.status_code}{Colors.RESET}")
            return None

    except Exception as e:
        print(f"{Colors.RED}[MASTER] ✗ Connection failed: {str(e)[:60]}{Colors.RESET}")
        return None


def save_results(backtest_data, epoch, run_number):
    """Save results with timestamp"""

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = f'DETERMINISTIC_BACKTEST_{epoch}_RUN{run_number}_{timestamp}.json'

    with open(filename, 'w') as f:
        json.dump(backtest_data, f, indent=2)

    return filename


def compare_runs(run1, run2):
    """Compare two backtest runs for reproducibility"""

    print(f"\n{Colors.BOLD}{Colors.YELLOW}[VERIFY] Comparing two runs...{Colors.RESET}\n")

    agg1 = run1.get('aggregate_stats', {})
    agg2 = run2.get('aggregate_stats', {})

    matches = []
    diffs = []

    # Compare aggregate metrics
    if agg1.get('total_trades') == agg2.get('total_trades'):
        matches.append(f"✓ Total trades match: {agg1.get('total_trades')}")
    else:
        diffs.append(f"✗ Total trades differ: {agg1.get('total_trades')} vs {agg2.get('total_trades')}")

    if agg1.get('aggregate_win_rate') == agg2.get('aggregate_win_rate'):
        matches.append(f"✓ Win rate matches: {agg1.get('aggregate_win_rate')*100:.1f}%")
    else:
        diffs.append(f"✗ Win rate differs: {agg1.get('aggregate_win_rate')*100:.1f}% vs {agg2.get('aggregate_win_rate')*100:.1f}%")

    if agg1.get('total_pnl') == agg2.get('total_pnl'):
        matches.append(f"✓ Total P&L matches: ₹{agg1.get('total_pnl'):.2f}")
    else:
        diffs.append(f"✗ Total P&L differs: ₹{agg1.get('total_pnl'):.2f} vs ₹{agg2.get('total_pnl'):.2f}")

    # Compare symbol results
    results1 = {r['symbol']: r for r in run1.get('results', []) if r.get('status') == 'completed'}
    results2 = {r['symbol']: r for r in run2.get('results', []) if r.get('status') == 'completed'}

    for symbol in results1.keys():
        if symbol in results2:
            r1 = results1[symbol]
            r2 = results2[symbol]

            if r1.get('file_hash') != r2.get('file_hash'):
                diffs.append(f"✗ {symbol}: file hash differs")
            elif r1.get('total_trades') != r2.get('total_trades'):
                diffs.append(f"✗ {symbol}: trades differ ({r1.get('total_trades')} vs {r2.get('total_trades')})")
            elif r1.get('win_rate') != r2.get('win_rate'):
                diffs.append(f"✗ {symbol}: win rate differs")

    print(f"{Colors.GREEN}Matches:{Colors.RESET}")
    for m in matches:
        print(f"  {m}")

    if diffs:
        print(f"\n{Colors.RED}Differences:{Colors.RESET}")
        for d in diffs:
            print(f"  {d}")
        return False
    else:
        print(f"\n{Colors.BOLD}{Colors.GREEN}✓ REPRODUCIBILITY VERIFIED: All runs identical{Colors.RESET}\n")
        return True


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Main orchestration flow"""

    print_header()

    print(f"{Colors.BOLD}{Colors.YELLOW}[MASTER] Configuration:{Colors.RESET}")
    print(f"  Status: OFFLINE RESEARCH ONLY")
    print(f"  Symbols: {len(NIFTY50_SYMBOLS)}")
    print(f"  Mode: Deterministic (reproducible every run)")
    print(f"  Workers: {len(WORKER_NODES)}")
    print("\n" + "="*100 + "\n")

    # Health check
    if not check_worker_health():
        print(f"\n{Colors.RED}CANNOT PROCEED: No healthy workers{Colors.RESET}\n")
        return

    print("="*100 + "\n")

    # Select epoch
    epoch = 'VALIDATION'  # Default: validation set
    print(f"{Colors.BOLD}{Colors.YELLOW}[MASTER] Using epoch: {epoch}{Colors.RESET}\n")
    print(f"  Date range: 2025-02-25 to 2026-02-24")
    print(f"  Purpose: Model evaluation (held-out during training)\n")

    # Run 1
    print("="*100)
    print(f"{Colors.BOLD}{Colors.CYAN}[MASTER] BACKTEST RUN #1{Colors.RESET}")
    print("="*100 + "\n")

    run1_data = submit_to_worker(
        WORKER_NODES[0]['ip'],
        WORKER_NODES[0]['port'],
        NIFTY50_SYMBOLS,
        epoch=epoch
    )

    if not run1_data:
        print(f"\n{Colors.RED}Run #1 failed{Colors.RESET}\n")
        return

    run1_file = save_results(run1_data, epoch, 1)
    print(f"{Colors.GREEN}✓ Run #1 saved: {run1_file}{Colors.RESET}")

    # Run 2 (for reproducibility verification)
    print("\n" + "="*100)
    print(f"{Colors.BOLD}{Colors.CYAN}[MASTER] BACKTEST RUN #2 (Reproducibility Check){Colors.RESET}")
    print("="*100 + "\n")

    run2_data = submit_to_worker(
        WORKER_NODES[0]['ip'],
        WORKER_NODES[0]['port'],
        NIFTY50_SYMBOLS,
        epoch=epoch
    )

    if not run2_data:
        print(f"\n{Colors.RED}Run #2 failed{Colors.RESET}\n")
        return

    run2_file = save_results(run2_data, epoch, 2)
    print(f"{Colors.GREEN}✓ Run #2 saved: {run2_file}{Colors.RESET}")

    # Compare
    print("\n" + "="*100)
    reproducible = compare_runs(run1_data, run2_data)

    # Summary
    print("\n" + "="*100)
    print(f"{Colors.BOLD}{Colors.GREEN}")
    print("║" + " BACKTEST COMPLETE ".center(98) + "║")
    print("╚" + "═"*98 + "╝")
    print(f"{Colors.RESET}\n")

    agg = run1_data.get('aggregate_stats', {})
    print(f"{Colors.BOLD}Results:{Colors.RESET}")
    print(f"  Total Trades: {agg.get('total_trades')}")
    print(f"  Winning Trades: {agg.get('winning_trades')}")
    print(f"  Win Rate: {agg.get('aggregate_win_rate')*100:.1f}%")
    print(f"  Total P&L: ₹{agg.get('total_pnl', 0):.2f}")
    print(f"  Symbols Tested: {run1_data.get('symbols_processed')}/{run1_data.get('symbols_requested')}")

    print(f"\n{Colors.BOLD}Reproducibility:{Colors.RESET}")
    if reproducible:
        print(f"  {Colors.GREEN}✓ VERIFIED{Colors.RESET} (both runs identical)")
    else:
        print(f"  {Colors.RED}✗ FAILED{Colors.RESET} (runs differ - investigate)")

    print(f"\n{Colors.BOLD}Output Files:{Colors.RESET}")
    print(f"  Run #1: {run1_file}")
    print(f"  Run #2: {run2_file}")

    print(f"\n{Colors.BOLD}{Colors.YELLOW}Important:{Colors.RESET}")
    print(f"  This is OFFLINE RESEARCH ONLY")
    print(f"  Do NOT use for trading until externally validated")
    print(f"  Results are deterministic and reproducible")
    print("\n")


if __name__ == '__main__':
    main()
