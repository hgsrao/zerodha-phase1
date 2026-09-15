#!/usr/bin/env python3
"""
TEST: Single Symbol Intraday with Triple-Layer Integration
=========================================================

Run INFY (or any symbol) intraday with:
- ECS (Voltage/Speed signals)
- Synchronizer (NIFTY 50 context)
- Grid (Market structure risk)

This is a WORKING test that demonstrates the integration.

Usage:
    $env:KITE_API_KEY = "your_key"
    $env:KITE_ACCESS_TOKEN = "your_token"
    python TEST_SINGLE_SYMBOL_INTRADAY_INTEGRATION.py

Output:
    - Live monitoring of entry/exit signals
    - ECS adjustments in real-time
    - NIFTY 50 context checks
    - Grid risk assessments
    - Paper trade execution logs
"""

import os
import sys
import json
import logging
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Dict, Optional, List
import time

import pandas as pd
import numpy as np
from kiteconnect import KiteConnect

# Import our modules
from ECS_TradingSupervisor_Production import (
    ECS_TradingSupervisor,
    MarketState,
)

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger('SINGLE_SYMBOL_TEST')

# ============================================================================
# CONFIGURATION
# ============================================================================

TEST_SYMBOL = "INFY"  # Single symbol to test
NOTIONAL_PER_TRADE = 10000.0  # ₹10,000 per trade
POLL_INTERVAL_SECONDS = 60  # Check every 60 seconds
MARKET_OPEN = dtime(9, 15)
MARKET_CLOSE = dtime(15, 30)

# Log files
PROJECT_ROOT = Path(__file__).parent
LOG_DIR = PROJECT_ROOT / "test_logs"
LOG_DIR.mkdir(exist_ok=True)

SIGNALS_LOG = LOG_DIR / f"signals_{TEST_SYMBOL}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
TRADES_LOG = LOG_DIR / f"trades_{TEST_SYMBOL}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
MONITORING_LOG = LOG_DIR / f"monitoring_{TEST_SYMBOL}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"


# ============================================================================
# KITE SETUP: Fetch NIFTY 50 Data from Kite Connect
# ============================================================================

class KiteDataFetcher:
    """Fetch live data from Kite Connect (NIFTY 50 + symbol)"""

    def __init__(self, api_key: str, access_token: str):
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        logger.info("Kite Connect initialized")

    def get_live_quote(self, symbol: str) -> Dict:
        """Get current price and change % for a symbol"""
        try:
            data = self.kite.quote(symbols=[symbol])
            if symbol in data['data']:
                quote = data['data'][symbol]
                return {
                    'symbol': symbol,
                    'last_price': quote['last_price'],
                    'change': quote['change'],
                    'timestamp': datetime.now().isoformat()
                }
            else:
                logger.error(f"Symbol {symbol} not found in quote response")
                return None
        except Exception as e:
            logger.error(f"Error fetching quote for {symbol}: {e}")
            return None

    def get_minute_bars(self, symbol: str, interval: str = "minute", limit: int = 100) -> Optional[pd.DataFrame]:
        """
        Get 1-minute or 5-minute candles from Kite

        IMPORTANT: This fetches from Kite Connect historical data
        Symbol format: "NSE:INFY" or use instrument token
        """
        try:
            logger.info(f"Fetching {limit} {interval} bars for {symbol}...")

            # Get instrument token
            instruments = self.kite.instruments("NSE")
            instrument_token = None

            for instrument in instruments:
                if instrument['tradingsymbol'] == symbol:
                    instrument_token = instrument['instrument_token']
                    break

            if not instrument_token:
                logger.error(f"Could not find instrument token for {symbol}")
                return None

            # Fetch historical data
            data = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=datetime.now() - timedelta(days=1),
                to_date=datetime.now(),
                interval=interval  # "minute" or "5minute"
            )

            if not data:
                logger.warning(f"No historical data returned for {symbol}")
                return None

            # Convert to DataFrame
            df = pd.DataFrame(data)
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
            df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            logger.info(f"Fetched {len(df)} bars for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error fetching bars for {symbol}: {e}")
            return None

    def get_nifty_bars(self, limit: int = 50) -> Optional[pd.DataFrame]:
        """Fetch NIFTY 50 index bars - THIS IS CRITICAL!"""
        return self.get_minute_bars("NIFTY 50", interval="minute", limit=limit)

    def get_nifty_quote(self) -> Dict:
        """Get current NIFTY 50 quote"""
        return self.get_live_quote("NIFTY 50")


