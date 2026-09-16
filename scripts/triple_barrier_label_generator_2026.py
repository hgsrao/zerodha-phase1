#!/usr/bin/env python3
"""
Triple-Barrier Label Generator: Temporal Alignment for 15-Minute Structural Features
======================================================================================

Core Thesis:
- 15-minute VWAP, efficiency, and volatility features predict mean-reversion/momentum
  that unfolds over 15-45 minutes, NOT 60 seconds
- 1-minute labels require a 45-bar (45-minute) holding window
- Triple-barrier with asymmetric geometry ensures cost survival

Barrier Configuration:
- Holding Period: 45 bars (45 minutes)
- Profit Target: +1.5×ATR(14)
- Stop Loss: -1.0×ATR(14)
- Entry: Open of bar t+1 (removes hindsight bias)
- ATR Lookback: 14 bars (1-minute bars)
- Output: 4-class (TARGET_FIRST, STOP_FIRST, TIMEOUT, INTRABAR_ORDER_UNKNOWN)
- Model Binary: 1=TARGET_FIRST, 0=all others
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
import logging
from datetime import datetime
from typing import Dict, Tuple

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('triple_barrier_label_generator_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TripleBarrierLabelGenerator:
    """Generate labels using triple-barrier method with temporal alignment."""

    def __init__(self,
                 feature_dir: str = "revision2/features_mtf_2026",
                 output_dir: str = "revision2/labels_triple_barrier_2026",
                 holding_bars: int = 45,
                 atr_lookback: int = 14,
                 target_atr_multiple: float = 1.5,
                 stop_atr_multiple: float = 1.0):
        """Initialize triple-barrier label generator."""
        self.feature_dir = Path(feature_dir)
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)

        self.holding_bars = holding_bars
        self.atr_lookback = atr_lookback
        self.target_atr_multiple = target_atr_multiple
        self.stop_atr_multiple = stop_atr_multiple

        logger.info("✅ TripleBarrierLabelGenerator initialized")
        logger.info(f"   Holding Period: {holding_bars} bars ({holding_bars} minutes)")
        logger.info(f"   Profit Target: +{target_atr_multiple}×ATR({atr_lookback})")
        logger.info(f"   Stop Loss: -{stop_atr_multiple}×ATR({atr_lookback})")
        logger.info(f"   Entry: Open of bar t+1")

    def calculate_atr(self, df: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate Average True Range."""
        high = df['high']
        low = df['low']
        close = df['close']

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=1).mean()

        return atr

    def generate_triple_barrier_labels_for_symbol(self, symbol_path: Path) -> Tuple[pd.Series, Dict]:
        """
        Generate triple-barrier labels for one symbol.

        Returns:
            (binary_labels_series, stats) - Series with binary labels, statistics
        """
        # Load data
        df = pd.read_parquet(symbol_path).reset_index(drop=True)

        if len(df) < self.holding_bars + 1:
            logger.warning(f"  {symbol_path.stem}: insufficient data ({len(df)} < {self.holding_bars + 1})")
            return None, None

        # Calculate ATR
        atr = self.calculate_atr(df, self.atr_lookback)

        # Initialize labels (0=not labeled, 1=target_first, 2=stop_first, 3=timeout)
        multiclass_labels = np.zeros(len(df), dtype=int)
        binary_labels = np.full(len(df), np.nan)

        symbol = symbol_path.stem.replace('_mtf_2026', '')

        # Triple-barrier logic
        # For each bar t, we enter at open of bar t+1, hold for 45 bars
        for t in range(len(df) - self.holding_bars - 1):
            # Entry: open of bar t+1
            entry_bar = t + 1
            entry_px = df.loc[entry_bar, 'open']

            if pd.isna(entry_px) or pd.isna(atr.iloc[t]):
                continue

            # Barriers
            atr_val = atr.iloc[t]
            target_px = entry_px + (self.target_atr_multiple * atr_val)
            stop_px = entry_px - (self.stop_atr_multiple * atr_val)

            # Holding window: bars t+1 to t+1+holding_bars
            holding_start = entry_bar
            holding_end = min(entry_bar + self.holding_bars, len(df) - 1)

            # Check which barrier is hit first
            exit_state = 3  # Default: TIMEOUT
            exit_idx = holding_end

            for idx in range(holding_start, holding_end + 1):
                bar_high = df.loc[idx, 'high']
                bar_low = df.loc[idx, 'low']

                # Check profit target (high crosses target)
                if not pd.isna(bar_high) and bar_high >= target_px:
                    exit_state = 1  # TARGET_FIRST
                    exit_idx = idx
                    break

                # Check stop loss (low crosses stop)
                if not pd.isna(bar_low) and bar_low <= stop_px:
                    exit_state = 2  # STOP_FIRST
                    exit_idx = idx
                    break

            # Assign label at entry bar (t+1)
            # 1 = TARGET_FIRST (binary positive), 0 = all others
            multiclass_labels[entry_bar] = exit_state
            binary_labels[entry_bar] = 1.0 if exit_state == 1 else 0.0

        # Statistics
        valid_mask = ~np.isnan(binary_labels)
        if valid_mask.sum() == 0:
            stats = {
                'symbol': symbol,
                'total_bars': len(df),
                'labeled_bars': 0,
                'target_first': 0,
                'stop_first': 0,
                'timeout': 0,
                'target_first_rate': 0,
                'binary_positive_rate': 0
            }
        else:
            binary_valid = binary_labels[valid_mask]
            multiclass_valid = multiclass_labels[valid_mask]

            target_first_count = np.sum(multiclass_valid == 1)
            stop_first_count = np.sum(multiclass_valid == 2)
            timeout_count = np.sum(multiclass_valid == 3)

            stats = {
                'symbol': symbol,
                'total_bars': len(df),
                'labeled_bars': int(valid_mask.sum()),
                'target_first': int(target_first_count),
                'stop_first': int(stop_first_count),
                'timeout': int(timeout_count),
                'target_first_rate': float(target_first_count / valid_mask.sum() * 100),
                'binary_positive_rate': float(np.nanmean(binary_valid) * 100),
            }

        return pd.Series(binary_labels), stats

    def run_train_split(self) -> Dict:
        """Generate triple-barrier labels for TRAIN split (all 48 symbols)."""
        logger.info("\n" + "="*80)
        logger.info("TRIPLE-BARRIER LABEL GENERATION - TRAIN SPLIT (45-BAR HORIZON)")
        logger.info("="*80 + "\n")

        all_stats = []
        all_binary = []

        feature_files = sorted(self.feature_dir.glob("*_mtf_2026.parquet"))
        logger.info(f"Processing {len(feature_files)} symbols...\n")

        for idx, symbol_file in enumerate(feature_files, 1):
            symbol = symbol_file.stem.replace('_mtf_2026', '')

            try:
                binary_series, stats = self.generate_triple_barrier_labels_for_symbol(symbol_file)

                if binary_series is None:
                    logger.warning(f"[{idx}/{len(feature_files)}] {symbol:15s} - Skipped (insufficient data)")
                    continue

                all_binary.append(binary_series)
                all_stats.append(stats)

                logger.info(f"[{idx}/{len(feature_files)}] {symbol:15s} ✅ "
                           f"({stats['labeled_bars']:6d} bars, {stats['target_first_rate']:5.2f}% target-first)")

            except Exception as e:
                logger.error(f"[{idx}/{len(feature_files)}] {symbol:15s} - ERROR: {str(e)[:60]}")

        # Aggregate
        logger.info("\n" + "="*80)
        logger.info("AGGREGATE STATISTICS")
        logger.info("="*80 + "\n")

        total_labeled = sum(s['labeled_bars'] for s in all_stats)
        total_targets = sum(s['target_first'] for s in all_stats)
        total_stops = sum(s['stop_first'] for s in all_stats)
        total_timeouts = sum(s['timeout'] for s in all_stats)

        target_first_rate = total_targets / total_labeled * 100 if total_labeled > 0 else 0
        stop_rate = total_stops / total_labeled * 100 if total_labeled > 0 else 0
        timeout_rate = total_timeouts / total_labeled * 100 if total_labeled > 0 else 0

        logger.info(f"Total labeled bars: {total_labeled:,}")
        logger.info(f"  TARGET_FIRST: {total_targets:,} ({target_first_rate:.2f}%)")
        logger.info(f"  STOP_FIRST:   {total_stops:,} ({stop_rate:.2f}%)")
        logger.info(f"  TIMEOUT:      {total_timeouts:,} ({timeout_rate:.2f}%)")

        # Save labels
        logger.info(f"\nSaving labels...")

        # Binary
        if len(all_binary) == 0:
            logger.error("No labels to save!")
            return None

        combined_binary = pd.concat(all_binary, axis=0, ignore_index=True)
        binary_path = self.output_dir / "train_labels.parquet"
        combined_binary.to_frame(name="label").to_parquet(binary_path)
        logger.info(f"  ✅ Labels: {binary_path}")

        # Metadata
        metadata = {
            "generator_config": {
                "holding_bars": self.holding_bars,
                "atr_lookback": self.atr_lookback,
                "target_atr_multiple": self.target_atr_multiple,
                "stop_atr_multiple": self.stop_atr_multiple,
                "entry_mechanics": "Open of bar t+1",
            },
            "aggregate_stats": {
                "total_labeled_bars": int(total_labeled),
                "target_first_count": int(total_targets),
                "target_first_rate_pct": float(target_first_rate),
                "stop_first_count": int(total_stops),
                "timeout_count": int(total_timeouts),
                "binary_positive_rate_pct": float(np.nanmean(combined_binary) * 100),
            },
            "symbol_stats": all_stats,
            "generated_at": datetime.utcnow().isoformat()
        }

        metadata_path = self.output_dir / "label_generation_metadata.json"
        with open(metadata_path, 'w') as f:
            json.dump(metadata, f, indent=2, default=str)
        logger.info(f"  ✅ Metadata: {metadata_path}")

        return metadata


if __name__ == "__main__":
    generator = TripleBarrierLabelGenerator(
        holding_bars=45,
        atr_lookback=14,
        target_atr_multiple=1.5,
        stop_atr_multiple=1.0
    )
    results = generator.run_train_split()

    print("\n" + "="*80)
    print("✅ TRIPLE-BARRIER LABEL GENERATION COMPLETE")
    print("="*80)
    print(f"\nConfiguration:")
    print(f"  Horizon: {results['generator_config']['holding_bars']} bars (45 minutes)")
    print(f"  Target: +{results['generator_config']['target_atr_multiple']}×ATR({results['generator_config']['atr_lookback']})")
    print(f"  Stop: -{results['generator_config']['stop_atr_multiple']}×ATR({results['generator_config']['atr_lookback']})")
    print(f"\nResults:")
    print(f"  Total labeled: {results['aggregate_stats']['total_labeled_bars']:,}")
    print(f"  Target-First Rate: {results['aggregate_stats']['target_first_rate_pct']:.2f}%")
    print(f"  Binary Positive Rate: {results['aggregate_stats']['binary_positive_rate_pct']:.2f}%")
    print(f"\nLabels saved to: revision2/labels_triple_barrier_2026/")
