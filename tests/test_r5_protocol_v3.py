"""Protocol V3 closed-loop position control, the single-gamma reference path and the exit-only
paired bridge (shadow exits must reproduce the real engine exactly under the legacy policy)."""
import math
from dataclasses import replace

import numpy as np
import pandas as pd
import pytest

from revision2.contracts import IDDecision
from revision2_external.closed_loop_control import TradeReferencePath
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator as Engine
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.exit_shadow import ShadowExitBridge, validate_legacy
from revision5.governor import BAY_GOVERNOR_SPECS, BayTurbineClosedLoopGovernor, PositionControlV3
from revision5.topology import BAY_IDS
from tests_external.test_audit_remediation import signal

PC = PositionControlV3(mfe_activation_r=0.30, kappa=0.25, gamma_fast=0.85, tau_error_multiplier=1.0,
                       gamma_slow=0.98, base_gap_r=0.45, minimum_gap_r=0.15, noise_floor_mult=1.0,
                       grace_bars=2)


def _gov():
    return BayTurbineClosedLoopGovernor(BAY_GOVERNOR_SPECS[BAY_IDS[0]])


def _step(gov, pc=PC, *, measured, reference=0.0, mfe=0.0, elapsed=3, noise=0.2, pid="A"):
    return gov.evaluate_position_control(
        measured_r=measured, reference_r=reference, max_favorable_r=mfe, elapsed_bars=elapsed,
        min_hold_bars=1, max_hold_bars=60, hard_stop_r=-1.0, trade_target_r=3.0, position_id=pid,
        path_noise_r=noise, position_control=pc)


# ------------------------------------------------------------------ control law

def test_two_rate_leaky_integral_applies_ki_once_and_is_clamped():
    gov = _gov()
    kp, ki, kd = gov.runtime_kp, gov.runtime_ki, gov.runtime_kd
    first = _step(gov, measured=-0.1, reference=0.0, noise=0.2)          # |e|=0.1 <= tau 0.2 -> slow
    assert first["v3"]["gamma"] == PC.gamma_slow
    assert first["integral_error"] == pytest.approx(0.1)                 # 0.98*0 + e
    assert first["derivative"] == 0.0                                    # first sample: no kick
    assert first["control_u"] == pytest.approx(kp * 0.1 + ki * 0.1)
    second = _step(gov, measured=-0.6, reference=0.0, noise=0.2)         # |e|=0.6 > tau -> fast
    assert second["v3"]["gamma"] == PC.gamma_fast
    assert second["integral_error"] == pytest.approx(0.85 * 0.1 + 0.6)
    assert second["derivative"] == pytest.approx(0.6 - 0.1)
    assert second["control_u"] == pytest.approx(kp * 0.6 + ki * second["integral_error"] + kd * 0.5)
    for _ in range(200):
        out = _step(gov, measured=-0.9, reference=0.5, noise=0.2)
    assert out["integral_error"] == pytest.approx(gov.runtime_integral_clamp)


def test_trailing_waits_for_grace_and_mfe_activation_and_never_crosses_the_plan_stop():
    gov = _gov()
    early = _step(gov, measured=0.5, mfe=0.6, elapsed=1)                 # inside grace
    assert early["protected_r_floor"] == -1.0 and not early["v3"]["trailing_active"]
    low_mfe = _step(_gov(), measured=0.1, mfe=0.2, elapsed=5)            # below 0.30R activation
    assert low_mfe["protected_r_floor"] == -1.0
    active = _step(gov, measured=0.5, mfe=0.6, elapsed=3)
    assert active["v3"]["trailing_active"] and active["v3"]["activated_this_bar"]
    assert active["derivative"] == 0.0                                   # activation sample: no kick
    assert active["protected_r_floor"] == pytest.approx(0.6 - active["v3"]["gap_r"])
    losing = _step(_gov(), measured=-0.8, mfe=0.35, elapsed=4, noise=0.05)
    assert losing["protected_r_floor"] >= -1.0


