"""R5-D01.1 permanent integration regression: the real offline replay path that originally exposed R5-D01.

Path exercised (nothing below is constructed by the test):
  Revision2ExternalEngineOrchestrator (normal constructor) -> Orchestrator.run() -> Engine A signal/entry ->
  CostedPaperBrokerAdapter fill -> lifecycle registration (_register_position_lifecycle -> CombinedCycleRuntime.register_fill) ->
  next bars (_maybe_exit -> CombinedCycleRuntime.handle_bar) -> CombinedCycleRuntime.reconcile.

Input classification: DERIVED_REAL_REPLAY_FIXTURE.  tests/fixtures/r5_d01/ holds rows copied unchanged from the output of the
repository's own Block 1 loader (scripts/run_r5_step5_candidate.prepare_block) for TITAN, the NIFTY/VIX 15-minute feeds and the
Trial 007 parameter file; see tests/fixtures/r5_d01/PROVENANCE.json for sources, hashes, row counts and the extraction procedure.
The bars are historical market data, not generated data.

Real-replay coverage of this test is SELL only: the verified TITAN replay produces one SELL trade.  BUY and SELL product behaviour
is covered separately by tests/test_r5_d01_product_reconciliation.py.

TEST_OBSERVATION_SPY: `observe_reconcile` below records the arguments of each call to CombinedCycleRuntime.reconcile and then
calls the real method unchanged; it neither alters a return value or an exception nor touches broker, lifecycle or product state.
"""
import json
from pathlib import Path

import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.combined_cycle_runtime import CombinedCycleRuntime
from revision5.combined_cycle_store import CombinedCycleStore
from revision5.engine_b_management import EngineBController, EngineBPolicy
from revision5.handoff_manager import HandoffConfig, HandoffManager

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "r5_d01"
PROTOCOL = Path(__file__).resolve().parents[1] / "revision5" / "step5_sealed_calibration_protocol_v2.json"
SYMBOL = "TITAN"


def load_inputs():
    titan = pd.read_csv(FIXTURES / "titan_1min_block1_session1.csv")
    titan["timestamp"] = pd.to_datetime(titan["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    feeds = {}
    for name, file in (("nifty", "nifty_50_15min_prefix.csv"), ("vix", "india_vix_15min_prefix.csv")):
        feed = pd.read_csv(FIXTURES / file)
        feed["timestamp"] = pd.to_datetime(feed["timestamp"], utc=True)
        feeds[name] = feed
    return titan, feeds["nifty"], feeds["vix"]


def test_fixture_files_match_their_recorded_hashes():
    import hashlib
    provenance = json.loads((FIXTURES / "PROVENANCE.json").read_text())
    assert provenance["classification"] == "DERIVED_REAL_REPLAY_FIXTURE"
    for name, record in provenance["files"].items():
        assert hashlib.sha256((FIXTURES / name).read_bytes()).hexdigest() == record["sha256"], name
    assert hashlib.sha256((FIXTURES / "trial_007_params.json").read_bytes()).hexdigest() == provenance["params_source"]["sha256"]


def build_orchestrator(tmp_path):
    """Normal construction of the orchestrator exactly as the Step 5 block executor builds it, plus a real combined-cycle runtime."""
    protocol = json.loads(PROTOCOL.read_text())
    contract = protocol["block_execution_contract"]
    params = json.loads((FIXTURES / "trial_007_params.json").read_text())
    titan, nifty, vix = load_inputs()
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    equity = float(contract["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    store = CombinedCycleStore(tmp_path / "cycle.db")
    runtime = CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)),
                                   EngineBController(EngineBPolicy(enabled=True)))
    orch = Revision2ExternalEngineOrchestrator(
        [SYMBOL], registry, calibration_overrides=params, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(nifty, vix), real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="compact",
        governor_authority="full", combined_cycle_runtime=runtime)

    observed = []
    real_reconcile = runtime.reconcile

    def observe_reconcile(engine, snapshot):                    # TEST_OBSERVATION_SPY
        position = engine.broker.get_position(snapshot.record.symbol)
        observed.append({"trade_id": snapshot.record.position_id, "state": snapshot.record.lifecycle_state,
                         "record_product": snapshot.record.product, "broker_quantity": position.get("quantity"),
                         "broker_product": position.get("product"), "fills_so_far": len(engine.broker.fills)})
        return real_reconcile(engine, snapshot)

    runtime.reconcile = observe_reconcile
    return orch, runtime, store, observed, titan, int(contract["stock_warmup_bars_per_symbol"])


def test_orchestrator_run_reconciles_after_the_paper_fill_beyond_the_d01_failure_point(tmp_path):
    orch, runtime, store, observed, titan, warmup = build_orchestrator(tmp_path)

    # The concrete broker and the runtime are established before anything runs.
    assert type(orch.broker).__qualname__ == "CostedPaperBrokerAdapter"
    assert orch.broker.environment == "paper"
    assert orch.combined_cycle_runtime is runtime and type(runtime).__qualname__ == "CombinedCycleRuntime"

    report = orch.run({SYMBOL: titan}, warmup=warmup)           # the real replay; any reconciliation error propagates

    # A real Engine A fill happened through run() and the paper broker.
    trades = report["trades"]
    assert len(trades) == 1 and trades[0]["symbol"] == SYMBOL and trades[0]["side"] == "SELL"
    assert len(orch.broker.fills) >= 2 and orch.broker.fills[0]["side"] == "SELL"

    # Lifecycle and durable runtime state were created by production code.
    trade_id, quantity = trades[0]["trade_id"], trades[0]["quantity"]
    assert trade_id in orch._position_lifecycle
    durable = store.load(trade_id).record
    assert (durable.symbol, durable.direction, durable.owner_engine, durable.product) == (SYMBOL, "SELL", "ENGINE_A", "MIS")
    assert durable.lifecycle_state == "CLOSED" and store.list_open() == [] and orch.open_trades == {}

    # Reconciliation ran after the fill, more than once, with broker and durable owner in agreement.
    assert len(observed) >= 2, "the replay must continue past the first post-fill reconciliation"
    assert observed[0]["fills_so_far"] >= 1, "the first reconciliation must come after the paper fill"
    for call in observed:
        assert call["state"] == "A_OPEN" and call["record_product"] == "MIS" and call["broker_product"] == "MIS"
        assert call["broker_quantity"] == -quantity
