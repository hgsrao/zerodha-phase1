"""R5-C02.1 fleet actuation integration regression.

FIXTURE CLASSIFICATION: CONSTRUCTED_OVEREXPOSURE_FIXTURE.
This is NOT historical evidence, NOT calibration evidence and NOT profitability evidence. Its only purpose is a
deterministic functional proof that, when the EXISTING fleet policy is made binding, the real orchestrator path changes the
quantity returned to the entry/order path.

Real production objects exercised (nothing below is replaced by a mock that returns a number):
  Revision2ExternalEngineOrchestrator (normal constructor, PAPER_APPLY)
    -> _plant_control_shadow_step -> PlantControlChain.evaluate -> FleetLoadingController.update   (real fleet decision)
    -> ECS demand replaced by fleet output -> SectorDispatchController -> governor references
    -> _paper_plant_entry_limit -> BayTurbineClosedLoopGovernor.cap_dispatch_entry                 (real executor cap)
The quantity returned by _paper_plant_entry_limit is exactly the value the entry path assigns to `quantity` before order construction.

Constructed state (identical in control and treatment): the engine's own ramp-limited ECS demand (0.1), existing exposure in a bay
other than the candidate's, and a same-timestamp exit.  The plant is sampled once per timestamp, so exposure measured at the start
of the timestamp (0.06 pu) can exceed the reference (0.05 pu) and make the fleet limit binding, while a position exit processed
later in the same timestamp lowers the executor's live gross (0.045 pu) so the control case still has positive admissible
quantity.  The exit is modelled by removing the position from ``open_trades`` (what the exit path ultimately does); the broker exit
leg itself is NOT exercised.  Stopped short of: order construction/submission, later gross/sector/safety gates, and a full run().
The fleet policy is the unmodified production class with its default gains; no gain, threshold or parameter is changed.
No side/profitability claim is made; the candidate side plays no role in the fleet decision.
"""
import json
import math

import pandas as pd
import pytest

from revision5.fleet_loading_controller import FleetLoadingController, FleetLoadingPolicy, new_risk_headroom_pu
from tests.test_fleet_loading_integration import build

CANDIDATE = "TITAN"            # bay CSTG2_CONSUMER_AUTO
CANDIDATE_PRICE = 100.0
REQUESTED_QUANTITY = 100       # unconstrained request: 10,000 notional
# Existing exposure sits in GTG2_TECH_TELECOM, never in the candidate's bay.
STAYING = ("INFY", 450, 100.0)       # 45,000 notional, stays open
EXITING = ("HCLTECH", 150, 100.0)    # 15,000 notional, exits inside the same timestamp


def run_scenario(tmp_path, policy, *, exit_before_entry=True):
    engine, store, bars, _ = build(tmp_path, policy)
    ts = bars.timestamp.iloc[100]
    for symbol, quantity, price in (STAYING, EXITING):
        engine.open_trades[symbol] = dict(quantity=quantity, entry_price=price, side="BUY")
    limit = float(engine.safety_contract.values["max_gross_exposure_fraction"])
    engine._plant_control_shadow_step(ts, limit)            # plant/fleet sampled once for this timestamp
    snapshot = engine._paper_plant_snapshot
    equity = engine._equity()
    sampled_gross = engine._gross_exposure_notional()
    if exit_before_entry:
        engine.open_trades.pop(EXITING[0])                   # same-timestamp exit before the entry is sized
    live_gross = engine._gross_exposure_notional()
    quantity = engine._paper_plant_entry_limit(CANDIDATE, REQUESTED_QUANTITY, CANDIDATE_PRICE, ts)
    return dict(engine=engine, store=store, ts=ts, snapshot=snapshot, equity=equity, limit=limit,
                sampled_gross=sampled_gross, live_gross=live_gross, quantity=quantity)


def single_subtraction_quantity(case, *, subtractions=1):
    """Independent re-derivation of the executor formula (observation only; compared with the real method's return)."""
    snapshot, engine = case["snapshot"], case["engine"]
    reference = next(r for r in snapshot.governor_references if r.bay_id == "CSTG2_CONSUMER_AUTO")
    budget = case["equity"] * case["limit"]
    bay_term = budget * reference.dispatch_reference_pu                     # no existing exposure in the candidate bay
    plant_term = budget * reference.plant_demand_reference_pu - subtractions * case["live_gross"]
    remaining = max(0.0, min(bay_term, plant_term))
    return min(REQUESTED_QUANTITY, int(remaining / CANDIDATE_PRICE)), bay_term, plant_term


