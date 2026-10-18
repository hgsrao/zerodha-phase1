#!/usr/bin/env python3
"""
LAYER 5: Entry Probability Validator
======================================

Uses backtested historical data to predict if a specific trade will win.

Before entering ANY trade, we check:
1. Has this symbol+regime combination won in the past?
2. What was the historical win rate?
3. Is win probability > 70% threshold?
4. What's the expected R-multiple (avg_win / avg_loss)?

Only enters if:
  - Historical win probability > 70%
  - Expected R-multiple > 2.0 (even at 40% win rate, still profitable)
  - Trade is directionally aligned with trend
"""

import numpy as np
import pandas as pd
from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass


@dataclass
class TradeSignature:
    """Identifies a trade by its characteristics for probability lookup."""
    symbol: str
    regime: str  # "strong_bull", "weak_bull", "sideways", "weak_bear", "strong_bear"
    pa_confidence: float  # 0.0-1.0
    studies_confidence: float  # 0.0-1.0
    direction: int  # 1 = long, -1 = short
    trend_alignment: float  # 0.0-1.0 (how well trade aligns with market trend)
    vix_level: float  # VIX value (10-30 normal, > 30 panic, < 10 complacent)
    phase_angle: float  # Stock-index phase difference in degrees


@dataclass
class WinProbability:
    """Result of entry probability validation."""
    symbol: str
    win_probability: float  # 0.0-1.0
    historical_trades: int  # How many similar trades in history?
    avg_win_pct: float  # Average % gain on winning trades
    avg_loss_pct: float  # Average % loss on losing trades
    r_multiple: float  # avg_win_pct / avg_loss_pct
    confidence: str  # "high", "medium", "low", "insufficient_data"
    passes_threshold: bool  # win_probability > 0.70?
    passes_expectancy: bool  # r_multiple > 2.0?
    reason: str  # Why pass or fail?


