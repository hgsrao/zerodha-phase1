"""Instrumented PAPER_APPLY replay proving the native R5 plant drives execution.

Spies on the five native plant entry points while a real orchestrator replay runs
(no stubbed strategy box), then prints the call-count table and the observed state
changes of the GTG/STG bays and the HRSG.  Bars are deterministic synthetic OHLCV
(seeded) because the frozen 48-symbol dataset is not shipped with the repository.
No broker network access.
"""
from __future__ import annotations

import argparse
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd

from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
from revision5.topology import SYMBOL_TO_BAY

# Two members of every bay: GTG1, GTG2, CSTG1, CSTG2, BPSTG.
SYMBOLS = ["RELIANCE", "TATASTEEL", "INFY", "TCS", "HDFCBANK", "ICICIBANK",
           "MARUTI", "ITC", "SUNPHARMA", "CIPLA"]
SPIED = ("step_bar", "evaluate_entry", "on_trade_filled", "evaluate_position_control", "release_position")


def synthetic_session(symbol: str, start: pd.Timestamp, bars: int, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    drift = rng.choice([-1.0, 1.0]) * 1.2e-3
    returns = drift + rng.normal(0.0, 1.6e-3, bars)
    close = 1000.0 * np.exp(np.cumsum(returns))
    open_ = np.concatenate([[1000.0], close[:-1]])
    spread = np.abs(rng.normal(0.0, 1.2e-3, bars)) * close
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    minute = np.arange(bars)
    volume = rng.lognormal(10.0, 0.45, bars) * (1.0 + 0.8 * np.exp(-minute / 20.0))
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=bars, freq="min"),
                         "open": open_, "high": high, "low": low, "close": close, "volume": volume})


def grid_feeds(start: pd.Timestamp):
    times = pd.date_range(start - pd.Timedelta(days=12), start + pd.Timedelta(hours=8), freq="15min")
    rng = np.random.default_rng(7)
    nifty = 22000.0 * np.exp(np.cumsum(rng.normal(0.0, 6e-4, len(times))))
    return (pd.DataFrame({"timestamp": times, "close": nifty}),
            pd.DataFrame({"timestamp": times, "close": 14.0 + rng.normal(0.0, 0.2, len(times))}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bars", type=int, default=100, help="replayed bars after warm-up")
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--seed", type=int, default=11)
    args = parser.parse_args()

    start = pd.Timestamp("2024-03-04 09:15", tz="Asia/Kolkata")
    length = args.warmup + args.bars + 1          # +1: the last replayed bar needs a next open
    data = {s: synthetic_session(s, start, length, args.seed + i) for i, s in enumerate(SYMBOLS)}
    nifty, vix = grid_feeds(start)
    provider = SealedGridContextProvider(nifty, vix)
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:")

    calls: Counter = Counter()
    for name in SPIED:
        original = getattr(plant, name)

        def spy(*a, __name=name, __original=original, **kw):
            calls[__name] += 1
            return __original(*a, **kw)
        setattr(plant, name, spy)

    states = []
    original_step = plant.step_bar

    def recording_step(*a, **kw):
        result = original_step(*a, **kw)
        thermal = result["thermal"]
        states.append({
            "pressure": round(thermal.steam_pressure_pu, 6),
            "heat": None if thermal.heat_input_pu is None else round(thermal.heat_input_pu, 6),
            "steam_factor": round(thermal.steam_capacity_factor, 6),
            "reserve": round(plant.hrsg_reserve_cash, 2),
            **{f"{bay_id}.alloc": round(bay.effective_allocation, 2) for bay_id, bay in plant.bays.items()},
            **{f"{bay_id}.kp": round(bay.governor.runtime_kp, 6) for bay_id, bay in plant.bays.items()},
            **{f"{bay_id}.state": (bay.tripped_offline, bay.cooldown_remaining(max(0, plant.current_bar_index or 0)),
                                   bay.governor.last_error) for bay_id, bay in plant.bays.items()},
        })
        return result
    plant.step_bar = recording_step

    orch = Revision2ExternalEngineOrchestrator(
        SYMBOLS, grid_context_provider=provider, real_plant_dcs=plant, plant_control_mode="PAPER_APPLY",
        closed_loop_mode="active_paper", telemetry_mode="full", governor_authority="full")
    report = orch.run(data, warmup=args.warmup)

    wiring = report["plant_dcs_wiring"]
    print(f"\nInstrumented PAPER_APPLY replay: {len(SYMBOLS)} symbols x {args.bars} bars "
          f"({report['bars_processed']} symbol-bars), engine {wiring['engine_mode']}")
    print("\n  Native plant call             spy count   orchestrator count")
    print("  ---------------------------   ---------   ------------------")
    for name in SPIED:
        print(f"  {name:<27}   {calls[name]:>9}   {wiring['call_counts'].get(name, '-'):>18}")

    print("\n  Execution funnel")
    for key in ("orders_submitted", "fills", "completed_trades", "plant_entry_rejections", "exit_orders_submitted"):
        print(f"  {key:<27}   {report[key]:>9}")
    print(f"  plant entry rejections by reason: {wiring['entry_rejection_reasons']}")
    print(f"  plant trip exits armed:           {wiring['trip_exits_armed']}")
    print(f"  bound positions still open:       {wiring['bound_positions_open']}")
    bound = Counter(e["bay_id"] for e in orch.controller_telemetry if e["event_type"] == "PLANT_FILL_BOUND")
    print(f"  fills bound per bay:              {dict(bound)}")
    print(f"  exit reasons:                     {dict(Counter(t['reason'] for t in report['trades']))}")

    print("\n  State variable                       distinct values   first -> last")
    print("  ----------------------------------   ---------------   -------------")
    for key in states[0]:
        series = [row[key] for row in states]
        distinct = len({repr(v) for v in series})
        print(f"  {key:<34}   {distinct:>15}   {series[0]} -> {series[-1]}")


if __name__ == "__main__":
    main()
