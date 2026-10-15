"""End-to-end controller-authority proofs through the shared R5 orchestrator (Engine A) on the
seeded synthetic feed shared by Engines A, B and C.  A parameter is changed only through the
registry (as a sealed protocol would) or through a validated calibration override, and the proof
is a change in an ORDER: its existence, size, price levels or exit.

Engines B and C run the identical controller code (they subclass this orchestrator); their own
end-to-end proofs are in tests_engine_b/ and tests_engine_c/.
"""
from __future__ import annotations

import copy
from dataclasses import replace

import pandas as pd
import pytest

from canonical_parameter_registry import CanonicalParameterRegistry
from engine_c.data_contract import load_synthetic
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.ccpp_unified_plant import CentralPlantMasterDCS

SYMBOLS = ["HCLTECH", "INFY", "TCS", "WIPRO"]   # one bay (GTG2): one shared governor outer loop
BARS, WARMUP = 900, 60
# Amplified outer-loop feedback so that the few synthetic closes move the entry comparator.
AMPLIFIED = dict(gov_gtg2_feedback_gain=2.0, gov_gtg2_dynamic_offset_min=-1.5, gov_gtg2_dynamic_offset_max=0.9)

ALL_MODES = dict(governor_position_mode="v3", avr_voltage_mode="measured", dispatcher_allocation_mode="bounded",
                 hrsg_correlation_mode="causal_bay_returns", final_exit_authority=True, authority_telemetry=True)


def registry_with(**changes):
    registry = copy.deepcopy(CanonicalParameterRegistry())
    for name, value in changes.items():
        registry.params[name] = replace(registry.params[name], default=value)
    registry.FROZEN_IDENTITY_SHA256 = registry.identity_sha256()
    return registry


@pytest.fixture(scope="module")
def feed():
    data, nifty, vix, _ = load_synthetic(SYMBOLS, BARS, WARMUP)
    return data, nifty, vix


def run_a(feed, registry=None, **kwargs):
    data, nifty, vix = feed
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    engine = Revision2ExternalEngineOrchestrator(
        sorted(data), registry=registry, grid_context_provider=SealedGridContextProvider(nifty, vix),
        real_plant_dcs=plant, plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
        telemetry_mode="full", governor_authority="full", starting_equity=1_000_000.0, **kwargs)
    report = engine.run({s: data[s] for s in sorted(data)}, warmup=WARMUP)
    return engine, report


def orders(report):
    return [(t["symbol"], str(t["entry_timestamp"]), t["side"], t["quantity"], round(t["entry_price"], 6),
             round(t["planned_stop_price"], 6), round(t["planned_target_price"], 6), str(t["exit_timestamp"]),
             round(t["exit_price"], 6), t["reason"])
            for t in report["trades"]]


@pytest.fixture(scope="module")
def base(feed):
    return run_a(feed)[1]


def test_baseline_has_closed_trades(base):
    assert len(base["trades"]) >= 3


@pytest.mark.parametrize("changes", [{"gov_gtg2_base_z": -1.0}, {"avr_loading_fraction_base": 0.08},
                                     {"gov_gtg2_feedback_gain": 2.0, "gov_gtg2_dynamic_offset_min": -1.5,
                                      "gov_gtg2_dynamic_offset_max": 0.9}], ids=lambda c: ",".join(c))
def test_registry_parameter_changes_an_engine_a_order(feed, base, changes):
    _, report = run_a(feed, registry=registry_with(**changes))
    assert orders(report) != orders(base), changes


@pytest.fixture(scope="module")
def amplified(feed):
    return run_a(feed, registry=registry_with(**AMPLIFIED))[1]


@pytest.mark.parametrize("changes", [{"gov_gtg2_kp": 3.9}, {"gov_gtg2_kd": 1.9}], ids=lambda c: ",".join(c))
def test_governor_outer_loop_gains_change_an_engine_a_order(feed, amplified, changes):
    """Governor Kp / Kd: through the realized-R outer loop, the dynamic_z entry comparator and the
    governor's ENTRY / EXIT authority, a gain change admits or blocks a different order."""
    _, report = run_a(feed, registry=registry_with(**AMPLIFIED, **changes))
    assert orders(report) != orders(amplified), changes


@pytest.mark.parametrize("override", [{"pid_ki_entry": 0.2}, {"pid_kd_entry": 0.2}, {"pid_ki_exit": 0.15}])
def test_box6_entry_gains_move_order_prices(feed, base, override):
    _, report = run_a(feed, calibration_overrides=override)
    assert orders(report) != orders(base)


def test_measured_avr_mode_changes_order_sizes(feed, base):
    _, report = run_a(feed, avr_voltage_mode="measured")
    assert orders(report) != orders(base)