def test_gap_is_tanh_bounded_and_floored_by_noise_and_minimum():
    for u_sign, measured in ((1, -0.9), (-1, 0.9)):
        out = _step(_gov(), measured=measured, reference=0.0, mfe=1.0, elapsed=3, noise=0.01)
        gap = out["v3"]["gap_r"]
        assert PC.base_gap_r * (1 - PC.kappa) - 1e-12 <= gap <= PC.base_gap_r * (1 + PC.kappa) + 1e-12
        assert (gap < PC.base_gap_r) == (u_sign > 0)                     # lagging tightens, leading widens
    noisy = _step(_gov(), measured=-0.9, reference=0.0, mfe=1.0, elapsed=3, noise=0.6)
    assert noisy["v3"]["gap_r"] == pytest.approx(0.6)                    # one bar of noise
    tight = replace(PC, kappa=0.39, base_gap_r=0.2, minimum_gap_r=0.18)
    floored = _step(_gov(), tight, measured=-0.9, reference=0.0, mfe=1.0, elapsed=3, noise=0.01)
    assert floored["v3"]["gap_r"] == pytest.approx(0.18)


def test_floor_is_one_way_and_there_is_no_binary_path_error_exit():
    gov = _gov()
    floors = []
    for mfe, measured in ((0.8, 0.7), (0.8, 0.1), (0.9, 0.85), (0.9, 0.5)):
        floors.append(_step(gov, measured=measured, reference=2.0, mfe=mfe, elapsed=5)["protected_r_floor"])
    assert floors == sorted(floors)
    far_behind = _step(_gov(), measured=-0.5, reference=1.5, mfe=0.0, elapsed=10, noise=0.05)
    assert far_behind["action"] == "HOLD"                                # V2 would exit on path error


def test_position_control_validation():
    for bad in (dict(kappa=0.0), dict(kappa=1.0), dict(gamma_fast=0.99), dict(minimum_gap_r=0.5),
                dict(grace_bars=-1), dict(tau_error_multiplier=0.0), dict(mfe_activation_r=float("nan"))):
        with pytest.raises(ValueError):
            replace(PC, **bad)
    with pytest.raises(ValueError):
        _gov().evaluate_position_control(
            measured_r=0.0, reference_r=0.0, max_favorable_r=0.0, elapsed_bars=1, min_hold_bars=1,
            max_hold_bars=60, hard_stop_r=-1.0, trade_target_r=3.0, position_control=PC)


def test_reference_path_applies_gamma_once():
    base = dict(symbol="X", side="BUY", entry_price=100.0, initial_risk=1.0, target_r=2.0,
                max_hold_bars=60, response_time_bars=10.0)
    linear = TradeReferencePath(**base)
    curved = TradeReferencePath(**base, curve_gamma=2.0)
    p1 = linear.progress(12)
    assert curved.progress(12) == pytest.approx(p1 ** 2)
    assert curved.expected_r(12) == pytest.approx(2.0 * p1 ** 2)
    assert curved.lower_bound_r(12) == pytest.approx(-1.0 + 3.0 * p1 ** 2)


# ------------------------------------------------------------------ engine mode + bridge

def _replay(mode="legacy", shadow=None, n=240, seed=3):
    grid = pd.date_range("2024-02-28 00:00", periods=5 * 96, freq="15min", tz="Asia/Kolkata")
    nifty = [21000.0 * (1 + 0.001 * ((i % 7) - 3)) for i in range(len(grid))]
    provider = SealedGridContextProvider(pd.DataFrame({"timestamp": grid, "close": nifty}),
                                         pd.DataFrame({"timestamp": grid, "close": 15.0}))
    orch = Engine(["INFY"], grid_context_provider=provider,
                  real_plant_dcs=CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:"),
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                  governor_authority="full", governor_position_control=mode)
    orch.pa.evaluate = lambda snapshot, cfg: (signal(), [])
    orch.id_box.evaluate = lambda *a, **kw: (IDDecision(True, "fixture", .8, 2, .6), [])
    orch.id_box._current_regime = lambda *a: "calm"
    orch.exit_shadow = shadow
    i = np.arange(n)
    close = 1000.0 + 0.05 * i + 0.8 * np.sin(i / 3.0) + np.cumsum(np.random.default_rng(seed).normal(0, 0.6, n))
    open_ = np.concatenate([[close[0]], close[:-1]])
    frame = pd.DataFrame({
        "timestamp": pd.date_range("2024-03-01 09:30", periods=n, freq="min", tz="Asia/Kolkata"),
        "open": open_, "close": close, "high": np.maximum(open_, close) + 0.3,
        "low": np.minimum(open_, close) - 0.3, "volume": 1000.0})
    return orch, orch.run({"INFY": frame}, warmup=30)


