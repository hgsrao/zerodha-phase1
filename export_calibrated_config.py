import json

config = {
    "version": "2023.1.0-CALIBRATED",
    "description": "Grid-calibrated parameter profile with 1.0x ATR stop and 2.25R target",
    "symbols": ["TCS", "HDFCBANK", "BAJFINANCE", "LT", "TATAMOTORS", "INFY"],
    "slot_capital": 333333.33,
    "max_concurrent_positions": 3,
    "z_arm_threshold": -2.20,
    "rsi_arm_threshold": 32.0,
    "base_r_target": 2.25,
    "base_z_target": 0.60,
    "stop_atr_multiplier": 1.00,
    "trailing_profit_lock_r": 1.00,
    "trailing_profit_lock_pct": 0.50,
    "min_harvest_r": 0.35,
    "decay_start_time": "12:30:00",
    "entry_cutoff_time": "13:30:00",
    "session_close_time": "15:15:00",
    "ema_trend_filter": {
        "enabled": True,
        "timeframe": "15min",
        "span": 50,
        "max_distance_pct": -0.015
    }
}

with open("config_2023_calibrated.json", "w") as f:
    json.dump(config, f, indent=4)

print("✓ Exported calibrated production config to 'config_2023_calibrated.json'")
