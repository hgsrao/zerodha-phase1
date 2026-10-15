"""Direct single-signal logical verification through Engine A and Engine B.

Engine A: revision2.portfolio_orchestrator.Revision2PortfolioOrchestrator (in-house boxes).
Engine B: revision2_external.orchestrator.Revision2ExternalEngineOrchestrator driving the native
          revision5 CentralPlantMasterDCS (PAPER_APPLY, active_paper, full governor authority).

Both engines replay the same 100-bar, 2-symbol synthetic feed (seeded random walk with drift
and positive range; 60 warm-up bars, then a directional regime expansion).  Every component is wrapped pass-through: the wrappers
record calls and return the original result unchanged.  No broker network access.
"""
from __future__ import annotations

import argparse
import math
from collections import Counter, defaultdict
from pathlib import Path
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

SYMBOLS = ("INFY", "TITAN")
BARS, WARMUP = 100, 60
START = pd.Timestamp("2024-03-04 09:15", tz="Asia/Kolkata")
# Post-warm-up directional regime expansion: steeper drift, wider bars (ATR expansion) and
# heavier volume confirming the move.  Warm-up bars keep the baseline regime.
BASE_DRIFT, TREND_DRIFT = 1.2e-3, 2.0e-3
TREND_RANGE_MULT, TREND_VOLUME_MULT = 1.6, 2.0


def feed(seed: int):
    data = {}
    trend = np.arange(BARS) >= WARMUP
    for k, symbol in enumerate(SYMBOLS):
        rng = np.random.default_rng(seed + k)
        returns = np.where(trend, TREND_DRIFT, BASE_DRIFT) + rng.normal(0.0, 1.6e-3, BARS)
        close = 1000.0 * np.exp(np.cumsum(returns))
        open_ = np.concatenate([[1000.0], close[:-1]])
        wick = np.abs(rng.normal(0.0, 1.2e-3, BARS)) * close * np.where(trend, TREND_RANGE_MULT, 1.0)
        data[symbol] = pd.DataFrame({
            "timestamp": pd.date_range(START, periods=BARS, freq="min"),
            "open": open_, "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick, "close": close,
            "volume": rng.lognormal(10.0, 0.4, BARS) * np.where(trend, TREND_VOLUME_MULT, 1.0)})
    return data


def spy(obj, name, log, label):
    original = getattr(obj, name)

    def wrapper(*args, **kwargs):
        result = original(*args, **kwargs)
        log.append((label, args, kwargs, result))
        return result
    setattr(obj, name, wrapper)


def verdict(ok: bool) -> str:
    return "PASS" if ok else "FAIL"


# --------------------------------------------------------------------------- Engine A
ENGINE_A_BOXES = [  # (label, attribute, method) in per-candidate execution order
    ("B01 DataIngestionBox.admit", "data_ingestion", "admit"),
    ("B02 L2DataCertifierBox.certify", "l2_certifier", "certify"),
    ("B03 UnifiedExecutionBox.check_window", "unified_execution", "check_window"),
    ("B04 PredictiveAnalyticsBox.evaluate", "pa", "evaluate"),
    ("B05 IntelligentDiscriminationBox.evaluate", "id_box", "evaluate"),
    ("B06 ModelPredictiveControlBox.build_plan", "mpc", "build_plan"),
    ("B07 SafetyGatesTargetBox.evaluate_pre_sizing", "safety_gates_target", "evaluate_pre_sizing"),
    ("B08 PositionManagerBox.size", "position_manager", "size"),
    ("B09 P01DBox.create_order", "p01d", "create_order"),
    ("B10 PaperBrokerAdapter.place_order", "broker", "place_order"),
]


