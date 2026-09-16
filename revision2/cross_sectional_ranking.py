"""
Cross-Sectional Relative Strength Ranking (Causal, Universe-Wide)
==================================================================

Calculates per-symbol relative strength percentile and excess return
against the completed 15-minute universe at exact timestamp alignment.

Strict constraints:
- Per-symbol 4-bar returns computed on completed bar sequence only
- No forward-fill of missing symbols
- Minimum coverage enforcement (default: 45/48 symbols)
- All rankings based on identical completion timestamp
"""

import pandas as pd
import numpy as np
from typing import Dict, Tuple
import logging

logger = logging.getLogger(__name__)


class CrossSectionalRanking:
    """Calculate causal cross-sectional features across 48-symbol universe."""

    def __init__(self, min_coverage: int = 45, lookback_bars: int = 4):
        """
        Args:
            min_coverage: Minimum number of symbols with valid bars at timestamp
            lookback_bars: Number of 15-minute bars back for return calculation
        """
        self.min_coverage = min_coverage
        self.lookback_bars = lookback_bars
        logger.info(f"CrossSectionalRanking: min_coverage={min_coverage}, lookback={lookback_bars} bars")

    def calculate_per_symbol_returns(self, symbol_dfs: Dict[str, pd.DataFrame]) -> Dict[str, pd.Series]:
        """
        Calculate per-symbol 4-bar returns on their own completed bar sequences.

        This must happen BEFORE matrix construction to avoid timestamp skew.

        Args:
            symbol_dfs: Dict of symbol -> 15m completed DataFrame (indexed by available_at)

        Returns:
            Dict of symbol -> Series of log returns (no forward-fill across symbols)
        """
        returns_dict = {}

        for symbol, df in symbol_dfs.items():
            # Per-symbol calculation: only this symbol's bars, no cross-symbol interference
            close = df['close']
            close_lagged = close.shift(self.lookback_bars)

            # Calculate log return: ln(C_t / C_{t-4})
            returns = np.log(close / close_lagged)

            returns_dict[symbol] = returns

            # Log sparsity
            valid_count = returns.notna().sum()
            logger.debug(f"{symbol}: {valid_count}/{len(returns)} valid 4-bar returns")

        return returns_dict

    def calculate_rankings(self, returns_dict: Dict[str, pd.Series]) -> Tuple[Dict[str, pd.Series], Dict[str, pd.Series]]:
        """
        Calculate percentile ranks and excess returns at each exact timestamp.

        Args:
            returns_dict: Per-symbol returns (no forward-fill)

        Returns:
            (percentile_dict, excess_dict) - Both indexed by symbol
        """
        # Form universe matrix (aligned on exact timestamps)
        returns_matrix = pd.DataFrame(returns_dict)

        logger.info(f"Universe matrix shape: {returns_matrix.shape}")
        logger.info(f"Per-timestamp symbol count: min={returns_matrix.notna().sum(axis=1).min()}, "
                   f"max={returns_matrix.notna().sum(axis=1).max()}, "
                   f"mean={returns_matrix.notna().sum(axis=1).mean():.1f}")

        # Calculate cross-sectional metadata
        cross_section_count = returns_matrix.notna().sum(axis=1)
        missing_count = len(returns_dict) - cross_section_count

        # Enforce minimum coverage: rows below threshold become all-NaN
        valid_mask = cross_section_count >= self.min_coverage
        filtered_returns = returns_matrix.where(valid_mask, np.nan)

        logger.info(f"Coverage filter: {valid_mask.sum()}/{len(valid_mask)} timestamps meet min_coverage={self.min_coverage}")

        # Calculate percentile ranks (within each row, only among valid values)
        rs_percentile = filtered_returns.rank(axis=1, pct=True)

        # Calculate excess return (distance from row median)
        rs_median = filtered_returns.median(axis=1)
        rs_excess = filtered_returns.sub(rs_median, axis=0)

        # Convert back to per-symbol dictionaries
        percentile_dict = {symbol: rs_percentile[symbol] for symbol in returns_dict.keys()}
        excess_dict = {symbol: rs_excess[symbol] for symbol in returns_dict.keys()}

        # Metadata tracking
        self.cross_section_count = cross_section_count
        self.missing_count = missing_count

        return percentile_dict, excess_dict

    def attach_to_symbol_dfs(self,
                            symbol_dfs: Dict[str, pd.DataFrame],
                            percentile_dict: Dict[str, pd.Series],
                            excess_dict: Dict[str, pd.Series]) -> Dict[str, pd.DataFrame]:
        """
        Attach cross-sectional features back to individual symbol DataFrames.

        Enforces that any symbol without a valid return at T has NaN cross-sectional features.

        Args:
            symbol_dfs: Original 15m completed DataFrames
            percentile_dict: Per-symbol percentile ranks
            excess_dict: Per-symbol excess returns

        Returns:
            Updated symbol_dfs with cross-sectional features attached
        """
        output_dfs = {}

        for symbol, df in symbol_dfs.items():
            sym_df = df.copy()

            # Attach percentile and excess
            sym_df['15m_rs_percentile'] = percentile_dict[symbol]
            sym_df['15m_rs_excess'] = excess_dict[symbol]

            # Metadata for auditability
            sym_df['15m_cross_section_count'] = self.cross_section_count
            sym_df['15m_missing_symbol_count'] = self.missing_count

            # Self-validity check: if this symbol lacks a return at T, its cross-sectional features are NaN
            # This is enforced by the percentile/excess calculation, but explicit for clarity
            output_dfs[symbol] = sym_df

        logger.info(f"Attached cross-sectional features to {len(output_dfs)} symbols")
        return output_dfs

    def run(self, symbol_dfs: Dict[str, pd.DataFrame]) -> Dict[str, pd.DataFrame]:
        """
        Full pipeline: returns → percentile ranks → attach to DataFrames.

        Args:
            symbol_dfs: Dict of 15-minute completed DataFrames

        Returns:
            Updated symbol_dfs with cross-sectional features
        """
        logger.info(f"Starting cross-sectional ranking for {len(symbol_dfs)} symbols")

        # Step 1: Per-symbol returns (no forward-fill)
        returns_dict = self.calculate_per_symbol_returns(symbol_dfs)

        # Step 2: Universe-wide rankings
        percentile_dict, excess_dict = self.calculate_rankings(returns_dict)

        # Step 3: Attach back to DataFrames
        output_dfs = self.attach_to_symbol_dfs(symbol_dfs, percentile_dict, excess_dict)

        logger.info(f"✅ Cross-sectional ranking complete")
        return output_dfs
