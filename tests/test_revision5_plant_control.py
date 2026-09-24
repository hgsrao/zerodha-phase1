"""Plant-control chain: grid synchronizer -> ECS supervisor -> sector dispatch -> governor refs.

SHADOW-first: everything is computed and recorded, nothing is applied.
"""

from __future__ import annotations

import ast
import random
import re
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from gates_framework import EntryDecisionEngine, SafetyGateConfig, SystemState
from revision2.contracts import EffectiveConfig
from revision2_external.closed_loop_control import ClosedLoopSupervisor
from revision2_external.grid_context import SealedGridContextProvider
from revision5.ccpp_unified_plant import DynamicBayLoadDispatcher
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor
from revision5 import plant_control
from revision5.plant_control import (
    BayStatus, ECSPlantSupervisor, GovernorDispatchReference, PlantControlChain, PlantControlError,
    PlantControlMode, PlantGridSynchronizer, SectorDispatchController, build_governor_references,
)
from revision5.topology import BAY_IDS

REG = CanonicalParameterRegistry()
ROOT = Path(__file__).resolve().parent.parent
GRID_NAMES = ["grid_vix_operating_min", "grid_vix_operating_max", "grid_vix_derate_start",
              "grid_vix_slope_bars", "grid_vix_slope_derate_fraction", "grid_nifty_ema_period",
              "grid_nifty_deviation_derate_fraction", "grid_max_staleness_seconds", "grid_min_aligned_bars",
              "ecs_derate_demand_pu", "ecs_demand_restore_step_pu"]


def cfg(**overrides):
    values = {n: s.default for n, s in REG.params.items()}
    values.update(overrides)
    return EffectiveConfig.build(values, registry_hash=REG.FROZEN_IDENTITY_SHA256)


def grid_frames(n=120, vix=15.0, vix_last=None, nifty_drift=0.0, start="2024-03-01 03:45:00+00:00"):
    idx = pd.date_range(start, periods=n, freq="15min", tz="UTC")
    nifty = pd.DataFrame({"timestamp": idx, "close": 21000.0 * (1 + nifty_drift * np.arange(n))})
    vixv = np.full(n, float(vix))
    if vix_last is not None:
        vixv[-1] = vix_last
    return nifty, pd.DataFrame({"timestamp": idx, "close": vixv})


def decision_after(nifty, seconds=60):
    return nifty["timestamp"].iloc[-1] + pd.Timedelta(seconds=seconds)


def all_available():
    return {bay: BayStatus() for bay in BAY_IDS}


def sync(config=None, **frame_kwargs):
    nifty, vix = grid_frames(**frame_kwargs)
    return PlantGridSynchronizer(config or cfg(), SealedGridContextProvider(nifty, vix)), nifty


def chain(config=None, **frame_kwargs):
    nifty, vix = grid_frames(**frame_kwargs)
    return PlantControlChain(config or cfg(), SealedGridContextProvider(nifty, vix), DynamicBayLoadDispatcher()), nifty


def settle(c, nifty, n=15, **kw):
    """The ECS always ramps up from zero; evaluate until the restoration has settled."""
    snap = None
    for _ in range(n):
        snap = c.evaluate(decision_after(nifty), all_available(), gross_exposure_fraction=0.0,
                          gross_exposure_limit_fraction=0.5, **kw)
    return snap


# ---------------------------------------------------------------- grid: causality

def test_synchronized_grid_produces_full_plant_demand():
    c, nifty = chain()
    snap = settle(c, nifty)
    assert snap.grid.state == "SYNCHRONIZED" and snap.grid.available
    assert snap.ecs.plant_demand_reference_pu == 1.0 and snap.ecs.operating_mode == "NORMAL"
    assert snap.dispatch.feasible and sum(r for _, r in snap.dispatch.references_pu) == pytest.approx(1.0)


def test_unsynchronized_grid_produces_no_new_demand():
    c, nifty = chain(vix_last=45.0)                       # VIX outside the operating band
    snap = c.evaluate(decision_after(nifty), all_available(), gross_exposure_fraction=0.0,
                      gross_exposure_limit_fraction=0.5)
    assert snap.grid.state == "UNSYNCHRONIZED"
    assert snap.ecs.plant_demand_reference_pu == 0.0 and snap.ecs.operating_mode == "HOLD"
    assert all(r == 0.0 for _, r in snap.dispatch.references_pu)


def test_derated_grid_reduces_demand_to_the_registry_value():
    c, nifty = chain(vix_last=26.0)                       # inside the band, above derate start
    snap = settle(c, nifty)
    assert snap.grid.state == "DERATED"
    assert snap.ecs.plant_demand_reference_pu == REG.params["ecs_derate_demand_pu"].default
    assert snap.ecs.operating_mode == "DERATED"


def test_future_grid_bars_are_never_used():
    s, nifty = sync()
    base = s.evaluate(decision_after(nifty))
    nifty2, vix2 = grid_frames(n=130, vix=15.0)                 # 10 extra FUTURE bars, wild values
    nifty2.loc[120:, "close"] = 1.0
    vix2.loc[120:, "close"] = 99.0
    s2 = PlantGridSynchronizer(cfg(), SealedGridContextProvider(nifty2, vix2))
    same = s2.evaluate(decision_after(nifty))
    assert (base.state, base.nifty_close, base.vix_close) == (same.state, same.nifty_close, same.vix_close)


