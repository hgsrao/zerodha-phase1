#!/usr/bin/env python3
"""
Master startup script for Zerodha paper trading system
Launches all components: Kite adapter → Trading engine → Monitoring
"""

import subprocess
import time
import sys
import os
from pathlib import Path

def check_credentials():
    """Verify Zerodha credentials are set"""
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key:
        print("❌ ERROR: KITE_API_KEY not set")
        return False
    if not access_token:
        print("❌ ERROR: KITE_ACCESS_TOKEN not set")
        print("\nTo set credentials (PowerShell):")
        print("  $env:KITE_API_KEY = 'your_api_key'")
        print("  $env:KITE_ACCESS_TOKEN = 'your_access_token'")
        return False

    print(f"✓ Credentials verified (API Key: {api_key[:10]}...)")
    return True

def check_dependencies():
    """Verify required libraries"""
    try:
        import kiteconnect
        print("✓ kiteconnect installed")
        return True
    except ImportError:
        print("❌ ERROR: kiteconnect not installed")
        print("\nInstall with: pip install kiteconnect")
        return False

def start_trading_engine():
    """Start paper trading engine"""
    print("\n[1/2] Starting paper trading engine...")
    try:
        proc = subprocess.Popen([sys.executable, 'paper_trading_engine.py'])
        print(f"✓ Paper trading engine started (PID: {proc.pid})")
        return proc
    except Exception as e:
        print(f"❌ Failed to start trading engine: {e}")
        return None

def start_monitoring():
    """Start monitoring dashboard"""
    print("\n[2/2] Starting monitoring dashboard...")
    try:
        if os.name == 'nt':  # Windows
            proc = subprocess.Popen([sys.executable, 'monitoring_dashboard.py'])
        else:  # Unix
            proc = subprocess.Popen([sys.executable, 'monitoring_dashboard.py'])
        print(f"✓ Monitoring dashboard started (PID: {proc.pid})")
        return proc
    except Exception as e:
        print(f"❌ Failed to start monitoring: {e}")
        return None

def main():
    """Main startup sequence"""
    print("="*80)
    print("🚀 ZERODHA PAPER TRADING - SYSTEM STARTUP")
    print("="*80)

    # Verify credentials
    print("\n[Setup] Checking credentials...")
    if not check_credentials():
        sys.exit(1)

    # Check dependencies
    print("[Setup] Checking dependencies...")
    if not check_dependencies():
        sys.exit(1)

    # Start processes
    try:
        print("\n[Startup] Initializing components...")
        engine_proc = start_trading_engine()
        time.sleep(2)

        monitor_proc = start_monitoring()

        print("\n" + "="*80)
        print("✓ SYSTEM READY FOR PAPER TRADING")
        print("="*80)
        print("\nOutput files:")
        print("  - PAPER_EXECUTION_LOG.json : All executed orders")
        print("  - PAPER_SIGNALS_LOG.json   : All generated signals")
        print("  - PAPER_TRADE_LOG.jsonl    : Detailed trade log")
        print("\nMonitoring active - Press Ctrl+C in dashboard to stop")
        print("="*80 + "\n")

        # Keep processes alive
        while True:
            time.sleep(1)
            if engine_proc and engine_proc.poll() is not None:
                print("❌ Trading engine stopped unexpectedly!")
                break
            if monitor_proc and monitor_proc.poll() is not None:
                print("Dashboard stopped - Run START_PAPER_TRADING.py again to restart")
                break

    except KeyboardInterrupt:
        print("\n\n[Shutdown] Stopping system...")
        if engine_proc:
            engine_proc.terminate()
            print("✓ Trading engine stopped")
        if monitor_proc:
            monitor_proc.terminate()
            print("✓ Monitoring stopped")
        print("✓ System stopped\n")

if __name__ == "__main__":
    main()
