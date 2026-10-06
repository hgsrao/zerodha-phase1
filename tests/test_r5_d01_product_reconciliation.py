"""R5-D01 regression and negative-detection tests.

Defect: a real paper fill left the broker position without the ``product`` that
``CombinedCycleRuntime.reconcile`` compares against the lifecycle record, so the runtime aborted on the first
bar after every fill.

Classification of what these tests use (see the D01 report):
  REAL PAPER BROKER   ``CostedPaperBrokerAdapter`` (and, in one negative test, the real parent ``PaperBrokerAdapter``)
  REAL RUNTIME/STORE  ``CombinedCycleRuntime`` + ``CombinedCycleStore`` (SQLite file), ``HandoffManager``, ``EngineBController``
  NORMAL ORCHESTRATOR ``Revision2ExternalEngineOrchestrator`` constructed normally (no ``__new__``); ``run()`` is NOT called here
  SYNTHETIC_UNIT_FIXTURE the trade dict and its insertion into ``open_trades`` (the inputs ``run()`` would build).
No mock, no shim, no monkey-patch.  Nothing here is replay evidence; the replay proof is the separate real-run command in
outputs/R5_D01_evidence/.
"""
import pytest

from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.combined_cycle_runtime import CombinedCycleReconciliationError, CombinedCycleRuntime
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.handoff_manager import HandoffConfig, HandoffManager
from runtime.operating_mode import PaperBrokerAdapter

SYMBOL = "INFY"
QTY = 10


def build(tmp_path):
    store = CombinedCycleStore(tmp_path / "cycle.db")
    runtime = CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)),
                                   EngineBController(EngineBPolicy(enabled=True)))
    return Engine([SYMBOL], combined_cycle_runtime=runtime), runtime, store


def fill(orch, side, quantity=QTY, via_parent=False):
    """A real paper fill.  ``via_parent`` calls the real parent PaperBrokerAdapter.place_order directly."""
    order = PaperBrokerAdapter.place_order if via_parent else type(orch.broker).place_order
    result = order(orch.broker, SYMBOL, side, quantity, "MARKET", 100.0, orch.safety_contract.as_dict(), orch.registry)
    assert result["passed"]
    return result


def open_registered(orch, side="BUY", via_parent=False):
    """Fill, then register through the production path (_register_position_lifecycle -> runtime.register_fill)."""
    entry = fill(orch, side, via_parent=via_parent)["filled_price"]
    stop, target = (entry - 5, entry + 10) if side == "BUY" else (entry + 5, entry - 10)
    trade = dict(side=side, entry_price=entry, quantity=QTY, stop_price=stop, target_price=target,       # SYNTHETIC_UNIT_FIXTURE
                 minimum_hold_bars=2, maximum_hold_bars=375, entry_timestamp="2024-02-13 09:33", entry_atr=1.0,
                 planned_entry_price=entry, planned_stop_price=stop, planned_target_price=target,
                 trade_id="trade-1", candidate_id="candidate-1")
    orch.open_trades[SYMBOL] = trade
    orch._register_position_lifecycle(SYMBOL, trade, "2024-02-13 09:33")
    return trade


def reconcile(orch, runtime, store):
    runtime.reconcile(orch, store.load("trade-1"))


def test_concrete_broker_is_the_paper_implementation(tmp_path):
    orch, _, _ = build(tmp_path)
    assert type(orch.broker).__qualname__ == "CostedPaperBrokerAdapter"
    assert orch.broker.environment == "paper"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_real_fill_carries_explicit_mis_product(tmp_path, side):
    orch, _, _ = build(tmp_path)
    fill(orch, side)
    assert orch.broker.get_position(SYMBOL)["product"] == "MIS"
    assert [p["product"] for p in orch.broker.snapshot()["positions"] if p["tradingsymbol"] == SYMBOL] == ["MIS"]


def test_fill_does_not_overwrite_a_converted_product(tmp_path):
    orch, _, _ = build(tmp_path)
    fill(orch, "BUY")
    assert orch.broker.request_product_conversion("c1", SYMBOL, QTY, timestamp="2024-02-13 15:10")["status"] == "ACKNOWLEDGED"
    fill(orch, "BUY", quantity=5)
    assert orch.broker.get_position(SYMBOL)["product"] == "CNC"


@pytest.mark.parametrize("side", ["BUY", "SELL"])
def test_reconcile_accepts_the_genuine_state_a_real_fill_creates(tmp_path, side):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, side)
    reconcile(orch, runtime, store)                      # must not raise
    assert store.load("trade-1").record.product == "MIS"


# ----------------------------------------------------------------------------- negative detection preserved

def test_wrong_quantity_is_still_rejected(tmp_path):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY")
    fill(orch, "SELL", quantity=4)                       # real partial exit at the broker
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)


def test_wrong_product_contradicting_the_lifecycle_is_still_rejected(tmp_path):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY")                         # lifecycle A_OPEN / MIS
    assert orch.broker.request_product_conversion("c1", SYMBOL, QTY, timestamp="2024-02-13 15:10")["status"] == "ACKNOWLEDGED"
    assert store.load("trade-1").record.lifecycle_state == "A_OPEN"
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)                  # broker CNC vs lifecycle MIS


def test_missing_broker_position_is_still_rejected(tmp_path):
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY")
    fill(orch, "SELL")                                   # real full exit at the broker
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)


def test_a_position_with_no_product_is_not_defaulted_by_the_runtime(tmp_path):
    """The real parent PaperBrokerAdapter fills without a product key.  The runtime must still fail closed."""
    orch, runtime, store = build(tmp_path)
    open_registered(orch, "BUY", via_parent=True)
    assert "product" not in orch.broker.get_position(SYMBOL)
    with pytest.raises(CombinedCycleReconciliationError, match="quantity/product differs"):
        reconcile(orch, runtime, store)
