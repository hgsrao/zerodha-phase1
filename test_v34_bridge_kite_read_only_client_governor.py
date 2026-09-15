"""Tests for KiteReadOnlyClient's optional request-governor wiring
(EA1-R1 step 3 integration continued, v34_bridge_kite_read_only_client.py).
"""
from __future__ import annotations

import json

import pytest

from kite_request_governor import DEFAULT, QUOTE, KiteRequestGovernor
from test_v34_bridge_kite_read_only_client import FakeKiteConnect
from v34_bridge_kite_read_only_client import KiteReadOnlyClient


class _FakeClock:
    def __init__(self, start: float = 1_000_000.0):
        self.t = start

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class _RecordingSleep:
    def __init__(self, clock: _FakeClock):
        self.clock = clock
        self.calls = []

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        self.clock.advance(seconds)


def _client(tmp_path, *, kite=None):
    clock = _FakeClock()
    sleeper = _RecordingSleep(clock)
    governor = KiteRequestGovernor(state_dir=tmp_path, now_fn=clock.now, sleep_fn=sleeper)
    kite = kite or FakeKiteConnect()
    return KiteReadOnlyClient(kite, governor=governor), kite, sleeper


def test_no_governor_supplied_is_a_complete_no_op(tmp_path):
    client = KiteReadOnlyClient(FakeKiteConnect())
    client.get_positions()
    client.get_orders()
    assert not (tmp_path / "kite_rate_governor_ledger.json").exists()


def test_ltp_is_gated_under_quote_class(tmp_path):
    client, kite, sleeper = _client(tmp_path)
    kite.ltp_response = {"NSE:SBIN": {"last_price": 500.0}}
    client.ltp(["SBIN"])
    assert sleeper.calls == []
    client.ltp(["SBIN"])  # second call this window must wait
    assert sleeper.calls == [pytest.approx(1.0, abs=1e-6)]


def test_get_positions_get_orders_and_get_trades_share_the_default_budget(tmp_path):
    client, kite, sleeper = _client(tmp_path)
    for _ in range(3):
        client.get_positions()
        client.get_orders()
        client.get_trades()
    assert sleeper.calls == []  # 9 combined DEFAULT-class calls, budget is 10
    client.get_positions()  # the 10th
    assert sleeper.calls == []
    client.get_orders()  # the 11th
    assert len(sleeper.calls) == 1


def test_get_virtual_contract_note_for_one_order_is_gated_exactly_once(tmp_path):
    client, kite, sleeper = _client(tmp_path)
    kite.virtual_contract_note_response = [{"charges": {"total": "10.00"}}]
    client.get_virtual_contract_note_for_one_order({"tradingsymbol": "SBIN"})
    ledger = json.loads((tmp_path / "kite_rate_governor_ledger.json").read_text())
    assert len(ledger.get(DEFAULT, [])) == 1  # not double-counted via the batch method it delegates to


def test_get_tick_size_only_gates_the_underlying_instruments_call_once(tmp_path):
    client, kite, sleeper = _client(tmp_path)
    kite.instruments_response = [{"tradingsymbol": "SBIN", "tick_size": "0.05"}]
    client.get_tick_size("SBIN")
    client.get_tick_size("SBIN")  # cached - must not hit the governor a second time
    ledger = json.loads((tmp_path / "kite_rate_governor_ledger.json").read_text())
    assert len(ledger.get(DEFAULT, [])) == 1
