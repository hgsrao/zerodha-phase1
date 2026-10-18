"""PAPER_APPLY blocker closure: real R5 protection -> ECS bay availability (BLOCKER 1), and
authoritative external-engine realized-R close -> DynamicBayLoadDispatcher.register_trade()
(BLOCKER 2).

Still SHADOW-only: nothing here enables PAPER_APPLY or live actuation.  See
outputs/R5-ECS-GRID-DISPATCH-INTEGRATION-AUDIT.md section 10 for the blocker closure record.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from revision2.contracts import EffectiveConfig
from revision5.ccpp_unified_plant import CentralPlantMasterDCS, DynamicBayLoadDispatcher
from revision5.governor import BAY_GOVERNOR_SPECS
from revision5.plant_control import (
    BayStatus, ECSPlantSupervisor, PlantControlError, PlantGridState, PlantGridStateName,
    SectorDispatchController,
)
from revision5.protection_snapshot import (
    CONNECTED_SOURCE, FALLBACK_SOURCE, build_plant_protection_snapshot,
    derive_bay_status_and_master_block,
)
from revision5.startup_synchronization import MachineKind, StartupState, TurbineStartupSequencer
from revision5.topology import BAY_IDS, bay_for_symbol

from revision2_external.orchestrator import (
    PositionReconciliationError, Revision2ExternalEngineOrchestrator as Engine,
)

ROOT = Path(__file__).resolve().parent.parent
REG = CanonicalParameterRegistry()


def cfg(**overrides):
    values = {n: s.default for n, s in REG.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REG.FROZEN_IDENTITY_SHA256)


def real_plant():
    return CentralPlantMasterDCS(total_capital=1_000_000.0, db_path=":memory:")


def all_available_fallback():
    return {bay: BayStatus() for bay in BAY_IDS}


def synchronized_grid():
    return PlantGridState(PlantGridStateName.SYNCHRONIZED.value, True, "GRID_SYNCHRONIZED",
                          "2024-01-01T00:00:00+00:00")


def install_stub_sequencer(bay, *, tripped=False):
    """A minimal startup sequencer for a unit test focused on the READ side (snapshot/ECS), not on
    the sequencer's own (separately tested) transition logic.  Default state OFF -> dispatch_ready
    False, exactly as an un-started real unit would read."""
    machine_kind = MachineKind.GAS_TURBINE if bay.bay_id.startswith("GTG") else MachineKind.STEAM_TURBINE
    sequencer = TurbineStartupSequencer(machine_kind)
    if tripped:
        sequencer.state = StartupState.TRIPPED
    bay.startup_sequencer = sequencer
    return sequencer


def _ledger(orch):
    return [(t["symbol"], t["side"], round(t["pnl"], 6), round(t["net_pnl"], 6)) for t in orch.completed_trades]


def open_pos(orch, symbol="INFY", side="BUY", price=100.0, quantity=10):
    fill = orch.broker.place_order(symbol, side, quantity, "MARKET", price,
                                   orch.safety_contract.as_dict(), orch.registry)
    assert fill["passed"]
    entry = fill["filled_price"]
    stop, target = (entry - 5, entry + 10) if side == "BUY" else (entry + 5, entry - 10)
    trade = dict(side=side, entry_price=entry, quantity=quantity, stop_price=stop, target_price=target,
                minimum_hold_bars=2, maximum_hold_bars=60, entry_timestamp="2024-01-01 10:00",
                entry_atr=1.0, planned_entry_price=entry, planned_stop_price=stop, planned_target_price=target,
                trade_id=f"trade-test-{orch._trade_sequence}")
    orch._trade_sequence += 1
    orch.open_trades[symbol] = trade
    orch._exit_controller_states[symbol] = orch.exit_controller.open_position(side, entry, stop, target, 60)
    return trade


# ===================================================================== BLOCKER 1: protection -> ECS

def test_real_sel_unit_trip_makes_ecs_bay_unavailable():
    """Requirement 1: a real SEL/unit trip makes the corresponding ECS bay unavailable."""
    plant = real_plant()
    tripped_bay = BAY_IDS[0]
    plant.trip_unit_breaker(bay_id=tripped_bay, reason="SEL300G_TRIP:ANSI87G:test",
                            source="SEL300G_UNIT_PROTECTION", lockout=False)
    snapshot = build_plant_protection_snapshot(plant)
    assert snapshot.connected is True and snapshot.source == CONNECTED_SOURCE
    assert snapshot.bay(tripped_bay).available is False
    assert snapshot.bay(tripped_bay).reason == "UNIT_TRIPPED_OFFLINE"

    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, all_available_fallback())
    assert master_block is False
    ecs = ECSPlantSupervisor(cfg())
    out = ecs.evaluate(synchronized_grid(), bay_status, gross_exposure_fraction=0.0,
                       gross_exposure_limit_fraction=0.5, plant_protection_tripped=master_block,
                       bay_availability_source=source)
    mask = dict(out.bay_availability_mask)
    assert mask[tripped_bay] is False
    assert all(mask[b] for b in BAY_IDS if b != tripped_bay)
    assert out.plant_protection_connected is True and out.bay_availability_source == CONNECTED_SOURCE


def test_ansi86_lockout_makes_ecs_bay_unavailable_with_lockout_reason():
    """Requirement 2 (lockout half): an ANSI-86 lockout is a distinct, correctly-labelled block."""
    plant = real_plant()
    bay_id = BAY_IDS[1]
    plant.trip_unit_breaker(bay_id=bay_id, reason="SEVERE_TRIP", source="TEST", lockout=True)
    snapshot = build_plant_protection_snapshot(plant)
    assert snapshot.bay(bay_id).lockout_86 is True
    assert snapshot.bay(bay_id).reason == "ANSI_86_LOCKOUT"
    assert snapshot.bay(bay_id).available is False


def test_real_mechanical_trip_makes_ecs_bay_unavailable():
    """Requirement 2 (mechanical half): a real mechanical trip/lockout makes the bay unavailable."""
    plant = real_plant()
    bay_id = BAY_IDS[2]
    install_stub_sequencer(plant.bays[bay_id], tripped=True)
    snapshot = build_plant_protection_snapshot(plant)
    assert snapshot.bay(bay_id).mechanical_tripped is True
    assert snapshot.bay(bay_id).available is False
    assert snapshot.bay(bay_id).reason == "MECHANICAL_PROTECTION_TRIP"

    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, all_available_fallback())
    assert bay_status[bay_id].available is False
    ecs = ECSPlantSupervisor(cfg())
    out = ecs.evaluate(synchronized_grid(), bay_status, gross_exposure_fraction=0.0,
                       gross_exposure_limit_fraction=0.5, plant_protection_tripped=master_block,
                       bay_availability_source=source)
    assert dict(out.bay_availability_mask)[bay_id] is False


def test_master_micom_block_cannot_be_overridden_by_ecs_or_dispatch():
    """Requirement 3: a MiCOM master/plant block cannot be overridden by ECS/dispatch, even
    though every individual bay is otherwise healthy and the grid is SYNCHRONIZED."""
    plant = real_plant()
    trip = plant.grid_relay.evaluate_grid_intertie(
        nifty_15m_return=0.0, nifty_vol_z=0.0, fleet_equity_drawdown_pct=0.05)
    assert trip.tripped and plant.grid_relay.master_breaker_open

    snapshot = build_plant_protection_snapshot(plant)
    assert snapshot.master_block is True
    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, all_available_fallback())
    assert master_block is True
    assert all(bay_status[b].available for b in BAY_IDS)          # every individual bay healthy

    ecs = ECSPlantSupervisor(cfg())
    out = ecs.evaluate(synchronized_grid(), bay_status, gross_exposure_fraction=0.0,
                       gross_exposure_limit_fraction=0.5, plant_protection_tripped=master_block,
                       bay_availability_source=source)
    assert out.plant_demand_reference_pu == 0.0
    assert "PLANT_PROTECTION_TRIPPED" in out.reasons
    dispatch = SectorDispatchController(DynamicBayLoadDispatcher()).dispatch(out)
    assert dispatch.allocated_pu == 0.0
    assert all(value == 0.0 for _, value in dispatch.references_pu)


def test_healthy_real_plant_state_cannot_override_an_existing_symbol_trip_fallback():
    """Requirement 4: the disconnected fallback path never sees (and so can never override) real
    plant state -- an existing symbol-trip fallback signal passes through untouched."""
    snapshot = build_plant_protection_snapshot(None)
    fallback = all_available_fallback()
    tripped_bay = BAY_IDS[3]
    fallback[tripped_bay] = BayStatus(available=False, tripped=True, reason="ALL_SYMBOLS_TRIPPED")

    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, fallback)
    assert bay_status == fallback
    assert bay_status[tripped_bay].available is False
    assert master_block is None
    assert source == FALLBACK_SOURCE


def test_false_legacy_trip_flag_cannot_clear_a_real_master_block():
    """Requirement 5: once a real plant is connected, a "healthy" legacy fallback signal is
    ignored entirely -- it can never clear a real master block."""
    plant = real_plant()
    plant.grid_relay.evaluate_grid_intertie(
        nifty_15m_return=0.0, nifty_vol_z=0.0, fleet_equity_drawdown_pct=0.05)
    snapshot = build_plant_protection_snapshot(plant)

    false_healthy_legacy_fallback = all_available_fallback()      # claims everything is fine
    bay_status, master_block, source = derive_bay_status_and_master_block(
        snapshot, false_healthy_legacy_fallback)
    assert master_block is True                                    # real block still enforced
    assert source == CONNECTED_SOURCE


def test_synchronized_grid_cannot_make_a_native_startup_bay_dispatch_ready():
    """Requirement 6: a SYNCHRONIZED market grid raises the plant demand target, but it cannot
    make a bay whose native R5 startup has not reached DISPATCH_READY dispatch-ready."""
    plant = real_plant()
    bay_id = BAY_IDS[4]
    install_stub_sequencer(plant.bays[bay_id])                     # default OFF -> not dispatch-ready
    snapshot = build_plant_protection_snapshot(plant)
    assert snapshot.bay(bay_id).dispatch_ready is False

    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, all_available_fallback())
    ecs = ECSPlantSupervisor(cfg())
    out = ecs.evaluate(synchronized_grid(), bay_status, gross_exposure_fraction=0.0,
                       gross_exposure_limit_fraction=0.9, plant_protection_tripped=master_block,
                       bay_availability_source=source)
    assert out.grid_state == "SYNCHRONIZED" and out.plant_demand_reference_pu > 0.0
    mask = dict(out.bay_availability_mask)
    assert mask[bay_id] is False
    dispatch = SectorDispatchController(DynamicBayLoadDispatcher()).dispatch(out)
    assert dict(dispatch.references_pu)[bay_id] == 0.0


def test_unavailable_bay_gets_zero_dispatch_and_others_redistribute_within_ceilings():
    """Requirements 7 & 8: an unavailable bay gets exactly zero dispatch; the remaining bays
    redistribute the demand, each staying within the dispatcher's own max ceiling."""
    plant = real_plant()
    tripped_bay = BAY_IDS[0]
    plant.trip_unit_breaker(bay_id=tripped_bay, reason="TEST", source="TEST", lockout=True)
    snapshot = build_plant_protection_snapshot(plant)
    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, all_available_fallback())
    ecs = ECSPlantSupervisor(cfg())
    out = ecs.evaluate(synchronized_grid(), bay_status, gross_exposure_fraction=0.0,
                       gross_exposure_limit_fraction=0.9, plant_protection_tripped=master_block,
                       bay_availability_source=source)
    merit = DynamicBayLoadDispatcher()
    dispatch = SectorDispatchController(merit).dispatch(out)
    refs = dict(dispatch.references_pu)
    assert refs[tripped_bay] == 0.0
    other_bays = [b for b in BAY_IDS if b != tripped_bay]
    assert all(refs[b] <= merit.max_ceiling + 1e-9 for b in other_bays)
    assert dispatch.feasible
    assert abs(sum(refs.values()) - out.plant_demand_reference_pu) < 1e-9


