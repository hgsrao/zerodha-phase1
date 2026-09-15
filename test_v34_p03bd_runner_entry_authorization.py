"""P0-3B-D acceptance tests: local fakes only; never starts the runner."""
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import types

import pytest

if "kiteconnect" not in sys.modules:
    fake_module = types.ModuleType("kiteconnect")
    fake_module.KiteConnect = object
    sys.modules["kiteconnect"] = fake_module

from institutional_engine_v34_p01d_candidate import BrokerRiskSnapshot
from run_production_p01d_candidate import (
    KiteBrokerAdapter,
    RunnerEntryAuthorizer,
    RunnerEntryStateStore,
)


class FakeKite:
    def __init__(self, *, user_id="acct-1", positions=None, orders=None, charges=None):
        self.user_id = user_id
        self.positions_payload = positions if positions is not None else {"net": [], "day": []}
        self.orders_payload = orders if orders is not None else []
        self.charges = charges or {}
        self.place_calls = []
        self.position_calls = 0

    def profile(self):
        return {"user_id": self.user_id}

    def positions(self):
        self.position_calls += 1
        return self.positions_payload

    def orders(self):
        return self.orders_payload

    def order_charges(self, order_id):
        return self.charges[order_id]

    def place_order(self, **kwargs):
        self.place_calls.append(kwargs)
        return f"ORDER-{len(self.place_calls)}"


def v34_order(symbol="INFY"):
    return {
        "order_id": "V34-1", "exchange": "NSE", "tradingsymbol": symbol,
        "transaction_type": "BUY", "product": "MIS", "quantity": 1,
        "filled_quantity": 1, "average_price": "100", "status": "COMPLETE",
        "tag": "V3.4_ENTRY",
    }


def position(*, quantity=0, realised=0, unrealised=0):
    return {
        "exchange": "NSE", "tradingsymbol": "INFY", "product": "MIS",
        "quantity": quantity, "average_price": "100", "realised": realised,
        "unrealised": unrealised,
    }


def make_adapter(tmp_path, kite=None, *, live=True):
    kite = kite or FakeKite()
    adapter = KiteBrokerAdapter(kite, live_trading=live, expected_account_id="acct-1")
    authorizer = RunnerEntryAuthorizer(
        broker=adapter,
        state_store=RunnerEntryStateStore(tmp_path / "entry-state.json"),
        kill_switch_file=tmp_path / "KILL_SWITCH",
        capital_limit=Decimal("20000"),
        daily_loss_limit=Decimal("2000"),
    )
    adapter.set_entry_authorizer(authorizer)
    return adapter, kite, authorizer


def submit_buy(adapter):
    return adapter.place_order(
        exchange="NSE", tradingsymbol="INFY", transaction_type="BUY", quantity=1,
        product="MIS", order_type="LIMIT", price="100", tag="V3.4_ENTRY",
    )


def test_P03BD_01_valid_fresh_snapshot_permits_authorized_buy(tmp_path):
    adapter, kite, _ = make_adapter(tmp_path)
    assert submit_buy(adapter) == "ORDER-1"
    assert [call["transaction_type"] for call in kite.place_calls] == ["BUY"]


