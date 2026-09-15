
import json
import sys
from decimal import Decimal
from pathlib import Path

import pytest
import types

# The candidate runner imports KiteConnect at module load time. P0-3B is a
# local broker-boundary contract test and must not require the real SDK,
# credentials, or network access.
if "kiteconnect" not in sys.modules:
    fake_kiteconnect = types.ModuleType("kiteconnect")
    class _KiteConnectStub:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("KiteConnect stub must never be instantiated in P0-3B.")
    fake_kiteconnect.KiteConnect = _KiteConnectStub
    sys.modules["kiteconnect"] = fake_kiteconnect

ROOT = Path("/mnt/data")
sys.path.insert(0, str(ROOT))

from institutional_engine_v34_p01d_candidate import (
    BrokerObservationContractViolation,
    Config,
    TradingEngineV34,
)

class FakeKite:
    def __init__(self, *, positions=None, orders=None, charges=None):
        self._positions = positions
        self._orders = orders
        self._charges = charges

    def positions(self):
        return self._positions

    def orders(self):
        return self._orders

    def order_charges(self, order_id):
        if self._charges is None:
            raise RuntimeError("charges unavailable")
        return self._charges[order_id]


class SnapshotHarness:
    """
    RED-first contract harness.

    The candidate must expose a concrete broker-risk snapshot seam through
    KiteBrokerAdapter.get_daily_risk_snapshot().  We intentionally do not
    provide a placeholder implementation here.
    """
    def __init__(self, kite):
        from run_production_p01d_candidate import KiteBrokerAdapter
        self.kite = kite
        self.adapter = KiteBrokerAdapter.__new__(KiteBrokerAdapter)
        self.adapter.kite = kite
        self.adapter.config = Config(alert_webhook_url=None, max_daily_loss=Decimal("2000"))

    def snapshot(self):
        return self.adapter.get_daily_risk_snapshot()


def clean_positions():
    return {
        "net": [{
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "product": "MIS",
            "quantity": 10,
            "average_price": 1500,
            "last_price": 1490,
            "realised": -100,
            "unrealised": -100,
            "pnl": -200,
        }],
        "day": [{
            "exchange": "NSE",
            "tradingsymbol": "RELIANCE",
            "product": "MIS",
            "quantity": 10,
            "average_price": 1500,
            "last_price": 1490,
            "realised": -100,
            "unrealised": -100,
            "pnl": -200,
        }],
    }


def clean_orders():
    return [{
        "order_id": "V34-001",
        "exchange": "NSE",
        "tradingsymbol": "RELIANCE",
        "transaction_type": "BUY",
        "product": "MIS",
        "quantity": 10,
        "filled_quantity": 10,
        "average_price": 1500,
        "status": "COMPLETE",
        "tag": "V3.4_ENTRY",
    }]


def clean_charges():
    return {
        "V34-001": Decimal("20")
    }


def make_clean():
    return SnapshotHarness(FakeKite(
        positions=clean_positions(),
        orders=clean_orders(),
        charges=clean_charges(),
    ))


# ---------------------------------------------------------------------------
# P0-3B-B RED acceptance tests
# ---------------------------------------------------------------------------

def test_P03B_01_clean_broker_snapshot_returns_valid_risk_snapshot():
    snap = make_clean().snapshot()
    assert snap.valid is True
    assert snap.realized_pnl == Decimal("-100")
    assert snap.unrealized_pnl == Decimal("-100")
    assert snap.charges == Decimal("20")
    assert snap.daily_net_pnl == Decimal("-220")
    assert snap.deployed_capital == Decimal("15000")
    assert snap.observation_timestamp is not None


def test_P03B_02_missing_positions_fails_closed():
    h = SnapshotHarness(FakeKite(
        positions=None,
        orders=clean_orders(),
        charges=clean_charges(),
    ))
    with pytest.raises(BrokerObservationContractViolation):
        h.snapshot()


def test_P03B_03_malformed_positions_payload_fails_closed():
    h = SnapshotHarness(FakeKite(
        positions={"net": "not-a-list", "day": []},
        orders=clean_orders(),
        charges=clean_charges(),
    ))
    with pytest.raises(BrokerObservationContractViolation):
        h.snapshot()


def test_P03B_04_non_numeric_position_quantity_fails_closed():
    positions = clean_positions()
    positions["net"][0]["quantity"] = "ten"
    h = SnapshotHarness(FakeKite(
        positions=positions,
        orders=clean_orders(),
        charges=clean_charges(),
    ))
    with pytest.raises(BrokerObservationContractViolation):
        h.snapshot()


