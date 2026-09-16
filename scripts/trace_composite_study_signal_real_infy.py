#!/usr/bin/env python3
"""Real INFY data through CompositeStudySignal -- shows the 4 studies'
PID-adjusted weights actually moving over time, not fixed at 1/4 each
forever. Standalone demo, not wired into anything else.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.composite_study_signal import CompositeStudySignal, STUDY_NAMES

SYMBOL = "INFY"


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv(SYMBOL).head(600).reset_index(drop=True)

    engine = CompositeStudySignal()
    checkpoints = [100, 200, 300, 400, 500, 599]
    for i in range(80, len(frame)):
        result = engine.evaluate(SYMBOL, frame.iloc[:i + 1])
        if i in checkpoints:
            print(f"\n--- bar {i} ({frame.iloc[i]['timestamp']}) ---")
            print(f"  confidence={result['confidence']:.4f}  direction={result['direction']:+d}")
            print(f"  votes:   {result['votes']}")
            print(f"  weights: {({k: round(v, 3) for k, v in result['weights'].items()})}")
            print(f"  hit_rates: {({k: round(v, 3) for k, v in result['hit_rates'].items()})}")


if __name__ == "__main__":
    main()