def test_bar_stamped_exactly_at_the_decision_time_is_excluded():
    s, nifty = sync()
    at_last = nifty["timestamp"].iloc[-1]
    state = s.evaluate(at_last)                                  # last bar is NOT strictly earlier
    assert state.source_timestamp == nifty["timestamp"].iloc[-2].isoformat()


def test_stale_grid_fails_closed():
    c, nifty = chain()
    snap = c.evaluate(nifty["timestamp"].iloc[-1] + pd.Timedelta(hours=3), all_available(),
                      gross_exposure_fraction=0.0, gross_exposure_limit_fraction=0.5)
    assert snap.ecs.plant_protection_connected is False
    assert snap.grid.state == "ISLANDED_SAFE" and snap.grid.reason == "GRID_CONTEXT_STALE"
    assert not snap.grid.available and snap.ecs.plant_demand_reference_pu == 0.0


@pytest.mark.parametrize("n, reason", [(0, "GRID_CONTEXT_NOT_YET_AVAILABLE"), (30, "GRID_CONTEXT_WARMUP_INSUFFICIENT")])
def test_missing_or_insufficient_grid_fails_closed_without_a_neutral_state(n, reason):
    nifty, vix = grid_frames(n=max(n, 1))
    if n == 0:
        nifty, vix = nifty.iloc[0:0], vix.iloc[0:0]
    s = PlantGridSynchronizer(cfg(), SealedGridContextProvider(nifty, vix))
    state = s.evaluate(pd.Timestamp("2024-03-01 09:00:00+00:00") if n == 0 else decision_after(nifty))
    assert state.state == "ISLANDED_SAFE" and state.reason == reason
    assert state.nifty_close is None and state.vix_close is None       # nothing synthesized


def test_misaligned_grid_fails_closed():
    nifty, vix = grid_frames()
    vix = vix.copy()
    vix["timestamp"] = vix["timestamp"] - pd.Timedelta(minutes=15)      # VIX lags Nifty by one bar
    state = PlantGridSynchronizer(cfg(), SealedGridContextProvider(nifty, vix)).evaluate(decision_after(nifty))
    assert state.state == "ISLANDED_SAFE" and state.reason == "GRID_CONTEXT_TIMESTAMP_MISMATCH"


def test_reason_codes_match_the_existing_sealed_grid_context_provider():
    """The plant synchronizer must not diverge from the existing causal availability rules."""
    for kwargs in ({}, {"n": 30}):
        nifty, vix = grid_frames(**kwargs)
        provider = SealedGridContextProvider(nifty, vix)
        plant = PlantGridSynchronizer(cfg(), provider).evaluate(decision_after(nifty))
        symbol_bars = nifty.rename(columns={"close": "close"})
        legacy = provider.observe("INFY", symbol_bars, decision_after(nifty), 1)
        if not legacy.available:
            assert plant.reason == legacy.reason
    stale = decision_after(nifty, seconds=3 * 3600)
    provider = SealedGridContextProvider(nifty, vix)
    assert PlantGridSynchronizer(cfg(), provider).evaluate(stale).reason == \
        provider.observe("INFY", nifty, stale, 1).reason


def test_naive_decision_time_is_read_as_exchange_local():
    s, nifty = sync()
    naive_local = (decision_after(nifty).tz_convert("Asia/Kolkata")).tz_localize(None)
    aware = s.evaluate(decision_after(nifty))
    assert s.evaluate(naive_local).source_timestamp == aware.source_timestamp


def test_grid_measurements_are_reported_plant_wide():
    s, nifty = sync(vix_last=15.0)
    state = s.evaluate(decision_after(nifty))
    assert state.vix_close == 15.0 and state.nifty_close == pytest.approx(21000.0)
    assert state.authority == "INFORMATION_ONLY"
    with pytest.raises(Exception):
        state.state = "DERATED"                                          # frozen


# ---------------------------------------------------------- ECS supervisor authority

def _ecs_eval(ecs, grid_state="SYNCHRONIZED", **kw):
    grid = plant_control.PlantGridState(grid_state, grid_state not in ("ISLANDED_SAFE",), "T", "t")
    args = dict(gross_exposure_fraction=0.0, gross_exposure_limit_fraction=0.5)
    args.update(kw)
    return ecs.evaluate(grid, all_available(), **args)


def _settled(ecs, grid_state="SYNCHRONIZED", n=15, **kw):
    out = None
    for _ in range(n):
        out = _ecs_eval(ecs, grid_state, **kw)
    return out


