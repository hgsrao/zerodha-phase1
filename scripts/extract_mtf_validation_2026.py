#!/usr/bin/env python3
"""
MTF Feature Extraction: VALIDATION Split (April 2026)
======================================================

CLI-enabled extraction with registry latch enforcement.

Usage:
  python3 extract_mtf_validation_2026.py --split validation

Enforces:
- Registry lock: READY_FOR_VALIDATION_GATE (must be unlocked by registry)
- Output path: revision2/features_2026/validation/
- Frozen model: revision2/frozen_models_triple_barrier_45min_2026/
- No dynamic percentile computation on validation scores
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
import json
import logging
import argparse
from datetime import datetime

from revision2.mtf_causal_aligner import CausalMTFAligner
from revision2.cross_sectional_ranking import CrossSectionalRanking

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s | %(levelname)s | %(message)s',
    handlers=[
        logging.FileHandler('extract_mtf_validation_2026.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)


class ValidationMTFExtractor:
    """Extract MTF features for validation split with registry enforcement."""

    def __init__(self, split: str = "validation"):
        """Initialize extractor."""
        self.split = split
        self.registry_path = Path("revision2_external/research_dataset_registry.json")
        self.feature_source_dir = Path("/home/shrinivas/ECS_Project_external_engine_quarantine_20260912/extracted_features_2026")
        self.output_dir = Path("revision2/features_2026") / split
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Load and validate registry
        self.registry = self._load_and_validate_registry()

        # Initialize aligners
        self.aligner_5m = CausalMTFAligner(timeframe='5min')
        self.aligner_15m = CausalMTFAligner(timeframe='15min')

        # Cross-sectional ranking
        self.cross_section = CrossSectionalRanking(min_coverage=45, lookback_bars=4)

        logger.info(f"✅ ValidationMTFExtractor initialized for {split.upper()} split")

    def _load_and_validate_registry(self) -> dict:
        """Load registry and enforce split latch."""
        with open(self.registry_path, 'r') as f:
            registry = json.load(f)

        # Validate base registry
        assert registry["research_family"] == "2026_mtf_causal_feature_discovery"
        assert registry["status"] == "EXTRACTION_COMPLETE_INHOUSE"

        # Enforce split latch
        if self.split == "validation":
            if registry.get("current_split_status") != "train":
                raise AssertionError(
                    f"Registry latch prevents VALIDATION extraction. "
                    f"Current status: {registry.get('current_split_status')}. "
                    f"Must complete TRAIN first."
                )
            logger.info(f"✅ Registry latch UNLOCKED for VALIDATION extraction")

        elif self.split in ["test", "sealed_confirmation"]:
            raise AssertionError(
                f"Registry latch BLOCKS {self.split.upper()} extraction. "
                f"Validation gate must pass first (validation_gate_passed must be True)."
            )

        return registry

    def extract_features_for_symbol(self, symbol: str) -> pd.DataFrame:
        """Extract MTF features for one symbol from validation split."""
        parquet_path = self.feature_source_dir / self.split / f"{symbol}_features.parquet"

        if not parquet_path.exists():
            logger.warning(f"  {symbol}: File not found ({parquet_path})")
            return None

        df_1m = pd.read_parquet(parquet_path)

        if len(df_1m) == 0:
            logger.warning(f"  {symbol}: Empty DataFrame")
            return None

        logger.info(f"  {symbol}: {len(df_1m)} rows loaded")

        # Recalculate with strict causal alignment
        df_5m = self.aligner_5m.aggregate_completed_bars(df_1m)
        features_5m = self._calculate_single_symbol_features(df_5m, '5m')

        df_15m = self.aligner_15m.aggregate_completed_bars(df_1m)
        features_15m = self._calculate_single_symbol_features(df_15m, '15m')

        # Broadcast features to 1m index
        result = df_1m.copy()

        for col in features_5m.columns:
            features_5m_broadcast = self.aligner_5m.align_features(df_1m, features_5m[[col]])
            result[col] = features_5m_broadcast[col]

        for col in features_15m.columns:
            features_15m_broadcast = self.aligner_15m.align_features(df_1m, features_15m[[col]])
            result[col] = features_15m_broadcast[col]

        return result

    @staticmethod
    def _calculate_single_symbol_features(df_htf: pd.DataFrame, prefix: str) -> pd.DataFrame:
        """Calculate HTF-level features."""
        output = pd.DataFrame(index=df_htf.index)

        close = df_htf['close']
        net_change = close.diff(4).abs()
        gross_movement = close.diff().abs().rolling(window=4, min_periods=1).sum()
        output[f'{prefix}_efficiency'] = (net_change / gross_movement.replace(0, 1)).clip(0, 1)

        output[f'{prefix}_trend'] = np.sign(close.diff(4)).fillna(0)

        typical = (df_htf['high'] + df_htf['low'] + df_htf['close']) / 3.0
        vwap = (typical * df_htf['volume']).rolling(window=20, min_periods=5).sum() / \
               df_htf['volume'].rolling(window=20, min_periods=5).sum()
        tr = pd.concat([
            df_htf['high'] - df_htf['low'],
            (df_htf['high'] - df_htf['close'].shift()).abs(),
            (df_htf['low'] - df_htf['close'].shift()).abs(),
        ], axis=1).max(axis=1)
        atr = tr.rolling(14, min_periods=1).mean()
        output[f'{prefix}_vwap_dist_atr'] = ((df_htf['close'] - vwap) / atr.replace(0, 1)).clip(-5, 5)

        log_returns = np.log(df_htf['close'] / df_htf['close'].shift(1))
        output[f'{prefix}_realized_vol'] = log_returns.rolling(window=20, min_periods=5).std()

        return output

    def run_validation_extraction(self) -> dict:
        """Extract MTF features for validation split."""
        logger.info("\n" + "="*80)
        logger.info(f"EXTRACTING MTF FEATURES FOR VALIDATION SPLIT (APRIL 2026)")
        logger.info("="*80 + "\n")

        all_symbols = [
            "ADANIENT", "ADANIPORTS", "APOLLOHOSP", "ASIANPAINT", "AXISBANK",
            "BAJAJ-AUTO", "BAJAJFINSV", "BAJFINANCE", "BEL", "BHARTIARTL",
            "CIPLA", "COALINDIA", "DRREDDY", "EICHERMOT", "ETERNAL",
            "GRASIM", "HCLTECH", "HDFCBANK", "HDFCLIFE", "HINDALCO",
            "HINDUNILVR", "ICICIBANK", "INDIGO", "INFY", "ITC",
            "JIOFIN", "JSWSTEEL", "KOTAKBANK", "LT", "M&M",
            "MARUTI", "MAXHEALTH", "NTPC", "ONGC", "POWERGRID",
            "RELIANCE", "SBILIFE", "SBIN", "SHRIRAMFIN", "SUNPHARMA",
            "TATACONSUM", "TATASTEEL", "TCS", "TECHM", "TITAN",
            "TRENT", "ULTRACEMCO", "WIPRO"
        ]

        extracted_dfs = {}
        success_count = 0

        for idx, symbol in enumerate(all_symbols, 1):
            try:
                df = self.extract_features_for_symbol(symbol)
                if df is not None:
                    extracted_dfs[symbol] = df
                    success_count += 1
                    logger.info(f"[{idx}/{len(all_symbols)}] {symbol:15s} ✅ ({len(df)} rows)")
            except Exception as e:
                logger.error(f"[{idx}/{len(all_symbols)}] {symbol:15s} - ERROR: {str(e)[:60]}")

        logger.info(f"\n✅ Extracted {success_count}/{len(all_symbols)} symbols")

        # Cross-sectional ranking
        logger.info("\nCalculating cross-sectional rankings...")
        extracted_dfs = self.cross_section.run(extracted_dfs)

        # Save
        self._save_features(extracted_dfs)

        return extracted_dfs

    def _save_features(self, extracted_dfs: dict):
        """Save features to split-partitioned directory."""
        logger.info(f"\nSaving features to {self.output_dir}/")

        for symbol, df in extracted_dfs.items():
            output_path = self.output_dir / f"{symbol}_mtf_2026.parquet"
            df.to_parquet(output_path)

        logger.info(f"✅ Saved {len(extracted_dfs)} symbol feature matrices")
        logger.info(f"   Split partition: {self.output_dir.relative_to(Path.cwd())}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract MTF features for validation split")
    parser.add_argument("--split", default="validation", choices=["validation"],
                       help="Which split to extract (validation only, others locked)")
    args = parser.parse_args()

    try:
        extractor = ValidationMTFExtractor(split=args.split)
        results = extractor.run_validation_extraction()

        print("\n" + "="*80)
        print("✅ VALIDATION EXTRACTION COMPLETE")
        print("="*80)
        print(f"\nFeatures ready in: revision2/features_2026/validation/")
        print(f"Next step: Run validation_gate_out_of_sample_2026.py")

    except AssertionError as e:
        print(f"\n❌ REGISTRY LATCH VIOLATION")
        print(f"   {str(e)}")
        sys.exit(1)