def test_P03B_05_partial_fills_are_aggregated_from_execution_reality():
    orders = [
        {**clean_orders()[0], "filled_quantity": 6, "average_price": 1500},
        {
            **clean_orders()[0],
            "order_id": "V34-002",
            "filled_quantity": 4,
            "average_price": 1510,
        },
    ]
    charges = {"V34-001": Decimal("12"), "V34-002": Decimal("8")}
    h = SnapshotHarness(FakeKite(
        positions=clean_positions(),
        orders=orders,
        charges=charges,
    ))
    snap = h.snapshot()
    assert snap.executed_buy_quantity == 10
    assert snap.executed_buy_value == Decimal("15040")
    assert snap.charges == Decimal("20")


def test_P03B_06_duplicate_execution_records_fail_closed():
    orders = [clean_orders()[0], dict(clean_orders()[0])]
    h = SnapshotHarness(FakeKite(
        positions=clean_positions(),
        orders=orders,
        charges=clean_charges(),
    ))
    with pytest.raises(BrokerObservationContractViolation):
        h.snapshot()


def test_P03B_07_charge_calculation_failure_fails_closed():
    h = SnapshotHarness(FakeKite(
        positions=clean_positions(),
        orders=clean_orders(),
        charges=None,
    ))
    with pytest.raises(BrokerObservationContractViolation):
        h.snapshot()


def test_P03B_08_manual_untagged_trade_is_excluded_from_v34_scope():
    orders = clean_orders() + [{
        "order_id": "MANUAL-001",
        "exchange": "NSE",
        "tradingsymbol": "TCS",
        "transaction_type": "BUY",
        "product": "MIS",
        "quantity": 100,
        "filled_quantity": 100,
        "average_price": 4000,
        "status": "COMPLETE",
        "tag": None,
    }]
    charges = {"V34-001": Decimal("20"), "MANUAL-001": Decimal("500")}
    h = SnapshotHarness(FakeKite(
        positions=clean_positions(),
        orders=orders,
        charges=charges,
    ))
    snap = h.snapshot()
    assert snap.deployed_capital == Decimal("15000")
    assert snap.charges == Decimal("20")
    assert snap.executed_buy_quantity == 10


def test_P03B_09_exact_20000_capital_is_allowed():
    positions = clean_positions()
    positions["net"][0]["quantity"] = 10
    positions["net"][0]["average_price"] = 1500
    orders = clean_orders() + [{
        "order_id": "V34-002",
        "exchange": "NSE",
        "tradingsymbol": "INFY",
        "transaction_type": "BUY",
        "product": "MIS",
        "quantity": 5,
        "filled_quantity": 5,
        "average_price": 1000,
        "status": "OPEN",
        "tag": "V3.4_ENTRY",
    }]
    h = SnapshotHarness(FakeKite(
        positions=positions,
        orders=orders,
        charges={"V34-001": Decimal("20"), "V34-002": Decimal("0")},
    ))
    snap = h.snapshot()
    assert snap.deployed_capital + snap.pending_buy_exposure == Decimal("20000")
    assert snap.capital_ceiling_ok is True


def test_P03B_10_20000_01_capital_is_blocked():
    positions = clean_positions()
    orders = clean_orders() + [{
        "order_id": "V34-002",
        "exchange": "NSE",
        "tradingsymbol": "INFY",
        "transaction_type": "BUY",
        "product": "MIS",
        "quantity": 5001,
        "filled_quantity": 5001,
        "average_price": 1000,
        "status": "OPEN",
        "tag": "V3.4_ENTRY",
    }]
    h = SnapshotHarness(FakeKite(
        positions=positions,
        orders=orders,
        charges={"V34-001": Decimal("20"), "V34-002": Decimal("0")},
    ))
    snap = h.snapshot()
    assert snap.deployed_capital + snap.pending_buy_exposure > Decimal("20000")
    assert snap.capital_ceiling_ok is False


def test_P03B_11_exact_minus_2000_daily_loss_trips():
    positions = clean_positions()
    positions["net"][0]["realised"] = -1800
    positions["net"][0]["unrealised"] = -150
    positions["day"][0]["realised"] = -1800
    positions["day"][0]["unrealised"] = -150
    h = SnapshotHarness(FakeKite(
        positions=positions,
        orders=clean_orders(),
        charges={"V34-001": Decimal("50")},
    ))
    snap = h.snapshot()
    assert snap.daily_net_pnl == Decimal("-2000")
    assert snap.daily_loss_limit_ok is False


def test_P03B_12_stale_snapshot_blocks_new_entry():
    h = make_clean()
    snap = h.snapshot()
    assert snap.is_fresh(max_age_seconds=0) is False
    assert snap.entry_allowed is False


def test_P03B_13_kill_switch_does_not_disable_emergency_exit():
    h = make_clean()
    snap = h.snapshot()
    assert snap.entry_allowed is False if snap.kill_switch_active else True
    assert snap.emergency_exit_allowed is True