def engine_a(data) -> bool:
    from revision2.portfolio_orchestrator import Revision2PortfolioOrchestrator
    print("=" * 78)
    print("ENGINE A  revision2.Revision2PortfolioOrchestrator(starting_equity=1_000_000)")
    print("=" * 78)
    orch = Revision2PortfolioOrchestrator(list(SYMBOLS), starting_equity=1_000_000)
    log = []
    for label, attr, method in ENGINE_A_BOXES:
        spy(getattr(orch, attr), method, log, label)
    spy(orch.safety_gates_target, "evaluate_post_sizing", log, "B07 SafetyGatesTargetBox.evaluate_post_sizing")
    spy(orch.entry_decision_engine, "evaluate", log, "GATES EntryDecisionEngine.evaluate")
    report = orch.run({s: f.copy() for s, f in data.items()}, warmup=WARMUP)

    counts = Counter(label for label, *_ in log)
    print(f"[A1] replay: {report['bars_processed']} symbol-bars after {WARMUP}-bar warm-up")
    for label, *_ in ENGINE_A_BOXES:
        print(f"     {label:<48} calls={counts[label]}")

    fills = [(i, row) for i, row in enumerate(log) if row[0].startswith("B10") and row[3].get("passed")]
    sequential = False
    gate12 = None
    order_ok = False
    if fills:
        index, (_, args, kwargs, result) = fills[0]
        symbol = kwargs.get("symbol")
        # Walk back from the first fill to that candidate's B01 call: the box order on that path.
        start = max(i for i in range(index) if log[i][0].startswith("B01")
                    and (log[i][1][:1] == (symbol,) or log[i][2].get("symbol") == symbol))
        path = [row[0] for row in log[start:index + 1]
                if row[0][:3] in {b[0][:3] for b in ENGINE_A_BOXES}]
        # First appearance of each box must run B01..B10 in order (B07 legitimately runs a
        # second time, after sizing, as the post-sizing safety pass).
        first_seen = list(dict.fromkeys(int(p[1:3]) for p in path))
        sequential = first_seen == list(range(1, 11))
        print(f"[A2] first filled candidate {symbol}: box path {' -> '.join(p[:3] for p in path)}")
        print(f"     Box 1..Box 10 executed sequentially on that candidate: {'YES' if sequential else 'NO'}")
        gate_rows = [row for row in log[start:index] if row[0].startswith("GATES")]
        decisions = gate_rows[-1][3]["decisions"] if gate_rows else []
        gate12 = next((d for d in decisions if d.gate_name == "Gate12StrategySignals"), None)
        print(f"[A3] Gate12StrategySignals: {'PASS' if gate12 and gate12.passed else 'FAIL'}"
              f" ({gate12.reason if gate12 else 'not evaluated'}); all gates passed: "
              f"{gate_rows[-1][3]['passed'] if gate_rows else False}")
        sized = [row for row in log[start:index] if row[0].startswith("B08")][-1][3][0]
        order = [row for row in log[start:index] if row[0].startswith("B09")][-1][3][0]
        order_ok = bool(order) and sized > 0 and result["filled_quantity"] > 0
        print(f"[A4] order generated: {order.side} {order.quantity} {order.symbol} {order.order_type}; "
              f"sized qty={sized}; filled {result['filled_quantity']} @ {result['filled_price']:.2f}")
    else:
        print("[A2] no candidate reached a fill")
    print(f"[A5] funnel: gates_evaluated={report['gates_evaluated']} gates_passed={report['gates_passed']} "
          f"orders_submitted={report['orders_submitted']} fills={report['fills']} "
          f"completed_trades={report['completed_trades']} net_pnl={report['net_pnl']:.2f}")
    ok = bool(fills) and sequential and bool(gate12 and gate12.passed) and order_ok
    print(f"ENGINE_A_LOGICAL_FLOW: {verdict(ok)}\n")
    return ok


