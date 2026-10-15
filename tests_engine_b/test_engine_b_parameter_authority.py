"""Engine B (NautilusTrader native execution): controller-authority proofs end to end.

Engine B inherits every controller from the shared orchestrator; these tests prove that the
registry-owned values reach Nautilus ORDERS (size, price levels, existence), that the opt-in modes
actuate through Nautilus, and that the default run is unchanged by telemetry.
"""
import copy
from dataclasses import replace

import pytest

pytest.importorskip("nautilus_trader")

from canonical_parameter_registry import CanonicalParameterRegistry  # noqa: E402
from engine_c.data_contract import load_synthetic  # noqa: E402
from engine_b.runner import run_engine_b  # noqa: E402

BARS, WARMUP = 500, 60


def registry_with(**changes):
    registry = copy.deepcopy(CanonicalParameterRegistry())
    for name, value in changes.items():
        registry.params[name] = replace(registry.params[name], default=value)
    registry.FROZEN_IDENTITY_SHA256 = registry.identity_sha256()
    return registry


@pytest.fixture(scope="module")
def feed():
    data, nifty, vix, _ = load_synthetic(["INFY"], BARS, WARMUP)
    return data, nifty, vix


def run(feed, **kwargs):
    data, nifty, vix = feed
    return run_engine_b(data, nifty, vix, warmup=WARMUP, orchestrator_kwargs=kwargs)


def fills(report):
    return [{k: v for k, v in r.items() if k != "client_order_id"} for r in report["order_audit"] if r["event"] == "FILLED"]


@pytest.fixture(scope="module")
def base(feed):
    return run(feed)


def test_base_has_filled_orders(base):
    assert fills(base)


@pytest.mark.parametrize("kwargs", [
    {"registry": "avr_loading_fraction_base=0.08"},
    {"registry": "gov_gtg2_base_z=-1.0"},
    {"calibration_overrides": {"pid_ki_exit": 0.15}},
    {"calibration_overrides": {"pid_kd_exit": 0.15}},
    {"avr_voltage_mode": "measured"},
], ids=lambda k: str(k))
def test_parameter_and_mode_changes_reach_nautilus_orders(feed, base, kwargs):
    kwargs = dict(kwargs)
    if "registry" in kwargs:
        name, value = kwargs.pop("registry").split("=")
        kwargs["registry"] = registry_with(**{name: float(value)})
    report = run(feed, **kwargs)
    assert fills(report) != fills(base) or report["trades"] != base["trades"]


def test_startup_report_carries_the_effective_controller_values(feed):
    report = run(feed, registry=registry_with(gov_gtg2_kp=1.7), authority_telemetry=True)
    startup = report["r5_authority"]["startup"]
    assert startup["non_default"] == {"gov_gtg2_kp": 1.7}
    assert startup["registry_identity_sha256"] != CanonicalParameterRegistry.FROZEN_IDENTITY_SHA256


def test_authority_telemetry_does_not_change_engine_b_orders(feed, base):
    report = run(feed, authority_telemetry=True)
    assert fills(report) == fills(base)
    assert report["r5_authority"]["applied_value_summary"]


def test_box6_entry_pid_has_no_actuator_in_this_engine(feed, base):
    """FINDING (documented, not a defect introduced here): this engine fills market orders at the
    venue and re-anchors the plan's stop/target at the fill price with the PLANNED risk and reward
    distances.  The Box 6 entry PID only moves the planned reference price, so its gains (and
    mpc_entry_price_gain / mpc_schedule_*_gain) cannot change a Nautilus order; they actuate only in
    Engine A, whose paper broker fills at the adjusted reference.  The Box 6 exit PID changes the
    distances and does reach orders (test above)."""
    for override in ({"pid_ki_entry": 0.2}, {"pid_kd_entry": 0.2}, {"pid_kp_entry": 0.3}):
        report = run(feed, calibration_overrides=override)
        assert fills(report) == fills(base) and report["trades"] == base["trades"], override