def test_v3_mode_requires_full_authority_and_is_reported():
    with pytest.raises(ValueError):
        Engine(["INFY"], governor_position_control="closed_loop_v3")
    with pytest.raises(ValueError):
        Engine(["INFY"], governor_position_control="v4")
    _, report = _replay("closed_loop_v3")
    assert report["governor_authority"]["position_control"] == "closed_loop_v3"
    assert not any("GOVERNOR_PATH_ERROR" in k for k in report["governor_authority"]["position_decisions"])


@pytest.mark.parametrize("seed", [3, 11])
def test_legacy_shadow_reproduces_every_real_exit_and_is_passive(seed):
    orch, _ = _replay(n=10)                                               # warm config for the policy
    from revision5.governor_authority import position_control_v3_from_config
    bridge = ShadowExitBridge({"legacy": None, "v3": position_control_v3_from_config(orch.config)})
    _, observed = _replay(shadow=bridge, seed=seed)
    _, plain = _replay(seed=seed)
    assert observed["trades"] == plain["trades"]                          # the observer changes nothing
    assert observed["trades"], "fixture produced no trades"
    check = validate_legacy(observed["trades"], bridge.closed["legacy"])
    assert check["exact"], check
    assert {t["trade_id"] for t in bridge.closed["v3"]} == {t["trade_id"] for t in observed["trades"]}
    for t in bridge.closed["v3"]:
        assert math.isfinite(t["net_pnl"]) and t["realized_r"] is not None


def test_bridge_pairing_and_comparison_metrics_are_strict_and_consistent():
    import importlib.util
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]

    def load(rel):
        spec = importlib.util.spec_from_file_location(rel.stem, root / rel)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    bridge_mod = load(Path("scripts/protocol_v3/paired_v2_v3_bridge.py"))
    compare_mod = load(Path("scripts/protocol_v3/compare_baseline_vs_v3.py"))
    orch, _ = _replay(n=10)
    from revision5.governor_authority import position_control_v3_from_config
    bridge = ShadowExitBridge({"legacy": None, "v3": position_control_v3_from_config(orch.config)})
    _, report = _replay(shadow=bridge, seed=11)
    paired = bridge_mod.pair(bridge.closed["legacy"], bridge.closed["v3"])
    s = paired["summary"]
    assert s["paired_trades"] == len(report["trades"])
    assert s["improved"] + s["worsened"] + s["unchanged"] == s["paired_trades"]
    for row in paired["rows"]:
        assert row["delta_r"] == pytest.approx(row["v3"]["realized_r"] - row["v2"]["realized_r"])
    summary = bridge_mod.summarize(bridge.closed["v3"])
    json.dumps(bridge_mod._sanitize({"s": summary, "p": paired}), allow_nan=False)
    assert sum(summary["exit_classes"].values()) == summary["trades"]
    result = {"stage": "A", "aggregate": {"score": -1.0, "mtm_max_drawdown_fraction": 0.01},
              "blocks": [{"block": 1, "trades": [dict(t, planned_stop_price=t["entry_price"] - 1.0
                                                      if t["side"] == "BUY" else t["entry_price"] + 1.0,
                                                      mfe_r=0.5) for t in report["trades"]]}]}
    row = compare_mod.evaluate(result)
    assert row["trades"] == len(report["trades"])
    assert row["net_pnl"] == pytest.approx(sum(t["net_pnl"] for t in report["trades"]))
    assert row["r_drawdown_depth"] >= 0.0


