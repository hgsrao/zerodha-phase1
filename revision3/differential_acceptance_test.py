import numpy as np
import pandas as pd
from safety_panel import Revision3SafetyPanel

if __name__ == "__main__":
    print("=== REVISION 3 FULL SAFETY PANEL & DIFFERENTIAL FAT ===")
    panel = Revision3SafetyPanel(max_drawdown_limit=-0.15, phase_tolerance_deg=15.0)

    # Create proper DatetimeIndex for QuantStats compatibility
    dates_normal = pd.date_range(start="2026-01-01", periods=40, freq="D")
    dates_crash = pd.date_range(start="2026-02-10", periods=4, freq="D")

    # 1. Test ANSI 32: Drawdown Relay
    print("\n--- Test 1: ANSI 32 Drawdown Relay ---")
    safe_returns = pd.Series(np.random.normal(0.001, 0.01, 40), index=dates_normal)
    print(f"Normal Telemetry Check: {panel.evaluate_plant_health(safe_returns)}")
    
    crash_returns = pd.Series([0.01, -0.06, -0.07, -0.05], index=dates_crash)
    print(f"Crash Telemetry Check (Should trip): {panel.evaluate_plant_health(crash_returns)}")
    print(f"Panel State after crash: {panel.state} | Reason: {panel.trip_reason}")

    # Reset panel for next test
    panel.reset()
    print(f"Panel Reset State: {panel.state}")

    # 2. Test Grid Phase Sync (Hilbert Transform)
    print("\n--- Test 2: Grid Phase Synchronization (Tie-Line) ---")
    time = np.linspace(0, 4 * np.pi, 100)
    grid_prices = 100 + 10 * np.sin(time)
    
    # In-phase asset
    plant_sync = 50 + 5 * np.sin(time)
    is_sync, delta = panel.evaluate_tie_line(plant_sync, grid_prices)
    print(f"In-Phase Asset -> Sync: {is_sync} (Δϕ: {delta:.2f}°)")

    # Out-of-phase asset
    plant_out = 50 + 5 * np.sin(time + np.pi)
    is_sync, delta = panel.evaluate_tie_line(plant_out, grid_prices)
    print(f"Out-of-Phase Asset -> Sync: {is_sync} (Δϕ: {delta:.2f}°)")

    # 3. Test ANSI 87G: Unit Differential (Asset-Level)
    print("\n--- Test 3: ANSI 87G Unit Differential (Asset Isolation) ---")
    unit_ok = panel.evaluate_unit_differential_87g("RELIANCE", model_expected_return=0.02, actual_realized_return=0.021, threshold=0.05)
    print(f"Normal Unit Execution (RELIANCE): {'✅ PASS' if unit_ok else '❌ UNIT TRIP'}")

    unit_fail = panel.evaluate_unit_differential_87g("NIFTY_CALL", model_expected_return=0.03, actual_realized_return=-0.08, threshold=0.05)
    print(f"Rogue Unit Execution (NIFTY_CALL): {'✅ PASS' if unit_fail else '❌ UNIT TRIP'} | Reason: {panel.trip_reason}")

    # 4. Test ANSI 87O: Overall Plant Differential (Facility-Level)
    print("\n--- Test 4: ANSI 87O Overall Plant Differential ---")
    plant_ok = panel.evaluate_plant_differential_87o(total_gross_exposure=150000, total_capital_base=100000, max_allowable_ratio=2.0)
    print(f"Normal Plant Balance Check (1.5x ratio): {'✅ CLOSED' if plant_ok else '❌ PLANT TRIP'}")

    plant_fail = panel.evaluate_plant_differential_87o(total_gross_exposure=250000, total_capital_base=100000, max_allowable_ratio=2.0)
    print(f"Excessive Exposure Check (2.5x ratio): {'✅ CLOSED' if plant_fail else '❌ PLANT TRIP'}")
    print(f"Final Master Panel State: {panel.state} | Master Fault: {panel.trip_reason}")