@pytest.fixture
def cases(tmp_path):
    control = run_scenario(tmp_path / "control", FleetLoadingPolicy())                    # fleet disabled
    treatment = run_scenario(tmp_path / "treatment", FleetLoadingPolicy(enabled=True))    # unmodified production policy
    try:
        yield control, treatment
    finally:
        control["store"].close()
        treatment["store"].close()


def test_constructed_overexposure_binding_fleet_changes_entry_quantity(cases):
    control, treatment = cases
    c_snap, t_snap = control["snapshot"], treatment["snapshot"]

    # A. both cases reach the fleet decision point of the real plant chain for the same timestamp
    assert control["engine"].plant_control_evaluations == treatment["engine"].plant_control_evaluations == 1
    assert c_snap is not None and t_snap is not None and control["ts"] == treatment["ts"]
    assert control["engine"].plant_control.fleet_loading.policy.enabled is False
    assert treatment["engine"].plant_control.fleet_loading.policy.enabled is True
    assert json.loads(treatment["engine"].plant_control.fleet_loading.export_state())["updates"] == 1
    assert c_snap.fleet_loading is None and t_snap.fleet_loading is not None

    # identical constructed opportunity and non-fleet state in both cases
    assert control["equity"] == treatment["equity"] and control["limit"] == treatment["limit"]
    assert control["sampled_gross"] == treatment["sampled_gross"] == 60000.0
    assert control["live_gross"] == treatment["live_gross"] == 45000.0
    assert c_snap.ecs.plant_demand_reference_pu == pytest.approx(0.1)           # ECS output before any fleet action
    assert c_snap.ecs.bay_availability_mask == t_snap.ecs.bay_availability_mask
    assert not control["engine"].plant_control_observer_failures and not treatment["engine"].plant_control_observer_failures

    # B. fleet disabled: positive admissible quantity, and the plant term (not a bay/safety cap) is the binder
    expected_control, c_bay_term, c_plant_term = single_subtraction_quantity(control)
    assert control["quantity"] == expected_control == 50
    assert 0 < c_plant_term < c_bay_term, "control must be limited by the shared plant headroom, not by a different cap"
    assert control["quantity"] < REQUESTED_QUANTITY

    # C. fleet enabled: the real controller is binding (output strictly below the capacity it was handed)
    fleet = t_snap.fleet_loading
    reference_pu = c_snap.ecs.plant_demand_reference_pu * treatment["limit"]
    assert fleet.error_pu < 0 and sampled_exposure_pu(treatment) > reference_pu
    assert fleet.allowed_capacity_pu < reference_pu and fleet.raw_output_pu < reference_pu
    assert not fleet.protection_tripped
    assert t_snap.ecs.authority == "FLEET_LOADING_WITHIN_ECS"
    assert t_snap.ecs.plant_demand_reference_pu < c_snap.ecs.plant_demand_reference_pu

    # D. downstream actuation changed: nonzero derating of the quantity handed to the order path
    expected_treatment, t_bay_term, t_plant_term = single_subtraction_quantity(treatment)
    assert treatment["quantity"] == expected_treatment == 24
    assert 0 < treatment["quantity"] < control["quantity"]
    assert 0 < t_plant_term < t_bay_term, "treatment difference must come from the fleet-lowered plant term"
    assert t_plant_term < c_plant_term

    # nothing but the fleet-owned demand differs between the two executor evaluations
    assert t_bay_term <= c_bay_term  # bay references are scaled by the fleet-lowered demand, same weights
    assert treatment["live_gross"] == control["live_gross"]

    print("C02_1_RECORD " + json.dumps(dict(
        equity=control["equity"], limit=control["limit"], sampled_gross=control["sampled_gross"],
        live_gross=control["live_gross"], ecs_reference_pu=reference_pu,
        control_total_capacity_pu=reference_pu, control_headroom_pu=new_risk_headroom_pu(reference_pu, control["live_gross"] / control["equity"]),
        treatment_total_capacity_pu=fleet.allowed_capacity_pu,
        treatment_headroom_pu=new_risk_headroom_pu(fleet.allowed_capacity_pu, treatment["live_gross"] / treatment["equity"]),
        requested=REQUESTED_QUANTITY, control_quantity=control["quantity"], treatment_quantity=treatment["quantity"],
        control_terms=[c_bay_term, c_plant_term], treatment_terms=[t_bay_term, t_plant_term],
        fleet_error_pu=fleet.error_pu, fleet_raw_pu=fleet.raw_output_pu), sort_keys=True))


