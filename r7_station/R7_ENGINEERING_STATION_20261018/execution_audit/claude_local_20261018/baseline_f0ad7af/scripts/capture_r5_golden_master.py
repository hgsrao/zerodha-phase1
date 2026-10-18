"""Golden-master capture for the shared R5 orchestrator (revision2_external + CCPP + paper broker).

Runs a fixed matrix of deterministic offline replays through the UNMODIFIED
Revision2ExternalEngineOrchestrator.run() and records a canonical JSON hash of each full
report plus per-section hashes (trades, funnel, controller telemetry, plant trace, MTM curve).
The baseline is the regression oracle for the Engine B orchestrator extraction: after the
refactor every hash must be byte-identical.

    python scripts/capture_r5_golden_master.py --write   # capture baseline
    python scripts/capture_r5_golden_master.py --check   # compare against stored baseline

Synthetic bars only (the frozen 48-symbol dataset is local to the operator's machine).
No broker network access.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import numpy as np
import pandas as pd

from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.ccpp_unified_plant import CentralPlantMasterDCS

import r5_plant_wiring_probe as probe
import verify_single_signal_pipeline as single

OUT_DIR = ROOT / "golden_master" / "r5_shared_orchestrator"
SECTIONS = ("trades", "controller_telemetry", "post_fill_checks", "plant_dcs_wiring",
            "mtm_equity_curve", "entry_candidate_observations", "position_sizing_events",
            "grid_shadow", "governor_authority", "plant_control")
FUNNEL_KEYS = ("bars_processed", "pa_evaluations", "directional_signals", "confidence_qualified",
               "id_approvals", "id_rejections", "mpc_plans", "safety_approvals", "safety_rejections",
               "gates_evaluated", "gates_passed", "gates_rejected", "orders_submitted",
               "exit_orders_submitted", "fills", "portfolio_cap_rejections", "plant_entry_rejections",
               "bay_trip_rejections", "bay_cooldown_rejections", "completed_trades",
               "gross_pnl", "net_pnl", "ending_equity", "execution_halted", "safety_violations")


def canonical(value):
    """Exact, order-independent JSON form. Floats keep full repr precision."""
    if isinstance(value, dict):
        return {str(k): canonical(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [canonical(v) for v in value]
    if isinstance(value, (set, frozenset)):
        return sorted((canonical(v) for v in value), key=repr)
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (float, np.floating)):
        value = float(value)
        return repr(value) if (math.isnan(value) or math.isinf(value)) else value
    if isinstance(value, (np.bool_,)):
        return bool(value)
    if isinstance(value, (str, int, bool)) or value is None:
        return value
    if isinstance(value, np.ndarray):
        return canonical(value.tolist())
    if hasattr(value, "__dataclass_fields__"):
        return canonical({f: getattr(value, f) for f in value.__dataclass_fields__})
    if hasattr(value, "value") and hasattr(value, "name"):  # Enum
        return f"{type(value).__name__}.{value.name}"
    return str(value)


def digest(value) -> str:
    blob = json.dumps(canonical(value), sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(blob.encode()).hexdigest()


def provider_from(nifty: pd.DataFrame, vix: pd.DataFrame) -> SealedGridContextProvider:
    return SealedGridContextProvider(nifty, vix)


def multi_day_session(symbol: str, days, seed: int) -> pd.DataFrame:
    """Full 09:15-15:29 sessions so day roll, entry cutoff and square-off paths execute."""
    frames = []
    rng = np.random.default_rng(seed)
    price = 1000.0
    for day in days:
        start = pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata")
        n = 375
        drift = rng.choice([-1.0, 1.0]) * 1.5e-4
        returns = drift + rng.normal(0.0, 1.4e-3, n)
        close = price * np.exp(np.cumsum(returns))
        open_ = np.concatenate([[price], close[:-1]])
        wick = np.abs(rng.normal(0.0, 1.0e-3, n)) * close
        frames.append(pd.DataFrame({
            "timestamp": pd.date_range(start, periods=n, freq="min"),
            "open": open_, "high": np.maximum(open_, close) + wick,
            "low": np.minimum(open_, close) - wick, "close": close,
            "volume": rng.lognormal(10.0, 0.45, n)}))
        price = float(close[-1])
    return pd.concat(frames, ignore_index=True)


def regime_session(days, seed: int, last_day_bars: int = 375) -> pd.DataFrame:
    """Full sessions built from trend / reversal / chop segments so entries clear Gate 12 and
    stop, target, gap, regime, saturation, MiCOM and end-of-run exit paths all execute."""
    rng = np.random.default_rng(seed)
    price, frames = 1000.0, []
    for d, day in enumerate(days):
        n = last_day_bars if d == len(days) - 1 else 375
        mu = np.zeros(n)
        k = 0
        while k < n:
            length = int(rng.integers(25, 60))
            kind = rng.choice(["up", "down", "chop", "reversal"], p=[.35, .35, .15, .15])
            mu[k:k + length] = {"up": 2.2e-3, "down": -2.2e-3, "chop": 0.0,
                                "reversal": -3.5e-3 * rng.choice([-1, 1])}[kind]
            k += length
        mult = np.where(mu != 0, 1.6, 1.0)
        close = price * np.exp(np.cumsum(mu + rng.normal(0, 1.6e-3, n)))
        open_ = np.concatenate([[price], close[:-1]])
        wick = np.abs(rng.normal(0, 1.2e-3, n)) * close * mult
        frames.append(pd.DataFrame({
            "timestamp": pd.date_range(pd.Timestamp(f"{day} 09:15", tz="Asia/Kolkata"), periods=n, freq="min"),
            "open": open_, "high": np.maximum(open_, close) + wick, "low": np.minimum(open_, close) - wick,
            "close": close, "volume": rng.lognormal(10, .4, n) * mult * 1.5}))
        price = float(close[-1])
    return pd.concat(frames, ignore_index=True)


def multi_day_grid(days):
    start = pd.Timestamp(f"{days[0]} 09:15", tz="Asia/Kolkata")
    end = pd.Timestamp(f"{days[-1]} 15:30", tz="Asia/Kolkata")
    times = pd.date_range(start - pd.Timedelta(days=12), end + pd.Timedelta(hours=1), freq="15min")
    rng = np.random.default_rng(7)
    nifty = 22000.0 * np.exp(np.cumsum(rng.normal(0.0, 6e-4, len(times))))
    return (pd.DataFrame({"timestamp": times, "close": nifty}),
            pd.DataFrame({"timestamp": times, "close": 14.0 + rng.normal(0.0, 0.2, len(times))}))


def scenario_matrix():
    """name -> (orchestrator kwargs factory, data factory, warmup)."""
    out = {}
    probe_start = pd.Timestamp("2024-03-04 09:15", tz="Asia/Kolkata")

    def probe_data(seed, bars=100, warmup=60):
        length = warmup + bars + 1
        return {s: probe.synthetic_session(s, probe_start, length, seed + i)
                for i, s in enumerate(probe.SYMBOLS)}

    for seed in (11, 12, 13, 14):
        out[f"probe_paper_apply_full_seed{seed}"] = dict(
            symbols=list(probe.SYMBOLS), data=lambda seed=seed: probe_data(seed),
            grid=lambda: probe.grid_feeds(probe_start), warmup=60, plant=True,
            kwargs=dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                        telemetry_mode="full", governor_authority="full"))
    for seed in (11, 12, 13, 14):
        out[f"single_signal_paper_apply_full_seed{seed}"] = dict(
            symbols=list(single.SYMBOLS), data=lambda seed=seed: single.feed(seed),
            grid=lambda: probe.grid_feeds(single.START), warmup=single.WARMUP, plant=True,
            kwargs=dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                        governor_authority="full"))
    # Branch coverage: default governor authority, shadow closed loop, shadow plant control, no plant.
    out["probe_paper_apply_default_governor_seed11"] = dict(
        symbols=list(probe.SYMBOLS), data=lambda: probe_data(11),
        grid=lambda: probe.grid_feeds(probe_start), warmup=60, plant=True,
        kwargs=dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="full"))
    out["probe_shadow_closed_loop_seed12"] = dict(
        symbols=list(probe.SYMBOLS), data=lambda: probe_data(12),
        grid=lambda: probe.grid_feeds(probe_start), warmup=60, plant=True,
        kwargs=dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="shadow", telemetry_mode="full"))
    out["probe_plant_shadow_seed13"] = dict(
        symbols=list(probe.SYMBOLS), data=lambda: probe_data(13),
        grid=lambda: probe.grid_feeds(probe_start), warmup=60, plant=True,
        kwargs=dict(plant_control_mode="SHADOW", closed_loop_mode="active_paper", telemetry_mode="full"))
    out["probe_no_plant_seed14"] = dict(
        symbols=list(probe.SYMBOLS), data=lambda: probe_data(14),
        grid=lambda: probe.grid_feeds(probe_start), warmup=60, plant=False,
        kwargs=dict(closed_loop_mode="active_paper", telemetry_mode="full"))
    # Multi-day full sessions: day roll, 14:30 cutoff, 15:15 square-off, end-of-run reconciliation.
    days = ["2024-03-04", "2024-03-05"]
    md_symbols = ["RELIANCE", "INFY", "HDFCBANK", "MARUTI", "SUNPHARMA"]
    out["multiday_paper_apply_full"] = dict(
        symbols=md_symbols,
        data=lambda: {s: multi_day_session(s, days, 101 + i) for i, s in enumerate(md_symbols)},
        grid=lambda: multi_day_grid(days), warmup=60, plant=True,
        kwargs=dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
                    telemetry_mode="full", governor_authority="full"))
    # Regime-structured multi-day sessions; the last day is truncated mid-session so open
    # positions reach end-of-run reconciliation.
    regime_data = lambda: {s: regime_session(days, 201 + i, last_day_bars=200) for i, s in enumerate(md_symbols)}
    for label, plant, kwargs in (
        ("regime_multiday_paper_apply_full", True,
         dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="full",
              governor_authority="full")),
        ("regime_multiday_paper_apply_default_governor", True,
         dict(plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="full")),
        ("regime_multiday_plant_shadow", True,
         dict(plant_control_mode="SHADOW", closed_loop_mode="active_paper", telemetry_mode="full")),
        ("regime_multiday_no_plant", False, dict(closed_loop_mode="active_paper", telemetry_mode="full")),
    ):
        out[label] = dict(symbols=md_symbols, data=regime_data, grid=lambda: multi_day_grid(days),
                          warmup=60, plant=plant, kwargs=kwargs)
    return out


def run_scenario(spec):
    nifty, vix = spec["grid"]()
    plant = CentralPlantMasterDCS(total_capital=1_000_000, db_path=":memory:") if spec["plant"] else None
    orch = Revision2ExternalEngineOrchestrator(
        spec["symbols"], grid_context_provider=provider_from(nifty, vix), real_plant_dcs=plant, **spec["kwargs"])
    return orch.run(spec["data"](), warmup=spec["warmup"])


def summarize(report):
    return {
        "report_sha256": digest(report),
        "sections_sha256": {k: digest(report.get(k)) for k in SECTIONS},
        "funnel": canonical({k: report.get(k) for k in FUNNEL_KEYS}),
        "exit_reasons": canonical(sorted({t["reason"] for t in report["trades"]})),
        "report_keys": sorted(report.keys()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--print", action="store_true", help="run and print hashes only")
    parser.add_argument("--only", nargs="*", help="subset of scenario names")
    args = parser.parse_args()

    matrix = scenario_matrix()
    names = args.only or list(matrix)
    baseline_path = OUT_DIR / "baseline.json"
    baseline = json.loads(baseline_path.read_text()) if args.check else None
    results, failures = {}, []
    for name in names:
        report = run_scenario(matrix[name])
        results[name] = summarize(report)
        f = results[name]["funnel"]
        print(f"{name:<44} {results[name]['report_sha256'][:16]}  orders={f['orders_submitted']} "
              f"fills={f['fills']} exits={f['exit_orders_submitted']} trades={f['completed_trades']} "
              f"net={f['net_pnl']}")
        if args.write:
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            with gzip.open(OUT_DIR / f"{name}.report.json.gz", "wt") as fh:
                json.dump(canonical(report), fh, sort_keys=True, separators=(",", ":"))
        if args.check:
            expected = baseline["scenarios"].get(name)
            if expected != results[name]:
                diff = [k for k in results[name]["sections_sha256"]
                        if expected is None or expected["sections_sha256"].get(k) != results[name]["sections_sha256"][k]]
                failures.append((name, diff))
    if args.write:
        baseline_path.write_text(json.dumps({"orchestrator": "revision2_external/orchestrator.py",
                                             "scenarios": results}, indent=2, sort_keys=True) + "\n")
        print(f"wrote {baseline_path}")
    if args.check:
        for name, diff in failures:
            print(f"MISMATCH {name}: sections {diff or ['funnel/report']}")
        print("GOLDEN MASTER:", "PASS" if not failures else f"FAIL ({len(failures)}/{len(names)})")
        return 1 if failures else 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
