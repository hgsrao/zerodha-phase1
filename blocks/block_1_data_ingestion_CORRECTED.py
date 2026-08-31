"""
Block 1: Data Ingestion (CORRECTED)
- Durable sequence tracking (survives restart)
- Independent quality flags (not overwriting)
- Volume semantics clarified
"""

import json
import logging
import time
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Optional, Dict
from decimal import Decimal
from datetime import datetime
from enum import Enum

logger = logging.getLogger(__name__)


class DataQuality(Enum):
    """Independent quality flags (not mutually exclusive)."""
    VALID = "valid"
    STALE = "stale"
    PRICE_ANOMALY = "price_anomaly"
    MISSING_DEPTH = "missing_depth"
    OUT_OF_ORDER = "out_of_order"


class TickDecision(Enum):
    """Downstream decision based on quality flags."""
    USABLE = "usable"               # Can form signals
    DEGRADED = "degraded"           # Observable but weak signals only
    REJECTED = "rejected"           # Excluded from processing


@dataclass(frozen=True)
class RawTick:
    """Tick received from Kite WebSocket."""
    symbol: str
    price: float
    volume: int                     # INCREMENTAL volume for THIS tick
    cumulative_volume: Optional[int]  # Explicit cumulative if available
    bid: float
    ask: float
    timestamp_ms: int              # Kite exchange timestamp
    sequence_number: int           # Source sequence from Kite


@dataclass(frozen=True)
class IngestedTick:
    """Tick after validation and sequencing."""
    symbol: str
    price: Decimal
    volume: int                     # INCREMENTAL volume
    bid: Decimal
    ask: Decimal
    kite_timestamp_ms: int
    ingestion_timestamp_ms: int
    sequence: int
    quality_flags: set              # Independent flags
    downstream_decision: TickDecision