def test_ecs_demand_is_bounded_and_can_only_be_reduced_by_supporting_inputs():
    assert _settled(ECSPlantSupervisor(cfg())).plant_demand_reference_pu == 1.0
    assert _settled(ECSPlantSupervisor(cfg()), supporting_derate=5.0).plant_demand_reference_pu == 1.0   # never > 1
    assert _settled(ECSPlantSupervisor(cfg()), supporting_derate=0.4).plant_demand_reference_pu == pytest.approx(0.4)
    assert _settled(ECSPlantSupervisor(cfg()), supporting_derate=-3.0).plant_demand_reference_pu == 0.0
    out = _settled(ECSPlantSupervisor(cfg()), gross_exposure_fraction=0.4)         # 80% of the limit used
    assert out.plant_demand_reference_pu == pytest.approx(0.2) and "EXPOSURE_HEADROOM_LIMIT" in out.reasons


def test_ecs_headroom_equation_only_ever_reduces_and_never_widens_the_exposure_envelope():
    limit = 0.5
    for exposure in (0.0, 0.1, 0.25, 0.4, 0.5, 0.7):
        out = _settled(ECSPlantSupervisor(cfg()), gross_exposure_fraction=exposure,
                       gross_exposure_limit_fraction=limit)
        headroom = max(0.0, 1.0 - exposure / limit)
        assert out.plant_demand_reference_pu == pytest.approx(min(1.0, headroom))
        assert out.plant_demand_reference_pu <= 1.0
    at_limit = _settled(ECSPlantSupervisor(cfg()), gross_exposure_fraction=0.5, gross_exposure_limit_fraction=0.5)
    assert at_limit.plant_demand_reference_pu == 0.0 and at_limit.operating_mode == "HOLD"


def test_ecs_starts_from_hold_and_ramps_up_never_jumping_to_full_demand():
    ecs = ECSPlantSupervisor(cfg())
    step = REG.params["ecs_demand_restore_step_pu"].default
    first = _ecs_eval(ecs)
    assert first.plant_demand_reference_pu == pytest.approx(step)
    assert first.plant_demand_reference_pu < 1.0


def test_ecs_restoration_is_monotonic_bounded_and_never_overrides_a_continuing_derate():
    ecs = ECSPlantSupervisor(cfg())
    derate = REG.params["ecs_derate_demand_pu"].default
    series = [_ecs_eval(ecs, "DERATED").plant_demand_reference_pu for _ in range(20)]
    assert all(b >= a for a, b in zip(series, series[1:]))                      # monotonic up
    assert max(series) == pytest.approx(derate) and series[-1] == pytest.approx(derate)   # capped at the derate target
    # grid recovers -> restore to 1.0 in bounded steps; a fresh derate cuts immediately
    up = [_ecs_eval(ecs, "SYNCHRONIZED").plant_demand_reference_pu for _ in range(10)]
    assert all(b >= a for a, b in zip(up, up[1:])) and up[-1] == 1.0
    assert _ecs_eval(ecs, "DERATED").plant_demand_reference_pu == pytest.approx(derate)
    # protection trip while restoring drops to HOLD immediately and the ramp restarts from zero
    assert _ecs_eval(ecs, plant_protection_tripped=True).plant_demand_reference_pu == 0.0


def test_ecs_records_that_plant_protection_is_not_connected():
    out = _ecs_eval(ECSPlantSupervisor(cfg()))
    assert out.plant_protection_connected is False and out.bay_availability_source == "SYMBOL_TRIPS_ONLY"
    assert "PLANT_PROTECTION_NOT_CONNECTED" in out.reasons
    connected = _ecs_eval(ECSPlantSupervisor(cfg()), plant_protection_tripped=False)
    assert connected.plant_protection_connected is True and "PLANT_PROTECTION_NOT_CONNECTED" not in connected.reasons


def test_ecs_reductions_are_immediate_and_restoration_is_rate_limited():
    ecs = ECSPlantSupervisor(cfg())
    step = REG.params["ecs_demand_restore_step_pu"].default
    assert _settled(ecs).plant_demand_reference_pu == 1.0
    assert _ecs_eval(ecs, "UNSYNCHRONIZED").plant_demand_reference_pu == 0.0         # immediate
    restored = [_ecs_eval(ecs).plant_demand_reference_pu for _ in range(12)]
    assert restored[0] == pytest.approx(step)
    assert all(b - a <= step + 1e-12 for a, b in zip(restored, restored[1:]))
    assert restored[-1] == 1.0 and max(restored) <= 1.0


def test_ecs_protection_trip_and_no_available_bay_hold_the_plant():
    ecs = ECSPlantSupervisor(cfg())
    assert _settled(ecs).plant_demand_reference_pu == 1.0
    assert _ecs_eval(ecs, plant_protection_tripped=True).operating_mode == "HOLD"
    grid = plant_control.PlantGridState("SYNCHRONIZED", True, "T", "t")
    none_ok = {bay: BayStatus(available=False, tripped=True) for bay in BAY_IDS}
    out = ECSPlantSupervisor(cfg()).evaluate(grid, none_ok, gross_exposure_fraction=0.0,
                                             gross_exposure_limit_fraction=0.5)
    assert out.plant_demand_reference_pu == 0.0 and "NO_BAY_AVAILABLE" in out.reasons


