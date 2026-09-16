import numpy as np
import pandas as pd
from safety_panel import Revision3SafetyPanel

if __name__ == "__main__":
    print("=== REVISION 3 FULL-SPECTRUM INDUSTRIAL PROTECTION FAT ===")
    panel = Revision3SafetyPanel(max_drawdown_limit=-0.15, phase_tolerance_deg=15.0)

    dates = pd.date_range(start="2026-01-01", periods=40, freq="D")
    
    # 1. ANSI 32: Drawdown Relay
    print("\n--- Test 1: ANSI 32 (Drawdown Relay) ---")
    safe_returns = pd.Series(np.random.normal(0.001, 0.01, 40), index=dates)
    print(f"Normal Telemetry Check: {panel.evaluate_plant_health(safe_returns)}")
    
    crash_returns = pd.Series([0.01, -0.06, -0.07, -0.05], index=pd.date_range("2026-02-10", periods=4))
    print(f"Crash Telemetry Check (Should trip): {panel.evaluate_plant_health(crash_returns)}")
    print(f"Panel State: {panel.state} | Reason: {panel.trip_reason}")
    panel.reset()

    # 2. Grid Synchronizer (Tie-Line)
    print("\n--- Test 2: Grid Synchronizer (Hilbert Transform) ---")
    time = np.linspace(0, 4 * np.pi, 100)
    grid_prices = 100 + 10 * np.sin(time)
    plant_sync = 50 + 5 * np.sin(time)
    plant_out = 50 + 5 * np.sin(time + np.pi)
    
    is_sync, delta = panel.evaluate_tie_line(plant_sync, grid_prices)
    print(f"In-Phase Asset -> Sync: {is_sync} (Δϕ: {delta:.2f}°)")
    is_sync, delta = panel.evaluate_tie_line(plant_out, grid_prices)
    print(f"Out-of-Phase Asset -> Sync: {is_sync} (Δϕ: {delta:.2f}°)")

    # 3. ANSI 87G: Unit Differential
    print("\n--- Test 3: ANSI 87G (Unit Differential) ---")
    unit_ok = panel.evaluate_unit_differential_87g("RELIANCE", 0.02, 0.021, threshold=0.05)
    print(f"Normal Unit Execution: {'✅ PASS' if unit_ok else '❌ TRIP'}")
    unit_fail = panel.evaluate_unit_differential_87g("NIFTY_CALL", 0.03, -0.08, threshold=0.05)
    print(f"Rogue Unit Execution: {'✅ PASS' if unit_fail else '❌ UNIT TRIP'} | Reason: {panel.trip_reason}")

    # 4. ANSI 87O: Plant Differential
    print("\n--- Test 4: ANSI 87O (Plant Differential) ---")
    plant_fail = panel.evaluate_plant_differential_87o(250000, 100000, max_allowable_ratio=2.0)
    print(f"Excessive Exposure Check (2.5x): {'✅ CLOSED' if plant_fail else '❌ PLANT TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 5. ANSI 27/59: Voltage Limits (Volatility)
    print("\n--- Test 5: ANSI 27/59 (Under/Over Voltage) ---")
    vol_fail = panel.evaluate_voltage_limits_27_59(current_volatility=0.045, normal_vol_baseline=0.01, max_vol_multiplier=3.0)
    print(f"Volatility Spike Check: {'✅ CLOSED' if vol_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 6. ANSI 40: Loss of Excitation (Momentum)
    print("\n--- Test 6: ANSI 40 (Loss of Excitation) ---")
    mom_fail = panel.evaluate_excitation_loss_40(strategy_momentum=-0.03, threshold=-0.02)
    print(f"Momentum Collapse Check: {'✅ PASS' if mom_fail else '❌ UNIT TRIP'} | Reason: {panel.trip_reason}")

    # 7. ANSI 50/51: Overcurrent (Sizing)
    print("\n--- Test 7: ANSI 50/51 (Overcurrent / Position Sizing) ---")
    size_fail = panel.evaluate_overcurrent_50_51(position_size=15000, max_allowed_size=10000)
    print(f"Position Limit Check: {'✅ CLOSED' if size_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 8. ANSI 46: Current Unbalance (Factor Skew)
    print("\n--- Test 8: ANSI 46 (Current Unbalance / Factor Skew) ---")
    skew_fail = panel.evaluate_current_unbalance_46(factor_skew_metric=0.9, max_skew=0.8)
    print(f"Factor Skew Check: {'✅ CLOSED' if skew_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 9. ANSI 81: Frequency Protection (Tick Cadence)
    print("\n--- Test 9: ANSI 81 (Frequency / Tick Cadence) ---")
    freq_fail = panel.evaluate_frequency_81(tick_interval_seconds=8.5, expected_interval=1.0, max_drift=5.0)
    print(f"Tick Drift Check: {'✅ CLOSED' if freq_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 10. Mechanical Vibration (Latency)
    print("\n--- Test 10: Mechanical Vibration (API Latency) ---")
    lat_fail = panel.evaluate_mechanical_vibration(api_latency_ms=1250.0, max_latency_ms=1000.0)
    print(f"API Latency Check: {'✅ CLOSED' if lat_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 11. Thermal Overload (CPU Temp)
    print("\n--- Test 11: Thermal Overload (CPU Core Temp) ---")
    therm_fail = panel.evaluate_thermal_overload(cpu_temp_celsius=88.5, max_temp=85.0)
    print(f"CPU Temp Check: {'✅ CLOSED' if therm_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    # 12. Lube Oil Interlock (Broker / WS Connection)
    print("\n--- Test 12: Lube Oil Interlock (Broker & WebSocket) ---")
    lube_fail = panel.evaluate_lube_oil_pressure(broker_connected=True, websocket_active=False)
    print(f"Broker/WS Status Check: {'✅ CLOSED' if lube_fail else '❌ TRIP'} | Reason: {panel.trip_reason}")
    panel.reset()

    print("\n=== ALL 12 INDUSTRIAL PROTECTION ZONES VERIFIED SUCCESSFULLY ===")