class EntryProbabilityValidator:
    """
    Validates entry probability based on historical backtest data.

    This layer answers: "Should we take THIS trade based on its historical win rate?"

    Strategy:
    1. Build a historical database of trades by (symbol, regime, confidence levels, direction)
    2. For each new trade, find similar historical trades
    3. Calculate win rate of those historical trades
    4. Only allow entry if win_prob > 70% AND r_multiple > 2.0
    5. Reject if insufficient historical data (< 20 similar trades)
    """

    def __init__(self, threshold_win_probability=0.70, threshold_r_multiple=2.0):
        """
        Args:
            threshold_win_probability: Min win rate to allow entry (default 70%)
            threshold_r_multiple: Min expected R-multiple (default 2.0x)
        """
        self.threshold_win_prob = threshold_win_probability
        self.threshold_r_multiple = threshold_r_multiple

        # Historical trade database
        # Key: (symbol, regime, direction, pa_bucket, studies_bucket)
        # Value: List of past trade outcomes
        self.trade_history: Dict[Tuple, List[Dict]] = {}

        self.total_trades_analyzed = 0
        self.trades_allowed = 0
        self.trades_rejected = 0

    def record_trade_outcome(self, signature: TradeSignature, pnl_pct: float,
                            bars_held: int, max_profit: float, max_loss: float):
        """
        Record a historical trade outcome for future probability calculation.

        Args:
            signature: Trade characteristics
            pnl_pct: P&L as percentage of entry price
            bars_held: How many bars trade was held
            max_profit: Maximum profit reached (%)
            max_loss: Maximum loss reached (%)
        """
        # Create signature key (discretize confidence levels into buckets)
        pa_bucket = int(signature.pa_confidence * 10) / 10  # 0.0, 0.1, 0.2, ... 1.0
        studies_bucket = int(signature.studies_confidence * 10) / 10

        key = (
            signature.symbol,
            signature.regime,
            signature.direction,
            round(pa_bucket, 1),
            round(studies_bucket, 1),
        )

        if key not in self.trade_history:
            self.trade_history[key] = []

        self.trade_history[key].append({
            'pnl_pct': pnl_pct,
            'bars_held': bars_held,
            'max_profit': max_profit,
            'max_loss': max_loss,
            'won': 1 if pnl_pct > 0 else 0,
            'vix_level': signature.vix_level,
            'phase_angle': signature.phase_angle,
            'trend_alignment': signature.trend_alignment,
        })

    def validate_entry(self, signature: TradeSignature) -> WinProbability:
        """
        Validate if a trade should be entered based on historical probability.

        Returns:
            WinProbability with pass/fail recommendation
        """
        self.total_trades_analyzed += 1

        # Create signature key
        pa_bucket = round(int(signature.pa_confidence * 10) / 10, 1)
        studies_bucket = round(int(signature.studies_confidence * 10) / 10, 1)

        key = (
            signature.symbol,
            signature.regime,
            signature.direction,
            pa_bucket,
            studies_bucket,
        )

        # Check if we have historical data for this trade type
        if key not in self.trade_history or len(self.trade_history[key]) < 20:
            # Insufficient data - reject conservatively
            self.trades_rejected += 1
            return WinProbability(
                symbol=signature.symbol,
                win_probability=0.5,  # Unknown
                historical_trades=len(self.trade_history.get(key, [])),
                avg_win_pct=0.0,
                avg_loss_pct=0.0,
                r_multiple=0.0,
                confidence="insufficient_data",
                passes_threshold=False,
                passes_expectancy=False,
                reason=f"Insufficient historical data ({len(self.trade_history.get(key, []))} trades, need 20+)",
            )

        # Calculate win probability and expectancy
        trades = self.trade_history[key]

        # Win rate
        wins = sum(t['won'] for t in trades)
        win_prob = wins / len(trades)

        # Average win and loss
        winning_trades = [t for t in trades if t['won']]
        losing_trades = [t for t in trades if not t['won']]

        avg_win_pct = np.mean([t['pnl_pct'] for t in winning_trades]) if winning_trades else 0.0
        avg_loss_pct = abs(np.mean([t['pnl_pct'] for t in losing_trades])) if losing_trades else 0.1

        # R-multiple (how much do we win vs lose?)
        r_multiple = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 0.0

        # Expected value per trade
        expected_value = (win_prob * avg_win_pct) - ((1 - win_prob) * avg_loss_pct)

        # Decision logic
        passes_prob = win_prob >= self.threshold_win_prob
        passes_expectancy = r_multiple >= self.threshold_r_multiple

        if passes_prob and passes_expectancy:
            self.trades_allowed += 1
            reason = f"Win prob {100*win_prob:.1f}% > {100*self.threshold_win_prob:.0f}%, R={r_multiple:.2f}x > {self.threshold_r_multiple}x"
        elif not passes_prob:
            self.trades_rejected += 1
            reason = f"Win prob {100*win_prob:.1f}% < {100*self.threshold_win_prob:.0f}% threshold"
        else:
            self.trades_rejected += 1
            reason = f"R-multiple {r_multiple:.2f}x < {self.threshold_r_multiple}x (low expectancy)"

        return WinProbability(
            symbol=signature.symbol,
            win_probability=win_prob,
            historical_trades=len(trades),
            avg_win_pct=avg_win_pct,
            avg_loss_pct=avg_loss_pct,
            r_multiple=r_multiple,
            confidence="high" if len(trades) > 50 else "medium" if len(trades) > 20 else "low",
            passes_threshold=passes_prob,
            passes_expectancy=passes_expectancy,
            reason=reason,
        )

    def get_statistics(self) -> Dict:
        """Return overall statistics on entry validation."""
        return {
            'total_trades_analyzed': self.total_trades_analyzed,
            'trades_allowed': self.trades_allowed,
            'trades_rejected': self.trades_rejected,
            'allow_rate': self.trades_allowed / self.total_trades_analyzed if self.total_trades_analyzed > 0 else 0.0,
            'rejection_rate': self.trades_rejected / self.total_trades_analyzed if self.total_trades_analyzed > 0 else 0.0,
            'unique_signatures': len(self.trade_history),
        }

    def sample_signature(self, symbol: str, pa_conf: float, studies_conf: float,
                        direction: int, regime: str, vix: float, phase: float,
                        trend_align: float) -> TradeSignature:
        """Create a trade signature for validation."""
        return TradeSignature(
            symbol=symbol,
            regime=regime,
            pa_confidence=pa_conf,
            studies_confidence=studies_conf,
            direction=direction,
            trend_alignment=trend_align,
            vix_level=vix,
            phase_angle=phase,
        )