def test_ecs_output_shape_and_invalid_input_rejection():
    out = _settled(ECSPlantSupervisor(cfg()))
    assert out.plant_derate == pytest.approx(1.0 - out.plant_demand_reference_pu)
    assert [bay for bay, _ in out.bay_availability_mask] == list(BAY_IDS) and out.reasons
    ecs = ECSPlantSupervisor(cfg())
    with pytest.raises(PlantControlError):
        _ecs_eval(ecs, gross_exposure_limit_fraction=0.0)
    with pytest.raises(PlantControlError):
        _ecs_eval(ecs, supporting_derate=float("nan"))
    with pytest.raises(PlantControlError):
        ecs.evaluate(plant_control.PlantGridState("SYNCHRONIZED", True, "T", "t"), {"GTG1_HEAVY_INDUSTRY": BayStatus()},
                     gross_exposure_fraction=0.0, gross_exposure_limit_fraction=0.5)


# ------------------------------------------------------------------- sector dispatch

def _ecs_output(demand, mask=None):
    mask = mask or {bay: True for bay in BAY_IDS}
    return plant_control.ECSPlantOutput("NORMAL", demand, 1.0 - demand, tuple((b, mask[b]) for b in BAY_IDS),
                                        ("t",), "SYNCHRONIZED")


def test_dispatch_uses_the_existing_dynamic_bay_load_dispatcher_weights():
    merit = DynamicBayLoadDispatcher()
    for bay, r in zip(BAY_IDS, (2.0, -1.0, 0.5, 1.5, -0.5)):
        for _ in range(4):
            merit.register_trade(bay, r)
    result = SectorDispatchController(merit).dispatch(_ecs_output(0.6))
    refs = result.as_dict()
    assert result.feasible and sum(refs.values()) == pytest.approx(0.6)
    assert all(refs[bay] == pytest.approx(0.6 * merit.weights[bay]) for bay in BAY_IDS)   # merit-proportional
    assert SectorDispatchController(merit).merit_source is merit                          # reused, not replaced


def test_tripped_or_unavailable_bay_gets_zero_reference_and_load_is_redistributed():
    mask = {bay: True for bay in BAY_IDS}
    mask[BAY_IDS[0]] = False
    result = SectorDispatchController(DynamicBayLoadDispatcher()).dispatch(_ecs_output(0.8, mask))
    refs = result.as_dict()
    assert refs[BAY_IDS[0]] == 0.0 and result.feasible
    assert sum(refs.values()) == pytest.approx(0.8)
    assert any("UNAVAILABLE_ZERO_DISPATCH" in r for r in result.reasons)


def test_dispatch_is_deterministic():
    merit = DynamicBayLoadDispatcher()
    mask = {bay: bay != BAY_IDS[2] for bay in BAY_IDS}
    runs = [SectorDispatchController(merit).dispatch(_ecs_output(0.9, mask)) for _ in range(3)]
    assert runs[0] == runs[1] == runs[2]


def test_ceilings_are_never_exceeded_and_sums_hold_across_random_cases():
    rng = random.Random(7)
    for _ in range(300):
        merit = DynamicBayLoadDispatcher(max_ceiling=rng.choice([0.25, 0.3, 0.35, 0.5, 1.0]))
        weights = [rng.random() + 0.01 for _ in BAY_IDS]
        total = sum(weights)
        merit.weights = {bay: w / total for bay, w in zip(BAY_IDS, weights)}
        mask = {bay: rng.random() > 0.3 for bay in BAY_IDS}
        demand = rng.random()
        result = SectorDispatchController(merit).dispatch(_ecs_output(demand, mask))
        refs = result.as_dict()
        capacity = sum(merit.max_ceiling for bay in BAY_IDS if mask[bay])
        assert all(0.0 <= refs[bay] <= merit.max_ceiling + 1e-9 for bay in BAY_IDS)     # no negative, no ceiling breach
        assert all(refs[bay] == 0.0 for bay in BAY_IDS if not mask[bay])
        if demand <= capacity + 1e-9:
            assert result.feasible and sum(refs.values()) == pytest.approx(demand, abs=1e-9)
        else:
            assert not result.feasible
            assert result.unallocated_pu == pytest.approx(demand - capacity, abs=1e-9)
            assert sum(refs.values()) == pytest.approx(capacity, abs=1e-9)


def test_infeasible_demand_is_reported_not_hidden():
    merit = DynamicBayLoadDispatcher()                                   # ceiling 0.35 per bay
    mask = {bay: i < 2 for i, bay in enumerate(BAY_IDS)}                 # only two bays: capacity 0.70
    result = SectorDispatchController(merit).dispatch(_ecs_output(1.0, mask))
    assert not result.feasible
    assert result.unallocated_pu == pytest.approx(0.30)
    assert "INFEASIBLE_CAPACITY_DEMAND_EXCEEDS_AVAILABLE_BAY_CEILINGS" in result.reasons
    assert max(r for _, r in result.references_pu) <= merit.max_ceiling + 1e-12
    assert result.allocated_pu == pytest.approx(0.70)


def test_dispatch_rejects_invalid_inputs():
    merit = DynamicBayLoadDispatcher()
    with pytest.raises(PlantControlError):
        SectorDispatchController(merit).dispatch(_ecs_output(1.5))
    merit.weights = {bay: float("nan") for bay in BAY_IDS}
    with pytest.raises(PlantControlError):
        SectorDispatchController(merit).dispatch(_ecs_output(0.5))


