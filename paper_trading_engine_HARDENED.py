#!/usr/bin/env python3
"""
HARDENED Paper Trading Engine with All 5 Critical Fixes Applied
================================================

Fixes implemented:
✓ Fix #1: Undefined variable crash (line 98)
✓ Fix #2: State persistence with atomic transactions
✓ Fix #3: Circuit breaker on daily loss limit
✓ Fix #4: Rate limit handling with exponential backoff
✓ Fix #5: Position-level stop-loss enforcement

Runs trading signals on real market data without real money
"""

import json
import logging
import time
import sqlite3
import pytz
from datetime import datetime, timedelta, time as dtime
from typing import List, Dict, Optional
from contextlib import contextmanager
from pathlib import Path
import sys
import os
from requests.exceptions import HTTPError, ConnectionError

sys.path.insert(0, '.')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - [%(name)s] - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

IST = pytz.timezone('Asia/Kolkata')

# ============================================================================
# FIX #2: STATE PERSISTENCE WITH ATOMIC TRANSACTIONS
# ============================================================================
# ============================================================================
# CENTRALIZED TRADING CONFIGURATION
# ============================================================================
TRADING_CONFIG = {
    "ma_period": 20,
    "volume_ratio_threshold": 1.05,
    "score_threshold_buy": 0.20,
    "score_threshold_sell": -0.60,
    "risk_per_trade_rupees": 500.0,
    "stop_loss_pct": 0.02,
    "target_profit_pct": 0.02,
    "max_qty_per_position": 100,
    "slippage_pct": 0.001,
}

class StateManager:
    """Atomic state persistence using SQLite"""

    def __init__(self, db_path: str = "paper_trading_state.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        """Initialize database schema"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS open_positions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT UNIQUE,
            qty INTEGER,
            entry_price REAL,
            stop_price REAL,
            target_price REAL,
            entry_time REAL,
            hold_cycles INTEGER DEFAULT 0,
                    id INTEGER PRIMARY KEY,
                    symbol TEXT NOT NULL UNIQUE,
                    entry_time TIMESTAMP,
                    entry_price REAL,
                    qty INTEGER,
                    stop_price REAL,
                    target_price REAL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS closed_trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    entry_time TIMESTAMP,
                    exit_time TIMESTAMP,
                    entry_price REAL,
                    exit_price REAL,
                    qty INTEGER,
                    pnl REAL,
                    reason TEXT
                )
            """)
            conn.commit()
            logger.info(f"✓ State database initialized: {self.db_path}")

    @contextmanager
    def transaction(self):
        """Atomic transaction wrapper"""
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Transaction rollback: {e}")
            raise
        finally:
            conn.close()

    def load_open_positions(self) -> Dict[str, Dict]:
        """Load all open positions from persistent store"""
        try:
            with sqlite3.connect(self.db_path) as conn:
                cursor = conn.execute("""
                    SELECT symbol, entry_time, entry_price, qty, stop_price, target_price
                    FROM open_positions
                """)
                positions = {}
                for row in cursor.fetchall():
                    positions[row[0]] = {
                        'entry_time': row[1],
                        'entry_price': row[2],
                        'qty': row[3],
                        'stop_price': row[4],
                        'target_price': row[5]
                    }
                if positions:
                    logger.info(f"✓ Recovered {len(positions)} open positions from database")
                return positions
        except Exception as e:
            logger.error(f"Failed to load positions: {e}")
            return {}

    def save_position(self, symbol: str, position: Dict):
        """Atomically save a single position"""
        try:
            with self.transaction() as conn:
                conn.execute("""
                    INSERT OR REPLACE INTO open_positions
                    (symbol, entry_time, entry_price, qty, stop_price, target_price)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (symbol, position.get('entry_time'), position['entry_price'],
                      position['qty'], position.get('stop_price'), position.get('target_price')))
                logger.debug(f"✓ Position saved: {symbol}")
        except Exception as e:
            logger.error(f"Failed to save position {symbol}: {e}")
            raise

    def close_position(self, symbol: str, exit_price: float, exit_time: str, reason: str) -> Optional[float]:
        """Atomically close a position and calculate P&L"""
        try:
            with self.transaction() as conn:
                cursor = conn.execute("SELECT entry_price, qty FROM open_positions WHERE symbol = ?", (symbol,))
                row = cursor.fetchone()

                if not row:
                    logger.warning(f"Position not found: {symbol}")
                    return None

                entry_price, qty = row
                pnl = (exit_price - entry_price) * qty

                conn.execute("""
                    INSERT INTO closed_trades (symbol, entry_time, exit_time, entry_price, exit_price, qty, pnl, reason)
                    SELECT symbol, entry_time, ?, entry_price, ?, qty, ?, ?
                FROM open_positions WHERE symbol = ?
            """, (exit_time, exit_price, pnl, reason, symbol))

                conn.execute("DELETE FROM open_positions WHERE symbol = ?", (symbol,))

                logger.info(f"✓ Position closed: {symbol} | P&L: ₹{pnl:+,.2f} | Reason: {reason}")
                return pnl
        except Exception as e:
            logger.error(f"Failed to close position {symbol}: {e}")
            raise