# ============================================================================
# SYNCHRONIZER: NIFTY 50 Context
# ============================================================================

class SimpleSynchronizer:
    """Lightweight synchronizer for single symbol testing"""

    def __init__(self, kite_fetcher: KiteDataFetcher):
        self.fetcher = kite_fetcher
        self.nifty_quote = None
        self.nifty_change = 0.0
        self.market_regime = "NEUTRAL"

    def update(self):
        """Fetch current NIFTY 50 and determine regime"""
        try:
            self.nifty_quote = self.fetcher.get_nifty_quote()

            if self.nifty_quote:
                self.nifty_change = self.nifty_quote['change']
                self.market_regime = self._classify_regime(self.nifty_change)

                logger.info(f"NIFTY 50: {self.nifty_quote['last_price']:.0f} "
                           f"({self.nifty_change:+.2f}%) → {self.market_regime}")
        except Exception as e:
            logger.error(f"Error updating synchronizer: {e}")

    def _classify_regime(self, change_pct: float) -> str:
        """Classify market regime based on NIFTY change"""
        if change_pct < -2.0:
            return 'STRONG_DOWN'
        elif change_pct < -1.0:
            return 'DOWN'
        elif change_pct < -0.5:
            return 'SLIGHT_DOWN'
        elif change_pct > 2.0:
            return 'STRONG_UP'
        elif change_pct > 1.0:
            return 'UP'
        else:
            return 'NEUTRAL'

    def should_skip_entry(self) -> bool:
        """Should we skip BUY entries?"""
        return self.market_regime in ['DOWN', 'STRONG_DOWN']

    def should_exit_position(self) -> bool:
        """Should we exit existing position?"""
        return self.market_regime == 'STRONG_DOWN'


# ============================================================================
# GRID: Market Structure Risk
# ============================================================================

class SimpleGrid:
    """Lightweight grid for single symbol testing"""

    def __init__(self, kite_fetcher: KiteDataFetcher):
        self.fetcher = kite_fetcher
        self.risk_level = "MEDIUM"
        self.trend_direction = "NEUTRAL"
        self.volatility_regime = "NORMAL"

    def update(self):
        """Analyze NIFTY 50 structure"""
        try:
            # Fetch NIFTY bars
            nifty_df = self.fetcher.get_nifty_bars(limit=50)

            if nifty_df is not None and len(nifty_df) > 0:
                # Calculate trend (simple: compare close to MA20)
                ma20 = nifty_df['close'].rolling(window=20).mean().iloc[-1]
                current_close = nifty_df['close'].iloc[-1]

                if current_close > ma20:
                    self.trend_direction = "UP"
                elif current_close < ma20:
                    self.trend_direction = "DOWN"
                else:
                    self.trend_direction = "NEUTRAL"

                # Calculate volatility (ATR-based)
                atr = self._calculate_atr(nifty_df)
                if atr > 150:  # High volatility
                    self.volatility_regime = "HIGH"
                elif atr > 100:
                    self.volatility_regime = "NORMAL"
                else:
                    self.volatility_regime = "LOW"

                # Determine risk level
                self.risk_level = self._calculate_risk_level()

                logger.info(f"Grid: trend={self.trend_direction}, "
                           f"volatility={self.volatility_regime}, "
                           f"risk={self.risk_level}, atr={atr:.0f}")
        except Exception as e:
            logger.error(f"Error updating grid: {e}")

    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """Calculate Average True Range"""
        df = df.copy()
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift()),
                abs(df['low'] - df['close'].shift())
            )
        )
        return df['tr'].rolling(window=14).mean().iloc[-1]

    def _calculate_risk_level(self) -> str:
        """Calculate risk level"""
        if self.trend_direction == "DOWN" and self.volatility_regime == "HIGH":
            return "HIGH"
        elif self.trend_direction == "DOWN" or self.volatility_regime == "HIGH":
            return "MEDIUM"
        else:
            return "LOW"

    def should_skip_entry(self) -> bool:
        """Should we skip entries due to risk?"""
        return self.risk_level == "HIGH"