def sampled_exposure_pu(case):
    return case["sampled_gross"] / case["equity"]


def test_actual_exposure_is_subtracted_exactly_once(cases):
    control, treatment = cases
    for case in (control, treatment):
        capacity_pu = (case["snapshot"].fleet_loading.allowed_capacity_pu if case["snapshot"].fleet_loading is not None
                       else case["snapshot"].ecs.plant_demand_reference_pu * case["limit"])
        actual_pu = case["live_gross"] / case["equity"]
        headroom_pu = new_risk_headroom_pu(capacity_pu, actual_pu)               # TOTAL capacity minus actual, once
        once, _, _ = single_subtraction_quantity(case, subtractions=1)
        twice, _, _ = single_subtraction_quantity(case, subtractions=2)
        assert case["quantity"] == once
        assert case["quantity"] == int(round(headroom_pu * case["equity"], 6) / CANDIDATE_PRICE)
        assert case["quantity"] != twice, "a second subtraction of actual exposure would give a different quantity"
        assert twice == 0   # double subtraction would exhaust headroom entirely in this constructed state
    # the fleet output itself is total capacity, not headroom: it is not reduced by actual exposure
    fleet = treatment["snapshot"].fleet_loading
    assert fleet.allowed_capacity_pu == pytest.approx(0.0474)
    assert fleet.allowed_capacity_pu > treatment["live_gross"] / treatment["equity"]


def test_disabled_policy_is_compatible_and_does_not_derate(cases):
    control, _ = cases
    assert control["snapshot"].fleet_loading is None
    assert control["snapshot"].ecs.authority != "FLEET_LOADING_WITHIN_ECS"
    unfleeted, _, _ = single_subtraction_quantity(control)
    assert control["quantity"] == unfleeted > 0   # existing executor formula only; no fleet derating
    # the disabled controller hands capacity back unchanged (production behavior of the unmodified class)
    probe = FleetLoadingController(FleetLoadingPolicy())
    assert probe.update(0.05, 0.06, 1.0, 0.05).allowed_capacity_pu == 0.05


def test_counterfactual_ignoring_fleet_output_removes_the_effect(tmp_path, monkeypatch):
    """Test-local observation: if the fleet output were ignored (update returns the capacity it was handed), the key
    treatment assertion (quantity below control) fails.  Nothing in the working tree is edited; monkeypatch is reverted."""
    control = run_scenario(tmp_path / "control", FleetLoadingPolicy())
    real_update = FleetLoadingController.update

    def ignore_output(self, reference_pu, actual_exposure_pu, dt_seconds, capacity_pu, protection_tripped=False):
        telemetry = real_update(self, reference_pu, actual_exposure_pu, dt_seconds, capacity_pu, protection_tripped)
        from dataclasses import replace
        return replace(telemetry, allowed_capacity_pu=capacity_pu)

    monkeypatch.setattr(FleetLoadingController, "update", ignore_output)
    neutralised = run_scenario(tmp_path / "neutralised", FleetLoadingPolicy(enabled=True))
    try:
        assert neutralised["quantity"] == control["quantity"] == 50
        assert not neutralised["quantity"] < control["quantity"]     # the treatment assertion would fail here
    finally:
        control["store"].close()
        neutralised["store"].close()


def test_protection_trip_still_zeroes_fleet_capacity_on_real_chain(tmp_path):
    case = run_scenario(tmp_path, FleetLoadingPolicy(enabled=True))
    try:
        engine = case["engine"]
        statuses = engine._plant_bay_status()
        tripped = engine.plant_control.evaluate(
            case["ts"] + pd.Timedelta(minutes=1), statuses,
            gross_exposure_fraction=case["live_gross"] / case["equity"],
            gross_exposure_limit_fraction=case["limit"], plant_protection_tripped=True)
        assert tripped.fleet_loading.protection_tripped is True
        assert tripped.fleet_loading.allowed_capacity_pu == 0.0
        assert tripped.dispatch.allocated_pu == 0
        assert math.isfinite(tripped.ecs.plant_demand_reference_pu)
    finally:
        case["store"].close()