@pytest.mark.parametrize("breaker", ["capital", "daily_loss", "kill", "live_disabled"])
def test_P03BD_02_independent_breakers_block_buy_and_persist_halt(tmp_path, breaker):
    kite = FakeKite(orders=[v34_order()], charges={"V34-1": Decimal("0")})
    if breaker == "capital":
        kite.positions_payload = {"net": [position(quantity=200)], "day": []}
    elif breaker == "daily_loss":
        kite.positions_payload = {"net": [position(realised=-2000)], "day": []}
    adapter, kite, _ = make_adapter(tmp_path, kite, live=breaker != "live_disabled")
    if breaker == "kill":
        (tmp_path / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    with pytest.raises(RuntimeError):
        submit_buy(adapter)
    assert kite.place_calls == []
    assert RunnerEntryStateStore(tmp_path / "entry-state.json").load()["clearance_required"] is True


@pytest.mark.parametrize("failure", ["stale", "malformed", "unavailable", "account_mismatch", "tag_mismatch"])
def test_P03BD_03_invalid_snapshot_or_identity_blocks_buy(tmp_path, failure):
    adapter, kite, _ = make_adapter(tmp_path)
    if failure == "stale":
        stale = BrokerRiskSnapshot(
            Decimal("0"), Decimal("0"), Decimal("0"), Decimal("0"),
            observation_timestamp=datetime.now(timezone.utc) - timedelta(seconds=3),
        )
        adapter.get_daily_risk_snapshot = lambda: stale
    elif failure == "malformed":
        adapter.get_daily_risk_snapshot = lambda: {"not": "a snapshot"}
    elif failure == "unavailable":
        def unavailable():
            raise RuntimeError("broker down")
        adapter.get_daily_risk_snapshot = unavailable
    elif failure == "account_mismatch":
        kite.user_id = "different-account"
    else:
        with pytest.raises(RuntimeError, match="V3.4_ENTRY"):
            adapter.place_order(exchange="NSE", tradingsymbol="INFY", transaction_type="BUY", quantity=1, product="MIS", order_type="LIMIT", price="100", tag="other")
        assert kite.place_calls == []
        return
    with pytest.raises(RuntimeError):
        submit_buy(adapter)
    assert kite.place_calls == []


def test_P03BD_04_snapshot_change_before_final_recheck_blocks_buy(tmp_path):
    kite = FakeKite(orders=[v34_order()], charges={"V34-1": Decimal("0")})
    kite.positions_payload = {"net": [], "day": []}
    adapter, kite, _ = make_adapter(tmp_path, kite)
    original_positions = kite.positions
    def changing_positions():
        data = original_positions()
        if kite.position_calls >= 3:  # final explicit authorizer recheck
            return {"net": [position(quantity=1)], "day": []}
        return data
    kite.positions = changing_positions
    with pytest.raises(RuntimeError, match="SNAPSHOT_CHANGED"):
        submit_buy(adapter)
    assert kite.place_calls == []


def test_P03BD_05_restart_cannot_bypass_final_presubmit_gate(tmp_path):
    adapter, kite, _ = make_adapter(tmp_path)
    with pytest.raises(RuntimeError):
        adapter.entry_authorizer._halt("TEST_DURABLE_HALT")
    fresh_adapter, fresh_kite, _ = make_adapter(tmp_path)
    with pytest.raises(RuntimeError, match="DURABLE_HALT"):
        submit_buy(fresh_adapter)
    assert fresh_kite.place_calls == []


def test_P03BD_06_denied_entry_does_not_suppress_emergency_exit(tmp_path):
    adapter, kite, _ = make_adapter(tmp_path, live=True)
    (tmp_path / "KILL_SWITCH").write_text("stop", encoding="utf-8")
    with pytest.raises(RuntimeError):
        submit_buy(adapter)
    assert adapter.submit_emergency_exit(
        symbol="INFY", quantity=1, trigger_price=Decimal("99.95"), source_ltp=Decimal("100"),
        tick_size=Decimal("0.05"), tag="V3.4_EXIT",
    ) == "ORDER-1"
    assert kite.place_calls[0]["transaction_type"] == "SELL"


def test_P03BD_07_single_buy_path_cannot_bypass_authorizer(tmp_path):
    adapter, kite, authorizer = make_adapter(tmp_path)
    calls = []
    def deny(**kwargs):
        calls.append(kwargs)
        raise RuntimeError("denied by spy")
    authorizer.authorize_buy = deny
    with pytest.raises(RuntimeError, match="denied by spy"):
        submit_buy(adapter)
    assert len(calls) == 1
    assert kite.place_calls == []
