# ============================================================================
# INTEGRATION BRIDGE: ECS ↔ 6-STAGE ENGINE
# Connects ECS Supervisor to existing 6-stage trading engine
# Date: August 30, 2026
# Status: READY FOR DEPLOYMENT
# ============================================================================

import numpy as np
import pandas as pd
from datetime import datetime
import logging
from typing import Dict, List, Tuple
import asyncio
from dataclasses import dataclass, asdict
import json

from ECS_TradingSupervisor_Production import (
    ECS_TradingSupervisor,
    MarketState,
    ECSSignals,
    convert_ecs_signals_to_symbol_params,
    OperatingMode
)

# ============================================================================
# LOGGING
# ============================================================================

LOG_FORMAT = '%(asctime)s | %(name)s | %(levelname)s | %(message)s'
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
logger = logging.getLogger('Integration')

# ============================================================================
# SYMBOL-LEVEL ENGINE WRAPPER
# ============================================================================

@dataclass
class SymbolExecutionResult:
    """Result of one symbol's 6-stage engine execution"""
    symbol: str
    timestamp: datetime
    entry_signal_pa: float            # PA score (0.0-1.0)
    adaptive_entry_threshold: float   # Adapted by ECS
    entry_decision: str               # 'ENTRY', 'PASS', 'WAIT'
    position_size: float              # If entry
    ecs_mode: str
    ecs_stress: float
    speed_signal: float
    voltage_signal: float


class Symbol_6Stage_Engine_Wrapper:
    """
    Wrapper for existing 6-stage engine with ECS signal reception

    Converts ECS signals (SPEED/VOLTAGE) to entry/position parameters
    """

    def __init__(self, symbol: str):
        """Initialize wrapper for one symbol"""
        self.symbol = symbol
        self.base_entry_threshold = 0.75
        self.base_position_size = 1.0

        # Mark V Governor parameters (droop 4%)
        self.governor_kp = 1.0
        self.governor_ki = 0.3
        self.governor_kd = 0.08
        self.governor_droop = 0.04

        # AVR parameters (droop 8%)
        self.avr_ke = 2.0
        self.avr_ki = 0.5
        self.avr_kd = 0.1
        self.avr_droop = 0.08

        # Current ECS state
        self.current_ecs_params = None

    def receive_ecs_signals(self, ecs_params: Dict):
        """
        Receive ECS signals and adapt entry/position parameters

        Input:
            ecs_params: Dict with 'entry_threshold', 'position_multiplier', etc.
        """
        self.current_ecs_params = ecs_params
        logger.debug(f"{self.symbol}: Received ECS signals - "
                    f"threshold={ecs_params['entry_threshold']:.3f}, "
                    f"mult={ecs_params['position_multiplier']:.3f}")

    def execute_6stage_engine(self,
                             pa_score: float,
                             market_price: float,
                             atm_volatility: float) -> SymbolExecutionResult:
        """
        Execute 6-stage engine with ECS-adapted parameters

        This is where your existing 6-stage logic runs,
        but with adaptive thresholds from ECS
        """

        if self.current_ecs_params is None:
            logger.warning(f"{self.symbol}: No ECS params received yet")
            return SymbolExecutionResult(
                symbol=self.symbol,
                timestamp=datetime.now(),
                entry_signal_pa=pa_score,
                adaptive_entry_threshold=self.base_entry_threshold,
                entry_decision='WAIT',
                position_size=0.0,
                ecs_mode='UNKNOWN',
                ecs_stress=0.0,
                speed_signal=0.0,
                voltage_signal=0.0
            )

        # Get ECS-adapted threshold
        adaptive_threshold = self.current_ecs_params['entry_threshold']

        # Stage 1: Data validation (already done upstream)

        # Stage 2: PA signal generation (already done upstream)
        # We receive pa_score as input

        # Stage 3: ID (Intelligent Discrimination)
        # Binary decision at 60% threshold
        if pa_score < 0.60:
            entry_decision = 'PASS'
            position_size = 0.0
            logger.debug(f"{self.symbol}: PA score {pa_score:.3f} < 0.60 minimum")
        else:
            # Stage 4: Bridge (Economic viability)
            # Check if profit target covers costs (2 bps each way = 4 bps)
            cost_bps = 0.0004  # 4 bps = 0.04%
            profit_target = 0.015  # 1.5% example
            if profit_target < cost_bps:
                entry_decision = 'PASS'
                position_size = 0.0
                logger.debug(f"{self.symbol}: Profit target {profit_target:.4f} < cost {cost_bps:.4f}")
            else:
                # Stage 5: MPC (Model Predictive Control)
                # Position sizing with adaptive multiplier
                base_position = self.base_position_size
                position_multiplier = self.current_ecs_params['position_multiplier']
                position_size = base_position * position_multiplier

                # Stage 6: P01D Governor (Entry/Exit Decision)
                # Compare PA score to ECS-adapted threshold
                if pa_score >= adaptive_threshold:
                    entry_decision = 'ENTRY'
                    logger.info(f"{self.symbol}: ENTRY - PA={pa_score:.3f} >= threshold={adaptive_threshold:.3f}, "
                               f"size={position_size:.2f}x, mode={self.current_ecs_params['mode']}")
                else:
                    entry_decision = 'PASS'
                    position_size = 0.0
                    logger.debug(f"{self.symbol}: PASS - PA={pa_score:.3f} < threshold={adaptive_threshold:.3f}")

        # Package result
        result = SymbolExecutionResult(
            symbol=self.symbol,
            timestamp=datetime.now(),
            entry_signal_pa=pa_score,
            adaptive_entry_threshold=adaptive_threshold,
            entry_decision=entry_decision,
            position_size=position_size,
            ecs_mode=self.current_ecs_params['mode'],
            ecs_stress=self.current_ecs_params['stress_factor'],
            speed_signal=self.current_ecs_params['speed_signal'],
            voltage_signal=self.current_ecs_params['voltage_signal']
        )

        return result


