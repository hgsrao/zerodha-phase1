"""FSR (Mark V size_multiplier) application on the REAL orchestrator code paths.

Native entry quantity = min(S, P) where
    S = position_manager.size(..., size_mult * FSR, ...)            (sizing, orchestrator ~2528)
    P = floor(min(avr_lot, floor(headroom/price)) * FSR)            (_plant_entry_gate ~1123)
Both caps carry the factor FSR once, so min(S, P) is derated by FSR once (never FSR**2).
Only the plant evaluation (protection + AVR lot) is stubbed to a fixed admitted lot so the
arithmetic of the native gate itself is exercised.  Run:
    PYTHONPATH=<worktree> python -m pytest -q revision6_avr/test_r6_fsr_single_application.py
"""
import math
from types import SimpleNamespace

import pandas as pd
import pytest

from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.ccpp_unified_plant import CentralPlantMasterDCS

FSRS = (0.0, 0.5, 0.75, 1.0)
PRICE = 1000.0


def make_engine():
    times = pd.date_range("2024-03-01 09:15", periods=100, freq="15min", tz="Asia/Kolkata")
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": times, "close": 21000.0}),
                                         pd.DataFrame({"timestamp": times, "close": 15.0}))
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    o = Engine(["INFY", "TCS"], grid_context_provider=provider, real_plant_dcs=plant,
               plant_control_mode="PAPER_APPLY", governor_authority="full",
               closed_loop_mode="active_paper")
    assert getattr(o, "full_adaptation", False) is False and o._governor_full and o._plant_wired
    return o, times[-1] + pd.Timedelta("1min")


def gate(o, ts, *, avr_lot, fsr):
    bay_id = "GTG2_TECH_TELECOM"
    o._governor_telemetry["INFY"] = SimpleNamespace(available=True, atr=2.0, z_score=-2.5)
    o.real_plant_dcs.evaluate_entry = lambda **k: {"admitted": True, "quantity": avr_lot, "bay_id": bay_id}
    bar = {"high": 1001.0, "low": 999.0}
    q = o._plant_entry_gate("INFY", ts, "BUY", 10_000, PRICE, bar, "c1",
                            {"action": "ENTRY", "size_multiplier": fsr})
    headroom = float(o.real_plant_dcs.bays[bay_id].effective_allocation)
    return q, headroom


@pytest.mark.parametrize("fsr", FSRS)
def test_plant_gate_applies_fsr_once_when_avr_lot_limits(fsr):
    o, ts = make_engine()
    q, headroom = gate(o, ts, avr_lot=40, fsr=fsr)
    assert 40 < math.floor(headroom / PRICE)                     # the AVR lot is the binding cap
    assert q == math.floor(40 * fsr)


@pytest.mark.parametrize("fsr", FSRS)
def test_plant_gate_applies_fsr_once_when_headroom_limits(fsr):
    o, ts = make_engine()
    q, headroom = gate(o, ts, avr_lot=10_000_000, fsr=fsr)
    cap = math.floor(headroom / PRICE)
    assert q == math.floor(cap * fsr)                            # headroom binding, FSR once


@pytest.mark.parametrize("fsr", FSRS)
def test_final_quantity_is_fsr_times_min_of_caps_never_fsr_squared(fsr):
    """min(f*a, f*b) == f*min(a, b): the two caps cannot compound the derate."""
    for a, b in ((30, 50), (50, 30), (40, 40)):
        final = min(math.floor(a * fsr), math.floor(b * fsr))
        assert final == math.floor(min(a, b) * fsr)
        assert final >= math.floor(min(a, b) * fsr * fsr)
        if 0 < fsr < 1 and min(a, b) * fsr >= 2:
            assert final > math.floor(min(a, b) * fsr * fsr)     # would differ if applied twice


@pytest.mark.parametrize("stop", (0.5, 30.0))
def test_sizing_side_fsr_is_masked_by_rupee_and_safety_caps(stop):
    """Finding (unchanged code): risk_budget = min(base*FSR*conviction, max_loss_per_trade_rupees)
    and quantity = min(raw, safety_max).  While FSR*base*conviction exceeds the rupee cap, or the
    safety notional max binds, FSR does not change the sized quantity; it then acts only through
    the plant cap P.  FSR = 0 always zeroes the size.  Never applied twice."""
    o, ts = make_engine()
    plan = SimpleNamespace(side="BUY", entry_price=PRICE, stop_price=PRICE - stop, target_price=PRICE + 2 * stop,
                           minimum_hold_bars=1, maximum_hold_bars=30)
    out = {}
    for fsr in FSRS:
        q, _ = o.position_manager.size(plan, 1_000_000.0, fsr, o.config, symbol="INFY",
                                       portfolio_weights={"INFY": 0.5, "TCS": 0.5},
                                       max_exposure_per_symbol_fraction=0.02,
                                       open_positions_count=0, symbol_positions_count=0)
        out[fsr] = (q, dict(o.position_manager.last_sizing_telemetry))
    assert out[0.0][0] == 0
    for fsr in (0.5, 0.75, 1.0):
        q, t = out[fsr]
        assert q > 0 and t["raw_quantity"] > 0                                  # not vacuous
        assert t["base_risk_budget"] == pytest.approx(out[1.0][1]["base_risk_budget"] * fsr)
        masked = (t["derated_risk_budget"] >= t["max_loss_per_trade_rupees"]
                  or t["safety_max_quantity"] <= t["raw_quantity"])
        assert masked and q == out[1.0][0]          # FSR scales the budget but not the capped size
