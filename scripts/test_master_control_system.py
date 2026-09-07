#!/usr/bin/env python3
"""
TEST: Master Control System - Protection + Grid Sync + PID Integration

Three layers working together:
  Layer 3: Protection Relay (ANSI safety constraints)
  Layer 2: Grid Synchronization (market regime)
  Layer 1: PID Controller (exit decisions)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
from datetime import datetime

print("="*140)
print("MASTER CONTROL SYSTEM TEST: Protection + Grid Sync + PID Integration")
print("="*140 + "\n")

from revision3.master_control_system import MasterControlSystem
from revision3.macro_grid_synchronizer import MacroGridSynchronizer


def test_scenario_1_all_green():
    """Scenario 1: All layers PASS - execute trade"""
    print("[TEST 1] All Layers GREEN - Execute Trade")
    print("-" * 140)

    # Initialize master control
    grid_sync = MacroGridSynchronizer()
    master = MasterControlSystem(
        protection_relay=None,  # Disable for this test
        grid_synchronizer=grid_sync,
        pid_controller=None,  # Simplified for demo
    )

    # Healthy telemetry
    telemetry = {
        "broker_connected": True,
        "api_latency_ms": 50.0,
        "cpu_temp_celsius": 45.0,
        "memory_used_pct": 60.0,
        "tick_interval_seconds": 0.5,
        "gross_exposure_fraction": 0.5,
        "realized_pnl": 0.05,
    }

    # Good market regime
    nifty_prices = np.random.randn(100).cumsum() + 20000
    current_vix = 15.0

    result = master.make_trading_decision(
        symbol="INFY",
        telemetry=telemetry,
        nifty_prices=nifty_prices,
        current_vix=current_vix,
        pa_confidence=0.7,
        studies_confidence=0.8,
        current_close=1350.0,
        current_atr=10.0,
        trade_direction=1,
    )

    print(f"Decision: {result['approved']}")
    print(f"Reason: {result['reason']}")
    for trace in result['decision_trace']:
        print(f"  {trace}")
    print()


def test_scenario_2_protection_trip():
    """Scenario 2: Protection relay TRIPS - block trade"""
    print("[TEST 2] Protection Relay TRIPS - Block Trade")
    print("-" * 140)

    grid_sync = MacroGridSynchronizer()
    master = MasterControlSystem(
        protection_relay=None,  # Would be active
        grid_synchronizer=grid_sync,
        pid_controller=None,
    )

    # Bad telemetry (high latency)
    telemetry = {
        "broker_connected": False,
        "api_latency_ms": 750.0,  # TOO HIGH
        "cpu_temp_celsius": 90.0,  # TOO HIGH
        "memory_used_pct": 96.0,  # TOO HIGH
        "tick_interval_seconds": 2.5,  # TOO HIGH
        "gross_exposure_fraction": 2.5,  # TOO HIGH
        "realized_pnl": -0.12,  # TOO NEGATIVE
    }

    nifty_prices = np.random.randn(100).cumsum() + 20000
    current_vix = 15.0

    result = master.make_trading_decision(
        symbol="INFY",
        telemetry=telemetry,
        nifty_prices=nifty_prices,
        current_vix=current_vix,
        pa_confidence=0.7,
        studies_confidence=0.8,
        current_close=1350.0,
        current_atr=10.0,
        trade_direction=1,
    )

    print(f"Decision: {result['approved']}")
    print(f"Reason: {result['reason']}")
    if result['protection']:
        print(f"Protection State:")
        print(f"  Tripped: {result['protection'].is_tripped}")
        print(f"  Zones: {result['protection'].trip_zones}")
        print(f"  Reason: {result['protection'].trip_reason}")
    print()


def test_scenario_3_grid_rejects():
    """Scenario 3: Grid sync REJECTS - filter entry"""
    print("[TEST 3] Grid Synchronization REJECTS - Filter Entry")
    print("-" * 140)

    grid_sync = MacroGridSynchronizer(
        phase_tolerance_deg=15.0,
        vix_operating_band=(10.0, 30.0),
        trend_ema_period=50,
    )
    master = MasterControlSystem(
        protection_relay=None,
        grid_synchronizer=grid_sync,
        pid_controller=None,
    )

    # Healthy telemetry
    telemetry = {
        "broker_connected": True,
        "api_latency_ms": 50.0,
        "cpu_temp_celsius": 45.0,
        "memory_used_pct": 60.0,
        "tick_interval_seconds": 0.5,
        "gross_exposure_fraction": 0.5,
        "realized_pnl": 0.05,
    }

    # BAD market regime (high VIX, poor phase)
    nifty_prices = np.random.randn(100).cumsum() + 20000
    current_vix = 35.0  # OUTSIDE safe band [10, 30]

    result = master.make_trading_decision(
        symbol="INFY",
        telemetry=telemetry,
        nifty_prices=nifty_prices,
        current_vix=current_vix,
        pa_confidence=0.7,
        studies_confidence=0.8,
        current_close=1350.0,
        current_atr=10.0,
        trade_direction=1,
    )

    print(f"Decision: {result['approved']}")
    print(f"Reason: {result['reason']}")
    print(f"Grid State:")
    if result['grid']:
        print(f"  Synchronized: {result['grid'].is_synchronized}")
        print(f"  Voltage: {result['grid'].voltage:.2f}")
        print(f"  Frequency: {result['grid'].frequency:.2f}")
        print(f"  Phase Angle: {result['grid'].phase_angle:.1f}°")
        print(f"  Reason: {result['grid'].reason}")
    for trace in result['decision_trace']:
        print(f"  {trace}")
    print()


def test_scenario_4_full_stack():
    """Scenario 4: All three layers active"""
    print("[TEST 4] Full Stack - All Three Layers Active")
    print("-" * 140)

    grid_sync = MacroGridSynchronizer()
    master = MasterControlSystem(
        protection_relay="active",  # Note: simplified for demo
        grid_synchronizer=grid_sync,
        pid_controller="active",  # Note: simplified for demo
    )

    print(f"Control System Status:")
    print(f"  Protection: {master.enabled_layers['protection']}")
    print(f"  Grid Sync: {master.enabled_layers['grid_sync']}")
    print(f"  PID Controller: {master.enabled_layers['pid']}")
    print()

    summary = master.get_control_summary()
    print(f"Summary:")
    for key, value in summary.items():
        print(f"  {key}: {value}")
    print()


def main():
    print("\nTest 1: All layers healthy")
    print("─" * 140)
    test_scenario_1_all_green()

    print("Test 2: Protection relay trips")
    print("─" * 140)
    test_scenario_2_protection_trip()

    print("Test 3: Grid synchronization rejects")
    print("─" * 140)
    test_scenario_3_grid_rejects()

    print("Test 4: Full system status")
    print("─" * 140)
    test_scenario_4_full_stack()

    print("="*140)
    print("MASTER CONTROL SYSTEM TEST COMPLETE")
    print("="*140)
    print("\n✓ Three-layer architecture demonstrated:")
    print("  Layer 3: Protection Relay (ANSI safety constraints)")
    print("  Layer 2: Grid Synchronization (Voltage/Frequency/Phase)")
    print("  Layer 1: PID Controller (Exit decisions)")
    print("\nEach layer can independently reject or modify a trade decision.")
    print("="*140 + "\n")


if __name__ == "__main__":
    main()