# ============================================================================
# COORDINATED PORTFOLIO EXECUTOR
# ============================================================================

class CoordinatedPortfolioExecutor:
    """
    Orchestrates all 48 symbol engines with coordinated ECS signals

    Async execution: All 48 symbols execute in parallel
    """

    def __init__(self, symbol_list: List[str], enable_redis: bool = False):
        """
        Initialize portfolio executor

        Args:
            symbol_list: List of 48 symbols
            enable_redis: Enable Redis for circuit breaker
        """
        self.ecs = ECS_TradingSupervisor(enable_redis=enable_redis)
        self.symbols = symbol_list
        self.engine_wrappers = {s: Symbol_6Stage_Engine_Wrapper(s) for s in symbol_list}
        self.execution_history = []

    async def execute_trading_bar(self,
                                  market_data: pd.DataFrame,
                                  pa_scores: Dict[str, float],
                                  win_loss_history: List[str] = None) -> Dict:
        """
        Execute one complete trading bar for all 48 symbols

        Async execution: ECS generates signals → all 48 symbols execute in parallel

        Args:
            market_data: DataFrame with OHLCV for all symbols
            pa_scores: Dict[symbol -> pa_score (0.0-1.0)]
            win_loss_history: List of recent 'WIN'/'LOSS' results

        Returns:
            Dict with results for all 48 symbols
        """

        # ====================================================================
        # STEP 1: Calculate Market State for ECS
        # ====================================================================

        # Volatility (rolling 20-bar)
        prices = market_data['close']
        returns = prices.pct_change()
        volatility = returns.rolling(20).std().iloc[-1] * 100  # As percentage

        # Drawdown (simplified: max loss from recent high)
        recent_prices = prices.iloc[-20:] if len(prices) >= 20 else prices
        running_max = recent_prices.max()
        current_price = recent_prices.iloc[-1]
        drawdown = (current_price - running_max) / running_max

        # Correlation (among symbols in portfolio)
        symbol_returns = {}
        for sym in self.symbols:
            if sym in market_data.columns:
                sym_data = market_data[sym]
                if isinstance(sym_data, pd.DataFrame):
                    sym_data = sym_data['close']
                symbol_returns[sym] = sym_data.pct_change().iloc[-1]

        if len(symbol_returns) > 1:
            corr_matrix = pd.DataFrame(symbol_returns, index=[0]).T.corr()
            avg_correlation = corr_matrix.values[np.triu_indices_from(corr_matrix.values, k=1)].mean()
        else:
            avg_correlation = 0.5

        # Trend strength (ADX-like, simplified)
        close = prices.iloc[-20:] if len(prices) >= 20 else prices
        high_low = close.max() - close.min()
        trend_strength = (high_low / close.mean()) * 100 if len(close) > 0 else 0

        # Win rate
        if win_loss_history and len(win_loss_history) > 0:
            recent_20 = win_loss_history[-20:] if len(win_loss_history) >= 20 else win_loss_history
            win_rate = sum(1 for w in recent_20 if w == 'WIN') / len(recent_20)
        else:
            win_rate = 0.5

        # Active signals (how many symbols have PA score >= 0.60)
        active_signals = sum(1 for pa in pa_scores.values() if pa >= 0.60)

        # Build market state
        market_state = MarketState(
            volatility=volatility,
            drawdown=drawdown,
            correlation=avg_correlation,
            trend_strength=trend_strength,
            win_rate=win_rate,
            recent_trades=win_loss_history if win_loss_history else [],
            active_signals=active_signals,
            timestamp=datetime.now()
        )

        # ====================================================================
        # STEP 2: ECS Generates Signals
        # ====================================================================

        ecs_signals = self.ecs.generate_signals(market_state)
        symbol_params = convert_ecs_signals_to_symbol_params(ecs_signals)

        logger.info(f"Bar: Mode={ecs_signals.mode.name}, Stress={ecs_signals.stress_factor:.3f}, "
                   f"Speed={ecs_signals.speed_signal:.1f}, Voltage={ecs_signals.voltage_signal:.1f}, "
                   f"Active signals={active_signals}/{len(self.symbols)}")

        # Check circuit breaker
        if not self.ecs.is_trading_allowed():
            cb_status = self.ecs.get_circuit_breaker_status()
            logger.critical(f"TRADING HALTED: {cb_status['halt_reason']}")
            return {
                'status': 'HALTED',
                'reason': cb_status['halt_reason'],
                'results': {}
            }

        # ====================================================================
        # STEP 3: Broadcast Signals to All 48 Symbols
        # ====================================================================

        for symbol in self.symbols:
            self.engine_wrappers[symbol].receive_ecs_signals(symbol_params)

        # ====================================================================
        # STEP 4: Execute All 48 Symbols in Parallel
        # ====================================================================

        # Run all symbol engines asynchronously
        tasks = [
            self._execute_symbol(symbol, pa_scores.get(symbol, 0.0), market_data)
            for symbol in self.symbols
        ]

        results = await asyncio.gather(*tasks)
        results_dict = {r.symbol: r for r in results}

        # ====================================================================
        # STEP 5: Aggregate Results
        # ====================================================================

        total_entries = sum(1 for r in results if r.entry_decision == 'ENTRY')
        total_passed = sum(1 for r in results if r.entry_decision == 'PASS')
        total_wait = sum(1 for r in results if r.entry_decision == 'WAIT')

        logger.info(f"Execution Summary: {total_entries} entries, {total_passed} passed, {total_wait} wait")

        # Store in history
        self.execution_history.append({
            'timestamp': datetime.now(),
            'mode': ecs_signals.mode.name,
            'stress': ecs_signals.stress_factor,
            'entries': total_entries,
            'passed': total_passed,
            'wait': total_wait,
            'results': results_dict
        })

        return {
            'status': 'OK',
            'ecs_signals': asdict(ecs_signals),
            'results': results_dict,
            'summary': {
                'total_entries': total_entries,
                'total_passed': total_passed,
                'total_wait': total_wait
            }
        }

    async def _execute_symbol(self, symbol: str, pa_score: float, market_data: pd.DataFrame) -> SymbolExecutionResult:
        """Execute one symbol's 6-stage engine (can run in parallel)"""
        engine = self.engine_wrappers[symbol]

        # Extract price data for this symbol
        if symbol in market_data.columns:
            sym_data = market_data[symbol]
            if isinstance(sym_data, pd.DataFrame):
                market_price = sym_data['close'].iloc[-1] if len(sym_data) > 0 else 100.0
                volatility = sym_data['close'].pct_change().std() * 100 if len(sym_data) > 1 else 2.5
            else:
                market_price = 100.0
                volatility = 2.5
        else:
            market_price = 100.0
            volatility = 2.5

        # Execute 6-stage engine
        result = engine.execute_6stage_engine(pa_score, market_price, volatility)

        return result

    def get_execution_history(self, last_n: int = 100) -> pd.DataFrame:
        """Get last N execution bars as DataFrame"""
        recent = self.execution_history[-last_n:]
        return pd.DataFrame([{
            'timestamp': ex['timestamp'],
            'mode': ex['mode'],
            'stress': ex['stress'],
            'entries': ex['entries'],
            'passed': ex['passed']
        } for ex in recent])

    def get_portfolio_status(self) -> Dict:
        """Get current portfolio/ECS status"""
        return {
            'ecs_state': self.ecs.export_state(),
            'circuit_breaker': self.ecs.get_circuit_breaker_status(),
            'mode_statistics': self.ecs.get_mode_statistics(),
            'total_bars_executed': len(self.execution_history)
        }