# ------------------------------------------------------------- authority boundaries

FORBIDDEN_MODULES = ("broker", "paper_execution", "operating_mode", "gates_framework", "kiteconnect",
                     "startup_synchronization", "closed_loop_control", "governor", "ccpp_protection")
FORBIDDEN_NAMES = ("place_order", "submit_order", "broker", "ProposedOrder", "ExecutionGate",
                   "PaperBrokerAdapter", "KiteConnect")


def _module_tree():
    return ast.parse((ROOT / "revision5/plant_control.py").read_text())


def test_plant_control_has_no_broker_order_execution_or_governor_dependencies():
    tree = _module_tree()
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
    for module in imported:
        assert not any(bad in module for bad in FORBIDDEN_MODULES), module
    identifiers = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} | \
                  {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert not identifiers & set(FORBIDDEN_NAMES)


def test_ecs_and_dispatcher_classes_expose_no_order_or_broker_api():
    for cls in (ECSPlantSupervisor, SectorDispatchController, PlantGridSynchronizer, PlantControlChain):
        assert not [n for n in dir(cls) if any(k in n.lower() for k in ("order", "broker", "submit", "execute"))]
    ecs = ECSPlantSupervisor(cfg())
    dispatcher = SectorDispatchController(DynamicBayLoadDispatcher())
    for obj in (ecs, dispatcher):
        assert not [a for a in vars(obj) if any(k in a.lower() for k in ("broker", "order", "adapter"))]


def test_governor_remains_final_local_admission_authority():
    spec = BAY_GOVERNOR_SPECS[BAY_IDS[1]]
    governor = BayTurbineClosedLoopGovernor(spec)
    baseline = governor.evaluate_entry_request(z_score=-3.0, grid_return_fraction=0.0)
    assert baseline["action"] == "ENTRY"
    # a plant HOLD (zero demand, zero dispatch) and a full-demand chain both leave the governor untouched
    for kwargs in ({"vix_last": 45.0}, {}):
        c, nifty = chain(**kwargs)
        snap = c.evaluate(decision_after(nifty), all_available(), gross_exposure_fraction=0.0,
                          gross_exposure_limit_fraction=0.5)
        assert governor.evaluate_entry_request(z_score=-3.0, grid_return_fraction=0.0) == baseline
        assert all(isinstance(r, GovernorDispatchReference) and not r.applied and r.authority == "INFORMATION_ONLY"
                   for r in snap.governor_references)
    assert not hasattr(governor, "accept_dispatch_reference")           # nothing consumes references in SHADOW
    assert governor.evaluate_entry_request(z_score=+5.0)["action"] == "NO_ACTION"       # governor still says no


def test_bb07_can_block_a_governor_approved_admission():
    governor = BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[BAY_IDS[1]])
    assert governor.evaluate_entry_request(z_score=-3.0)["action"] == "ENTRY"
    engine = EntryDecisionEngine(SafetyGateConfig())
    blocked = engine.evaluate_pre_submit(SystemState(portfolio_value=1_000_000.0, kill_switch_active=True),
                                         symbol="INFY", proposed_quantity=10)
    assert blocked["passed"] is False and blocked["decisions"][0].gate_name == "Gate01KillSwitch"


def test_governor_references_carry_the_dispatch_and_are_typed():
    c, nifty = chain()
    snap = c.evaluate(decision_after(nifty), all_available(), gross_exposure_fraction=0.0,
                      gross_exposure_limit_fraction=0.5)
    refs = {r.bay_id: r for r in snap.governor_references}
    assert list(refs) == list(BAY_IDS)
    for bay, value in snap.dispatch.references_pu:
        assert refs[bay].dispatch_reference_pu == value and refs[bay].mode == "SHADOW"
    assert build_governor_references(snap.dispatch, PlantControlMode.SHADOW) == snap.governor_references


def test_native_r5_25a_generator_synchronization_is_separate():
    import revision5.startup_synchronization as native
    src = (ROOT / "revision5/plant_control.py").read_text()
    assert "startup_synchronization" not in src and "AutomaticSynchronizer25A" not in src
    assert hasattr(native, "AutomaticSynchronizer25A") and hasattr(native, "SynchrocheckRelay25")
    assert PlantGridSynchronizer is not native.AutomaticSynchronizer25A
    from revision3.macro_grid_synchronizer import MacroGridSynchronizer
    assert not issubclass(PlantGridSynchronizer, MacroGridSynchronizer)         # per-stock logic preserved separately


def test_closed_loop_supervisor_is_not_the_ecs_plant_supervisor():
    assert ClosedLoopSupervisor is not ECSPlantSupervisor
    assert not issubclass(ECSPlantSupervisor, ClosedLoopSupervisor)
    assert not issubclass(ClosedLoopSupervisor, ECSPlantSupervisor)
    assert not [n for n in dir(ClosedLoopSupervisor) if "plant_demand" in n or "dispatch" in n]
    assert "closed_loop" not in (ROOT / "revision5/plant_control.py").read_text().split('"""', 2)[2]


# ------------------------------------------------------------------- mode + registry

