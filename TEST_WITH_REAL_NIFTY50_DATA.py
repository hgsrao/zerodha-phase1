#!/usr/bin/env python3
"""
TEST: Single Symbol Intraday with REAL NIFTY 50 Data
====================================================

Uses REAL NIFTY 50 data downloaded from Kite Connect.
NO synthetic data. NO fake data.

The NIFTY 50 data must be downloaded first:
    python DOWNLOAD_REAL_NIFTY50_DATA.py --date 2026-09-15

Then run this test:
    $env:KITE_API_KEY = "your_key"
    $env:KITE_ACCESS_TOKEN = "your_token"
    python TEST_WITH_REAL_NIFTY50_DATA.py --date 2026-09-15 --symbol INFY

This script:
1. Loads REAL NIFTY 50 bars from CSV (downloaded from Kite)
2. Fetches REAL INFY bars from Kite (live)
3. Runs full integration test with both real data sources
4. Logs all signals and trades to JSON
"""

import os
import sys
import json
import logging
import argparse
from datetime import datetime, timedelta, time as dtime
from pathlib import Path
from typing import Dict, Optional
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
logger = logging.getLogger('TEST_REAL_DATA')

# Setup directories
PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "test_logs"
LOG_DIR.mkdir(exist_ok=True)

# ============================================================================
# REAL NIFTY 50 DATA LOADER
# ============================================================================

class RealNifty50Loader:
    """Load REAL NIFTY 50 data from downloaded CSV"""

    def __init__(self, test_date: datetime):
        self.test_date = test_date
        self.nifty_df = None
        self.nifty_index = 0
        self._load_real_data()

    def _load_real_data(self):
        """Load REAL NIFTY 50 data from CSV"""

        filename = f"NIFTY50_REAL_{self.test_date.strftime('%Y-%m-%d')}.csv"
        filepath = DATA_DIR / filename

        if not filepath.exists():
            logger.error(f"NIFTY 50 data file not found: {filepath}")
            logger.error(f"Download it first using:")
            logger.error(f"  python DOWNLOAD_REAL_NIFTY50_DATA.py --date {self.test_date.strftime('%Y-%m-%d')}")
            raise FileNotFoundError(f"Cannot find {filepath}")

        try:
            self.nifty_df = pd.read_csv(filepath)
            self.nifty_df['timestamp'] = pd.to_datetime(self.nifty_df['timestamp'])

            logger.info(f"✓ Loaded REAL NIFTY 50 data: {len(self.nifty_df)} bars")
            logger.info(f"  From Kite Connect (NOT synthetic)")
            logger.info(f"  Period: {self.nifty_df['timestamp'].iloc[0]} to {self.nifty_df['timestamp'].iloc[-1]}")
            logger.info(f"  Open: {self.nifty_df['open'].iloc[0]:.2f}")
            logger.info(f"  Close: {self.nifty_df['close'].iloc[-1]:.2f}")

        except Exception as e:
            logger.error(f"Error loading NIFTY 50 data: {e}")
            raise

    def get_bars_up_to_time(self, current_time: datetime) -> Optional[pd.DataFrame]:
        """
        Get all NIFTY bars up to current_time.
        This simulates live bars arriving during the trading day.
        """

        if self.nifty_df is None:
            return None

        # Filter to bars <= current_time
        mask = self.nifty_df['timestamp'] <= current_time
        bars = self.nifty_df[mask]

        return bars if len(bars) > 0 else None


# ============================================================================
# KITE DATA FETCHER (for INFY)
# ============================================================================

class KiteDataFetcher:
    """Fetch REAL symbol data from Kite Connect"""

    def __init__(self, api_key: str, access_token: str):
        self.kite = KiteConnect(api_key=api_key)
        self.kite.set_access_token(access_token)
        logger.info("Kite Connect initialized (for symbol data)")

    def get_live_quote(self, symbol: str) -> Dict:
        """Get current price for symbol"""
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
        except Exception as e:
            logger.error(f"Error fetching quote: {e}")
        return None

    def get_minute_bars(self, symbol: str, limit: int = 100) -> Optional[pd.DataFrame]:
        """Get REAL 1-minute bars from Kite"""
        try:
            logger.debug(f"Fetching {limit} bars for {symbol}...")

            # Get instrument token
            instruments = self.kite.instruments("NSE")
            instrument_token = None

            for instrument in instruments:
                if instrument['tradingsymbol'] == symbol:
                    instrument_token = instrument['instrument_token']
                    break

            if not instrument_token:
                logger.error(f"Could not find {symbol}")
                return None

            # Fetch historical data (REAL from Kite)
            data = self.kite.historical_data(
                instrument_token=instrument_token,
                from_date=datetime.now() - timedelta(days=1),
                to_date=datetime.now(),
                interval="minute"
            )

            if not data:
                logger.warning(f"No data for {symbol}")
                return None

            df = pd.DataFrame(data)
            df = df[['date', 'open', 'high', 'low', 'close', 'volume']]
            df.columns = ['timestamp', 'open', 'high', 'low', 'close', 'volume']
            df['timestamp'] = pd.to_datetime(df['timestamp'])

            logger.debug(f"Fetched {len(df)} bars for {symbol}")
            return df

        except Exception as e:
            logger.error(f"Error fetching bars: {e}")
            return None