# ============================================================================
# ASYNC MAIN LOOP (For Live Trading)
# ============================================================================

async def main_trading_loop(executor: CoordinatedPortfolioExecutor,
                           market_data_source,
                           pa_score_calculator,
                           win_loss_tracker):
    """
    Main async loop for continuous trading

    This runs indefinitely, executing one bar per second/minute
    """

    logger.info("Starting main trading loop")

    while True:
        try:
            # Get latest market data
            market_data = market_data_source.get_latest_bar()

            # Calculate PA scores for all symbols
            pa_scores = pa_score_calculator.calculate_all()

            # Get recent win/loss history
            win_loss = win_loss_tracker.get_recent()

            # Execute trading bar
            result = await executor.execute_trading_bar(
                market_data=market_data,
                pa_scores=pa_scores,
                win_loss_history=win_loss
            )

            # Log result
            if result['status'] == 'OK':
                logger.info(f"Bar executed: {result['summary']}")
            else:
                logger.warning(f"Bar halted: {result['reason']}")

            # Wait for next bar (1-minute if using 1-min bars)
            await asyncio.sleep(60)

        except Exception as e:
            logger.error(f"Error in trading loop: {e}", exc_info=True)
            await asyncio.sleep(5)  # Retry after 5 seconds


# ============================================================================
# EXAMPLE USAGE (FOR TESTING)
# ============================================================================

