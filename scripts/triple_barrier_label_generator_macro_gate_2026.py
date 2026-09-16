#!/usr/bin/env python3
"""
Triple-Barrier Label Generator with Macro Gate Filter
=====================================================

Generates 45-bar triple-barrier labels with regime-aware gating:
- Entry: Open of bar t+1 (no hindsight)
- Hold: 45 bars
- Target: +1.5×ATR(14)
- Stop: -1.0×ATR(14)
- Gate: Only generate labels when macro regime is favorable

Output: Binary labels (1 = TARGET_FIRST, 0 = STOP_FIRST or TIMEOUT)
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import logging
from glob import glob

from revision2.macro_gate_detector import MacroGateDetector

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('triple_barrier_macro_gate_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class TripleBarrierMacroGateLabelGenerator:
    """Generate triple-barrier labels with macro gate filtering."""

    # Barrier config
    TARGET_MULTIPLE = 1.5
    STOP_MULTIPLE = 1.0
    HOLDING_BARS = 45

    def __init__(self, data_dir: str = "revision2/features_2026/train"):
        """Initialize generator.

        Args:
            data_dir: Path to feature directory
        """
        self.data_dir = Path(data_dir)
        self.macro_detector = MacroGateDetector()

        logger.info("✅ TripleBarrierMacroGateLabelGenerator initialized")

    def calculate_atr(self, ohlcv: pd.DataFrame, period: int = 14) -> pd.Series:
        """Calculate ATR."""
        high = ohlcv['high']
        low = ohlcv['low']
        close = ohlcv['close']

        tr1 = high - low
        tr2 = (high - close.shift()).abs()
        tr3 = (low - close.shift()).abs()

        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.rolling(window=period, min_periods=1).mean()
        return atr

    def generate_labels(self, ohlcv: pd.DataFrame, gate_status: pd.Series) -> pd.Series:
        """Generate triple-barrier labels with gate filter.

        Args:
            ohlcv: OHLCV DataFrame (will be reset_index if needed)
            gate_status: Series of gate open/close (1/0)

        Returns:
            Series of binary labels (1 or 0)
        """
        # Reset index to ensure numeric indexing
        ohlcv = ohlcv.reset_index(drop=True)

        atr = self.calculate_atr(ohlcv)
        labels = np.full(len(ohlcv), np.nan)
        gate_filtered = 0

        for t in range(len(ohlcv) - self.HOLDING_BARS - 1):
            entry_bar = t + 1
            entry_px = ohlcv.iloc[entry_bar]['open']

            # Check gate status BEFORE entry
            if gate_status.iloc[t] == 0:  # Gate closed at signal time
                gate_filtered += 1
                continue

            if pd.isna(entry_px) or pd.isna(atr.iloc[t]):
                continue

            atr_val = atr.iloc[t]
            target_px = entry_px + (self.TARGET_MULTIPLE * atr_val)
            stop_px = entry_px - (self.STOP_MULTIPLE * atr_val)

            holding_start = entry_bar
            holding_end = min(entry_bar + self.HOLDING_BARS, len(ohlcv) - 1)

            exit_state = 0  # Default: TIMEOUT
            for idx in range(holding_start, holding_end + 1):
                bar_high = ohlcv.iloc[idx]['high']
                bar_low = ohlcv.iloc[idx]['low']

                if not pd.isna(bar_high) and bar_high >= target_px:
                    exit_state = 1  # TARGET_FIRST
                    break
                if not pd.isna(bar_low) and bar_low <= stop_px:
                    exit_state = 0  # STOP_FIRST
                    break

            labels[entry_bar] = float(exit_state)

        logger.info(f"   ✅ Generated {np.sum(~np.isnan(labels)):,} labels")
        logger.info(f"   ✅ Gate filtered {gate_filtered:,} potential entries")

        return pd.Series(labels)

    def run(self) -> dict:
        """Run label generation on full TRAIN split.

        Returns:
            Statistics dictionary
        """
        logger.info("\n" + "="*80)
        logger.info("GENERATING LABELS: TRAIN SPLIT (JAN-MAR 2026) WITH MACRO GATE")
        logger.info("="*80)

        # Load all symbols and compute macro gate
        logger.info("\nLoading data and computing macro regime...")
        closes_dict = {}
        symbol_files = sorted(self.data_dir.glob("*_mtf_2026.parquet"))

        for sym_file in symbol_files:
            sym = sym_file.name.split('_')[0]
            df = pd.read_parquet(sym_file)
            closes_dict[sym] = df['close'].reset_index(drop=True)

        # Compute macro gate for entire TRAIN split
        regime_df, regime_metrics = self.macro_detector.run(closes_dict)

        logger.info(f"\n📊 TRAIN MACRO REGIME:")
        logger.info(f"   Mean breadth: {regime_metrics['mean_breadth']*100:.1f}%")
        logger.info(f"   Mean stress: {regime_metrics['mean_stress']*100:.1f}%")
        logger.info(f"   Mean trend Z: {regime_metrics['mean_trend_z']:.3f}")
        logger.info(f"   Gate open: {regime_metrics['gate_open_pct']:.1f}% of bars")

        # Generate labels for each symbol
        logger.info("\nGenerating triple-barrier labels with gate filter...")
        all_labels = []
        total_filtered = 0

        for sym_file in symbol_files:
            sym = sym_file.name.split('_')[0]
            df = pd.read_parquet(sym_file)

            logger.info(f"  {sym}:")
            labels = self.generate_labels(df[['open', 'high', 'low', 'close']], regime_df['gate_status'])
            all_labels.append(labels)

        # Concatenate all labels
        labels_combined = pd.concat(all_labels, axis=0, ignore_index=True)

        # Statistics
        total_labels = np.sum(~np.isnan(labels_combined))
        positive_labels = np.sum(labels_combined == 1)
        negative_labels = np.sum(labels_combined == 0)

        if total_labels > 0:
            positive_rate = positive_labels / total_labels
        else:
            positive_rate = 0

        logger.info(f"\n" + "="*80)
        logger.info("LABEL GENERATION RESULTS (WITH MACRO GATE)")
        logger.info("="*80)

        logger.info(f"\nTotal labeled bars: {total_labels:,}")
        logger.info(f"Positive (TARGET_FIRST): {positive_labels:,} ({positive_rate*100:.2f}%)")
        logger.info(f"Negative (STOP_FIRST + TIMEOUT): {negative_labels:,}")

        logger.info(f"\n✅ Labels generated with macro gate filter applied")
        logger.info(f"   Strategy will only trade during favorable regimes")

        return {
            'total_labels': total_labels,
            'positive_labels': positive_labels,
            'negative_labels': negative_labels,
            'positive_rate': positive_rate,
            'regime_metrics': regime_metrics,
            'labels': labels_combined
        }


if __name__ == "__main__":
    generator = TripleBarrierMacroGateLabelGenerator()
    results = generator.run()

    print("\n" + "="*80)
    print("LABEL GENERATION COMPLETE")
    print("="*80)
    print(f"\nTotal: {results['total_labels']:,} labels")
    print(f"Positive rate: {results['positive_rate']*100:.2f}%")
    print(f"Gate-filtered regime: {results['regime_metrics']['gate_open_pct']:.1f}% of training data")