def test_shadow_default_and_explicit_paper_apply_have_no_live_mode():
    nifty, vix = grid_frames()
    provider = SealedGridContextProvider(nifty, vix)
    assert PlantControlChain(cfg(), provider, DynamicBayLoadDispatcher()).mode is PlantControlMode.SHADOW
    assert {m.value for m in PlantControlMode} == {"SHADOW", "PAPER_APPLY"}
    assert PlantControlChain(cfg(), provider, DynamicBayLoadDispatcher(), "PAPER_APPLY").mode is PlantControlMode.PAPER_APPLY
    for bad in ("LIVE", "shadow"):
        with pytest.raises(PlantControlError):
            PlantControlChain(cfg(), provider, DynamicBayLoadDispatcher(), bad)


def test_new_parameters_are_registry_owned_fixed_and_not_calibrated():
    for name in GRID_NAMES:
        spec = REG.params[name]
        assert spec.black_box == "PlantControl" and spec.calibratable is False
        assert spec.applicable_engines == "EXTERNAL" and name in REG.fixed_target_names()
        assert "ENGINEERING_INITIAL_VALUE" in spec.notes and "NOT_CALIBRATED" in spec.notes
        assert spec.minimum <= spec.default <= spec.maximum
        assert not REG.is_calibratable(name, "EXTERNAL") and not REG.is_calibratable(name, "IN_HOUSE")
    for name in ("grid_vix_operating_min", "grid_vix_operating_max", "grid_max_staleness_seconds"):
        assert "NEVER_CALIBRATE_SAFETY" in REG.params[name].notes
    assert REG.validate_calibration_payload({"ecs_derate_demand_pu": 0.4}, engine="EXTERNAL")   # refused


def test_invalid_vix_bounds_are_rejected_at_construction():
    nifty, vix = grid_frames()
    with pytest.raises(PlantControlError):
        PlantGridSynchronizer(cfg(grid_vix_derate_start=30.0, grid_vix_operating_max=25.0),
                              SealedGridContextProvider(nifty, vix))


def test_no_operational_numeric_literal_bypasses_the_registry():
    """Doctrine: operational constants are registry-owned; structural / mathematical identities are not.

    Allowed without ceremony: the identities 0, 1, 2 (and 0.0, 1.0) used as indices, complements,
    counts and bounds.  Any other numeric literal must be a named, module-level STRUCTURAL constant
    (leading-underscore/UPPER_CASE name).  Booleans, strings and enum values are not numbers here.
    """
    tree = _module_tree()
    identities = {0, 1, 2}
    named_structural = set()
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant) \
                and isinstance(node.value.value, (int, float)) and not isinstance(node.value.value, bool):
            for target in node.targets:
                assert isinstance(target, ast.Name) and target.id.lstrip("_").isupper(), target
                named_structural.add(node.value.value)
    assert named_structural == {1e-12}                     # only the numerical tolerance _EPS
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            if node.value not in identities and node.value not in named_structural:
                offenders.append((node.lineno, node.value))
    assert not offenders, offenders


# ------------------------------------------------- orchestrator: SHADOW leaves trades untouched

def _replay(provider=None, bars=1500):
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    data = {"INFY": loader._load_symbol_csv("INFY").tail(bars).reset_index(drop=True)}
    orch = Revision2ExternalEngineOrchestrator(
        ["INFY"], CanonicalParameterRegistry(), starting_equity=1_000_000.0,
        grid_context_provider=provider(data["INFY"]) if provider else None)
    report = orch.run(data, warmup=60)
    return orch, report


def _provider(vix_level):
    def build(frame):
        ts = pd.to_datetime(frame["timestamp"], utc=True)
        nifty = pd.DataFrame({"timestamp": ts, "close": frame["close"].to_numpy()}).set_index("timestamp")
        nifty = nifty.resample("15min").last().dropna().reset_index()
        vix = nifty.copy()
        vix["close"] = float(vix_level)
        return SealedGridContextProvider(nifty, vix)
    return build


def _ledger(orch):
    return [(t["symbol"], t["side"], round(t["pnl"], 6), round(t["net_pnl"], 6)) for t in orch.completed_trades]