class DirectionalBiasFilter:
    """
    Entry Filter: Only trade in direction of market trend.

    Rejects counter-trend trades (trades against the primary direction).

    Examples:
    - Nifty trending UP: Only allow BUY, reject SHORT
    - Nifty trending DOWN: Only allow SHORT, reject BUY
    - Sideways: Allow both, but flag as lower confidence
    """

    def __init__(self, nifty_ema_period=50):
        self.ema_period = nifty_ema_period

    def check_directional_bias(self, nifty_prices: np.ndarray,
                               trade_direction: int,  # 1=BUY, -1=SHORT
                               tolerance_pct=0.5) -> Tuple[bool, str]:
        """
        Check if trade aligns with market trend.

        Args:
            nifty_prices: Nifty 50 price series
            trade_direction: 1 for long, -1 for short
            tolerance_pct: How close to neutral before we reject?

        Returns:
            (passes, reason) - passes=True if trade is directionally aligned
        """
        if len(nifty_prices) < self.ema_period:
            return True, "Insufficient data for trend check"

        # Calculate Nifty trend using EMA
        prices_series = pd.Series(nifty_prices)
        ema = prices_series.ewm(span=self.ema_period, adjust=False).mean()

        trend_strength = (ema.iloc[-1] - ema.iloc[-self.ema_period]) / ema.iloc[-self.ema_period] * 100

        # Trend strength > 0.5% = strong uptrend
        # Trend strength < -0.5% = strong downtrend
        # -0.5% to 0.5% = sideways

        if trend_strength > tolerance_pct:  # Strong uptrend
            if trade_direction == 1:  # BUY in uptrend ✓
                return True, f"BUY aligned with uptrend (+{trend_strength:.2f}%)"
            else:  # SHORT in uptrend ✗
                return False, f"SHORT against uptrend (+{trend_strength:.2f}%) - HIGH RISK"

        elif trend_strength < -tolerance_pct:  # Strong downtrend
            if trade_direction == -1:  # SHORT in downtrend ✓
                return True, f"SHORT aligned with downtrend ({trend_strength:.2f}%)"
            else:  # BUY in downtrend ✗
                return False, f"BUY against downtrend ({trend_strength:.2f}%) - HIGH RISK"

        else:  # Sideways
            return True, f"Sideways market ({trend_strength:.2f}%) - Allow with caution"

    def get_trend_alignment_score(self, nifty_prices: np.ndarray,
                                 trade_direction: int) -> float:
        """
        Return alignment score 0.0-1.0.
        1.0 = perfect alignment, 0.0 = perfect opposition
        """
        if len(nifty_prices) < self.ema_period:
            return 0.5  # Unknown

        prices_series = pd.Series(nifty_prices)
        ema = prices_series.ewm(span=self.ema_period, adjust=False).mean()
        trend_strength = (ema.iloc[-1] - ema.iloc[-self.ema_period]) / ema.iloc[-self.ema_period]

        # Map trend_strength to alignment score
        if trend_strength > 0.01:  # Uptrend
            alignment = 1.0 if trade_direction == 1 else 0.0
        elif trend_strength < -0.01:  # Downtrend
            alignment = 1.0 if trade_direction == -1 else 0.0
        else:  # Sideways
            alignment = 0.5

        return alignment


class PositiveExpectancyFilter:
    """
    Entry Filter: Only trades with positive expected value.

    Expected Value = (Win% × AvgWin$) - (Loss% × AvgLoss$)

    Example:
    - Win% = 40%, AvgWin = $100, Loss% = 60%, AvgLoss = $50
    - EV = (0.40 × 100) - (0.60 × 50) = 40 - 30 = +$10 ✓ POSITIVE

    Only enter if EV > 0
    """

    def __init__(self, min_r_multiple=2.0):
        """
        Args:
            min_r_multiple: Minimum reward-to-risk ratio (default 2.0x)
        """
        self.min_r_multiple = min_r_multiple

    def check_expectancy(self, win_probability: float, avg_win_pct: float,
                        avg_loss_pct: float) -> Tuple[bool, str, float]:
        """
        Check if trade has positive expected value.

        Returns:
            (passes, reason, expected_value)
        """
        if avg_loss_pct <= 0:
            return False, "Invalid loss data", 0.0

        # R-multiple (risk/reward ratio)
        r_multiple = avg_win_pct / avg_loss_pct if avg_loss_pct > 0 else 0.0

        # Expected value per trade
        # EV = (win% × avg_win) - (loss% × avg_loss)
        expected_value = (win_probability * avg_win_pct) - ((1 - win_probability) * avg_loss_pct)

        passes_r = r_multiple >= self.min_r_multiple
        passes_ev = expected_value > 0

        if passes_r and passes_ev:
            return True, f"Positive EV ({expected_value:+.2f}%), R={r_multiple:.2f}x", expected_value
        elif not passes_r:
            return False, f"Low R-multiple {r_multiple:.2f}x < {self.min_r_multiple}x", expected_value
        else:
            return False, f"Negative expected value ({expected_value:.2f}%)", expected_value


class EntryQualityGate:
    """
    Raises entry signal quality thresholds.

    OLD: Enter if PA > 0.5 OR Studies > 0.5
    NEW: Enter if PA > 0.8 AND Studies > 0.8

    This significantly reduces entry count but improves quality.
    """

    def __init__(self, min_pa_confidence=0.8, min_studies_confidence=0.8):
        self.min_pa = min_pa_confidence
        self.min_studies = min_studies_confidence

    def check_signal_quality(self, pa_confidence: float,
                            studies_confidence: float) -> Tuple[bool, str]:
        """
        Check if entry signal is high quality.

        Returns:
            (passes, reason)
        """
        passes_pa = pa_confidence >= self.min_pa
        passes_studies = studies_confidence >= self.min_studies

        if passes_pa and passes_studies:
            return True, f"Both signals strong (PA={pa_confidence:.2f}, Studies={studies_confidence:.2f})"
        elif not passes_pa:
            return False, f"PA confidence {pa_confidence:.2f} < {self.min_pa} threshold"
        else:
            return False, f"Studies confidence {studies_confidence:.2f} < {self.min_studies} threshold"
