"""
Pytest configuration and shared fixtures.
"""

import pytest
import time
import sqlite3
from decimal import Decimal
from unittest.mock import MagicMock
import sys
import os

# Add blocks directory to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'blocks'))


class FakeClock:
    """Deterministic fake clock for testing."""

    def __init__(self, start_ms: int = 0):
        self.current_ms = start_ms

    def now(self) -> int:
        """Current time in ms."""
        return self.current_ms

    def advance(self, ms: int):
        """Advance time by N ms."""
        self.current_ms += ms

    def reset(self, ms: int = 0):
        """Reset to specific time."""
        self.current_ms = ms


class MockBroker:
    """Mock Kite broker for testing."""

    def __init__(self):
        self.orders = {}
        self.positions = {}
        self.fills = {}
        self.account_state = {
            'equity': Decimal('1000000'),
            'available_margin': Decimal('500000'),
            'daily_pnl': Decimal('0'),
            'positions': {}
        }

    def place_order(self, symbol, side, qty, order_type, price, tag):
        """Simulate order submission."""
        order_id = f"order_{int(time.time()*1000)}"
        self.orders[order_id] = {
            'symbol': symbol,
            'side': side,
            'qty': qty,
            'status': 'COMPLETE',
            'filled_qty': qty,
            'average_price': price or 1500.0,
            'tag': tag
        }
        self.positions[symbol] = qty if side == 'BUY' else -qty
        self.fills[order_id] = [{'fill_id': f'fill_1', 'qty': qty, 'price': price or 1500.0}]
        return order_id

    def get_order_status(self, order_id):
        """Get order status."""
        order = self.orders.get(order_id, {})
        return order.get('status', 'PENDING')

    def get_account_state(self):
        """Get account state."""
        self.account_state['positions'] = self.positions.copy()
        return self.account_state

    def get_position(self, symbol):
        """Get position for symbol."""
        return self.positions.get(symbol, 0)


@pytest.fixture
def fake_clock():
    """Provide fake clock for deterministic testing."""
    return FakeClock()


@pytest.fixture
def mock_broker():
    """Provide mock broker."""
    return MockBroker()


@pytest.fixture
def temp_db():
    """Provide temporary in-memory database."""
    db = sqlite3.connect(':memory:')
    yield db
    db.close()