def test_shadow_mode_leaves_the_replay_trade_ledger_unchanged():
    """No grid, a synchronized grid and a fully-blocking (UNSYNCHRONIZED, demand 0) grid must give
    the IDENTICAL trade ledger: the chain records, it never applies."""
    base_orch, base_report = _replay()
    assert base_report["completed_trades"] > 0
    sync_orch, sync_report = _replay(_provider(15.0))
    block_orch, block_report = _replay(_provider(45.0))

    assert _ledger(sync_orch) == _ledger(base_orch) == _ledger(block_orch)
    assert sync_report["net_pnl"] == base_report["net_pnl"] == block_report["net_pnl"]

    for orch, report in ((base_orch, base_report), (sync_orch, sync_report), (block_orch, block_report)):
        shadow = report["plant_control_shadow"]
        assert shadow["mode"] == "SHADOW" and shadow["applied"] is False and shadow["observer_failures"] == 0
        assert shadow["plant_protection_connected"] is False and shadow["bay_availability_source"] == "SYMBOL_TRIPS_ONLY"
        assert all(not snap.ecs.plant_protection_connected for snap in orch.plant_control_snapshots)
        assert shadow["evaluations"] > 0 and orch.plant_control_snapshots
        assert all(s.applied is False and s.mode == "SHADOW" for s in orch.plant_control_snapshots)
    assert set(base_report["plant_control_shadow"]["grid_state_counts"]) == {"ISLANDED_SAFE"}      # no grid -> fail closed
    assert set(block_report["plant_control_shadow"]["grid_state_counts"]) <= {"UNSYNCHRONIZED", "ISLANDED_SAFE"}
    blocked = [s for s in block_orch.plant_control_snapshots if s.grid.state == "UNSYNCHRONIZED"]
    assert blocked and all(s.ecs.plant_demand_reference_pu == 0.0 for s in blocked)
    synced = [s for s in sync_orch.plant_control_snapshots if s.grid.state in ("SYNCHRONIZED", "DERATED")]
    assert synced and any(s.ecs.plant_demand_reference_pu > 0.0 for s in synced)


def test_orchestrator_requires_connected_inputs_for_paper_apply():
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    with pytest.raises(PlantControlError):
        Revision2ExternalEngineOrchestrator(["INFY"], CanonicalParameterRegistry(), plant_control_mode="PAPER_APPLY")


def test_orchestrator_registers_plant_control_parameters_as_consumed():
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    orch = Revision2ExternalEngineOrchestrator(["INFY"], CanonicalParameterRegistry())
    assert set(GRID_NAMES) <= orch.consumed_parameters


# ----------------------------------------- grid logic: composition, not reimplementation

def test_plant_synchronizer_composes_the_existing_grid_implementations(monkeypatch):
    nifty, vix = grid_frames()
    provider = SealedGridContextProvider(nifty, vix)
    calls = {"context": 0, "frequency": 0}
    real_context = provider.causal_context

    def context(*args, **kwargs):
        calls["context"] += 1
        return real_context(*args, **kwargs)

    provider.causal_context = context
    synchronizer = PlantGridSynchronizer(cfg(), provider)
    real_frequency = synchronizer.frequency.check_frequency

    def frequency(vix_value):
        calls["frequency"] += 1
        return real_frequency(vix_value)

    synchronizer.frequency.check_frequency = frequency
    state = synchronizer.evaluate(decision_after(nifty))
    assert state.state == "SYNCHRONIZED" and calls == {"context": 1, "frequency": 1}
    from revision3.macro_grid_synchronizer import MacroGridSynchronizer
    assert isinstance(synchronizer.frequency, MacroGridSynchronizer) and synchronizer.provider is provider


def test_plant_control_does_not_reimplement_the_causal_data_contract():
    """Timestamp parsing, as-of selection, alignment/merge and the VIX band test live in ONE place each."""
    source = (ROOT / "revision5/plant_control.py").read_text()
    for token in ("to_datetime", "searchsorted", ".merge(", "is_monotonic", "vix_min", "vix_max", "<= vix_close <="):
        assert token not in source, token
    tz_rules = re.findall(r"tz_localize\(([^)]*)\)", source)
    assert tz_rules == ['"Asia/Kolkata"']                   # the single, documented naive-replay rule


def test_single_owner_of_the_causal_contract_observe_uses_causal_context():
    source = (ROOT / "revision2_external/grid_context.py").read_text()
    observe = source[source.index("    def observe("):]
    assert "self.causal_context(" in observe and "searchsorted" not in observe
    assert 'nifty["timestamp"] <' not in observe                 # the old inline selection is gone


def test_provider_defaults_equal_the_registry_owned_plant_limits():
    """The legacy provider's constructor defaults and the registry limits must not drift apart."""
    nifty, vix = grid_frames()
    provider = SealedGridContextProvider(nifty, vix)
    assert provider.max_staleness_seconds == REG.params["grid_max_staleness_seconds"].default
    assert provider.minimum_aligned_bars == REG.params["grid_min_aligned_bars"].default
    from revision3.macro_grid_synchronizer import MacroGridSynchronizer
    legacy = MacroGridSynchronizer()
    assert (legacy.vix_min, legacy.vix_max, legacy.trend_ema_period) == (
        REG.params["grid_vix_operating_min"].default, REG.params["grid_vix_operating_max"].default,
        REG.params["grid_nifty_ema_period"].default)


def test_causal_context_is_shared_and_observe_behaviour_is_unchanged():
    nifty, vix = grid_frames()
    provider = SealedGridContextProvider(nifty, vix)
    ctx = provider.causal_context(decision_after(nifty))
    assert ctx.available and ctx.source_timestamp == nifty["timestamp"].iloc[-1]
    assert len(ctx.nifty_prior) == len(nifty) and len(ctx.aligned) == len(nifty)
    naive = decision_after(nifty).tz_localize(None)
    with pytest.raises(ValueError):
        provider.causal_context(naive)                      # the data contract itself stays strict
    with pytest.raises(ValueError):
        provider.observe("INFY", nifty, naive, 1)


# ------------------------------------------------ time-zone / causality rule