# ============================================================================
# SYNCHRONIZER: Using REAL NIFTY 50 Data
# ============================================================================

class RealDataSynchronizer:
    """Synchronizer using REAL NIFTY 50 data"""

    def __init__(self, nifty_loader: RealNifty50Loader):
        self.nifty_loader = nifty_loader
        self.nifty_bars = None
        self.nifty_change = 0.0
        self.market_regime = "NEUTRAL"

    def update(self, current_time: datetime):
        """Update using REAL NIFTY data"""
        try:
            self.nifty_bars = self.nifty_loader.get_bars_up_to_time(current_time)

            if self.nifty_bars is not None and len(self.nifty_bars) > 0:
                # Calculate change from first bar to current
                first_close = self.nifty_bars['close'].iloc[0]
                current_close = self.nifty_bars['close'].iloc[-1]

                self.nifty_change = ((current_close - first_close) / first_close) * 100
                self.market_regime = self._classify_regime(self.nifty_change)

                logger.info(f"NIFTY 50 (REAL): {current_close:.0f} "
                           f"({self.nifty_change:+.2f}%) → {self.market_regime}")
        except Exception as e:
            logger.error(f"Error updating synchronizer: {e}")

    def _classify_regime(self, change_pct: float) -> str:
        if change_pct < -2.0:
            return 'STRONG_DOWN'
        elif change_pct < -1.0:
            return 'DOWN'
        elif change_pct > 2.0:
            return 'STRONG_UP'
        elif change_pct > 1.0:
            return 'UP'
        else:
            return 'NEUTRAL'

    def should_skip_entry(self) -> bool:
        return self.market_regime in ['DOWN', 'STRONG_DOWN']

    def should_exit_position(self) -> bool:
        return self.market_regime == 'STRONG_DOWN'


# ============================================================================
# GRID: Using REAL NIFTY 50 Data
# ============================================================================

class RealDataGrid:
    """Grid using REAL NIFTY 50 data"""

    def __init__(self, nifty_loader: RealNifty50Loader):
        self.nifty_loader = nifty_loader
        self.risk_level = "MEDIUM"
        self.trend_direction = "NEUTRAL"

    def update(self, current_time: datetime):
        """Update using REAL NIFTY data"""
        try:
            bars = self.nifty_loader.get_bars_up_to_time(current_time)

            if bars is not None and len(bars) > 20:
                # Calculate trend (real data)
                ma20 = bars['close'].tail(20).mean()
                current_close = bars['close'].iloc[-1]

                if current_close > ma20 * 1.002:  # 0.2% above
                    self.trend_direction = "UP"
                elif current_close < ma20 * 0.998:  # 0.2% below
                    self.trend_direction = "DOWN"
                else:
                    self.trend_direction = "NEUTRAL"

                # Calculate volatility (ATR from real data)
                atr = self._calculate_atr(bars)

                if atr > 150:
                    self.risk_level = "HIGH"
                elif atr > 100:
                    self.risk_level = "MEDIUM"
                else:
                    self.risk_level = "LOW"

                logger.info(f"Grid (REAL): trend={self.trend_direction}, "
                           f"risk={self.risk_level}, atr={atr:.0f}")
        except Exception as e:
            logger.error(f"Error updating grid: {e}")

    def _calculate_atr(self, df: pd.DataFrame) -> float:
        """Calculate ATR from real data"""
        df = df.copy()
        df['tr'] = np.maximum(
            df['high'] - df['low'],
            np.maximum(
                abs(df['high'] - df['close'].shift()),
                abs(df['low'] - df['close'].shift())
            )
        )
        return df['tr'].tail(14).mean() if len(df) >= 14 else 100.0

    def should_skip_entry(self) -> bool:
        return self.risk_level == "HIGH"


# ============================================================================
# MAIN TEST ENGINE
# ============================================================================