def test_explicit_fallback_is_recorded_when_real_plant_protection_is_not_connected():
    """Requirement 9: with no real plant supplied, the ECS output explicitly records the
    fallback -- never a silent/implicit assumption of connection or health."""
    snapshot = build_plant_protection_snapshot(None)
    assert snapshot.connected is False and snapshot.source == FALLBACK_SOURCE == "SYMBOL_TRIPS_ONLY"
    bay_status, master_block, source = derive_bay_status_and_master_block(snapshot, all_available_fallback())
    ecs = ECSPlantSupervisor(cfg())
    out = ecs.evaluate(synchronized_grid(), bay_status, gross_exposure_fraction=0.0,
                       gross_exposure_limit_fraction=0.5, plant_protection_tripped=master_block,
                       bay_availability_source=source)
    assert out.plant_protection_connected is False
    assert out.bay_availability_source == "SYMBOL_TRIPS_ONLY"
    assert "PLANT_PROTECTION_NOT_CONNECTED" in out.reasons


# ================================================================ BLOCKER 2: close -> dispatcher

def test_authoritative_close_feeds_dispatcher_exactly_once():
    """Requirement 10."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    before = len(dispatcher.trade_history_r[bay_id])
    trade = open_pos(orch)
    orch._execute_exit("INFY", "2024-01-01 10:02", trade, 110.0, "target")
    assert len(dispatcher.trade_history_r[bay_id]) == before + 1


def test_close_feedback_uses_the_correct_bay_for_symbol_mapping():
    """Requirement 11."""
    orch = Engine(["TCS"])
    assert bay_for_symbol("TCS") == "GTG2_TECH_TELECOM"
    trade = open_pos(orch, symbol="TCS")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    before = {bay: len(h) for bay, h in dispatcher.trade_history_r.items()}
    orch._execute_exit("TCS", "2024-01-01 10:02", trade, 110.0, "target")
    after = {bay: len(h) for bay, h in dispatcher.trade_history_r.items()}
    changed = [bay for bay in BAY_IDS if after[bay] != before[bay]]
    assert changed == ["GTG2_TECH_TELECOM"]


def test_exact_authoritative_realized_r_value_is_passed():
    """Requirement 12: reuses the exact existing realized-R equation (signed move / initial risk)."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    trade = open_pos(orch, price=100.0, quantity=10)                # stop=95 -> initial risk = 5
    orch._execute_exit("INFY", "2024-01-01 10:02", trade, 110.0, "target")
    fill = orch.broker.fills[-1]
    expected_r = (fill["price"] - trade["entry_price"]) / abs(trade["entry_price"] - trade["stop_price"])
    assert dispatcher.trade_history_r[bay_id][-1] == pytest.approx(expected_r)
    assert orch._bay_governors[bay_id].history_r[-1] == pytest.approx(expected_r)


