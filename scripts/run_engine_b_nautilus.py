"""Engine B: run the shared R5 CCPP engine inside a NautilusTrader backtest (offline only).

    # real frozen data (operator machine), INFY, last N bars
    PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --symbols INFY --bars 1200
    # seeded synthetic smoke feed (no market data needed; P&L meaningless)
    PYTHONPATH="$PWD" python scripts/run_engine_b_nautilus.py --synthetic-smoke --bars 900

Writes a machine-readable JSON report.  No broker network access, no Kite, no live trading.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np
import pandas as pd


def load_real(symbols, bars, data_dir, grid_manifest):
    from market_data_loader import MarketDataLoader
    from revision2.dataset_manifest import DatasetManifest, verify_manifest
    from scripts.run_r5_paper_replay import load_verified_grid_feeds

    manifest = DatasetManifest.load(str(ROOT / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"))
    data_dir = data_dir or manifest.data_dir
    verification = verify_manifest(manifest, data_dir=data_dir)
    if not verification.valid:
        raise SystemExit(f"Frozen dataset failed verification under {data_dir}: {verification.message}")
    feeds = load_verified_grid_feeds(grid_manifest)
    end = min(frame["timestamp"].max() for frame in feeds.values()).tz_convert("Asia/Kolkata").date()
    loader = MarketDataLoader(data_dir, synthetic_if_missing=False)
    if symbols == ["ALL"]:
        symbols = sorted(f.symbol for f in manifest.files)
    unknown = set(symbols) - {f.symbol for f in manifest.files}
    if unknown:
        raise SystemExit(f"Symbols outside the verified frozen universe: {sorted(unknown)}")
    data = {}
    for symbol in symbols:
        frame = loader._load_symbol_csv(symbol)
        times = pd.to_datetime(frame["timestamp"], utc=True).dt.tz_convert("Asia/Kolkata")
        data[symbol] = frame[times.dt.date <= end].tail(bars).reset_index(drop=True)
        if len(data[symbol]) <= 60:
            raise SystemExit(f"Insufficient real data for {symbol}")
    source = f"frozen dataset {manifest.manifest_hash[:12]} at {data_dir}, grid {Path(grid_manifest).name}, through {end}"
    return data, feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"], source


def load_synthetic(symbols, bars):
    """Seeded regime feed on the NSE 0.05 tick grid; full 375-bar sessions."""
    start = pd.Timestamp("2024-03-04 09:15", tz="Asia/Kolkata")
    stamps, day = [], start
    while len(stamps) < bars:
        if day.weekday() < 5:
            stamps.extend(pd.date_range(day, periods=375, freq="min"))
        day += pd.Timedelta(days=1)
    stamps = pd.DatetimeIndex(stamps[:bars])
    data = {}
    for k, symbol in enumerate(symbols):
        rng = np.random.default_rng(11 + k)
        regime = np.repeat(rng.choice([-1.8e-3, 0.0, 1.8e-3], size=bars // 45 + 1), 45)[:bars]
        close = 1500.0 * np.exp(np.cumsum(regime + rng.normal(0.0, 1.5e-3, bars)))
        open_ = np.concatenate([[1500.0], close[:-1]])
        wick = np.abs(rng.normal(0.0, 1.0e-3, bars)) * close * 1.4
        tick = lambda x: np.round(np.asarray(x) / 0.05) * 0.05
        frame = pd.DataFrame({"timestamp": stamps, "open": tick(open_), "close": tick(close)})
        frame["high"] = tick(np.maximum(open_, close) + wick)
        frame["low"] = tick(np.minimum(open_, close) - wick)
        frame["high"] = frame[["open", "high", "close"]].max(axis=1)
        frame["low"] = frame[["open", "low", "close"]].min(axis=1)
        frame["volume"] = rng.lognormal(10.0, 0.4, bars) * 1.5
        data[symbol] = frame[["timestamp", "open", "high", "low", "close", "volume"]]
    grid_t = pd.date_range(start - pd.Timedelta(days=12), stamps[-1] + pd.Timedelta(hours=1), freq="15min")
    rng = np.random.default_rng(7)
    nifty = pd.DataFrame({"timestamp": grid_t, "close": 22000.0 * np.exp(np.cumsum(rng.normal(0, 6e-4, len(grid_t))))})
    vix = pd.DataFrame({"timestamp": grid_t, "close": 14.0 + rng.normal(0.0, 0.2, len(grid_t))})
    return data, nifty, vix, "SYNTHETIC SMOKE FEED (seeded) -- not market data, P&L is meaningless"


def main() -> int:
    from runtime.live_trading_guard import require_live_trading_disabled
    require_live_trading_disabled()
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--symbols", default="INFY", help="comma list, or ALL for the 48-symbol frozen universe")
    parser.add_argument("--bars", type=int, default=1200)
    parser.add_argument("--warmup", type=int, default=60)
    parser.add_argument("--synthetic-smoke", action="store_true")
    parser.add_argument("--data-dir", default=os.environ.get("DATA_DIR"))
    parser.add_argument("--grid-manifest", type=Path, default=ROOT / "local_workspace/records/grid-manifest-local.json")
    parser.add_argument("--governor-authority", choices=["full", "advisory"], default="full")
    parser.add_argument("--report", type=Path, default=ROOT / "local_workspace/validation/engine_b/engine_b_report.json")
    from r5_authority_cli import add_authority_arguments, authority_kwargs
    add_authority_arguments(parser)
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    if args.bars <= args.warmup:
        parser.error("--bars must exceed --warmup")

    from engine_b.runner import run_engine_b

    if args.synthetic_smoke:
        data, nifty, vix, source = load_synthetic(symbols, args.bars)
    else:
        data, nifty, vix, source = load_real(symbols, args.bars, args.data_dir, args.grid_manifest)
    report = run_engine_b(data, nifty, vix, warmup=args.warmup,
                          orchestrator_kwargs={**authority_kwargs(args), "governor_authority": args.governor_authority})
    report["source"] = source
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2, default=str))

    o, acct, led, rec = report["orders"], report["nautilus_accounting"], report["r5_ledger"], report["reconciliation"]
    print(f"ENGINE B (Nautilus native execution)  {source}")
    print(f"  bars received {report['bars_received']}  decision ticks {report['decision_ticks']}  "
          f"max decision lag {report['max_decision_lag_ns']} ns")
    print(f"  orders submitted {o['submitted']} (entry {o['entry_submitted']}, exit {o['exit_submitted']})  "
          f"accepted {o['accepted']}  rejected {o['rejected']}  canceled {o['canceled']}  "
          f"fill events {o['fill_events']}  partial entry/exit {o['entry_partial_fills']}/{o['exit_partial_fills']}  "
          f"positions closed {o['positions_closed']}  inflight at end {o['inflight_at_end']}")
    print(f"  Nautilus: net P&L {acct['net_pnl']:.2f}  gross realized {acct['gross_realized_closed_positions']:.2f}  "
          f"commissions {acct['commissions']}  open positions {acct['open_positions']}")
    print(f"  R5 ledger: trades {led['completed_trades']}  gross {led['gross_pnl']:.2f}  costs {led['costs']:.2f}  "
          f"net {led['net_pnl']:.2f}  exits {led['exit_reasons']}")
    print(f"  reconciliation: checks {rec['checks']}  breaks {len(rec['breaks'])}  "
          f"gross mismatch {rec['ledger_gross_error']}")
    print(f"  report: {args.report}")
    return 1 if rec["breaks"] or rec["ledger_gross_error"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