# ============================================================================
# FIX #3: CIRCUIT BREAKER ON DAILY LOSS LIMIT
# ============================================================================
class CircuitBreaker:
    """Halt trading if daily loss exceeds threshold"""

    def __init__(self, max_daily_loss: float = -10000.0):
        self.max_daily_loss = max_daily_loss
        self.daily_pnl = 0.0
        self.is_broken = False
        self.broken_timestamp = None
        self.daily_date = datetime.now(IST).date()

    def record_trade(self, pnl: float):
        """Record trade outcome and check for circuit break"""
        current_date = datetime.now(IST).date()

        # Reset if date changed
        if current_date > self.daily_date:
            self.daily_pnl = 0.0
            self.is_broken = False
            self.daily_date = current_date
            logger.info(f"✓ Daily circuit breaker reset (new day: {current_date})")

        self.daily_pnl += pnl

        if self.daily_pnl < self.max_daily_loss:
            logger.critical(f"🔴 CIRCUIT BREAKER TRIGGERED: Daily loss ₹{self.daily_pnl:,.2f} < limit ₹{self.max_daily_loss:,.2f}")
            self.is_broken = True
            self.broken_timestamp = datetime.now(IST)

    def can_trade(self) -> bool:
        """Check if trading is allowed"""
        return not self.is_broken

    def get_status(self) -> Dict:
        """Get circuit breaker status"""
        return {
            'is_broken': self.is_broken,
            'daily_pnl': self.daily_pnl,
            'max_allowed_loss': self.max_daily_loss,
            'remaining_loss_budget': self.max_daily_loss - self.daily_pnl
        }


# ============================================================================
# FIX #4: RATE LIMIT HANDLING WITH EXPONENTIAL BACKOFF
# ============================================================================
class ResilientKiteAdapter:
    """Wrapper around Kite adapter with rate-limit retry logic"""

    def __init__(self, adapter, max_retries: int = 3):
        self.adapter = adapter
        self.max_retries = max_retries

    def get_1min_candles_resilient(self, symbol: str, limit: int = 100) -> List[Dict]:
        """Fetch candles with exponential backoff retry"""
        for attempt in range(self.max_retries):
            try:
                return self.adapter.get_1min_candles(symbol, limit)
            except HTTPError as e:
                if e.response.status_code == 429:  # Rate limit
                    wait_time = 2 ** attempt  # Exponential backoff
                    logger.warning(f"⚠️  Rate limited on {symbol}. Waiting {wait_time}s (attempt {attempt+1}/{self.max_retries})")
                    time.sleep(wait_time)
                elif e.response.status_code == 401:  # Unauthorized
                    logger.error(f"❌ Unauthorized (token expired?): {e}")
                    raise
                else:
                    logger.error(f"❌ HTTP error {e.response.status_code}: {e}")
                    raise
            except ConnectionError as e:
                if attempt < self.max_retries - 1:
                    wait_time = 2 ** attempt
                    logger.warning(f"⚠️  Connection error. Retrying in {wait_time}s (attempt {attempt+1}/{self.max_retries})")
                    time.sleep(wait_time)
                else:
                    raise

        raise RuntimeError(f"Failed to fetch {symbol} after {self.max_retries} attempts")

    def get_live_quotes_resilient(self, symbols: List[str]) -> Dict:
        """Fetch quotes with retry"""
        for attempt in range(self.max_retries):
            try:
                return self.adapter.get_live_quotes(symbols)
            except HTTPError as e:
                if e.response.status_code == 429:
                    wait_time = 2 ** attempt
                    logger.warning(f"⚠️  Rate limited. Waiting {wait_time}s...")
                    time.sleep(wait_time)
                else:
                    raise
        return {}