def test_decision_time_rule_naive_is_kolkata_and_aware_keeps_its_instant():
    rule = PlantGridSynchronizer.decision_utc
    instant = pd.Timestamp("2024-03-01 10:30:00+00:00")
    assert rule(instant) == instant
    assert rule(instant.tz_convert("Asia/Kolkata")) == instant
    assert rule(instant.tz_convert("America/New_York")) == instant
    assert rule(instant.tz_convert("Asia/Kolkata").tz_localize(None)) == instant     # naive = IST wall clock
    assert rule("2024-03-01 16:00:00") == instant


@pytest.mark.parametrize("zone", ["Asia/Kolkata", "America/New_York", "Europe/London", "Australia/Sydney"])
def test_same_instant_in_any_zone_gives_the_same_grid_state(zone):
    s, nifty = sync()
    instant = decision_after(nifty)
    assert s.evaluate(instant.tz_convert(zone)) == s.evaluate(instant)


def test_dst_transition_cannot_move_a_future_bar_into_the_causal_prefix():
    """US DST begins 2024-03-10 02:00 local (wall clock jumps).  Bars are stamped in UTC, so a bar one
    second AFTER the decision instant must stay out, expressed in any zone and across the jump."""
    idx = pd.date_range("2024-03-09 12:00:00+00:00", periods=120, freq="15min", tz="UTC")
    nifty = pd.DataFrame({"timestamp": idx, "close": 21000.0})
    vix = pd.DataFrame({"timestamp": idx, "close": 15.0})
    s = PlantGridSynchronizer(cfg(), SealedGridContextProvider(nifty, vix))
    jump = pd.Timestamp("2024-03-10 07:00:00+00:00")                    # instant of the NY spring-forward
    for offset in (-1, 0, 1):
        decision = jump + pd.Timedelta(seconds=offset)
        source = pd.Timestamp(s.evaluate(decision.tz_convert("America/New_York")).source_timestamp)
        assert source < decision                                        # strictly earlier, always
        assert source == idx[idx < decision][-1]
    late = idx[-1] + pd.Timedelta(seconds=1)
    assert pd.Timestamp(s.evaluate(late.tz_convert("Europe/London")).source_timestamp) == idx[-1]
    assert pd.Timestamp(s.evaluate(idx[-1].tz_convert("Europe/London")).source_timestamp) == idx[-2]   # == excluded


def test_a_future_bar_one_second_after_the_decision_is_excluded_in_every_zone():
    nifty, vix = grid_frames()
    s = PlantGridSynchronizer(cfg(), SealedGridContextProvider(nifty, vix))
    decision = nifty["timestamp"].iloc[-1] - pd.Timedelta(seconds=1)
    for zone in ("UTC", "Asia/Kolkata", "America/New_York"):
        assert pd.Timestamp(s.evaluate(decision.tz_convert(zone)).source_timestamp) == nifty["timestamp"].iloc[-2]


# ------------------------------------------------------- dispatcher reuse

def test_dispatch_controller_holds_no_merit_or_ceiling_table_of_its_own():
    merit = DynamicBayLoadDispatcher()
    controller = SectorDispatchController(merit)
    assert set(vars(controller)) == {"merit_source"} and controller.merit_source is merit
    source = (ROOT / "revision5/plant_control.py").read_text()
    for token in ("pstdev", "fmean", "downside", "register_trade", "0.85", "capital_weight"):
        assert token not in source, token                           # the merit algorithm is not copied


def test_live_merit_weights_reach_the_existing_controller_without_rebuilding_it():
    merit = DynamicBayLoadDispatcher()
    controller = SectorDispatchController(merit)                    # built ONCE, before any performance feedback
    before = controller.dispatch(_ecs_output(0.6)).as_dict()
    for _ in range(5):
        merit.register_trade(BAY_IDS[0], 2.0)                       # a future wiring would do exactly this
        merit.register_trade(BAY_IDS[1], -1.0)
    after = controller.dispatch(_ecs_output(0.6)).as_dict()
    assert after != before
    assert after[BAY_IDS[0]] > before[BAY_IDS[0]] and after[BAY_IDS[1]] < before[BAY_IDS[1]]
    assert sum(after.values()) == pytest.approx(0.6)


def test_dispatch_respects_a_custom_dispatcher_ceiling():
    merit = DynamicBayLoadDispatcher(max_ceiling=0.25)
    ok = SectorDispatchController(merit).dispatch(_ecs_output(1.0))       # 5 x 0.25 = 1.25 >= 1.0: feasible
    assert ok.feasible and max(r for _, r in ok.references_pu) <= 0.25 + 1e-12
    assert sum(r for _, r in ok.references_pu) == pytest.approx(1.0)
    mask = {bay: i < 3 for i, bay in enumerate(BAY_IDS)}                  # 3 x 0.25 = 0.75 < 1.0: infeasible
    result = SectorDispatchController(merit).dispatch(_ecs_output(1.0, mask))
    assert not result.feasible and max(r for _, r in result.references_pu) <= 0.25 + 1e-12
    assert result.allocated_pu == pytest.approx(0.75) and result.unallocated_pu == pytest.approx(0.25)