# ============================================================================
# TRIPLE-LAYER ENGINE: Single Symbol Test
# ============================================================================

class SingleSymbolTripleLayerEngine:
    """
    Complete integration test:
    - ECS (voltage/speed)
    - Synchronizer (NIFTY 50)
    - Grid (market structure)
    - Single symbol (INFY)
    - Intraday mode
    """

    def __init__(self, symbol: str, kite_fetcher: KiteDataFetcher):
        self.symbol = symbol
        self.fetcher = kite_fetcher

        # Modules
        self.ecs = ECS_TradingSupervisor()
        self.synchronizer = SimpleSynchronizer(kite_fetcher)
        self.grid = SimpleGrid(kite_fetcher)

        # State
        self.position = None  # None or {'entry_price': float, 'qty': int}
        self.signals_generated = 0
        self.signals_executed = 0
        self.trades_closed = 0
        self.trades = []

        logger.info(f"SingleSymbolTripleLayerEngine initialized for {symbol}")

    def fetch_symbol_bars(self, limit: int = 100) -> Optional[pd.DataFrame]:
        """Fetch symbol bars from Kite"""
        return self.fetcher.get_minute_bars(self.symbol, interval="minute", limit=limit)

    def generate_technical_signal(self, df: pd.DataFrame) -> Optional[str]:
        """
        Simple technical signal: 20-bar MA crossover

        ENTRY: price > MA20 and volume > avg_volume
        EXIT: price < MA20
        """
        if df is None or len(df) < 20:
            return None

        closes = df['close'].values
        volumes = df['volume'].values

        ma20 = np.mean(closes[-20:])
        avg_volume = np.mean(volumes[-20:])

        current_price = closes[-1]
        current_volume = volumes[-1]

        # ENTRY signal
        if current_price > ma20 and current_volume > avg_volume:
            return "BUY"

        # EXIT signal
        elif current_price < ma20 and self.position:
            return "SELL"

        return None

    def check_entry_filters(self, signal: str) -> bool:
        """Apply all 3 layers before entry"""

        if signal != "BUY":
            return True  # Not a buy signal

        # LAYER 1: ECS check
        if self.ecs_stress_factor > 0.5:  # High stress
            logger.warning(f"SKIP: ECS high stress ({self.ecs_stress_factor:.2f})")
            return False

        # LAYER 2: Synchronizer check
        if self.synchronizer.should_skip_entry():
            logger.warning(f"SKIP: Synchronizer says {self.synchronizer.market_regime}")
            return False

        # LAYER 3: Grid check
        if self.grid.should_skip_entry():
            logger.warning(f"SKIP: Grid says HIGH_RISK")
            return False

        return True

    def calculate_ecs_signals(self, df: pd.DataFrame):
        """Get ECS voltage/speed signals"""
        try:
            # Calculate portfolio metrics (simplified for single symbol)
            if self.position:
                current_price = df['close'].iloc[-1]
                entry_value = self.position['entry_price'] * self.position['qty']
                current_value = current_price * self.position['qty']
                drawdown = (current_value - entry_value) / entry_value if entry_value > 0 else 0
            else:
                drawdown = 0.0

            # Calculate volatility
            returns = df['close'].pct_change()
            volatility = returns.std() * np.sqrt(252)  # Annualized

            # Create market state for ECS
            market_state = MarketState(
                volatility=volatility,
                drawdown=drawdown,
                correlation=0.5,  # Simplified
                trend_strength=50,
                win_rate=0.5,
                recent_trades=['WIN', 'LOSS', 'WIN'],  # Simplified
                active_signals=1,
                timestamp=datetime.now()
            )

            # Get ECS signals
            ecs_signals = self.ecs.generate_signals(market_state)

            self.ecs_voltage = ecs_signals.voltage_signal
            self.ecs_speed = ecs_signals.speed_signal
            self.ecs_stress_factor = ecs_signals.stress_factor
            self.ecs_mode = ecs_signals.mode.name

            logger.info(f"ECS: voltage={self.ecs_voltage:+.1f}, "
                       f"speed={self.ecs_speed:+.1f}, "
                       f"stress={self.ecs_stress_factor:+.2f}, "
                       f"mode={self.ecs_mode}")

        except Exception as e:
            logger.error(f"Error calculating ECS signals: {e}")
            self.ecs_voltage = 0
            self.ecs_speed = 0
            self.ecs_stress_factor = 0

    def apply_position_sizing(self, base_qty: int = 1) -> int:
        """Apply ECS voltage to position size"""
        multiplier = 1.0 + (self.ecs_voltage / 100.0)
        adjusted_qty = int(base_qty * multiplier)
        return max(adjusted_qty, 1)

    def execute_entry(self, current_price: float):
        """Execute BUY order"""
        qty = self.apply_position_sizing(base_qty=1)

        self.position = {
            'entry_price': current_price,
            'qty': qty,
            'entry_time': datetime.now().isoformat(),
            'entry_source': 'TRIPLE_LAYER'
        }

        # Log trade
        trade_log = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'action': 'BUY',
            'price': current_price,
            'qty': qty,
            'notional': current_price * qty,
            'ecs_voltage': self.ecs_voltage,
            'nifty_regime': self.synchronizer.market_regime,
            'grid_risk': self.grid.risk_level
        }

        with open(TRADES_LOG, 'a') as f:
            f.write(json.dumps(trade_log) + '\n')

        self.signals_executed += 1
        logger.info(f"EXECUTE: BUY {qty} {self.symbol} @ ₹{current_price:.0f} "
                   f"(voltage={self.ecs_voltage:+.1f}, nifty={self.synchronizer.market_regime})")

    def execute_exit(self, current_price: float, reason: str = "MA_CROSSOVER"):
        """Execute SELL order"""
        if not self.position:
            return

        pnl = (current_price - self.position['entry_price']) * self.position['qty']
        pnl_pct = ((current_price / self.position['entry_price']) - 1) * 100

        # Log trade
        trade_log = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'action': 'SELL',
            'entry_price': self.position['entry_price'],
            'exit_price': current_price,
            'qty': self.position['qty'],
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'nifty_regime': self.synchronizer.market_regime,
            'grid_risk': self.grid.risk_level
        }

        with open(TRADES_LOG, 'a') as f:
            f.write(json.dumps(trade_log) + '\n')

        self.trades.append(trade_log)
        self.trades_closed += 1

        logger.info(f"CLOSE: SELL {self.position['qty']} {self.symbol} @ ₹{current_price:.0f} "
                   f"| P&L: ₹{pnl:+.0f} ({pnl_pct:+.2f}%) | Reason: {reason}")

        self.position = None

    def check_loss_cutting(self, current_price: float):
        """Check if we should cut losses"""
        if not self.position:
            return

        pnl_pct = ((current_price / self.position['entry_price']) - 1) * 100

        # Hard stop: 1% loss
        if pnl_pct < -1.0:
            logger.warning(f"HARD STOP: Loss {pnl_pct:.2f}%")
            self.execute_exit(current_price, reason="HARD_STOP_1PCT")
            return

        # ECS critical
        if self.ecs_voltage < -80:
            logger.warning(f"ECS CRITICAL: voltage {self.ecs_voltage:.1f}")
            self.execute_exit(current_price, reason="ECS_CRITICAL")
            return

        # Synchronizer strong down
        if self.synchronizer.should_exit_position():
            logger.warning(f"SYNC EXIT: NIFTY {self.synchronizer.market_regime}")
            self.execute_exit(current_price, reason=f"SYNC_{self.synchronizer.market_regime}")
            return

    def run_once(self):
        """Execute one cycle"""
        logger.info("="*80)
        logger.info(f"CYCLE: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} | {self.symbol}")

        # Check if market is open
        now = datetime.now().time()
        if not (MARKET_OPEN <= now <= MARKET_CLOSE):
            logger.info(f"Market closed. Skipping cycle.")
            return

        try:
            # STEP 1: Fetch symbol bars
            df = self.fetch_symbol_bars(limit=100)
            if df is None or len(df) == 0:
                logger.warning("No bars fetched")
                return

            current_price = df['close'].iloc[-1]
            logger.info(f"Current price: ₹{current_price:.2f}")

            # STEP 2: Update synchronizer (NIFTY 50 context)
            self.synchronizer.update()

            # STEP 3: Update grid (market structure)
            self.grid.update()

            # STEP 4: Calculate ECS signals
            self.calculate_ecs_signals(df)

            # STEP 5: Generate technical signal
            signal = self.generate_technical_signal(df)
            if signal:
                self.signals_generated += 1
                logger.info(f"Technical signal: {signal}")

            # STEP 6: Apply entry filters
            if signal == "BUY":
                if self.check_entry_filters(signal):
                    self.execute_entry(current_price)
                else:
                    logger.info(f"Signal filtered out")

            # STEP 7: Check exit conditions
            if signal == "SELL" or not signal:
                # Check if in position and need to exit
                if self.position:
                    self.check_loss_cutting(current_price)

                    if signal == "SELL":
                        self.execute_exit(current_price, reason="MA_CROSSOVER")

            # STEP 8: Save monitoring log
            monitor_log = {
                'timestamp': datetime.now().isoformat(),
                'symbol': self.symbol,
                'price': current_price,
                'ecs_voltage': self.ecs_voltage,
                'ecs_speed': self.ecs_speed,
                'ecs_stress': self.ecs_stress_factor,
                'nifty_change': self.synchronizer.nifty_change,
                'nifty_regime': self.synchronizer.market_regime,
                'grid_risk': self.grid.risk_level,
                'position': self.position,
                'signals_generated': self.signals_generated,
                'signals_executed': self.signals_executed,
                'trades_closed': self.trades_closed
            }

            with open(MONITORING_LOG, 'a') as f:
                f.write(json.dumps(monitor_log) + '\n')

            logger.info(f"Stats: signals_gen={self.signals_generated}, "
                       f"signals_exec={self.signals_executed}, "
                       f"trades_closed={self.trades_closed}")

        except Exception as e:
            logger.error(f"Error in run_once: {e}", exc_info=True)

        logger.info("="*80)


