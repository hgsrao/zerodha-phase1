"""Stale-CNC regression: real paper transitions, synthetic lifecycle trade inputs.

These component tests do not establish real replay or overnight readiness.
"""
import pytest

from test_r5_d01_product_reconciliation import (
    SYMBOL, QTY, build, fill, open_registered, reconcile,
)


def converted_position(orch):
    fill(orch, "BUY")
    receipt = orch.broker.request_product_conversion("d02-conversion", SYMBOL, QTY)
    assert receipt["status"] == "ACKNOWLEDGED"
    assert orch.broker.get_position(SYMBOL)["product"] == "CNC"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_converted_flat_position_reentry_registers_and_reconciles_as_mis(tmp_path, side):
    orch, runtime, store = build(tmp_path)
    converted_position(orch)
    fill(orch, "SELL")
    assert orch.broker.get_position(SYMBOL)["quantity"] == 0
    # Production registration must succeed after a genuine new fill, without
    # deleting the broker position or manually changing its product.
    open_registered(orch, side)
    assert orch.broker.get_position(SYMBOL)["product"] == "MIS"
    reconcile(orch, runtime, store)
    assert store.load("trade-1").record.product == "MIS"
    assert len(orch.broker.fills) == len(orch.broker.cost_ledger) == 3


def test_partial_close_preserves_live_cnc_position(tmp_path):
    orch, _, _ = build(tmp_path)
    converted_position(orch)
    fill(orch, "SELL", quantity=4)
    assert orch.broker.get_position(SYMBOL)["quantity"] == 6
    assert orch.broker.get_position(SYMBOL)["product"] == "CNC"


def test_rejected_order_does_not_change_flat_product(tmp_path):
    orch, _, _ = build(tmp_path)
    converted_position(orch)
    fill(orch, "SELL")
    before = dict(orch.broker.get_position(SYMBOL))
    result = orch.broker.place_order(SYMBOL, "BUY", QTY, "MARKET", None,
                                    orch.safety_contract.as_dict(), orch.registry)
    assert not result["passed"]
    assert orch.broker.get_position(SYMBOL) == before
    assert len(orch.broker.fills) == len(orch.broker.cost_ledger) == 2


def test_real_replay_reentry_after_accounted_converted_close(tmp_path):
    """Synthetic pre-session trade; subsequent entry is from the real replay.

    Setup uses the real broker, lifecycle registration and engine close books.
    It is not evidence that the replay itself generates a handoff.
    """
    from test_r5_d01_orchestrator_run_integration import build_orchestrator

    orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)
    symbol = "TITAN"
    fill_result = orch.broker.place_order(symbol, "BUY", 5, "MARKET", 100.0,
                                        orch.safety_contract.as_dict(), orch.registry)
    assert fill_result["passed"]
    entry = fill_result["filled_price"]
    setup_trade = dict(symbol=symbol, side="BUY", entry_price=entry, quantity=5,
                       stop_price=entry - 5, target_price=entry + 20,
                       entry_timestamp="2023-09-01 09:00", entry_atr=1.0,
                       trade_id="d02-setup", candidate_id="d02-setup")
    orch.open_trades[symbol] = setup_trade
    orch._register_position_lifecycle(symbol, setup_trade, setup_trade["entry_timestamp"])
    assert orch.broker.request_product_conversion(
        "d02-replay-conversion", symbol, 5)["status"] == "ACKNOWLEDGED"
    orch._execute_exit(symbol, "2023-09-01 09:01", setup_trade, 110.0, "d02_fixture_close")
    assert orch.broker.get_position(symbol)["quantity"] == 0
    assert orch.broker.get_position(symbol)["product"] == "CNC"
    report = orch.run({symbol: titan}, warmup=warmup)
    replay_trades = [t for t in report["trades"] if t["trade_id"] != "d02-setup"]
    assert len(replay_trades) == 1 and replay_trades[0]["side"] == "SELL"
    assert observed and all(call["broker_product"] == "MIS" for call in observed)
    assert store.load(replay_trades[0]["trade_id"]).record.lifecycle_state == "CLOSED"
    assert store.list_open() == [] and orch.open_trades == {}
    assert report["gross_pnl"] == pytest.approx(sum(t["pnl"] for t in report["trades"]))
    assert orch.broker.booked_costs == pytest.approx(sum(t["costs"] for t in report["trades"]))