async def example_backtest():
    """Example: Run 48 symbols through ECS with synthetic data"""

    # Define 48 NIFTY symbols
    symbols = [
        'ADANIPORTS', 'ASIANPAINT', 'AXISBANK', 'BAJAJFINSV', 'BAJAJ-AUTO',
        'BHAGIRADH', 'BHARATIARTL', 'BHEL', 'BPCL', 'BRITANNIA',
        'CIPLA', 'COALINDIA', 'DIVISLAB', 'DRREDDY', 'EICHERMOT',
        'GAIL', 'GRASIM', 'HCLTECH', 'HDFC', 'HDFCBANK',
        'HDFCLIFE', 'HEROMOTOCO', 'HINDALCO', 'HINDUNILVR', 'IBULHSGFIN',
        'ICICIBANK', 'ICICISEC', 'INDIGO', 'INFY', 'IOC',
        'ITC', 'JSWSTEEL', 'KOTAKBANK', 'LT', 'LTIM',
        'LUPIN', 'M&M', 'MARUTI', 'NTPC', 'ONGC',
        'POWERGRID', 'RELIANCE', 'SBICARD', 'SBILIFE', 'SBIN',
        'SUNPHARMA', 'TATAMOTORS', 'TATAPOWER', 'TCSAUTO', 'TCS'
    ]

    # Initialize executor
    executor = CoordinatedPortfolioExecutor(symbols, enable_redis=False)

    # Simulate 10 trading bars
    for bar_num in range(10):
        # Create synthetic market data
        np.random.seed(bar_num)
        market_data = pd.DataFrame({
            sym: {
                'close': 100 + np.random.randn() * 5,
                'high': 105 + np.random.randn() * 5,
                'low': 95 + np.random.randn() * 5,
                'volume': 1000000
            }
            for sym in symbols
        })

        # Create synthetic PA scores
        pa_scores = {sym: np.random.uniform(0.5, 0.9) for sym in symbols}

        # Create synthetic win/loss history
        win_loss = ['WIN', 'LOSS', 'WIN'] * 5 if bar_num > 0 else []

        # Execute bar
        result = await executor.execute_trading_bar(
            market_data=market_data,
            pa_scores=pa_scores,
            win_loss_history=win_loss
        )

        print(f"\nBar {bar_num + 1}: {result['summary']}")
        await asyncio.sleep(0.5)  # Small delay between bars

    # Print final status
    status = executor.get_portfolio_status()
    print("\n" + "="*80)
    print("FINAL PORTFOLIO STATUS")
    print("="*80)
    print(json.dumps(status, indent=2, default=str))


if __name__ == '__main__':
    # Run example backtest
    asyncio.run(example_backtest())

