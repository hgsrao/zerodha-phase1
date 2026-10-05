"""Position lifecycle / ownership contract and the Engine A-only intraday exit router (paper only)."""
import dataclasses
import itertools
import math

import pandas as pd
import pytest

from revision2.contracts import PASignal
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5 import position_lifecycle as lc

TS = pd.Timestamp("2024-02-13 09:33:00+05:30")


def make(direction="BUY", **overrides):
    stop = 95.0 if direction == "BUY" else 105.0
    kwargs = dict(position_id="trade-1", symbol="INFY", direction=direction, initial_risk_r=5.0, anchor_price=100.0,
                  initial_stop_price=stop, created_bar_timestamp=TS)
    kwargs.update(overrides)
    return lc.open_position(**kwargs)


# --------------------------------------------------------------------------- a. the contract

def test_new_position_is_engine_a_mis_a_open():
    r = make()
    assert (r.lifecycle_state, r.owner_engine, r.product) == (lc.A_OPEN, lc.ENGINE_A, lc.PRODUCT_MIS)
    assert r.is_open and r.current_stop_price == 95.0


def test_full_handoff_chain_flips_ownership_only_on_acknowledgement():
    r = make()
    r = lc.request_transfer(r)
    assert (r.lifecycle_state, r.owner_engine, r.product) == (lc.TRANSFER_REQUESTED, lc.ENGINE_A, lc.PRODUCT_MIS)
    r = lc.acknowledge_transfer(r)
    assert (r.lifecycle_state, r.owner_engine, r.product) == (lc.B_OPEN, lc.ENGINE_B, lc.PRODUCT_CNC)
    r = lc.close_position(r)
    assert r.lifecycle_state == lc.CLOSED and not r.is_open


def test_rejected_transfer_returns_to_engine_a():
    r = lc.reject_transfer(lc.request_transfer(make()))
    assert (r.lifecycle_state, r.owner_engine, r.product) == (lc.A_OPEN, lc.ENGINE_A, lc.PRODUCT_MIS)
    assert lc.request_transfer(r).lifecycle_state == lc.TRANSFER_REQUESTED      # may be requested again


def test_direct_a_open_to_b_open_is_illegal():
    with pytest.raises(lc.PositionLifecycleError, match="requested and acknowledged"):
        lc.transition(make(), lc.B_OPEN)
    with pytest.raises(lc.PositionLifecycleError):
        lc.acknowledge_transfer(make())


@pytest.mark.parametrize("src,dst", list(itertools.product(lc.LIFECYCLE_STATES, repeat=2)))
def test_every_state_pair_matches_the_legal_transition_table(src, dst):
    base = make()
    path = {lc.A_OPEN: [], lc.TRANSFER_REQUESTED: [lc.TRANSFER_REQUESTED],
            lc.B_OPEN: [lc.TRANSFER_REQUESTED, lc.B_OPEN], lc.CLOSED: [lc.CLOSED]}[src]
    record = base
    for step in path:
        record = lc.transition(record, step)
    if dst in lc.LEGAL_TRANSITIONS[src]:
        assert lc.transition(record, dst).lifecycle_state == dst
    else:
        with pytest.raises(lc.PositionLifecycleError, match="illegal transition"):
            lc.transition(record, dst)


def test_reject_is_only_valid_from_transfer_requested():
    for record in (make(), lc.acknowledge_transfer(lc.request_transfer(make())), lc.close_position(make())):
        with pytest.raises(lc.PositionLifecycleError):
            lc.reject_transfer(record)


def test_closed_is_terminal_and_unknown_states_are_rejected():
    closed = lc.close_position(make())
    for state in lc.LIFECYCLE_STATES:
        with pytest.raises(lc.PositionLifecycleError):
            lc.transition(closed, state)
    with pytest.raises(lc.PositionLifecycleError, match="unknown"):
        lc.transition(make(), "B_PENDING")


def test_identity_and_risk_survive_every_transition():
    start = make("SELL")
    end = lc.close_position(lc.acknowledge_transfer(lc.request_transfer(start)))
    for field in ("position_id", "symbol", "direction", "initial_risk_r", "anchor_price", "created_bar_timestamp"):
        assert getattr(end, field) == getattr(start, field)


def test_record_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make().owner_engine = lc.ENGINE_B


@pytest.mark.parametrize("field,value", [
    ("direction", "LONG"), ("owner_engine", "ENGINE_C"), ("product", "NRML"), ("lifecycle_state", "OPEN"),
    ("position_id", ""), ("symbol", ""), ("initial_risk_r", 0.0), ("initial_risk_r", -1.0),
    ("initial_risk_r", math.nan), ("anchor_price", 0.0), ("anchor_price", math.inf),
    ("current_stop_price", math.nan), ("current_stop_price", "95"), ("created_bar_timestamp", "2024-02-13"),
    ("created_bar_timestamp", pd.NaT),
])
def test_invalid_field_values_are_rejected(field, value):
    with pytest.raises(lc.PositionLifecycleError):
        dataclasses.replace(make(), **{field: value})