# --------------------------------------------------------------------------- Engine B
def engine_b(data) -> bool:
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS

    print("=" * 78)
    print("ENGINE B  revision2_external orchestrator + revision5 CentralPlantMasterDCS")
    print("          plant_control_mode=PAPER_APPLY closed_loop_mode=active_paper governor_authority=full")
    print("=" * 78)
    times = pd.date_range(START - pd.Timedelta(days=12), START + pd.Timedelta(hours=8), freq="15min")
    rng = np.random.default_rng(7)
    provider = SealedGridContextProvider(
        pd.DataFrame({"timestamp": times, "close": 22000.0 * np.exp(np.cumsum(rng.normal(0, 6e-4, len(times))))}),
        pd.DataFrame({"timestamp": times, "close": 14.0 + rng.normal(0.0, 0.2, len(times))}))
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")
    log = []
    for name in ("evaluate_entry", "on_trade_filled", "evaluate_position_control", "step_bar", "release_position"):
        spy(plant, name, log, name)
    orch = Revision2ExternalEngineOrchestrator(
        list(SYMBOLS), grid_context_provider=provider, real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", governor_authority="full")
    gate_failures = []
    original_gate = orch.entry_decision_engine.evaluate_pre_submit

    def gate(*args, **kwargs):
        result = original_gate(*args, **kwargs)
        if not result["passed"]:
            gate_failures.append(f"{kwargs.get('symbol')}: {result['gate']}: {result['reason']}")
        return result
    orch.entry_decision_engine.evaluate_pre_submit = gate
    report = orch.run({s: f.copy() for s, f in data.items()}, warmup=WARMUP)
    calls = Counter(label for label, *_ in log)
    print(f"[B1] replay: {report['bars_processed']} symbol-bars; plant calls {dict(calls)}")

    dispositions = Counter(row["execution_reason"] for row in report["entry_candidate_observations"]["rows"])
    print(f"[B1] candidate dispositions: {dict(dispositions)}")
    for decision in gate_failures:
        print(f"     entry_decision_gate rejection: {decision}")
    entries = [(kw, r) for label, _, kw, r in log if label == "evaluate_entry"]
    admitted = [(kw, r) for kw, r in entries if r.get("admitted")]
    reasons = Counter(str(r.get("reason")).split(":")[0] for kw, r in entries if not r.get("admitted"))
    print(f"[B2] plant.evaluate_entry(): fired {len(entries)}x, admitted {len(admitted)}x; "
          f"rejections {dict(reasons)}")
    for kw, r in admitted[:2]:
        print(f"     admitted {kw['side']} {kw['symbol']} @ bar {kw['bar_index']}: bay={r['bay_id']} "
              f"AVR qty={r['quantity']} dynamic_z={r['dynamic_z']:.3f} "
              f"HRSG allocation={r['hrsg_effective_allocation']:.0f}")

    fills = [(kw, r) for label, _, kw, r in log if label == "on_trade_filled"]
    bound_ok = bool(fills)
    for kw, accepted in fills:
        event = next(e for e in orch.controller_telemetry
                     if e["event_type"] == "PLANT_FILL_BOUND" and e["trade_id"] == kw["position_id"])
        bound_ok &= accepted and event["bay_id"] == next(
            r["bay_id"] for k, r in admitted if k["symbol"] == kw["symbol"])
        print(f"[B3] plant.on_trade_filled(): {kw['position_id']} {kw['side']} {kw['quantity']} {kw['symbol']} "
              f"@ {kw['fill_price']:.2f} -> bound to bay {event['bay_id']} (latch accepted={accepted})")
    if not fills:
        print("[B3] plant.on_trade_filled(): no fill")

    tracked = defaultdict(list)
    for label, _, kw, r in log:
        if label == "evaluate_position_control":
            tracked[kw["position_id"]].append((kw["bar_index"], r.get("action"), r.get("reason")))
    track_ok = bool(tracked) and all(len({b for b, *_ in rows}) >= 2 for rows in tracked.values())
    for pid, rows in tracked.items():
        actions = Counter(f"{a}:{str(r).split(':')[0]}" for _, a, r in rows)
        print(f"[B4] plant.evaluate_position_control(): {pid} tracked on {len(rows)} bars "
              f"(plant bars {rows[0][0]}..{rows[-1][0]}) actions {dict(actions)}")
    if not tracked:
        print("[B4] plant.evaluate_position_control(): never called")

    thermal = [r["thermal"] for label, _, kw, r in log if label == "step_bar"]
    tau = plant.hrsg.pressure_time_constant_bars
    p_max = plant.hrsg.max_steam_pressure_pu
    residuals, prev = [], 1.0
    for t in thermal:
        if t.heat_input_pu is not None:
            residuals.append(abs(t.steam_pressure_pu - (prev + (min(p_max, max(0.0, t.heat_input_pu)) - prev) / tau)))
        prev = t.steam_pressure_pu
    heat = [t.heat_input_pu for t in thermal if t.heat_input_pu is not None]
    pressure = [t.steam_pressure_pu for t in thermal]
    corr = float(np.corrcoef(heat[:-1], np.diff(pressure[-len(heat):]))[0, 1]) if len(heat) > 2 else math.nan
    hrsg_ok = len(residuals) > 0 and max(residuals) < 1e-12 and len(set(pressure)) > 1
    print(f"[B5] HRSG: {len(thermal)} steps, {len(residuals)} with GTG exhaust heat; pressure "
          f"{min(pressure):.3f}..{max(pressure):.3f} pu, heat {min(heat):.3f}..{max(heat):.3f} pu; "
          f"max |p_t - (p_t-1 + (heat_t - p_t-1)/tau)| = {max(residuals):.2e}; "
          f"steam capacity factor {thermal[-1].steam_capacity_factor:.3f}")
    print(f"[B6] funnel: orders_submitted={report['orders_submitted']} fills={report['fills']} "
          f"completed_trades={report['completed_trades']} exits={dict(Counter(t['reason'] for t in report['trades']))} "
          f"net_pnl={report['net_pnl']:.2f}")
    ok = bool(admitted) and bound_ok and track_ok and hrsg_ok
    print(f"ENGINE_B_LOGICAL_FLOW: {verdict(ok)}")
    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()
    data = feed(args.seed)
    print(f"Feed: {', '.join(SYMBOLS)} x {BARS} one-minute bars from {START}, seed {args.seed}, "
          f"warm-up {WARMUP}; close range "
          + ", ".join(f"{s} {f['close'].min():.1f}..{f['close'].max():.1f}" for s, f in data.items()) + "\n")
    a, b = engine_a(data), engine_b(data)
    sys.exit(0 if a and b else 1)


if __name__ == "__main__":
    main()
