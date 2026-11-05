"""Quarter-by-quarter offline backtest of the engine as it stands, fixed parameters, frozen data.

    LIVE_TRADING_ENABLED=False PYTHONPATH=. python scripts/run_quarterly_backtest.py \
        --data-dir <frozen_48_1min dir> --grid-manifest <grid-manifest-local.json> --out DIR \
        [--mode native|r7] [--workers 4] [--quarters 2023Q3,2023Q4,...] [--warmup 60]

Engine A (the shared core orchestrator with its paper broker, configured exactly as
scripts/run_engine_a_reference.py configures it) runs each calendar quarter of the frozen
48-symbol dataset in its own process, so memory is bounded by one quarter. ``--mode r7``
wraps the same engine in R7 with adaptation off. Every quarter starts from the same
capital; the engine squares off intraday positions each session, so quarters are
independent. The dataset is verified against its manifest and audited once, before any
quarter starts. The grid feeds are verified by checksum. Nothing connects to a broker.

The pass/fail criteria are fixed in PREREGISTERED_CRITERIA below and written to the output
before any quarter runs. ``--synthetic`` replays a seeded synthetic feed split into two
windows: a harness smoke only, whose verdict is always NOT_EVIDENCE.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import hashlib
import json
import os
import sys
import time
import warnings
from datetime import date
from pathlib import Path

warnings.filterwarnings("ignore")
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
STARTING_EQUITY = 1_000_000.0

PREREGISTERED_CRITERIA = {
    "minimum_total_trades": 30,
    "total_net_pnl_positive": True,
    "minimum_fraction_of_quarters_net_positive": 0.6,
    "average_net_pnl_per_trade_positive": True,
    "maximum_quarter_mtm_drawdown_fraction": 0.10,
    "last_four_quarters_combined_net_positive": True,
    "verdict_rule": ("INCONCLUSIVE if total trades < minimum_total_trades; otherwise PASS only if every "
                     "other criterion holds, else FAIL. Nifty buy-and-hold is reported, not a criterion."),
}


def quarters_between(first: date, last: date):
    out, y, q = [], first.year, (first.month - 1) // 3 + 1
    while date(y, 3 * q - 2, 1) <= last:
        start = max(first, date(y, 3 * q - 2, 1))
        end = min(last, date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1).fromordinal(
            date(y + (q == 4), 1 if q == 4 else 3 * q + 1, 1).toordinal() - 1))
        out.append((f"{y}Q{q}", start, end))
        y, q = (y + 1, 1) if q == 4 else (y, q + 1)
    return out


def _dig(x):
    return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()


def _window(frame, start, end, warmup):
    """Warmup bars strictly before ``start``, then every bar dated start..end (inclusive)."""
    import pandas as pd
    day = frame["timestamp"].dt.date
    first = int((day < start).sum())
    lo = max(0, first - warmup)
    hi = int((day <= end).sum())
    return frame.iloc[lo:hi].reset_index(drop=True), first - lo


def run_quarter(job):
    os.environ.setdefault("LIVE_TRADING_ENABLED", "False")
    assert os.environ["LIVE_TRADING_ENABLED"] == "False"
    import pandas as pd
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    t0 = time.time()
    label, start, end, warmup, mode = job["label"], job["start"], job["end"], job["warmup"], job["mode"]
    if job["synthetic"]:
        from engine_c.data_contract import load_synthetic
        from revision5.topology import SYMBOL_TO_BAY
        full, nifty, vix, _ = load_synthetic(sorted(SYMBOL_TO_BAY), job["synthetic_bars"], warmup)
        half = job["synthetic_bars"] // 2
        data = {s: (f.iloc[:half] if job["part"] == 0 else f.iloc[half - warmup:]).reset_index(drop=True)
                for s, f in full.items()}
    else:
        from engine_c.data_contract import normalise
        from scripts.run_r5_paper_replay import load_verified_grid_feeds
        data = {}
        for symbol, path in job["files"].items():
            frame, used_warmup = _window(normalise(pd.read_csv(path)), start, end, warmup)
            if used_warmup < warmup or len(frame) <= warmup:
                raise RuntimeError(f"{label}: {symbol} has {used_warmup} warmup bars / {len(frame)} rows")
            data[symbol] = frame
        feeds = load_verified_grid_feeds(job["grid_manifest"])
        cut = pd.Timestamp(end, tz="Asia/Kolkata") + pd.Timedelta(days=1)
        nifty = feeds["NIFTY_50_15MIN"][feeds["NIFTY_50_15MIN"]["timestamp"] < cut].reset_index(drop=True)
        vix = feeds["INDIA_VIX_15MIN"][feeds["INDIA_VIX_15MIN"]["timestamp"] < cut].reset_index(drop=True)
    kwargs = dict(grid_context_provider=SealedGridContextProvider(nifty, vix),
                  real_plant_dcs=CentralPlantMasterDCS(total_capital=int(STARTING_EQUITY), db_path=":memory:"),
                  plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="compact",
                  governor_authority="full", starting_equity=STARTING_EQUITY)
    symbols = sorted(data)
    if mode == "r7":
        from revision7_avr import Revision7Engine
        from revision7_avr.r7_state import default_configs
        engine = Revision7Engine(symbols, r7_configs=default_configs(symbols), **kwargs)
    else:
        engine = Revision2ExternalEngineOrchestrator(symbols, **kwargs)
    report = engine.run({s: data[s] for s in symbols}, warmup=warmup)
    trades = [t for t in report["trades"] if isinstance(t, dict)]
    net = [float(t.get("net_pnl", 0.0)) for t in trades]
    costs = sum(float(t.get("costs", 0.0)) for t in trades)
    turnover = sum(abs(float(t.get("quantity", 0))) * (float(t.get("entry_price", 0)) + float(t.get("exit_price", 0)))
                   for t in trades)
    curve = [float(x) for x in getattr(engine, "_equity_curve", []) or []] or [STARTING_EQUITY]
    peak, realized_dd = curve[0], 0.0
    for x in curve:
        peak = max(peak, x)
        realized_dd = max(realized_dd, (peak - x) / peak if peak > 0 else 0.0)
    live = {s: f.iloc[warmup:] for s, f in data.items()}
    first_ts = min(f["timestamp"].iloc[0] for f in live.values())
    last_ts = max(f["timestamp"].iloc[-1] for f in live.values())
    in_window = nifty[(nifty["timestamp"] >= first_ts) & (nifty["timestamp"] <= last_ts)]
    nifty_return = (float(in_window["close"].iloc[-1]) / float(in_window["close"].iloc[0]) - 1.0
                    if len(in_window) >= 2 else None)
    exits = {}
    for t in trades:
        reason = str(t.get("reason") or t.get("exit_reason"))
        exits[reason] = exits.get(reason, 0) + 1
    result = {
        "quarter": label, "mode": mode, "start": str(start), "end": str(end),
        "first_bar": str(first_ts), "last_bar": str(last_ts), "symbols": len(symbols),
        "bars_replayed": int(sum(len(f) for f in live.values())), "warmup_bars_per_symbol": warmup,
        "trades": len(trades), "wins": sum(1 for x in net if x > 0), "losses": sum(1 for x in net if x < 0),
        "gross_pnl": float(report.get("gross_pnl") or 0.0), "costs": costs, "net_pnl": float(sum(net)),
        "net_pnl_per_trade": (sum(net) / len(net)) if net else None,
        "return_on_capital": float(sum(net)) / STARTING_EQUITY,
        "mtm_max_drawdown_fraction": float(getattr(engine, "_mtm_max_drawdown_fraction", 0.0) or 0.0),
        "realized_max_drawdown_fraction": realized_dd, "turnover": turnover,
        "orders": {"entries": report.get("orders_submitted"), "exits": report.get("exit_orders_submitted"),
                   "fills": report.get("fills")},
        "exit_reasons": exits, "nifty_buy_and_hold_return": nifty_return,
        "trades_sha256": _dig(trades), "seconds": round(time.time() - t0, 1),
        "data_identity": {s: _dig([str(f["timestamp"].iloc[0]), str(f["timestamp"].iloc[-1]), len(f),
                                   float(f["close"].sum())]) for s, f in sorted(data.items())},
    }
    out = Path(job["out"])
    (out / f"{label}.json").write_text(json.dumps(result, indent=1, default=str))
    (out / f"{label}_trades.json").write_text(json.dumps(trades, indent=1, default=str))
    return result


def verdict(results, synthetic):
    c = PREREGISTERED_CRITERIA
    total_trades = sum(r["trades"] for r in results)
    total_net = sum(r["net_pnl"] for r in results)
    positive = sum(1 for r in results if r["net_pnl"] > 0)
    last_four = sum(r["net_pnl"] for r in results[-4:])
    checks = {
        "total_net_pnl_positive": total_net > 0,
        "fraction_of_quarters_net_positive": positive / len(results) >= c["minimum_fraction_of_quarters_net_positive"],
        "average_net_pnl_per_trade_positive": total_trades > 0 and total_net / total_trades > 0,
        "worst_quarter_mtm_drawdown_within_limit":
            max(r["mtm_max_drawdown_fraction"] for r in results) <= c["maximum_quarter_mtm_drawdown_fraction"],
        "last_four_quarters_combined_net_positive": last_four > 0,
    }
    if synthetic:
        status = "NOT_EVIDENCE"
    elif total_trades < c["minimum_total_trades"]:
        status = "INCONCLUSIVE"
    else:
        status = "PASS" if all(checks.values()) else "FAIL"
    return {"status": status, "checks": checks, "total_trades": total_trades, "total_net_pnl": total_net,
            "total_gross_pnl": sum(r["gross_pnl"] for r in results), "total_costs": sum(r["costs"] for r in results),
            "quarters_net_positive": positive, "quarters": len(results), "last_four_quarters_net": last_four,
            "net_pnl_per_trade": (total_net / total_trades) if total_trades else None,
            "worst_quarter_mtm_drawdown_fraction": max(r["mtm_max_drawdown_fraction"] for r in results)}


def main() -> int:
    from runtime.live_trading_guard import require_live_trading_disabled
    require_live_trading_disabled()
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True)
    p.add_argument("--data-dir")
    p.add_argument("--grid-manifest", type=Path)
    p.add_argument("--mode", choices=["native", "r7"], default="native")
    p.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1))
    p.add_argument("--quarters", default="ALL")
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--synthetic-bars", type=int, default=400)
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "PREREGISTERED_CRITERIA.json").write_text(json.dumps(PREREGISTERED_CRITERIA, indent=1))
    base = {"warmup": a.warmup, "mode": a.mode, "out": str(out), "synthetic": a.synthetic,
            "synthetic_bars": a.synthetic_bars}
    if a.synthetic:
        jobs = [{**base, "label": f"SYNTH{i + 1}", "start": None, "end": None, "part": i} for i in range(2)]
        identity = {"source": "SYNTHETIC_SMOKE (seeded) -- not market data, P&L is meaningless"}
    else:
        if not a.data_dir or not a.grid_manifest:
            raise SystemExit("--data-dir and --grid-manifest are required for the frozen-data run")
        import pandas as pd
        from engine_c.data_contract import MANIFEST, audit_bars, require_clean
        from market_data_loader import MarketDataLoader
        from revision2.dataset_manifest import DatasetManifest, verify_manifest
        manifest = DatasetManifest.load(str(MANIFEST))
        check = verify_manifest(manifest, data_dir=a.data_dir)
        if not check.valid:
            raise SystemExit(f"frozen dataset failed manifest verification: {check.message}")
        loader = MarketDataLoader(a.data_dir, synthetic_if_missing=False)
        files = {f.symbol: str(loader._resolve_csv(f.symbol)) for f in manifest.files}
        audits = {s: audit_bars(s, pd.read_csv(path)) for s, path in sorted(files.items())}
        require_clean(audits)                                  # whole files, before any quarter
        first = min(date.fromisoformat(f.first_timestamp[:10]) for f in manifest.files)
        last = max(date.fromisoformat(f.last_timestamp[:10]) for f in manifest.files)
        all_q = quarters_between(first, last)
        wanted = None if a.quarters == "ALL" else set(a.quarters.split(","))
        jobs = [{**base, "label": lab, "start": s, "end": e, "files": files, "grid_manifest": str(a.grid_manifest)}
                for lab, s, e in all_q if wanted is None or lab in wanted]
        identity = {"source": "FROZEN_NSE_48_1MIN", "manifest_hash": manifest.manifest_hash,
                    "symbols": len(files), "first": str(first), "last": str(last)}
    (out / "RUN_IDENTITY.json").write_text(json.dumps({**identity, "mode": a.mode, "engine": "A",
                                                        "quarters": [j["label"] for j in jobs]}, indent=1))
    results = []
    with cf.ProcessPoolExecutor(max_workers=a.workers) as pool:
        futures = {pool.submit(run_quarter, j): j["label"] for j in jobs}
        for fut in cf.as_completed(futures):
            r = fut.result()
            results.append(r)
            print(f"{r['quarter']}: trades={r['trades']} net={r['net_pnl']:.2f} costs={r['costs']:.2f} "
                  f"mtm_dd={r['mtm_max_drawdown_fraction']:.4f} ({r['seconds']}s)", flush=True)
    results.sort(key=lambda r: r["quarter"])
    summary = {**verdict(results, a.synthetic), "criteria": PREREGISTERED_CRITERIA, "identity": identity,
               "mode": a.mode, "per_quarter": results}
    (out / "SUMMARY.json").write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({k: summary[k] for k in ("status", "checks", "total_trades", "total_net_pnl", "total_costs",
                                              "quarters_net_positive", "quarters")}, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
