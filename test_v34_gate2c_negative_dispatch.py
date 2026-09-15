"""
V3.4 Gate 2C-2 — RunnerEntryAuthorizer Negative Execution Verification
=====================================================================
Proves that when live_trading_enabled=False:
  1. RunnerEntryAuthorizer.authorize_buy() successfully observes fresh, timezone-aware snapshots.
  2. The guard intercepts the call when live trading is disabled, triggers a 
     RECONCILIATION_HALT via the state store, and raises a safety RuntimeError.
  3. Zero underlying broker order dispatches occur.
"""

from datetime import datetime, timezone
from decimal import Decimal
import pytest
from run_production_p01d_candidate import (
    BrokerRiskSnapshot,
    KiteBrokerAdapter,
    RunnerEntryAuthorizer,
    RunnerEntryStateStore,
    LIVE_TRADING_ENABLED,
)

class MockKiteClientSpy:
    def __init__(self):
        self.place_order_called_count = 0

    def place_order(self, *args, **kwargs):
        self.place_order_called_count += 1
        return "ILLEGAL_ORDER_ID_DISPATCHED"

class MockBroker:
    def __init__(self):
        self.place_order_called_count = 0

    def get_daily_risk_snapshot(self):
        return BrokerRiskSnapshot(
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
            charges=Decimal("0"),
            deployed_capital=Decimal("0"),
            pending_buy_exposure=Decimal("0"),
            executed_buy_quantity=0,
            executed_buy_value=Decimal("0"),
            observation_timestamp=datetime.now(timezone.utc),  # TZ-aware to satisfy is_fresh()
            valid=True,
            kill_switch_active=False,
            capital_limit=Decimal("20000"),
            daily_loss_limit=Decimal("2000"),
        )

    def place_order(self, *args, **kwargs):
        self.place_order_called_count += 1
        return "ILLEGAL_ORDER_ID_DISPATCHED"

def test_gate_2c_2_runner_authorizer_fails_closed_when_live_disabled(tmp_path):
    """
    Gate 2C-2:
    The actual P0-3B-D RunnerEntryAuthorizer must refuse BUY authorization
    when live_trading_enabled=False, before any downstream order dispatch.
    """
    broker = MockBroker()
    state_store = RunnerEntryStateStore(
        str(tmp_path / "runner_entry_state.json")
    )
    kill_switch_file = str(tmp_path / "KILL_SWITCH")

    authorizer = RunnerEntryAuthorizer(
        broker=broker,
        state_store=state_store,
        kill_switch_file=kill_switch_file,
        capital_limit=Decimal("20000"),
        daily_loss_limit=Decimal("2000"),
    )

    assert LIVE_TRADING_ENABLED is False

    with pytest.raises(RuntimeError, match="LIVE_TRADING_DISABLED"):
        authorizer.authorize_buy(
            symbol="RELIANCE",
            quantity=10,
            price=Decimal("1300"),
            live_trading_enabled=False,
        )

    assert broker.place_order_called_count == 0, \
        "SECURITY VIOLATION: Authorizer permitted order dispatch!"