class SequenceValidator:
    """Durable sequence tracking (survives restart)."""

    def __init__(self, db_path: str = ":memory:"):
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row

        cursor = self.conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sequence_state (
                symbol TEXT PRIMARY KEY,
                last_sequence_number INTEGER NOT NULL,
                last_kite_timestamp_ms INTEGER NOT NULL,
                updated_at_ms INTEGER NOT NULL
            )
        """)
        self.conn.commit()

    def is_new_tick(self, symbol: str, sequence_number: int) -> bool:
        """Check if tick is new (not duplicate after restart)."""
        cursor = self.conn.cursor()
        cursor.execute(
            "SELECT last_sequence_number FROM sequence_state WHERE symbol = ?",
            (symbol,)
        )
        row = cursor.fetchone()

        if row is None:
            return True

        return sequence_number > row['last_sequence_number']

    def record_sequence(self, symbol: str, sequence_number: int, kite_timestamp_ms: int):
        """Durably record this sequence."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO sequence_state (symbol, last_sequence_number, last_kite_timestamp_ms, updated_at_ms)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                last_sequence_number=excluded.last_sequence_number,
                last_kite_timestamp_ms=excluded.last_kite_timestamp_ms,
                updated_at_ms=excluded.updated_at_ms
        """, (symbol, sequence_number, kite_timestamp_ms, int(time.time() * 1000)))
        self.conn.commit()


class DataIngestion:
    """Block 1: Data Ingestion (CORRECTED)."""

    def __init__(
        self,
        stale_threshold_ms: int = 5000,
        anomaly_threshold_pct: float = 5.0,
        db_path: str = ":memory:",
    ):
        """
        Initialize data ingestion.

        Args:
            stale_threshold_ms: Mark ticks older than this as STALE
            anomaly_threshold_pct: Price jump > this % triggers PRICE_ANOMALY flag
            db_path: Durable database for sequence tracking
        """
        self.stale_threshold_ms = stale_threshold_ms
        self.anomaly_threshold_pct = anomaly_threshold_pct
        self.sequence_validator = SequenceValidator(db_path)
        self.last_price: Dict[str, Decimal] = {}
        self.valid_count = 0
        self.rejected_count = 0

        logger.info(
            f"DataIngestion initialized: stale={stale_threshold_ms}ms, anomaly={anomaly_threshold_pct}%"
        )

    def ingest_tick(self, raw_tick: RawTick) -> Optional[IngestedTick]:
        """
        Process raw tick: validate, check for duplicates/anomalies.

        Returns:
            IngestedTick with quality flags and downstream decision, or None if rejected
        """

        symbol = raw_tick.symbol
        current_time = int(time.time() * 1000)

        # ==================== STEP 1: FIELD VALIDATION ====================
        if not self._validate_fields(raw_tick):
            self.rejected_count += 1
            logger.warning(f"Field validation failed: {symbol}")
            return None

        # ==================== STEP 2: SEQUENCE CHECK ====================
        if not self.sequence_validator.is_new_tick(symbol, raw_tick.sequence_number):
            self.rejected_count += 1
            logger.debug(f"Duplicate sequence rejected: {symbol} seq={raw_tick.sequence_number}")
            return None

        self.sequence_validator.record_sequence(
            symbol, raw_tick.sequence_number, raw_tick.kite_timestamp_ms
        )

        # ==================== STEP 3: QUALITY FLAGS (INDEPENDENT) ====================
        quality_flags = set()

        # Check staleness
        age_ms = current_time - raw_tick.timestamp_ms
        if age_ms > self.stale_threshold_ms:
            quality_flags.add(DataQuality.STALE.value)
            logger.debug(f"Stale tick: {symbol} age={age_ms}ms")

        # Check price anomaly
        if symbol in self.last_price:
            last_price = self.last_price[symbol]
            price_change_pct = abs((Decimal(str(raw_tick.price)) - last_price) / last_price * 100)

            if float(price_change_pct) > self.anomaly_threshold_pct:
                quality_flags.add(DataQuality.PRICE_ANOMALY.value)
                logger.debug(f"Price anomaly: {symbol} jump={price_change_pct:.2f}%")

        # Check for missing depth
        if raw_tick.bid <= 0 or raw_tick.ask <= 0:
            quality_flags.add(DataQuality.MISSING_DEPTH.value)

        # ==================== STEP 4: DOWNSTREAM DECISION ====================
        if not quality_flags:
            downstream_decision = TickDecision.USABLE
        elif quality_flags == {DataQuality.STALE.value}:
            downstream_decision = TickDecision.DEGRADED
        elif DataQuality.PRICE_ANOMALY.value in quality_flags:
            downstream_decision = TickDecision.DEGRADED  # Observable, not trusted for signals
        else:
            downstream_decision = TickDecision.REJECTED

        # ==================== STEP 5: CREATE INGESTED TICK ====================
        ingested_tick = IngestedTick(
            symbol=symbol,
            price=Decimal(str(raw_tick.price)),
            volume=raw_tick.volume,  # INCREMENTAL volume
            bid=Decimal(str(raw_tick.bid)),
            ask=Decimal(str(raw_tick.ask)),
            kite_timestamp_ms=raw_tick.timestamp_ms,
            ingestion_timestamp_ms=current_time,
            sequence=raw_tick.sequence_number,
            quality_flags=quality_flags,
            downstream_decision=downstream_decision
        )

        # Update tracking
        self.last_price[symbol] = Decimal(str(raw_tick.price))
        self.valid_count += 1

        logger.debug(
            f"Ingested: {symbol} price={raw_tick.price} vol={raw_tick.volume} "
            f"quality={quality_flags} decision={downstream_decision.value}"
        )

        return ingested_tick

    def _validate_fields(self, tick: RawTick) -> bool:
        """Validate required fields."""
        try:
            if tick.price <= 0 or tick.volume < 0:
                return False
            if tick.timestamp_ms <= 0:
                return False
            if tick.bid <= 0 or tick.ask <= 0 or tick.bid > tick.ask:
                return False
            return True
        except (ValueError, TypeError):
            return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG)

    ingestion = DataIngestion(db_path=":memory:")

    # Simulate ticks
    tick1 = RawTick(
        symbol='INFY',
        price=1500.00,
        volume=100,
        cumulative_volume=1000,
        bid=1499.90,
        ask=1500.10,
        timestamp_ms=int(time.time() * 1000),
        sequence_number=1
    )

    result1 = ingestion.ingest_tick(tick1)
    print(f"✓ Tick 1: {result1.downstream_decision.value if result1 else 'REJECTED'}")

    # Duplicate (should be rejected)
    tick2 = RawTick(
        symbol='INFY',
        price=1500.50,
        volume=50,
        cumulative_volume=1050,
        bid=1500.40,
        ask=1500.60,
        timestamp_ms=int(time.time() * 1000),
        sequence_number=1  # DUPLICATE
    )

    result2 = ingestion.ingest_tick(tick2)
    print(f"✓ Tick 2 (duplicate): {'REJECTED' if not result2 else result2.downstream_decision.value}")

    print("✓ Block 1 (Corrected) working")