def test_authority_telemetry_is_telemetry_only(feed, base):
    engine, report = run_a(feed, authority_telemetry=True)
    assert orders(report) == orders(base)
    events = {e["event_type"] for e in report["controller_telemetry"]}
    assert {"CONTROLLER_PARAMETER_STARTUP", "CONTROLLER_APPLIED_VALUES"} <= events
    summary = report["controller_authority"]["applied_value_summary"]
    assert summary["GTG2_TECH_TELECOM.governor.kp"]["bars"] > 0
    entry = next(e for e in report["controller_telemetry"] if e["event_type"] == "PLANT_DCS_ENTRY" and e.get("admitted"))
    assert {"dispatcher_weight", "bay_effective_allocation", "avr_excitation_command"} <= set(entry)


def test_measured_avr_records_the_voltage_path(feed):
    _, report = run_a(feed, avr_voltage_mode="measured", authority_telemetry=True)
    regulation = [e for e in report["controller_telemetry"] if e["event_type"] == "AVR_VOLTAGE_REGULATION"]
    assert regulation
    record = next(r for e in regulation for r in e["bays"].values() if not r.get("tripped"))
    for key in ("voltage_reference_pu", "bus_voltage_pu", "terminal_voltage_pu", "error", "p_term", "i_term",
                "d_term", "command_before", "command_requested", "command_after_limits", "bay_loading_pu"):
        assert key in record
    assert any(abs(r["error"]) > 0 for e in regulation for r in e["bays"].values() if "error" in r)


@pytest.mark.parametrize("changes", [{}, {"symbol_loss_cooldown_bars": 40}, {"symbol_trip_consecutive_losses": 1}],
                         ids=lambda c: ",".join(c) or "defaults")
def test_symbol_loss_cooldown_and_trip_count_change_entry_admission(changes):
    """The orchestrator's fixed-safety symbol latches, through the real authoritative-close path
    (_execute_exit): the cooldown horizon and the trip count are the registry values, and the entry
    gate blocks the symbol on exactly those latches (funnel bay_cooldown / bay_trip rejections)."""
    from tests_external.test_audit_remediation import open_position
    engine = Revision2ExternalEngineOrchestrator(["INFY"], registry=registry_with(**changes) if changes else None)
    engine._current_bar_idx = 100
    trade = open_position(engine)
    engine._execute_exit("INFY", "2024-01-01 10:02", trade, 90.0, "stop")
    assert engine.completed_trades[-1]["net_pnl"] < 0
    cooldown = changes.get("symbol_loss_cooldown_bars", 15)
    assert engine.symbol_cooldown_until_bar["INFY"] == 100 + cooldown
    assert engine.symbol_tripped.get("INFY", False) == (changes.get("symbol_trip_consecutive_losses", 2) <= 1)


def test_full_mode_set_is_causal(feed):
    """No controller (including the measured AVR, the HRSG correlation feed, V3, the bounded
    dispatcher and the final exit authority) reads future data: perturbing every stock and grid
    bar after a cut-off leaves every decision recorded before the cut-off identical."""
    data, nifty, vix = feed
    cut = sorted(data["INFY"]["timestamp"])[int(BARS * 0.6)]

    def perturb(frame):
        frame = frame.copy()
        mask = pd.to_datetime(frame["timestamp"]) >= pd.Timestamp(cut)
        for col in ("open", "high", "low", "close"):
            frame.loc[mask, col] = frame.loc[mask, col] * 1.07
        return frame

    def perturb_grid(frame):
        frame = frame.copy()
        mask = pd.to_datetime(frame["timestamp"]) >= pd.Timestamp(cut)
        frame.loc[mask, "close"] = frame.loc[mask, "close"] * 1.05
        return frame

    # FINDING (pre-existing, identical with the default modes): Engine A's execution model plans an
    # entry on the NEXT bar's open (EXECUTION_MODEL.entry_reference_price), so the decision bar just
    # before the cut records plan prices derived from a post-cut open.  Engine C plans on the decision
    # bar's close for exactly this reason.  The controllers under test must not read the future, so
    # the comparison stops one bar before the cut.
    horizon = str(pd.Timestamp(cut) - pd.Timedelta(minutes=1))

    def events_before(report):
        return [e for e in report["controller_telemetry"] if e["timestamp"] < horizon]

    _, a = run_a(feed, **ALL_MODES)
    _, b = run_a(({s: perturb(f) for s, f in data.items()}, perturb_grid(nifty), perturb_grid(vix)), **ALL_MODES)
    assert events_before(a) == events_before(b)
    assert events_before(a)
    assert [o for o in orders(a) if o[1] < horizon] == [o for o in orders(b) if o[1] < horizon]