@pytest.mark.parametrize("state,owner,product", [
    (lc.A_OPEN, lc.ENGINE_B, lc.PRODUCT_MIS), (lc.A_OPEN, lc.ENGINE_A, lc.PRODUCT_CNC),
    (lc.TRANSFER_REQUESTED, lc.ENGINE_B, lc.PRODUCT_CNC), (lc.B_OPEN, lc.ENGINE_A, lc.PRODUCT_CNC),
    (lc.B_OPEN, lc.ENGINE_B, lc.PRODUCT_MIS)])
def test_state_requires_matching_owner_and_product(state, owner, product):
    with pytest.raises(lc.PositionLifecycleError, match="requires owner/product"):
        dataclasses.replace(make(), lifecycle_state=state, owner_engine=owner, product=product)


@pytest.mark.parametrize("direction,stop", [("BUY", 100.0), ("BUY", 101.0), ("SELL", 100.0), ("SELL", 99.0)])
def test_initial_stop_must_be_on_the_protective_side(direction, stop):
    with pytest.raises(lc.PositionLifecycleError, match="protective side"):
        make(direction, initial_stop_price=stop)


def test_stop_only_tightens():
    buy = make("BUY")
    assert lc.tighten_stop(buy, 97.0).current_stop_price == 97.0
    assert lc.tighten_stop(buy, 95.0).current_stop_price == 95.0            # unchanged is allowed
    with pytest.raises(lc.PositionLifecycleError, match="loosens"):
        lc.tighten_stop(buy, 94.0)
    sell = make("SELL")
    assert lc.tighten_stop(sell, 103.0).current_stop_price == 103.0
    with pytest.raises(lc.PositionLifecycleError, match="loosens"):
        lc.tighten_stop(sell, 106.0)
    with pytest.raises(lc.PositionLifecycleError):
        lc.tighten_stop(buy, math.nan)
    with pytest.raises(lc.PositionLifecycleError, match="CLOSED"):
        lc.tighten_stop(lc.close_position(buy), 99.0)


# --------------------------------------------------------------------------- b/c. the exit router

BAR = dict(open=100.0, high=100.5, low=99.5, close=100.0)


def signal():
    return PASignal(symbol="INFY", timestamp="2024-02-13 10:00", direction=1, confidence=.8, momentum=.5,
                    volatility=.01, vwap_deviation=.1, volume_confirmation=.2, exit_confidence=.8)


def engine_with_position(side="BUY", engine="ENGINE_A", register=True):
    orch = Engine(["INFY"])
    fill = orch.broker.place_order("INFY", side, 10, "MARKET", 100.0, orch.safety_contract.as_dict(), orch.registry)
    assert fill["passed"]
    entry = fill["filled_price"]
    stop, target = (entry - 5, entry + 10) if side == "BUY" else (entry + 5, entry - 10)
    trade = dict(side=side, entry_price=entry, quantity=10, stop_price=stop, target_price=target,
                 minimum_hold_bars=2, maximum_hold_bars=375, entry_timestamp="2024-02-13 09:33", entry_atr=1.0,
                 planned_entry_price=entry, planned_stop_price=stop, planned_target_price=target,
                 trade_id="trade-1", candidate_id="candidate-1")
    orch.open_trades["INFY"] = trade
    orch._exit_controller_states["INFY"] = orch.exit_controller.open_position(side, entry, stop, target, 375)
    if register:
        orch._register_position_lifecycle("INFY", trade, "2024-02-13 09:33")
        if engine == "ENGINE_B":
            orch._transition_position_lifecycle(trade, lc.TRANSFER_REQUESTED, "2024-02-13 10:00")
            orch._transition_position_lifecycle(trade, lc.B_OPEN, "2024-02-13 10:01")
    return orch, trade


def step(orch, timestamp, bar=BAR, held=30, session_last_bar=False):
    orch.id_box._current_regime = lambda *args: "calm"
    orch._maybe_exit("INFY", timestamp, dict(bar), signal(), held, session_last_bar, .8)


def last_reason(orch):
    return orch.completed_trades[-1]["reason"] if orch.completed_trades else None


def test_engine_a_trade_is_force_closed_at_1525():
    orch, _ = engine_with_position("BUY", "ENGINE_A")
    step(orch, "2024-02-13 15:25")
    assert not orch.open_trades and last_reason(orch) == "force_close_time"