# ============================================================================
# MAIN: Run the test
# ============================================================================

def main():
    """Main entry point"""

    # Get credentials from environment
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key or not access_token:
        logger.error("ERROR: Set KITE_API_KEY and KITE_ACCESS_TOKEN environment variables")
        sys.exit(1)

    # Initialize Kite
    fetcher = KiteDataFetcher(api_key, access_token)

    # Create engine
    engine = SingleSymbolTripleLayerEngine(TEST_SYMBOL, fetcher)

    logger.info(f"Starting {TEST_SYMBOL} intraday test...")
    logger.info(f"Signals log: {SIGNALS_LOG}")
    logger.info(f"Trades log: {TRADES_LOG}")
    logger.info(f"Monitoring log: {MONITORING_LOG}")
    logger.info(f"Polling interval: {POLL_INTERVAL_SECONDS} seconds")

    # Main loop
    try:
        while True:
            engine.run_once()

            # Check if market is closed
            now = datetime.now().time()
            if now > MARKET_CLOSE:
                logger.info("Market closed. Exiting.")
                break

            time.sleep(POLL_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logger.info("Test stopped by user")

    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)

    finally:
        # Print final stats
        logger.info("\n" + "="*80)
        logger.info("TEST SUMMARY")
        logger.info("="*80)
        logger.info(f"Symbol: {TEST_SYMBOL}")
        logger.info(f"Signals generated: {engine.signals_generated}")
        logger.info(f"Signals executed: {engine.signals_executed}")
        logger.info(f"Trades closed: {engine.trades_closed}")

        if engine.trades:
            pnl_total = sum(t['pnl'] for t in engine.trades)
            win_count = sum(1 for t in engine.trades if t['pnl'] > 0)
            loss_count = sum(1 for t in engine.trades if t['pnl'] < 0)

            logger.info(f"\nP&L Summary:")
            logger.info(f"  Total P&L: ₹{pnl_total:+.0f}")
            logger.info(f"  Wins: {win_count}")
            logger.info(f"  Losses: {loss_count}")
            logger.info(f"  Win rate: {(win_count/len(engine.trades)*100):.1f}%")

        logger.info("="*80)


if __name__ == "__main__":
    main()