# ============================================================================
# MAIN: HARDENED PAPER TRADING ENGINE
# ============================================================================
class PaperTradingEngineHardened:
    """Execute trading signals on live market data with simulated orders"""

    
    def calculate_position_size(self, symbol: str, current_price: float) -> int:
        """Calculate shares based on fixed ₹500 risk and 2% stop distance"""
        risk = TRADING_CONFIG["risk_per_trade_rupees"]
        sl_rupees = current_price * TRADING_CONFIG["stop_loss_pct"]
        if sl_rupees <= 0:
            return 1
        quantity = int(risk / sl_rupees)
        return max(1, min(quantity, TRADING_CONFIG["max_qty_per_position"]))

    def simulate_slippage(self, price: float, side: str) -> float:
        """Simulate 0.1% bid-ask spread on executed fills"""
        spread = TRADING_CONFIG["slippage_pct"]
        if side.upper() == "BUY":
            return round(price * (1.0 + spread), 2)
        return round(price * (1.0 - spread), 2)

    def __init__(self, kite_adapter, symbols: List[str]):
        self.adapter = ResilientKiteAdapter(kite_adapter)
        self.symbols = symbols

        # FIX #2: State persistence
        self.state_manager = StateManager()
        self.paper_positions = self.state_manager.load_open_positions()

        # FIX #3: Circuit breaker
        self.circuit_breaker = CircuitBreaker(max_daily_loss=-10000.0)

        self.execution_log = []
        self.signal_log = []
        logger.info(f"✓ Hardened paper trading engine initialized for {len(symbols)} symbols")
        logger.info(f"✓ Open positions recovered: {len(self.paper_positions)}")

    def is_market_open(self) -> bool:
        """Check if NSE market is currently open (09:15-15:30 IST)"""
        ist_now = datetime.now(IST)
        market_open = dtime(9, 15, 0)
        market_close = dtime(15, 30, 0)

        # Only Monday-Friday
        if ist_now.weekday() > 4:
            return False

        # Only during market hours
        if ist_now.time() < market_open or ist_now.time() > market_close:
            return False

        return True

    def fetch_live_data(self) -> Dict:
        """Fetch current 1-min candles for all symbols (with rate limit handling)"""
        data = {}
        for symbol in self.symbols:
            try:
                candles = self.adapter.get_1min_candles_resilient(symbol, limit=100)
                if candles:
                    data[symbol] = candles
            except Exception as e:
                logger.error(f"❌ Failed to fetch data for {symbol}: {e}")
        return data

    def generate_signals(self, data: Dict) -> Dict:
        """Generate trading signals based on live data"""
        signals = {}

        for symbol, candles in data.items():
            try:
                # FIX #4: Validate candle count
                if len(candles) < 20:
                    logger.debug(f"⚠️ Insufficient candles for {symbol}: {len(candles)} < 20")
                    continue

                # FIX #4: Validate candle freshness
                last_candle = candles[-1]
                if "timestamp" in last_candle:
                    try:
                        candle_ts = datetime.fromisoformat(last_candle["timestamp"])
                        age = (datetime.now(IST) - candle_ts).total_seconds()
                        if age > 120:
                            logger.warning(f"⚠️ Stale data for {symbol}: {age}s old")
                            continue
                    except Exception:
                        pass

                # FIX #4: Validate closes (no NaN, non-zero)
                closes = [c.get("close", None) for c in candles]
                if any(pr is None or pr <= 0 or pr != pr for pr in closes):
                    logger.warning(f"⚠️ Invalid/NaN price in {symbol}")
                    continue

                # FIX #4: Validate volumes
                volumes = [c.get("volume", 0) for c in candles]
                if any(v is None or v < 0 for v in volumes) or sum(volumes[-20:]) == 0:
                    logger.warning(f"⚠️ Zero/Invalid volume for {symbol}")
                    continue

                ma20 = sum(closes[-20:]) / 20.0
                current_price = closes[-1]
                current_volume = volumes[-1]
                avg_volume = sum(volumes[-20:]) / 20.0

                if ma20 <= 0 or current_price <= 0 or avg_volume <= 0:
                    continue

                signal = {
                    "symbol": symbol,
                    "timestamp": datetime.now(IST).isoformat(),
                    "current_price": current_price,
                    "ma20": ma20,
                    "current_volume": current_volume,
                    "avg_volume": avg_volume,
                    "action": None,
                    "quantity": self.calculate_position_size(symbol, current_price),
                    "score": 0.0,
                    "pct_distance": 0.0
                }

                signal = {
                    'symbol': symbol,
                    'timestamp': datetime.now(IST).isoformat(),
                    'current_price': current_price,
                    'ma20': ma20,
                    'current_volume': current_volume,
                    'avg_volume': avg_volume,
                    'action': None,
                    'quantity': 1
                }

                # Continuous Score & Hysteresis Momentum Signal
                pct_dist = ((current_price - ma20) / ma20) * 100.0
                vol_ratio = current_volume / max(avg_volume, 1.0)

                # Normalized score: clip into [-1.0, 1.0]
                # A 0.25% distance maps to full 1.0 score
                score = max(-1.0, min(1.0, pct_dist / 0.25))
                signal['score'] = round(score, 3)

                # BUY: Decisive break above MA20 with volume expansion
                if current_price > ma20 and vol_ratio >= 1.05 and score >= 0.20:
                    signal['action'] = 'BUY'
                    signals[symbol] = signal
                # Decisive breakdown (score <= -0.60) required to force an early trend-exit
                elif symbol in self.paper_positions and score <= -0.60:
                    signal['action'] = 'SELL'
                    signals[symbol] = signal

            except Exception as e:
                logger.error(f"❌ Error generating signal for {symbol}: {e}")

        return signals

    def evaluate_active_position(self, symbol: str, current_price: float):
        """Check if position should be closed (stop-loss or target) with atomic dict guard"""
        if symbol not in self.paper_positions:
            return

        pos = self.paper_positions[symbol]

        if "stop_price" in pos and current_price <= pos["stop_price"]:
            fill_price = self.simulate_slippage(current_price, "SELL")
            pnl = self.state_manager.close_position(
                symbol, fill_price,
                datetime.now(IST).isoformat(),
                "STOP_LOSS"
            )
            if pnl is not None:
                self.circuit_breaker.record_trade(pnl)
                if symbol in self.paper_positions:
                    del self.paper_positions[symbol]

        elif "target_price" in pos and current_price >= pos["target_price"]:
            fill_price = self.simulate_slippage(current_price, "SELL")
            pnl = self.state_manager.close_position(
                symbol, fill_price,
                datetime.now(IST).isoformat(),
                "TARGET_HIT"
            )
            if pnl is not None:
                self.circuit_breaker.record_trade(pnl)
                if symbol in self.paper_positions:
                    del self.paper_positions[symbol]

    def execute_signals(self, signals: Dict) -> List[Dict]:
        """Execute trading signals (with circuit breaker check)"""
        # FIX #3: Check circuit breaker before executing
        if not self.circuit_breaker.can_trade():
            logger.warning(f"⛔ Circuit breaker active. Status: {self.circuit_breaker.get_status()}")
            return []

        executed_orders = []

        for symbol, signal in signals.items():
            try:
                # FIX #1: Use signal['quantity'] (not undefined variable)
                logger.info(f"Signal → {signal['action']} {signal['quantity']} {symbol} @ ₹{signal['current_price']:.2f}")

                order = self.adapter.adapter.submit_paper_order(
                    symbol=symbol,
                    quantity=signal['quantity'],
                    side=signal['action'],
                    price=signal['current_price']
                )

                executed_orders.append(order)
                self.execution_log.append(order)
                self.signal_log.append(signal)

                # Track position in persistent store (FIX #2)
                if signal['action'] == 'BUY':
                    clean_price = signal['current_price']
                    fill_price = self.simulate_slippage(clean_price, "BUY")
                    position = {
                        'entry_time': datetime.now(IST).isoformat(),
                        'entry_price': fill_price,
                        'qty': signal['quantity'],
                        'stop_price': round(clean_price * (1.0 - TRADING_CONFIG["stop_loss_pct"]), 2),
                        'target_price': round(clean_price * (1.0 + TRADING_CONFIG["target_profit_pct"]), 2)
                    }
                    try:
                        self.state_manager.save_position(symbol, position)
                        self.paper_positions[symbol] = position
                        logger.info(f"✓ BUY recorded: {symbol} @ ₹{signal['current_price']:.2f} | DB: ✓ Dict: ✓")
                    except Exception as e:
                        logger.error(f"❌ Failed to save position {symbol}: {e}")
                        continue

                elif signal['action'] == 'SELL' and symbol in self.paper_positions:
                    if symbol not in self.paper_positions:
                        logger.warning(f"⚠️ Position already closed: {symbol}")
                        continue
                    clean_price = signal['current_price']
                    fill_price = self.simulate_slippage(clean_price, "SELL")
                    pnl = self.state_manager.close_position(
                        symbol, fill_price,
                        datetime.now(IST).isoformat(),
                        "SIGNAL_CLOSE"
                    )
                    if pnl is not None:
                        if symbol in self.paper_positions:
                            del self.paper_positions[symbol]
                        self.circuit_breaker.record_trade(pnl)
                        logger.info(f"✓ SELL recorded: {symbol} | P&L: ₹{pnl:+.2f}")
                    else:
                        logger.error(f"❌ SELL failed (DB): {symbol}")

            except Exception as e:
                logger.error(f"❌ Error executing signal for {symbol}: {e}", exc_info=True)

        return executed_orders

    def run_once(self):
        """Execute one cycle: fetch data → generate signals → execute"""
        try:
            # Check market hours
            if not self.is_market_open():
                logger.info("📅 Market closed. Skipping cycle.")
                return

            logger.info("=" * 70)
            logger.info(f"CYCLE STARTED: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")

            # Fetch live data
            data = self.fetch_live_data()
            if not data:
                logger.warning("⚠️  No data received, skipping cycle")
                return

            logger.info(f"✓ Fetched data for {len(data)} symbols")

            # Evaluate active positions (FIX #5: stop-loss checks)
            for symbol in list(self.paper_positions.keys()):
                if symbol in data and data[symbol]:
                    last_candle = data[symbol][-1]
                    self.evaluate_active_position(symbol, last_candle['close'])

            # Generate signals
            signals = self.generate_signals(data)
            logger.info(f"✓ Generated {len(signals)} signals")

            # Execute signals
            if signals:
                orders = self.execute_signals(signals)
                logger.info(f"✓ Executed {len(orders)} orders")
            else:
                logger.info("ℹ️  No signals to execute")

            # Save logs
            self._save_logs()

            # Log status
            logger.info(f"📊 Circuit Breaker Status: {self.circuit_breaker.get_status()}")
            logger.info(f"📈 Open Positions: {len(self.paper_positions)}")

            # Increment dwell hold_cycles for active monitored positions
            for pos in self.paper_positions.values():
                pos["hold_cycles"] = pos.get("hold_cycles", 0) + 1
            logger.info(f"CYCLE COMPLETED: {datetime.now(IST).strftime('%Y-%m-%d %H:%M:%S IST')}")
            logger.info("=" * 70 + "\n")

        except Exception as e:
            logger.error(f"❌ Error in cycle: {e}", exc_info=True)

    def run_continuous(self, interval_seconds: int = 60):
        """Run continuously at specified interval"""
        logger.info(f"Starting continuous execution (interval: {interval_seconds}s)")
        logger.info("Press Ctrl+C to stop\n")

        try:
            cycle = 0
            while True:
                cycle += 1
                logger.info(f"[Cycle {cycle}]")
                self.run_once()
                logger.info(f"Waiting {interval_seconds}s until next cycle...")
                time.sleep(interval_seconds)
        except KeyboardInterrupt:
            logger.info("\n✓ Stopping continuous execution")
            logger.info("✓ All positions saved to persistent store")

    def _save_logs(self):
        """Save execution and signal logs"""
        try:
            with open('PAPER_EXECUTION_LOG.json', 'w') as f:
                json.dump(self.execution_log, f, indent=2)

            with open('PAPER_SIGNALS_LOG.json', 'w') as f:
                json.dump(self.signal_log, f, indent=2)
        except Exception as e:
            logger.error(f"❌ Error saving logs: {e}")


# ============================================================================
# MAIN EXECUTION
# ============================================================================
if __name__ == "__main__":
    from zerodha_kite_live_adapter import ZerodhaKiteLiveAdapter

    # Get credentials
    api_key = os.getenv('KITE_API_KEY')
    access_token = os.getenv('KITE_ACCESS_TOKEN')

    if not api_key or not access_token:
        logger.error("❌ ERROR: Set KITE_API_KEY and KITE_ACCESS_TOKEN environment variables")
        sys.exit(1)

    try:
        # Initialize adapter
        adapter = ZerodhaKiteLiveAdapter(api_key, access_token)
        symbols = ['RELIANCE', 'TCS', 'INFY', 'HDFCBANK', 'ICICIBANK', 'SBIN', 'MARUTI', 'LT']

        # Initialize hardened engine
        engine = PaperTradingEngineHardened(adapter, symbols)

        # Run
        if len(sys.argv) > 1 and sys.argv[1] == '--test':
            logger.info("🧪 Running test cycle...")
            engine.run_once()
        else:
            logger.info("🚀 Starting continuous execution...")
            engine.run_continuous(interval_seconds=60)

    except Exception as e:
        logger.error(f"❌ Fatal error: {e}", exc_info=True)
        sys.exit(1)
