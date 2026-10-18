#!/usr/bin/env python3
"""Screens all 48 symbols for real ATR-based target-distance headroom
above the Rs 5 profit floor (minimum_absolute_profit_rupees/10), using the
SAME talib.ATR(14) calculation Box 4 (TA-Lib PA) actually runs -- not a
proxy metric. Ranks by how often raw_target_distance = ATR * 1.65 clears
the floor with real margin to survive Box 6's exit_tightness shrink
(which can reduce it as much as 50%), based on the last ~15,000 bars of
each symbol's real data.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import talib

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest

PROFIT_MULT = 1.5
MARGIN_BUFFER = 0.1
FLOOR = 5.0
TAIL_BARS = 15000


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)

    results = []
    t0 = time.time()
    for f in manifest.files:
        symbol = f.symbol
        frame = loader._load_symbol_csv(symbol)
        if frame is None or len(frame) < 100:
            continue
        frame = frame.tail(TAIL_BARS).reset_index(drop=True)
        high = frame["high"].to_numpy(dtype=float)
        low = frame["low"].to_numpy(dtype=float)
        close = frame["close"].to_numpy(dtype=float)
        atr = talib.ATR(high, low, close, timeperiod=14)
        atr = atr[~np.isnan(atr)]
        raw_target_distance = atr * PROFIT_MULT * (1 + MARGIN_BUFFER)
        # "comfortable" margin: survives even the WORST-case 50% tightness
        # shrink (exit_tightness floors at 0.5) and still clears the floor.
        worst_case_after_tightness = raw_target_distance * 0.5
        results.append({
            "symbol": symbol,
            "median_price": float(np.median(close)),
            "median_atr": float(np.median(atr)),
            "median_raw_target_distance": float(np.median(raw_target_distance)),
            "pct_bars_clear_floor_raw": float(np.mean(raw_target_distance >= FLOOR) * 100),
            "pct_bars_clear_floor_worst_case_tightness": float(np.mean(worst_case_after_tightness >= FLOOR) * 100),
        })

    results.sort(key=lambda r: r["pct_bars_clear_floor_worst_case_tightness"], reverse=True)
    elapsed = time.time() - t0

    print(f"Screened {len(results)} symbols in {elapsed:.1f}s\n")
    print(f"{'Symbol':<14}{'MedianPrice':>12}{'MedianATR':>11}{'RawTargetDist':>15}{'%Clear(raw)':>13}{'%Clear(worst-case)':>20}")
    for r in results:
        print(f"{r['symbol']:<14}{r['median_price']:>12.1f}{r['median_atr']:>11.3f}"
              f"{r['median_raw_target_distance']:>15.3f}{r['pct_bars_clear_floor_raw']:>13.1f}"
              f"{r['pct_bars_clear_floor_worst_case_tightness']:>20.1f}")


if __name__ == "__main__":
    main()
