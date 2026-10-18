"""Single-symbol empirical audit of Engine B on INFY (native engine, no external harness).

Revision2ExternalEngineOrchestrator + CentralPlantMasterDCS, PAPER_APPLY / active_paper /
full governor authority, Rs 1,000,000 starting equity, max_loss_per_trade_rupees = Rs 5,000.

Data: the checksum-verified frozen 1-minute dataset (revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json)
and the verified NIFTY 50 / India VIX 15-minute grid feeds (--grid-manifest), loaded exactly as
scripts/run_r5_paper_replay.py loads them.  --synthetic-smoke replaces both with seeded synthetic
bars only to prove the script runs end to end; its P&L means nothing.

Costs: the engine books its frozen replay friction model (revision2/transaction_costs.leg_cost:
brokerage, exchange fee, sell-side STT) and fills every leg at a half-spread slippage band.  This
script additionally re-costs every fill at the statutory NSE equity-intraday rates below
(brokerage, STT, exchange transaction charge, SEBI fee, GST, stamp duty).  No broker network access.
"""
from __future__ import annotations

import argparse
from collections import Counter
import math
import os
from pathlib import Path
import sys
import time
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")

import numpy as np
import pandas as pd

SYMBOL = "INFY"
STARTING_EQUITY = 1_000_000.0
MAX_LOSS_PER_TRADE = 5_000.0
WARMUP = 60

# Statutory equity-intraday (MIS) charges per leg, as fractions of turnover.  Check against the
# current Zerodha / NSE schedule before relying on the statutory figures.
BROKERAGE_RATE, BROKERAGE_CAP = 0.0003, 20.0      # 0.03% or Rs 20 per executed order, lower
STT_SELL_RATE = 0.00025                           # 0.025% on the sell leg
EXCHANGE_RATE = 0.0000297                         # NSE transaction charge 0.00297%
SEBI_RATE = 10.0 / 1e7                            # Rs 10 per crore
GST_RATE = 0.18                                   # on brokerage + exchange + SEBI
STAMP_BUY_RATE = 0.00003                          # 0.003% on the buy leg


def statutory_leg(price: float, quantity: int, side: str) -> dict:
    turnover = float(price) * int(quantity)
    brokerage = min(BROKERAGE_CAP, BROKERAGE_RATE * turnover)
    exchange = EXCHANGE_RATE * turnover
    sebi = SEBI_RATE * turnover
    row = {
        "brokerage": brokerage,
        "stt": STT_SELL_RATE * turnover if side == "SELL" else 0.0,
        "exchange": exchange,
        "sebi": sebi,
        "gst": GST_RATE * (brokerage + exchange + sebi),
        "stamp": STAMP_BUY_RATE * turnover if side == "BUY" else 0.0,
    }
    row["total"] = sum(row.values())
    return row


# --------------------------------------------------------------------------- data
def load_real(args):
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest, verify_manifest
    from scripts.run_r5_paper_replay import load_verified_grid_feeds

    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
    data_dir = args.data_dir or manifest.data_dir
    # Every file is still checked against the manifest's SHA-256, so a mirror is accepted
    # only if it is byte-identical to the frozen dataset.
    verification = verify_manifest(manifest, data_dir=data_dir)
    if not verification.valid:
        raise SystemExit(f"Frozen dataset failed verification under {data_dir}: {verification.message}")
    feeds = load_verified_grid_feeds(args.grid_manifest)
    end = min(frame["timestamp"].max() for frame in feeds.values()).tz_convert("Asia/Kolkata").date()
    frame = MarketDataLoader(data_dir, synthetic_if_missing=False)._load_symbol_csv(SYMBOL)
    times = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
    frame = frame[times.dt.date <= end].reset_index(drop=True)
    if args.bars:
        frame = frame.tail(args.bars).reset_index(drop=True)
    source = f"frozen dataset {manifest.manifest_hash[:12]} at {data_dir}, grid {Path(args.grid_manifest).name}, through {end}"
    return frame, feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"], source


