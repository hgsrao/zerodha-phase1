import numpy as np
import talib

def check_phase_sync(plant_close, grid_close, tolerance=15.0):
    """The core math for the Revision 3 Grid Synchronizer"""
    # TA-Lib needs about 63 bars to warm up the DSP filter
    if len(plant_close) < 63:
        return True, 0.0 
        
    plant_phase = talib.HT_DCPHASE(plant_close)[-1]
    grid_phase = talib.HT_DCPHASE(grid_close)[-1]
    
    delta_phi = abs(plant_phase - grid_phase)
    if delta_phi > 180.0:
        delta_phi = 360.0 - delta_phi
        
    is_sync = delta_phi <= tolerance
    return is_sync, delta_phi

if __name__ == "__main__":
    print("=== REVISION 3 SYNCHROSCOPE FAT ===")
    
    # Create 100 days of artificial market cycle data (Sine waves)
    time = np.linspace(0, 4 * np.pi, 100)
    
    # The Grid (Nifty 50) is our baseline wave
    grid_prices = 100 + 10 * np.sin(time)
    
    # 1. Test In-Phase (Stock is moving with the market)
    print("\n--- Test 1: Plant and Grid in Lockstep ---")
    plant_prices_sync = 50 + 5 * np.sin(time)  # Same timing, different price scale
    
    is_safe, delta = check_phase_sync(plant_prices_sync, grid_prices)
    print(f"Delta Phase (Δϕ): {delta:.2f}°")
    print(f"Breaker Action: {'✅ CLOSE (Trade Allowed)' if is_safe else '❌ TRIP (Trade Blocked)'}")

    # 2. Test Out-of-Phase (Stock is crashing while market rallies)
    # We shift the wave by pi (180 degrees) to simulate opposite momentum
    print("\n--- Test 2: Plant and Grid Anti-Correlated ---")
    plant_prices_out = 50 + 5 * np.sin(time + np.pi) 
    
    is_safe, delta = check_phase_sync(plant_prices_out, grid_prices)
    print(f"Delta Phase (Δϕ): {delta:.2f}°")
    print(f"Breaker Action: {'✅ CLOSE (Trade Allowed)' if is_safe else '❌ TRIP (Trade Blocked)'}")