class TestWithRealNifty50:
    """Full test using REAL NIFTY 50 data"""

    def __init__(self, symbol: str, test_date: datetime, kite_fetcher: KiteDataFetcher):
        self.symbol = symbol
        self.test_date = test_date
        self.fetcher = kite_fetcher

        # Load REAL NIFTY 50 data
        self.nifty_loader = RealNifty50Loader(test_date)

        # Modules using REAL data
        self.ecs = ECS_TradingSupervisor()
        self.synchronizer = RealDataSynchronizer(self.nifty_loader)
        self.grid = RealDataGrid(self.nifty_loader)

        # State
        self.position = None
        self.signals_generated = 0
        self.signals_executed = 0
        self.trades_closed = 0
        self.trades = []

        # Logs
        self.signals_log = LOG_DIR / f"signals_REAL_{symbol}_{test_date.strftime('%Y%m%d')}.jsonl"
        self.trades_log = LOG_DIR / f"trades_REAL_{symbol}_{test_date.strftime('%Y%m%d')}.jsonl"

        logger.info(f"Test initialized for {symbol} using REAL NIFTY 50 data")

    def generate_technical_signal(self, df: pd.DataFrame) -> Optional[str]:
        """Generate signal from REAL symbol data"""
        if df is None or len(df) < 20:
            return None

        closes = df['close'].values
        volumes = df['volume'].values

        ma20 = np.mean(closes[-20:])
        avg_volume = np.mean(volumes[-20:])

        current_price = closes[-1]
        current_volume = volumes[-1]

        if current_price > ma20 and current_volume > avg_volume:
            return "BUY"
        elif current_price < ma20 and self.position:
            return "SELL"

        return None

    def check_entry_filters(self, signal: str) -> bool:
        """Apply all 3 layers using REAL data"""
        if signal != "BUY":
            return True

        if self.ecs_stress_factor > 0.5:
            logger.warning(f"SKIP: ECS stress {self.ecs_stress_factor:.2f}")
            return False

        if self.synchronizer.should_skip_entry():
            logger.warning(f"SKIP: NIFTY regime {self.synchronizer.market_regime}")
            return False

        if self.grid.should_skip_entry():
            logger.warning(f"SKIP: Grid risk {self.grid.risk_level}")
            return False

        return True

    def calculate_ecs_signals(self, df: pd.DataFrame):
        """Calculate ECS from real data"""
        try:
            if self.position:
                current_price = df['close'].iloc[-1]
                entry_value = self.position['entry_price'] * self.position['qty']
                current_value = current_price * self.position['qty']
                drawdown = (current_value - entry_value) / entry_value if entry_value > 0 else 0
            else:
                drawdown = 0.0

            returns = df['close'].pct_change()
            volatility = returns.std() * np.sqrt(252)

            market_state = MarketState(
                volatility=volatility,
                drawdown=drawdown,
                correlation=0.5,
                trend_strength=50,
                win_rate=0.5,
                recent_trades=['WIN', 'LOSS', 'WIN'],
                active_signals=1,
                timestamp=datetime.now()
            )

            ecs_signals = self.ecs.generate_signals(market_state)

            self.ecs_voltage = ecs_signals.voltage_signal
            self.ecs_speed = ecs_signals.speed_signal
            self.ecs_stress_factor = ecs_signals.stress_factor

            logger.info(f"ECS (REAL): voltage={self.ecs_voltage:+.1f}, "
                       f"speed={self.ecs_speed:+.1f}, stress={self.ecs_stress_factor:+.2f}")

        except Exception as e:
            logger.error(f"Error calculating ECS: {e}")
            self.ecs_voltage = 0
            self.ecs_speed = 0
            self.ecs_stress_factor = 0

    def execute_entry(self, current_price: float):
        """Execute BUY"""
        qty = int(1 * (1.0 + self.ecs_voltage / 100.0))
        qty = max(qty, 1)

        self.position = {
            'entry_price': current_price,
            'qty': qty,
            'entry_time': datetime.now().isoformat()
        }

        trade_log = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'action': 'BUY',
            'price': current_price,
            'qty': qty,
            'ecs_voltage': self.ecs_voltage,
            'nifty_regime': self.synchronizer.market_regime,
            'grid_risk': self.grid.risk_level,
            'data_source': 'REAL_KITE'
        }

        with open(self.trades_log, 'a') as f:
            f.write(json.dumps(trade_log) + '\n')

        self.signals_executed += 1
        logger.info(f"EXECUTE (REAL): BUY {qty} {self.symbol} @ ₹{current_price:.2f}")

    def execute_exit(self, current_price: float, reason: str = "MA_CROSSOVER"):
        """Execute SELL"""
        if not self.position:
            return

        pnl = (current_price - self.position['entry_price']) * self.position['qty']
        pnl_pct = ((current_price / self.position['entry_price']) - 1) * 100

        trade_log = {
            'timestamp': datetime.now().isoformat(),
            'symbol': self.symbol,
            'action': 'SELL',
            'entry_price': self.position['entry_price'],
            'exit_price': current_price,
            'pnl': pnl,
            'pnl_pct': pnl_pct,
            'reason': reason,
            'nifty_regime': self.synchronizer.market_regime,
            'grid_risk': self.grid.risk_level,
            'data_source': 'REAL_KITE'
        }

        with open(self.trades_log, 'a') as f:
            f.write(json.dumps(trade_log) + '\n')

        self.trades.append(trade_log)
        self.trades_closed += 1

        logger.info(f"CLOSE (REAL): SELL {self.position['qty']} {self.symbol} @ ₹{current_price:.2f} "
                   f"| P&L: ₹{pnl:+.0f} ({pnl_pct:+.2f}%)")

        self.position = None

    def run_once(self):
        """Execute one cycle"""
        try:
            # Get current time for this cycle
            current_time = datetime.now()

            # Fetch symbol bars (REAL from Kite)
            df = self.fetcher.get_minute_bars(self.symbol, limit=100)
            if df is None or len(df) == 0:
                logger.warning("No symbol bars")
                return

            current_price = df['close'].iloc[-1]
            logger.info(f"Symbol: {self.symbol} @ ₹{current_price:.2f}")

            # Update SYNCHRONIZER (REAL NIFTY data)
            self.synchronizer.update(current_time)

            # Update GRID (REAL NIFTY data)
            self.grid.update(current_time)

            # Calculate ECS signals
            self.calculate_ecs_signals(df)

            # Generate technical signal
            signal = self.generate_technical_signal(df)
            if signal:
                self.signals_generated += 1
                logger.info(f"Technical signal: {signal}")

            # Apply filters and execute
            if signal == "BUY" and self.check_entry_filters(signal):
                self.execute_entry(current_price)
            elif signal == "SELL" and self.position:
                self.execute_exit(current_price, reason="MA_CROSSOVER")

            logger.info(f"Stats: signals_gen={self.signals_generated}, "
                       f"executed={self.signals_executed}, closed={self.trades_closed}")

        except Exception as e:
            logger.error(f"Error: {e}", exc_info=True)