def load_synthetic(args):
    bars = args.bars or 2_000
    start = pd.Timestamp("2024-03-04 09:15", tz="Asia/Kolkata")
    rng = np.random.default_rng(11)
    stamps, day = [], start
    while len(stamps) < bars:              # 375 one-minute bars per NSE session
        stamps.extend(pd.date_range(day, periods=375, freq="min"))
        day += pd.Timedelta(days=1)
    stamps = pd.DatetimeIndex(stamps[:bars])
    regime = np.repeat(rng.choice([-1.5e-3, 0.0, 1.5e-3], size=bars // 120 + 1), 120)[:bars]
    close = 1500.0 * np.exp(np.cumsum(regime * 0.6 + rng.normal(0.0, 1.4e-3, bars)))
    open_ = np.concatenate([[1500.0], close[:-1]])
    wick = np.abs(rng.normal(0.0, 1.0e-3, bars)) * close
    frame = pd.DataFrame({"timestamp": stamps, "open": open_, "high": np.maximum(open_, close) + wick,
                          "low": np.minimum(open_, close) - wick, "close": close,
                          "volume": rng.lognormal(10.0, 0.4, bars)})
    grid_t = pd.date_range(start - pd.Timedelta(days=12), stamps[-1] + pd.Timedelta(hours=1), freq="15min")
    nifty = pd.DataFrame({"timestamp": grid_t, "close": 22000.0 * np.exp(np.cumsum(rng.normal(0, 6e-4, len(grid_t))))})
    vix = pd.DataFrame({"timestamp": grid_t, "close": 14.0 + rng.normal(0.0, 0.2, len(grid_t))})
    return frame, nifty, vix, "SYNTHETIC SMOKE FEED (seeded) -- not market data, P&L is meaningless"


# --------------------------------------------------------------------------- run
def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR"),
                        help="directory holding NSE_<SYMBOL>_minute_*.csv (default: $DATA_DIR, else the manifest path)")
    parser.add_argument("--grid-manifest", type=Path, default=ROOT / "local_workspace/records/grid-manifest-local.json")
    parser.add_argument("--bars", type=int, default=0, help="replay only the last N bars (0 = all available)")
    parser.add_argument("--synthetic-smoke", action="store_true", help="seeded synthetic feed; proves the script runs")
    args = parser.parse_args()

    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    from revision5.topology import bay_for_symbol

    frame, nifty, vix, source = (load_synthetic if args.synthetic_smoke else load_real)(args)
    plant = CentralPlantMasterDCS(total_capital=STARTING_EQUITY, db_path=":memory:")
    calls, pressures, bound_bays, load_shed = Counter(), [], Counter(), Counter()
    for name in ("evaluate_entry", "on_trade_filled", "evaluate_position_control", "step_bar"):
        original = getattr(plant, name)

        def spy(*a, __name=name, __original=original, **kw):
            calls[__name] += 1
            result = __original(*a, **kw)
            if __name == "step_bar":
                pressures.append(result["thermal"].steam_pressure_pu)
            elif __name == "on_trade_filled":
                bound_bays[plant.bound_positions[kw["position_id"]]["bay_id"]] += 1
            return result
        setattr(plant, name, spy)

    orch = Revision2ExternalEngineOrchestrator(
        [SYMBOL], grid_context_provider=SealedGridContextProvider(nifty, vix), real_plant_dcs=plant,
        plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", governor_authority="full",
        starting_equity=STARTING_EQUITY, telemetry_mode="compact")
    # max_loss_per_trade_rupees is registry-locked (non-calibratable); verify, never override.
    if float(orch.config.require("max_loss_per_trade_rupees")) != MAX_LOSS_PER_TRADE:
        raise SystemExit("registry max_loss_per_trade_rupees is not Rs 5,000")

    exit_detail = {}                      # trade_id -> stop ratcheted above the plan stop at exit
    original_exit = orch._execute_exit

    def recording_exit(symbol, timestamp, trade, exit_price, reason):
        side = 1.0 if trade["side"] == "BUY" else -1.0
        ratcheted = side * (float(trade.get("governor_stop_price", trade["stop_price"])) - float(trade["stop_price"])) > 1e-9
        exit_detail[trade.get("trade_id")] = {"ratcheted": ratcheted, "quantity": int(trade["quantity"])}
        return original_exit(symbol, timestamp, trade, exit_price, reason)
    orch._execute_exit = recording_exit

    original_position_step = orch._governor_position_step

    def recording_position_step(*a, **kw):
        result = original_position_step(*a, **kw)
        load_shed[bool(result.get("load_shed"))] += 1
        return result
    orch._governor_position_step = recording_position_step

    print(f"INFY single-symbol audit  |  {source}")
    print(f"bars {len(frame):,}  ({frame['timestamp'].iloc[0]} .. {frame['timestamp'].iloc[-1]}), warm-up {WARMUP}, "
          f"equity Rs {STARTING_EQUITY:,.0f}, max_loss_per_trade_rupees Rs "
          f"{float(orch.config.require('max_loss_per_trade_rupees')):,.0f}, bay {bay_for_symbol(SYMBOL)}")
    started = time.time()
    report = orch.run({SYMBOL: frame}, warmup=WARMUP)
    print(f"replay finished in {time.time() - started:,.0f}s\n")

    trades = report["trades"]
    slip = float(orch.broker.slippage_fraction)
    rows = []
    for t in trades:
        close_side = "SELL" if t["side"] == "BUY" else "BUY"
        legs = (statutory_leg(t["entry_price"], t["quantity"], t["side"]),
                statutory_leg(t["exit_price"], t["quantity"], close_side))
        statutory = {k: legs[0][k] + legs[1][k] for k in legs[0]}
        # Fill prices already carry the half-spread band: fill = market x (1 +/- slip).
        slippage = sum(float(p) * int(t["quantity"]) * slip / (1.0 + (slip if s == "BUY" else -slip))
                       for p, s in ((t["entry_price"], t["side"]), (t["exit_price"], close_side)))
        rows.append({**t, "statutory": statutory, "slippage": slippage,
                     "net_statutory": float(t["pnl"]) - statutory["total"],
                     "notional": float(t["entry_price"]) * int(t["quantity"]),
                     **exit_detail.get(t.get("trade_id"), {"ratcheted": False})})

    print("== TRADE STATISTICS (net of the engine's booked costs)")
    if not rows:
        print("   no completed trades")
    else:
        net = np.array([r["net_pnl"] for r in rows])
        winners, losers = net[net > 0], net[net <= 0]
        gross_gain, gross_loss = winners.sum(), -losers.sum()
        print(f"   completed trades       {len(rows):>12,}")
        print(f"   win rate               {100.0 * len(winners) / len(rows):>11.2f}%")
        print(f"   profit factor          {(gross_gain / gross_loss) if gross_loss > 0 else math.inf:>12.3f}")
        print(f"   average winner         Rs {winners.mean() if len(winners) else 0.0:>9,.2f}")
        print(f"   average loser          Rs {losers.mean() if len(losers) else 0.0:>9,.2f}")
        worst = float(net.min())
        worst_gross = min(float(r["pnl"]) for r in rows)
        print(f"   maximum single loss    Rs {worst:>9,.2f} net / Rs {worst_gross:,.2f} gross  "
              f"(<= Rs {MAX_LOSS_PER_TRADE:,.0f} ceiling: {'YES' if -worst <= MAX_LOSS_PER_TRADE else 'NO -- gap/slippage beyond the stop'})")

        print("\n== FRICTION ACCOUNTING")
        gross = sum(float(r["pnl"]) for r in rows)
        engine_cost = sum(float(r["costs"]) for r in rows)
        statutory_total = sum(r["statutory"]["total"] for r in rows)
        slippage_total = sum(r["slippage"] for r in rows)
        gross_profits = sum(float(r["pnl"]) for r in rows if float(r["pnl"]) > 0)
        turnover = sum(r["notional"] for r in rows)
        print(f"   gross P&L (after half-spread slippage in fills)   Rs {gross:>12,.2f}")
        print(f"   half-spread slippage inside fills                 Rs {slippage_total:>12,.2f}   ({slip * 1e4:.2f} bps/leg)")
        print(f"   engine-booked costs (frozen leg_cost model)       Rs {engine_cost:>12,.2f}")
        print(f"   net realized P&L (engine)                         Rs {gross - engine_cost:>12,.2f}")
        parts = {k: sum(r["statutory"][k] for r in rows) for k in ("brokerage", "stt", "exchange", "sebi", "gst", "stamp")}
        print(f"   statutory fees (re-costed)                        Rs {statutory_total:>12,.2f}   "
              + " ".join(f"{k}={v:,.0f}" for k, v in parts.items()))
        print(f"   net realized P&L (statutory)                      Rs {gross - statutory_total:>12,.2f}")
        print(f"   friction drag: fees / gross profits               {100 * engine_cost / gross_profits if gross_profits else math.inf:>7.2f}% engine, "
              f"{100 * statutory_total / gross_profits if gross_profits else math.inf:.2f}% statutory, "
              f"{100 * (statutory_total + slippage_total) / gross_profits if gross_profits else math.inf:.2f}% statutory+slippage")
        print(f"   average net bps per trade (on entry notional)     {1e4 * (gross - engine_cost) / turnover:>7.2f} engine, "
              f"{1e4 * (gross - statutory_total) / turnover:.2f} statutory")

    print("\n== CCPP PLANT TELEMETRY")
    wiring = report["plant_dcs_wiring"]
    print(f"   evaluate_entry()            {calls['evaluate_entry']:>10,}")
    print(f"   on_trade_filled()           {calls['on_trade_filled']:>10,}")
    print(f"   evaluate_position_control() {calls['evaluate_position_control']:>10,}")
    print(f"   step_bar()                  {calls['step_bar']:>10,}")
    print(f"   fills bound to GTG2         {bound_bays.get('GTG2_TECH_TELECOM', 0):>10,}   (all bays: {dict(bound_bays)})")
    if pressures:
        print(f"   HRSG drum pressure          min {min(pressures):.3f} pu, max {max(pressures):.3f} pu")
    print(f"   governor load-shed bars     {load_shed[True]:>10,}   of {sum(load_shed.values()):,} position evaluations")
    print(f"   plant trip exits armed      {wiring['trip_exits_armed']}")
    print(f"   plant entry rejections      {wiring['entry_rejection_reasons']}")
    print(f"   MiCOM trip bars             {report['micom']['trip_bar_counts']}")

    print("\n== EXIT REASON BREAKDOWN")
    categories = Counter()
    for r in rows:
        reason = r["reason"]
        if reason in ("target", "target_gap"):
            categories["Target Hit"] += 1
        elif reason in ("stop", "stop_gap"):
            categories["Trailing Ratchet" if r["ratcheted"] else "Hard Stop"] += 1
        elif reason.startswith("governor_exit"):
            categories["Governor Conviction Drop"] += 1
        elif reason == "max_hold":
            categories["Max Hold Timeout"] += 1
        elif reason.startswith(("plant_trip", "micom_trip")):
            categories["Plant Trip"] += 1
        else:
            categories[f"Other ({reason})"] += 1
    for name in ("Target Hit", "Trailing Ratchet", "Governor Conviction Drop", "Max Hold Timeout", "Plant Trip", "Hard Stop"):
        print(f"   {name:<26} {categories.pop(name, 0):>8,}")
    for name, count in sorted(categories.items()):
        print(f"   {name:<26} {count:>8,}")
    print(f"   raw reasons: {dict(Counter(r['reason'] for r in rows))}")


if __name__ == "__main__":
    main()
