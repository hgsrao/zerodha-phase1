#!/usr/bin/env python3
"""
================================================================================
ADAPTIVE PARAMETER CALIBRATOR WITH RELATIVE SYNCHRONIZATION THRESHOLDS
================================================================================

Two-Level System:

LEVEL 1: RELATIVE SYNCHRONIZATION THRESHOLDS
  ├─ Calculate from market data (not hardcoded)
  ├─ dp_dt threshold = f(ATR, recent volatility)
  ├─ dv_dt threshold = f(average volume, volume std)
  └─ Adapts to each symbol and market regime

LEVEL 2: ADAPTIVE PARAMETER CALIBRATOR
  ├─ Run 500 backtests with different parameters
  ├─ Learn which combinations work best
  ├─ Auto-adjust profit_target, stop_loss, PID gains
  └─ Converge to optimal configuration

Starting Values: Smart defaults based on data analysis (not random)
================================================================================
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime
import json
from typing import Dict, List, Tuple, Optional
from scipy.optimize import differential_evolution
import warnings
warnings.filterwarnings('ignore')


class RelativeSynchronizationThresholds:
    """
    Calculate RELATIVE synchronization thresholds based on market characteristics

    Power Plant Analogy:
    - Frequency must match within tolerance (not just > 0)
    - Voltage must match within tolerance (not just > 0)
    - Price momentum must match typical move range (not just > 0)
    - Volume momentum must match typical volume range (not just > 0)
    """

    def __init__(self, verbose=True):
        self.verbose = verbose
        self.thresholds = {}

    def _log(self, message):
        if self.verbose:
            print(message)

    def calculate_from_data(self, df_history, symbol=""):
        """
        Calculate RELATIVE thresholds from market data

        Args:
            df_history: DataFrame with OHLCV data (at least 50 bars)
            symbol: Stock symbol (for logging)

        Returns:
            dict with calculated thresholds
        """

        if len(df_history) < 50:
            self._log(f"❌ Insufficient data ({len(df_history)} bars < 50)")
            return self._get_default_thresholds()

        # ===================================================================
        # PART 1: PRICE MOMENTUM THRESHOLD (dp/dt)
        # ===================================================================

        recent_closes = df_history['close'].tail(50)

        # Calculate Average True Range (ATR) - typical price move per bar
        high = df_history['high'].tail(50)
        low = df_history['low'].tail(50)
        close = df_history['close'].tail(50)

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.mean()

        # Price momentum threshold: 25% of typical daily move
        # Ensures we only enter when price change is MEANINGFUL
        dp_dt_threshold = atr * 0.25

        # Also get standard deviation for volatility adjustment
        price_std = recent_closes.std()
        price_mean = recent_closes.mean()
        price_cv = price_std / price_mean if price_mean > 0 else 0.01  # Coefficient of variation

        self._log(f"\n{'='*80}")
        self._log(f"RELATIVE SYNCHRONIZATION THRESHOLDS - {symbol}")
        self._log(f"{'='*80}")
        self._log(f"\n📊 PRICE MOMENTUM (dP/dt)")
        self._log(f"   ATR (typical move): ₹{atr:.2f}")
        self._log(f"   Price STD: ₹{price_std:.2f}")
        self._log(f"   Price CV: {price_cv:.3f}")
        self._log(f"   dP/dt threshold: ₹{dp_dt_threshold:.2f} (25% of ATR)")

        # ===================================================================
        # PART 2: VOLUME MOMENTUM THRESHOLD (dV/dt)
        # ===================================================================

        recent_volumes = df_history['volume'].tail(50)

        volume_mean = recent_volumes.mean()
        volume_std = recent_volumes.std()
        volume_cv = volume_std / volume_mean if volume_mean > 0 else 0.1

        # Volume momentum threshold: 10% of average volume
        # Ensures we only trade when volume is MEANINGFUL
        dv_dt_threshold = volume_mean * 0.10

        self._log(f"\n📊 VOLUME MOMENTUM (dV/dt)")
        self._log(f"   Avg Volume: {volume_mean:,.0f} shares")
        self._log(f"   Volume STD: {volume_std:,.0f}")
        self._log(f"   Volume CV: {volume_cv:.3f}")
        self._log(f"   dV/dt threshold: {dv_dt_threshold:,.0f} (10% of avg)")

        # ===================================================================
        # PART 3: VOLATILITY CLASSIFICATION
        # ===================================================================

        # Classify as Low/Medium/High volatility
        if price_cv < 0.02:
            vol_class = "LOW"
            vol_multiplier = 1.0
        elif price_cv < 0.04:
            vol_class = "MEDIUM"
            vol_multiplier = 1.0
        else:
            vol_class = "HIGH"
            vol_multiplier = 1.2  # Increase thresholds in high vol

        self._log(f"\n⚡ VOLATILITY CLASSIFICATION")
        self._log(f"   CV = {price_cv:.3f} → {vol_class} volatility")
        self._log(f"   Multiplier: {vol_multiplier:.2f}x")

        # ===================================================================
        # PART 4: VOLUME CLASSIFICATION
        # ===================================================================

        # Classify as Low/Medium/High volume
        if volume_cv < 0.3:
            volume_class = "CONSISTENT"
            volume_multiplier = 1.0
        elif volume_cv < 0.6:
            volume_class = "MODERATE"
            volume_multiplier = 1.0
        else:
            volume_class = "ERRATIC"
            volume_multiplier = 1.1

        self._log(f"\n📈 VOLUME PATTERN CLASSIFICATION")
        self._log(f"   CV = {volume_cv:.3f} → {volume_class}")
        self._log(f"   Multiplier: {volume_multiplier:.2f}x")

        # ===================================================================
        # PART 5: FINAL RELATIVE THRESHOLDS
        # ===================================================================

        # Apply volatility and volume adjustments
        dp_dt_threshold *= vol_multiplier
        dv_dt_threshold *= volume_multiplier

        self.thresholds = {
            'symbol': symbol,
            'calculated_at': datetime.now().isoformat(),
            'data_points': len(df_history),

            # Price momentum
            'dp_dt_threshold': float(dp_dt_threshold),
            'atr': float(atr),
            'price_std': float(price_std),
            'price_cv': float(price_cv),
            'price_mean': float(price_mean),

            # Volume momentum
            'dv_dt_threshold': float(dv_dt_threshold),
            'volume_mean': float(volume_mean),
            'volume_std': float(volume_std),
            'volume_cv': float(volume_cv),

            # Classifications
            'volatility_class': vol_class,
            'volume_class': volume_class,
            'vol_multiplier': float(vol_multiplier),
            'volume_multiplier': float(volume_multiplier),

            # Fixed checks
            'phase_alignment': 'SAME_DIRECTION',
            'signal_quality_min': 0.6
        }

        self._log(f"\n✅ FINAL RELATIVE THRESHOLDS")
        self._log(f"   dP/dt ≥ ₹{dp_dt_threshold:.2f} (was > ₹0)")
        self._log(f"   dV/dt ≥ {dv_dt_threshold:,.0f} shares (was > 0)")
        self._log(f"   Phase: SAME_DIRECTION")
        self._log(f"   Signal Quality: ≥ 0.60")
        self._log(f"\n{'='*80}\n")

        return self.thresholds

    def _get_default_thresholds(self):
        """Default thresholds when insufficient data"""
        return {
            'dp_dt_threshold': 0.50,
            'dv_dt_threshold': 50000,
            'phase_alignment': 'SAME_DIRECTION',
            'signal_quality_min': 0.6,
            'note': 'DEFAULT (insufficient data)'
        }


class SmartParameterInitializer:
    """
    Generate SMART starting values for parameters (not random)

    Based on:
    - ATR (volatility)
    - Historical win rates
    - Market regime
    - Known good values from testing
    """

    def __init__(self, verbose=True):
        self.verbose = verbose

    def _log(self, message):
        if self.verbose:
            print(message)

    def initialize_from_data(self, df_history, symbol=""):
        """
        Generate smart starting parameters based on market data

        Args:
            df_history: DataFrame with OHLCV data
            symbol: Stock symbol

        Returns:
            dict with starting parameter values
        """

        if len(df_history) < 50:
            return self._get_default_params()

        # Calculate volatility-based parameters
        high = df_history['high'].tail(50)
        low = df_history['low'].tail(50)
        close = df_history['close'].tail(50)

        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        atr = tr.mean()

        price_mean = close.mean()
        atr_pct = (atr / price_mean) * 100 if price_mean > 0 else 2.0

        self._log(f"\n{'='*80}")
        self._log(f"SMART PARAMETER INITIALIZATION - {symbol}")
        self._log(f"{'='*80}")
        self._log(f"\nMarket Characteristics:")
        self._log(f"  Price: ₹{price_mean:.2f}")
        self._log(f"  ATR: ₹{atr:.2f} ({atr_pct:.2f}%)")

        # ===================================================================
        # PROFIT TARGET (based on ATR)
        # ===================================================================

        if atr_pct < 1.0:
            # Low volatility stock
            profit_target = 0.50  # ₹0.50 per share
            profit_rationale = "Low volatility → smaller target"
        elif atr_pct < 2.0:
            # Medium volatility
            profit_target = 1.00  # ₹1.00 per share
            profit_rationale = "Medium volatility → normal target"
        else:
            # High volatility
            profit_target = 1.50  # ₹1.50 per share
            profit_rationale = "High volatility → larger target"

        # Alternative: profit_target = atr * 0.5 (50% of ATR)

        self._log(f"\n💰 PROFIT TARGET")
        self._log(f"   Initial: ₹{profit_target:.2f} per share")
        self._log(f"   Rationale: {profit_rationale}")

        # ===================================================================
        # STOP LOSS (based on ATR)
        # ===================================================================

        stop_loss = -atr * 1.0  # 100% of ATR as stop loss
        self._log(f"\n🛑 STOP LOSS")
        self._log(f"   Initial: ₹{stop_loss:.2f} per share")
        self._log(f"   Rationale: 1.0x ATR (wide enough to avoid noise)")

        # ===================================================================
        # PID GAINS (based on ATR volatility)
        # ===================================================================

        # Lower ATR% → higher gains (responsive)
        # Higher ATR% → lower gains (stable)

        if atr_pct < 1.0:
            entry_pid_kp = 0.15  # More responsive
            exit_pid_kp = 0.12
        elif atr_pct < 2.0:
            entry_pid_kp = 0.10  # Standard
            exit_pid_kp = 0.10
        else:
            entry_pid_kp = 0.08   # More stable
            exit_pid_kp = 0.08

        self._log(f"\n🎮 PID GAINS")
        self._log(f"   Entry Kp: {entry_pid_kp:.3f} (fast response)")
        self._log(f"   Exit Kp: {exit_pid_kp:.3f} (stable exit)")
        self._log(f"   Both Ki: 0.010, Kd: 0.010 (standard)")

        # ===================================================================
        # MIN/MAX HOLD BARS
        # ===================================================================

        # All stocks: 2-20 bars (30 min to 5 hours on 15-min bars)
        min_hold_bars = 2
        max_hold_bars = 20

        self._log(f"\n⏱️  HOLDING PERIOD")
        self._log(f"   Min: {min_hold_bars} bars (30 min)")
        self._log(f"   Max: {max_hold_bars} bars (5 hours)")

        # ===================================================================
        # STARTING VALUES FOR CALIBRATION
        # ===================================================================

        params = {
            'profit_target': {
                'current': profit_target,
                'min': 0.25,
                'max': 3.00,
                'step': 0.10
            },
            'stop_loss': {
                'current': stop_loss,
                'min': -3.00,
                'max': -0.10,
                'step': 0.10
            },
            'entry_pid_kp': {
                'current': entry_pid_kp,
                'min': 0.05,
                'max': 0.25,
                'step': 0.02
            },
            'exit_pid_kp': {
                'current': exit_pid_kp,
                'min': 0.05,
                'max': 0.25,
                'step': 0.02
            },
            'min_hold_bars': {
                'current': min_hold_bars,
                'min': 1,
                'max': 5,
                'step': 1
            },
            'max_hold_bars': {
                'current': max_hold_bars,
                'min': 10,
                'max': 120,
                'step': 5
            }
        }

        self._log(f"\n✅ STARTING PARAMETER SET")
        self._log(f"   Ready for calibration loop")
        self._log(f"   profit_target = ₹{params['profit_target']['current']:.2f}")
        self._log(f"   stop_loss = ₹{params['stop_loss']['current']:.2f}")
        self._log(f"   entry_pid_kp = {params['entry_pid_kp']['current']:.3f}")
        self._log(f"\n{'='*80}\n")

        return params

    def _get_default_params(self):
        """Default parameters when insufficient data"""
        return {
            'profit_target': {'current': 1.00, 'min': 0.25, 'max': 3.00},
            'stop_loss': {'current': -0.50, 'min': -3.00, 'max': -0.10},
            'entry_pid_kp': {'current': 0.10, 'min': 0.05, 'max': 0.25},
            'exit_pid_kp': {'current': 0.10, 'min': 0.05, 'max': 0.25},
            'min_hold_bars': {'current': 2, 'min': 1, 'max': 5},
            'max_hold_bars': {'current': 20, 'min': 10, 'max': 120},
        }


class AdaptiveParameterCalibrator:
    """
    Meta-learning system that automatically tunes parameters

    Three phases:
    1. Random Exploration (Runs 1-50): Try different combinations
    2. Bayesian Optimization (Runs 51-250): Focus on promising regions
    3. Fine-Tuning (Runs 251-500): Micro-adjust around best
    """

    def __init__(self, dcs_system_class, verbose=True):
        """
        Initialize calibrator

        Args:
            dcs_system_class: Reference to complete DCS system class
            verbose: Print debug messages
        """
        self.dcs_system = dcs_system_class
        self.verbose = verbose

        self.parameter_history = []
        self.performance_history = []
        self.run_count = 0

        # For tracking best found
        self.best_win_rate = 0.0
        self.best_params = None
        self.best_run = 0

    def _log(self, message):
        if self.verbose:
            print(message)

    def run_single_backtest(self, df_data, symbols_list, params_dict):
        """
        Run complete backtest with given parameters

        Args:
            df_data: Dict of {symbol: DataFrame}
            symbols_list: List of symbols to test
            params_dict: Parameter set to test

        Returns:
            metrics dict with win_rate, pnl, sharpe, etc.
        """

        total_trades = 0
        total_pnl = 0.0
        winning_trades = 0
        all_pnls = []

        # Test across all symbols
        for symbol in symbols_list:
            if symbol not in df_data:
                continue

            df_sym = df_data[symbol]
            if len(df_sym) < 100:
                continue

            # Create DCS instance with current parameters
            dcs = self.dcs_system(
                symbol=symbol,
                initial_capital=1000000,
                profit_target=params_dict['profit_target'],
                stop_loss=params_dict['stop_loss'],
                entry_pid_kp=params_dict['entry_pid_kp'],
                exit_pid_kp=params_dict['exit_pid_kp'],
                min_hold_bars=int(params_dict['min_hold_bars']),
                max_hold_bars=int(params_dict['max_hold_bars'])
            )

            # Run backtest on this symbol
            results = dcs.backtest(df_sym)

            # Accumulate results
            if results:
                for trade in results['trades']:
                    total_trades += 1
                    total_pnl += trade['pnl']
                    if trade['pnl'] > 0:
                        winning_trades += 1
                    all_pnls.append(trade['pnl'])

        # Calculate metrics
        if total_trades == 0:
            return {
                'win_rate': 0.0,
                'total_pnl': 0.0,
                'avg_pnl': 0.0,
                'total_trades': 0,
                'sharpe': 0.0,
                'max_drawdown': 0.0
            }

        win_rate = winning_trades / total_trades
        avg_pnl = total_pnl / total_trades

        # Calculate Sharpe ratio
        pnl_array = np.array(all_pnls)
        pnl_std = pnl_array.std()
        pnl_mean = pnl_array.mean()
        sharpe = pnl_mean / pnl_std if pnl_std > 0 else 0.0

        # Calculate max drawdown (cumulative)
        cumulative_pnl = np.cumsum(pnl_array)
        running_max = np.maximum.accumulate(cumulative_pnl)
        drawdown = cumulative_pnl - running_max
        max_drawdown = drawdown.min() if len(drawdown) > 0 else 0.0

        return {
            'win_rate': float(win_rate),
            'total_pnl': float(total_pnl),
            'avg_pnl': float(avg_pnl),
            'total_trades': int(total_trades),
            'sharpe': float(sharpe),
            'max_drawdown': float(max_drawdown)
        }

    def _phase1_random_exploration(self, param_ranges):
        """Phase 1: Random exploration (Runs 1-50)"""

        params = {}
        for param_name, param_config in param_ranges.items():
            current = param_config['current']
            min_val = param_config['min']
            max_val = param_config['max']

            # Random value in range
            params[param_name] = np.random.uniform(min_val, max_val)

        return params

    def _phase2_bayesian_optimization(self, param_ranges):
        """Phase 2: Bayesian optimization (Runs 51-250)"""

        # If we have some history, bias toward good regions
        if len(self.performance_history) > 10:
            # Find top 3 performing parameter sets
            indices = np.argsort([p['win_rate'] for p in self.performance_history])[-3:]

            # Start from average of top 3, add small random perturbation
            best_params = self.parameter_history[indices[0]]

            params = {}
            for param_name, value in best_params.items():
                param_config = param_ranges[param_name]
                min_val = param_config['min']
                max_val = param_config['max']

                # Add small random walk around best
                perturbation = np.random.normal(0, (max_val - min_val) * 0.1)
                new_val = value + perturbation
                params[param_name] = np.clip(new_val, min_val, max_val)

            return params
        else:
            return self._phase1_random_exploration(param_ranges)

    def _phase3_fine_tuning(self, param_ranges):
        """Phase 3: Fine-tuning (Runs 251-500)"""

        # Converge around best found
        if self.best_params:
            params = {}
            for param_name, value in self.best_params.items():
                param_config = param_ranges[param_name]
                min_val = param_config['min']
                max_val = param_config['max']
                step = param_config.get('step', (max_val - min_val) / 20)

                # Very small perturbation
                perturbation = np.random.normal(0, step * 0.3)
                new_val = value + perturbation
                params[param_name] = np.clip(new_val, min_val, max_val)

            return params
        else:
            return self._phase2_bayesian_optimization(param_ranges)

    def calibrate(self, df_data, symbols_list, param_ranges, target_runs=500):
        """
        Main calibration loop

        Args:
            df_data: Dict of {symbol: DataFrame}
            symbols_list: List of symbols to test
            param_ranges: Parameter configuration
            target_runs: Number of iterations to run

        Returns:
            Best parameters found
        """

        self._log(f"\n{'='*80}")
        self._log(f"ADAPTIVE PARAMETER CALIBRATION")
        self._log(f"Target: {target_runs} runs")
        self._log(f"Symbols: {len(symbols_list)}")
        self._log(f"{'='*80}")

        for run_num in range(target_runs):
            self._log(f"\n{'─'*80}")
            self._log(f"RUN {run_num + 1}/{target_runs}")
            self._log(f"{'─'*80}")

            # Determine which phase we're in
            if run_num < 50:
                phase = "🔀 RANDOM EXPLORATION"
                test_params = self._phase1_random_exploration(param_ranges)
            elif run_num < 250:
                phase = "🎯 BAYESIAN OPTIMIZATION"
                test_params = self._phase2_bayesian_optimization(param_ranges)
            else:
                phase = "🔬 FINE-TUNING"
                test_params = self._phase3_fine_tuning(param_ranges)

            self._log(f"Phase: {phase}")

            # Run backtest with these parameters
            metrics = self.run_single_backtest(df_data, symbols_list, test_params)

            # Track results
            self.parameter_history.append(test_params)
            self.performance_history.append(metrics)

            # Update best if improved
            if metrics['win_rate'] > self.best_win_rate:
                self.best_win_rate = metrics['win_rate']
                self.best_params = test_params.copy()
                self.best_run = run_num + 1
                print(f"\n🏆 NEW BEST (Run {self.best_run}): Win Rate = {self.best_win_rate:.2%}")

            # Print results
            self._log(f"\nResults:")
            self._log(f"  Win Rate:      {metrics['win_rate']:.2%}")
            self._log(f"  Total Trades:  {metrics['total_trades']}")
            self._log(f"  Total P&L:     ₹{metrics['total_pnl']:+,.0f}")
            self._log(f"  Avg P&L:       ₹{metrics['avg_pnl']:+,.2f}")
            self._log(f"  Sharpe:        {metrics['sharpe']:+.3f}")
            self._log(f"  Max Drawdown:  {metrics['max_drawdown']:+.2%}")

            self._log(f"\nParameters:")
            for param_name, value in test_params.items():
                self._log(f"  {param_name:20} = {value:8.3f}")

            self.run_count += 1

        # Final results
        self._log(f"\n{'='*80}")
        self._log(f"CALIBRATION COMPLETE")
        self._log(f"{'='*80}")
        self._log(f"\nBest Configuration (Run {self.best_run}/{target_runs}):")
        self._log(f"  Win Rate: {self.best_win_rate:.2%}")

        if self.best_params:
            self._log(f"\nOptimal Parameters:")
            for param_name, value in self.best_params.items():
                self._log(f"  {param_name:20} = {value:8.3f}")

        return self.best_params

    def save_calibration_results(self, filename="calibration_results.json"):
        """Save complete calibration history to file"""

        results = {
            'total_runs': self.run_count,
            'best_run': self.best_run,
            'best_win_rate': float(self.best_win_rate),
            'best_params': self.best_params,
            'run_history': []
        }

        for i, (params, metrics) in enumerate(zip(self.parameter_history, self.performance_history)):
            results['run_history'].append({
                'run': i + 1,
                'parameters': params,
                'metrics': metrics
            })

        with open(filename, 'w') as f:
            json.dump(results, f, indent=2, default=str)

        self._log(f"\n✅ Calibration results saved: {filename}")


# ============================================================================
# INTEGRATION WITH UNIFIED_P01D_GOVERNOR_STAGE6
# ============================================================================

class EnhancedP01DGovernor:
    """
    Enhanced P01D Governor with:
    - Relative synchronization thresholds
    - Adaptive parameters
    - Smart initialization
    """

    def __init__(self, symbol, initial_capital=1000000,
                 profit_target=1.0, stop_loss=-0.5,
                 entry_pid_kp=0.1, exit_pid_kp=0.1,
                 min_hold_bars=2, max_hold_bars=20,
                 verbose=True):
        """
        Initialize Enhanced P01D with adaptive parameters

        Args:
            symbol: Stock symbol
            initial_capital: Starting capital
            profit_target: ₹ per share target (learnable)
            stop_loss: ₹ per share stop (learnable)
            entry_pid_kp: Entry PID proportional gain (learnable)
            exit_pid_kp: Exit PID proportional gain (learnable)
            min_hold_bars: Minimum hold (learnable)
            max_hold_bars: Maximum hold (learnable)
            verbose: Print debug
        """

        self.symbol = symbol
        self.capital = initial_capital
        self.verbose = verbose

        # LEARNABLE PARAMETERS (from calibration)
        self.profit_target = profit_target
        self.stop_loss = stop_loss
        self.entry_pid_kp = entry_pid_kp
        self.exit_pid_kp = exit_pid_kp
        self.min_hold_bars = min_hold_bars
        self.max_hold_bars = max_hold_bars

        # FROZEN PARAMETERS (not learning)
        self.entry_signal_target = 0.75
        self.exit_signal_target = 0.25
        self.entry_pid_ki = 0.01
        self.entry_pid_kd = 0.01
        self.exit_pid_ki = 0.01
        self.exit_pid_kd = 0.01

        # RELATIVE THRESHOLDS (will be set from data)
        self.sync_thresholds = None

        # Tracking
        self.trade_history = []

    def set_relative_thresholds(self, df_history):
        """
        Set relative sync thresholds from market data
        (called once during initialization)
        """
        calculator = RelativeSynchronizationThresholds(verbose=self.verbose)
        self.sync_thresholds = calculator.calculate_from_data(df_history, self.symbol)

    def backtest(self, df_data):
        """Run complete backtest with current parameters"""
        # TODO: Integrate with unified P01D execution
        # Returns: {'trades': [...], 'summary': {...}}
        pass


# ============================================================================
# EXAMPLE USAGE
# ============================================================================

if __name__ == "__main__":
    print("\n" + "="*80)
    print("ADAPTIVE PARAMETER CALIBRATOR WITH RELATIVE THRESHOLDS")
    print("="*80)
    print("\n✅ Level 1: Relative Synchronization Thresholds")
    print("   - dP/dt normalized to ATR")
    print("   - dV/dt normalized to average volume")
    print("   - Adapts to each symbol automatically")
    print("\n✅ Level 2: Adaptive Parameter Calibrator")
    print("   - Random exploration (Runs 1-50)")
    print("   - Bayesian optimization (Runs 51-250)")
    print("   - Fine-tuning convergence (Runs 251-500)")
    print("   - Learns: profit_target, stop_loss, PID gains, hold periods")
    print("\n✅ Starting Values: Smart initialization from market data")
    print("   - Not random")
    print("   - Based on ATR, volatility, volume patterns")
    print("\nReady for integration!\n")