def test_engine_a_trade_is_closed_at_the_session_last_bar():
    orch, _ = engine_with_position("SELL", "ENGINE_A")
    step(orch, "2024-02-13 15:14", session_last_bar=True)
    assert not orch.open_trades and last_reason(orch) == "mis_session_close"


def test_trade_without_a_lifecycle_record_is_engine_a():
    orch, trade = engine_with_position("BUY", register=False)
    assert orch._owner_engine(trade) == "ENGINE_A"
    step(orch, "2024-02-13 15:25")
    assert last_reason(orch) == "force_close_time"


def test_transfer_requested_position_is_still_engine_a_owned_and_squared_off():
    orch, trade = engine_with_position("BUY", "ENGINE_A")
    orch._transition_position_lifecycle(trade, lc.TRANSFER_REQUESTED, "2024-02-13 15:00")
    assert orch._owner_engine(trade) == "ENGINE_A"
    step(orch, "2024-02-13 15:25")
    assert last_reason(orch) == "force_close_time"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_engine_b_trade_survives_the_1525_force_close(side):
    orch, trade = engine_with_position(side, "ENGINE_B")
    step(orch, "2024-02-13 15:25")
    assert "INFY" in orch.open_trades and not orch.completed_trades
    step(orch, "2024-02-13 15:29")
    assert "INFY" in orch.open_trades and not orch.completed_trades


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_engine_b_trade_survives_the_mis_session_close(side):
    orch, _ = engine_with_position(side, "ENGINE_B")
    step(orch, "2024-02-13 15:14", session_last_bar=True)
    assert "INFY" in orch.open_trades and not orch.completed_trades


@pytest.mark.parametrize("when", ["2024-02-13 11:00", "2024-02-13 15:26"])
def test_engine_b_buy_hard_stop_still_exits(when):
    orch, trade = engine_with_position("BUY", "ENGINE_B")
    bar = dict(open=99.0, high=99.5, low=trade["stop_price"] - 1.0, close=96.0)
    step(orch, when, bar)
    assert not orch.open_trades and last_reason(orch) in ("stop", "stop_gap")


@pytest.mark.parametrize("when", ["2024-02-13 11:00", "2024-02-13 15:26"])
def test_engine_b_sell_hard_stop_still_exits(when):
    orch, trade = engine_with_position("SELL", "ENGINE_B")
    bar = dict(open=101.0, high=trade["stop_price"] + 1.0, low=100.5, close=104.0)
    step(orch, when, bar)
    assert not orch.open_trades and last_reason(orch) in ("stop", "stop_gap")


def test_engine_b_trade_is_closed_by_the_drawdown_halt_kill_switch():
    orch, _ = engine_with_position("BUY", "ENGINE_B")
    orch._mtm_peak = orch.starting_equity * 10.0           # equity is far below its high-water mark
    step(orch, "2024-02-13 15:26")
    assert not orch.open_trades and last_reason(orch) == "forced_close_drawdown_halt"


def test_engine_b_trade_is_closed_by_a_micom_trip():
    orch, _ = engine_with_position("BUY", "ENGINE_B")
    orch._micom_trip = {"ansi_code": "ANSI_27"}
    step(orch, "2024-02-13 15:26")
    assert not orch.open_trades and last_reason(orch) == "micom_trip:ANSI_27"


def test_engine_b_trade_still_obeys_the_maximum_hold_ceiling():
    orch, _ = engine_with_position("BUY", "ENGINE_B")
    step(orch, "2024-02-13 11:00", held=375)
    assert last_reason(orch) == "max_hold"


def test_closing_a_trade_closes_its_lifecycle_record():
    orch, trade = engine_with_position("BUY", "ENGINE_B")
    orch._execute_exit("INFY", "2024-02-13 11:00", trade, 101.0, "stop")
    assert orch._position_lifecycle["trade-1"].lifecycle_state == lc.CLOSED
    assert not orch.open_trades and len(orch.completed_trades) == 1


def test_orchestrator_rejects_an_illegal_lifecycle_transition():
    orch, trade = engine_with_position("BUY", "ENGINE_A")
    with pytest.raises(lc.PositionLifecycleError):
        orch._transition_position_lifecycle(trade, lc.B_OPEN, "2024-02-13 10:00")
    assert orch._owner_engine(trade) == "ENGINE_A"


def test_lifecycle_records_are_not_added_to_completed_trade_records():
    orch, trade = engine_with_position("BUY", "ENGINE_A")
    orch._execute_exit("INFY", "2024-02-13 11:00", trade, 101.0, "stop")
    assert not {"owner_engine", "lifecycle_state", "product"} & set(orch.completed_trades[-1])