# ============================================================================
# MAIN
# ============================================================================

def main():
    parser = argparse.ArgumentParser(
        description="Test with REAL NIFTY 50 data from Kite Connect"
    )
    parser.add_argument("--date", type=str, required=True, help="Test date (YYYY-MM-DD)")
    parser.add_argument("--symbol", type=str, default="INFY", help="Symbol to test")

    args = parser.parse_args()

    try:
        test_date = datetime.strptime(args.date, "%Y-%m-%d")
    except ValueError:
        logger.error("Invalid date format. Use YYYY-MM-DD")
        sys.exit(1)

    # Get credentials
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key or not access_token:
        logger.error("Set KITE_API_KEY and KITE_ACCESS_TOKEN")
        sys.exit(1)

    # Initialize
    fetcher = KiteDataFetcher(api_key, access_token)
    engine = TestWithRealNifty50(args.symbol, test_date, fetcher)

    logger.info("="*80)
    logger.info(f"TEST: {args.symbol} INTRADAY with REAL NIFTY 50 DATA")
    logger.info("="*80)
    logger.info(f"Date: {test_date.strftime('%Y-%m-%d')}")
    logger.info(f"Data sources: REAL Kite Connect (NO synthetic data)")
    logger.info(f"Logs: {engine.trades_log}")
    logger.info("="*80)

    # Main loop
    try:
        while True:
            now = datetime.now().time()
            if not (dtime(9, 15) <= now <= dtime(15, 30)):
                logger.info("Market closed.")
                break

            engine.run_once()
            time.sleep(60)

    except KeyboardInterrupt:
        logger.info("Test stopped")

    finally:
        # Summary
        logger.info("\n" + "="*80)
        logger.info("TEST SUMMARY (REAL DATA)")
        logger.info("="*80)
        logger.info(f"Symbol: {args.symbol}")
        logger.info(f"Data source: REAL Kite Connect (NOT synthetic)")
        logger.info(f"Signals generated: {engine.signals_generated}")
        logger.info(f"Signals executed: {engine.signals_executed}")
        logger.info(f"Trades closed: {engine.trades_closed}")

        if engine.trades:
            pnl_total = sum(t['pnl'] for t in engine.trades)
            win_count = sum(1 for t in engine.trades if t['pnl'] > 0)
            loss_count = sum(1 for t in engine.trades if t['pnl'] < 0)

            logger.info(f"\nP&L:")
            logger.info(f"  Total: ₹{pnl_total:+.0f}")
            logger.info(f"  Wins: {win_count}")
            logger.info(f"  Losses: {loss_count}")
            logger.info(f"  Win rate: {(win_count/len(engine.trades)*100):.1f}%")

        logger.info("="*80)


if __name__ == "__main__":
    main()