def test_duplicate_close_feedback_call_is_a_noop():
    """Requirement 13 (receipt mechanism directly)."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    trade = open_pos(orch)
    orch._register_realized_r_close_feedback(symbol="INFY", trade=trade, bay_id=bay_id, realized_r=0.5)
    orch._register_realized_r_close_feedback(symbol="INFY", trade=trade, bay_id=bay_id, realized_r=0.5)
    assert len(dispatcher.trade_history_r[bay_id]) == 1
    assert len(orch._bay_governors[bay_id].history_r) == 1


def test_retried_execute_exit_is_rejected_by_reconciliation_and_never_feeds_twice():
    """Requirement 13 (end-to-end): a replay retry of the same close is rejected before it can
    reach the dispatcher a second time, because the position is already closed."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    trade = open_pos(orch)
    orch._execute_exit("INFY", "2024-01-01 10:02", trade, 110.0, "target")
    assert len(dispatcher.trade_history_r[bay_id]) == 1
    with pytest.raises(PositionReconciliationError):
        orch._execute_exit("INFY", "2024-01-01 10:03", trade, 111.0, "target")
    assert len(dispatcher.trade_history_r[bay_id]) == 1


def test_fault_after_dispatcher_mutation_leaves_unresolved_receipt_and_blocks_retry():
    """Requirement 14: an unexpected exception after the dispatcher mutation propagates, and
    leaves the receipt PENDING (not DONE) so a retry cannot feed the same close again."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    trade = open_pos(orch)
    governor = orch._bay_governors[bay_id]

    def boom(*args, **kwargs):
        raise RuntimeError("simulated unexpected failure after dispatcher mutation")

    governor.register_trade = boom                                 # fails AFTER dispatcher succeeds
    with pytest.raises(RuntimeError, match="simulated unexpected failure"):
        orch._register_realized_r_close_feedback(symbol="INFY", trade=trade, bay_id=bay_id, realized_r=0.4)

    assert len(dispatcher.trade_history_r[bay_id]) == 1             # dispatcher mutation is retained
    key = orch._close_feedback_key("INFY", trade)
    assert orch._close_feedback_receipts[key] == "PENDING"          # left unresolved, never DONE

    del governor.register_trade                                    # restore the real bound method
    orch._register_realized_r_close_feedback(symbol="INFY", trade=trade, bay_id=bay_id, realized_r=0.4)
    assert len(dispatcher.trade_history_r[bay_id]) == 1             # retry still did not feed again
    assert len(governor.history_r) == 0                             # governor never actually fed
    assert orch._close_feedback_receipts[key] == "PENDING"          # stays unresolved (no restart recovery)


def test_local_governor_and_plant_dispatch_feedback_are_separate_and_each_fires_once():
    """Requirement 15."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    dispatcher = orch.plant_control.dispatch_controller.merit_source
    governor = orch._bay_governors[bay_id]
    assert governor is not dispatcher
    trade = open_pos(orch)
    orch._execute_exit("INFY", "2024-01-01 10:02", trade, 110.0, "target")
    assert len(governor.history_r) == 1
    assert len(dispatcher.trade_history_r[bay_id]) == 1