def test_stop_computed_on_bar_t_is_executable_only_from_bar_t_plus_1():
    """Causality: the V3 floor raised on completed bar t never fills inside bar t itself."""
    from types import SimpleNamespace
    from revision2_external.closed_loop_control import TradePathObservation
    from revision5.governor_authority import GovernorAuthorityConfig, BarTelemetry

    cfg = GovernorAuthorityConfig(
        z_window_bars=20, atr_bars=14, fsr_entry_threshold=0.6, fsr_exit_threshold=0.25,
        fsr_min_floor=0.15, fsrt_drawdown_span=0.1, fsrt_slope=1.0, fsra_base=1.0, fsra_slope=0.1,
        fsrs_warmup_bars=1, fsrs_floor=1.0, fsrm_manual_limit=1.0, vibration_damper_start=10.0,
        vibration_damper_gain=0.0, exhaust_spread_hold=5.0, exhaust_spread_trip=6.0, path_error_sigma=2.0)
    orch = SimpleNamespace(
        entry_decision_engine=SimpleNamespace(config=SimpleNamespace(force_close_time="15:10")),
        config=SimpleNamespace(require=lambda name: 0.5), safety_contract=SimpleNamespace(values={
            "safety_drawdown_halt_threshold": 0.5}), _current_drawdown=lambda: 0.0, _micom_trip=None,
        broker=SimpleNamespace(slippage_fraction=0.0), governor_config=cfg,
        _governor_conviction=lambda *a: 1.0, _bay_exhaust_spread={}, _current_bar_idx=0,
        _governor_session_bar=lambda *a: 10_000,
        _governor_telemetry={"INFY": BarTelemetry(True, "", atr=0.2, velocity=0.0)},
        closed_loop=SimpleNamespace(observe_trade_path=lambda snap, close, held: TradePathObservation(
            0.0, 0.0, -1.0, 0.0, False, -1.0)))
    bridge = ShadowExitBridge({"v3": PC})
    trade = {"trade_id": "t1", "side": "BUY", "entry_price": 100.0, "stop_price": 99.0, "target_price": 110.0,
             "quantity": 10, "minimum_hold_bars": 1, "maximum_hold_bars": 60,
             "entry_timestamp": "2024-03-01 10:00", "closed_loop": {"reference_path": {}}}
    bridge.on_entry(orch, "INFY", trade, entry_bar_idx=0)
    bar = lambda o, h, l, c: {"open": o, "high": h, "low": l, "close": c}
    ts = lambda m: pd.Timestamp(f"2024-03-01 10:{m:02d}")
    # bars 0..2: rally to a +1.0R excursion; trailing activates at bar 2 (grace 2) and ratchets.
    for k, b in enumerate((bar(100, 100.4, 99.9, 100.3), bar(100.3, 100.8, 100.2, 100.7),
                           bar(100.7, 101.0, 100.6, 100.9))):
        bridge.on_bar(orch, "INFY", ts(k), b, None, None, k, False)
    shadow = bridge._open["v3"]["t1"]
    stop_after_bar2 = shadow["governor_stop_price"]
    assert stop_after_bar2 > 99.0 and not bridge.closed["v3"]
    # bar 3 trades below that stop intrabar: it fills there (the stop was armed on bar 2).
    bridge.on_bar(orch, "INFY", ts(3), bar(100.9, 100.95, stop_after_bar2 - 0.05, 100.5), None, None, 3, False)
    closed = bridge.closed["v3"][0]
    assert closed["reason"] == "stop" and closed["exit_price"] == pytest.approx(stop_after_bar2)
    assert closed["exit_timestamp"] == str(ts(3))

    # Same path, but the bar that first raises the stop dips below the new level inside itself:
    # no fill on that bar -- the new level applies only from the next bar.
    bridge = ShadowExitBridge({"v3": PC})
    bridge.on_entry(orch, "INFY", trade, entry_bar_idx=0)
    for k, b in enumerate((bar(100, 100.4, 99.9, 100.3), bar(100.3, 100.8, 100.2, 100.7))):
        bridge.on_bar(orch, "INFY", ts(k), b, None, None, k, False)
    assert bridge._open["v3"]["t1"]["governor_stop_price"] == 99.0          # still in grace (bar 1)
    bridge.on_bar(orch, "INFY", ts(2), bar(100.7, 101.0, 99.95, 100.9), None, None, 2, False)
    raised = bridge._open["v3"]["t1"]["governor_stop_price"]
    assert raised > 99.95 and not bridge.closed["v3"]                        # dipped below it, no fill