def test_dispatcher_merit_weights_change_causally_via_real_closes_without_rebuilding_controller():
    """Requirement 16."""
    orch = Engine(["INFY"])
    bay_id = bay_for_symbol("INFY")
    sector_controller = orch.plant_control.dispatch_controller
    before_weight = sector_controller.merit_source.weights[bay_id]
    for i in range(4):
        trade = open_pos(orch, price=100.0 + i)
        orch._execute_exit("INFY", f"2024-01-01 10:0{i + 2}", trade, (100.0 + i) - 6.0, "stop")
    after_weight = sector_controller.merit_source.weights[bay_id]
    assert after_weight != before_weight
    assert orch.plant_control.dispatch_controller is sector_controller     # never rebuilt


def test_attaching_a_real_plant_with_a_tripped_bay_still_leaves_the_shadow_ledger_unchanged():
    """Requirement 17: SHADOW ledger and net P&L remain identical whether or not real plant
    protection (with a tripped bay) is attached -- plant-control output is never consumed."""
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest

    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    bars = {"INFY": loader._load_symbol_csv("INFY").tail(800).reset_index(drop=True)}

    baseline = Engine(["INFY"], CanonicalParameterRegistry(), starting_equity=1_000_000.0)
    baseline_report = baseline.run(bars, warmup=60)

    plant = real_plant()
    plant.trip_unit_breaker(bay_id=BAY_IDS[1], reason="TEST", source="TEST", lockout=True)
    with_plant = Engine(["INFY"], CanonicalParameterRegistry(), starting_equity=1_000_000.0,
                        real_plant_dcs=plant)
    with_plant_report = with_plant.run(bars, warmup=60)

    assert baseline_report["completed_trades"] > 0
    assert _ledger(with_plant) == _ledger(baseline)
    assert with_plant_report["net_pnl"] == baseline_report["net_pnl"]
    shadow = with_plant_report["plant_control_shadow"]
    assert shadow["plant_protection_connected"] is True
    assert shadow["bay_availability_source"] == CONNECTED_SOURCE


def test_no_paper_apply_actuation_is_enabled():
    """Requirement 18."""
    orch = Engine(["INFY"])
    assert orch.plant_control.mode.value == "SHADOW"
    with pytest.raises(PlantControlError):
        Engine(["INFY"], plant_control_mode="PAPER_APPLY")
    trade = open_pos(orch)
    orch._execute_exit("INFY", "2024-01-01 10:02", trade, 110.0, "target")
    max_gross_fraction = float(orch.safety_contract.values["max_gross_exposure_fraction"])
    orch._plant_control_shadow_step("2024-01-01 10:02", max_gross_fraction)
    assert orch.plant_control_snapshots
    for snap in orch.plant_control_snapshots:
        assert snap.applied is False and snap.mode == "SHADOW"
        assert all(ref.applied is False and ref.authority == "INFORMATION_ONLY" for ref in snap.governor_references)


def test_no_live_broker_actuation_is_enabled():
    """Requirement 19."""
    orch = Engine(["INFY"])
    assert orch.broker.environment == "paper"
    tree = ast.parse((ROOT / "revision2_external/orchestrator.py").read_text())
    names = ({n.id for n in ast.walk(tree) if isinstance(n, ast.Name)}
             | {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)})
    assert "KiteConnect" not in names and "KiteBrokerAdapter" not in names
