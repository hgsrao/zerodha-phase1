#!/usr/bin/env python3
"""Block 1 four-arm isolation harness: process-isolated runs, exit quality, friction ledger, null.

Every (arm, repeat) executes ``block1_arm_worker.py`` in a dedicated, fresh Python sub-process,
so no controller state (integral accumulators, EMA baselines, drawdown trackers) can leak
between runs; each worker also asserts that all of it is zero before the first bar.

Sequence: arms run first (control 00 first); the zero-alpha random-entry null is then run in its
own process with the stop/target geometry, slippage and risk budget measured from the control arm.

Usage (repo root, project venv):
    python scripts/diagnostics/block1_isolation_harness.py \
        --params /path/to/trial_007_params.json --data-root-stock . \
        --data-root-grid ~/projects/zerodha-phase1 --output-dir outputs/block1_isolation

Diagnostic only.  No engine file is modified; Stage C, Step 6 and the holdout are never opened.
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import isolation_lib as lib  # noqa: E402

WORKER = HERE / "block1_arm_worker.py"
ARM_LABEL = {"00": "00 control", "10": "10 hold-only", "01": "01 PA-only", "11": "11 interaction"}


def make_data_root(stock_root: Path, grid_root: Path) -> Path:
    """Read-only symlink root: the stock manifest from one checkout, the grid manifest from another."""
    root = Path(tempfile.mkdtemp(prefix="block1_data_root_"))
    (root / "revision2").symlink_to(stock_root / "revision2")
    (root / "local_workspace").symlink_to(grid_root / "local_workspace")
    return root


PROCESS_LOG = []


def run_worker(arm: str, out: Path, data_root: Path, args, extra=()) -> dict:
    import datetime as dt
    import time
    cmd = [sys.executable, str(WORKER), "--arm", arm, "--out", str(out), "--data-root", str(data_root),
           "--params", str(args.params), "--symbols", args.symbols, "--block", str(args.block),
           "--artifact-dir", str(out.parent / "generated_sources"), *extra]
    started_wall, t0 = dt.datetime.now(dt.timezone.utc), time.monotonic()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
    child_pid = proc.pid
    stdout, stderr = proc.communicate()
    ended_wall, elapsed = dt.datetime.now(dt.timezone.utc), time.monotonic() - t0
    log = out.with_suffix(".log")
    log.write_text(f"# stdout\n{stdout}\n# stderr\n{stderr}\n")
    PROCESS_LOG.append({"arm": arm, "output": out.name, "log": log.name, "spawned_pid": child_pid,
                        "started_utc": started_wall.isoformat(), "ended_utc": ended_wall.isoformat(),
                        "elapsed_seconds": round(elapsed, 3), "exit_status": proc.returncode,
                        "argv": cmd})
    (out.parent / "process_manifest.json").write_text(json.dumps(PROCESS_LOG, indent=1))
    if proc.returncode != 0:
        raise RuntimeError(f"worker arm={arm} failed:\n{stderr[-3000:]}")
    return json.loads(out.read_text())


def write_ledger_csv(path: Path, rows) -> None:
    if not rows:
        path.write_text("")
        return
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def control_geometry(result: dict) -> dict:
    trades = result["trades"]
    if not trades:
        raise RuntimeError("control arm produced no trades; cannot derive null geometry")
    stop_mult = [abs(t["planned_entry_price"] - t["planned_stop_price"]) / t["entry_atr"] for t in trades]
    tgt_mult = [abs(t["planned_target_price"] - t["planned_entry_price"]) / t["entry_atr"] for t in trades]
    risk_budget = [abs(t["planned_entry_price"] - t["planned_stop_price"]) * t["quantity"] for t in trades]
    return {"stop": statistics.median(stop_mult), "target": statistics.median(tgt_mult),
            "risk_budget": statistics.median(risk_budget)}


def markdown_report(args, runs, determinism, null, geometry) -> str:
    out = ["# Block 1 four-arm isolation report", "",
           f"Symbols: `{args.symbols}` · block {args.block} · params `{Path(args.params).name}` · "
           f"repeats per arm: {args.repeats}", "",
           "## Determinism and isolation", "",
           "| arm | repeats | ledger SHA identical | controller-trace SHA identical | distinct PIDs | zero-state checks |",
           "|---|---|---|---|---|---|"]
    for arm, d in determinism.items():
        out.append(f"| {ARM_LABEL[arm]} | {d['repeats']} | {d['ledger_identical']} | {d['trace_identical']} | "
                   f"{d['distinct_pids']} | {d['zero_state_checks']} |")
    out += ["", "## Per-arm outcome (first repeat)", "",
            "| arm | trades | exits < min hold | FSRN exits < min hold | deferred FSRN exits | median bars held | "
            "gross frictionless Rs | friction Rs | net Rs | mean gross R | mean net R |",
            "|---|---|---|---|---|---|---|---|---|---|---|"]
    for arm, r in runs.items():
        s = r["ledger_summary"]
        held = [t["bars_held"] for t in r["trades"] if t.get("bars_held") is not None]
        f = lambda v: "n/a" if v is None else f"{v:,.2f}"
        out.append(f"| {ARM_LABEL[arm]} | {s['trades']} | {r['exits_before_min_hold']} | "
                   f"{r['fsrn_exits_before_min_hold']} | {r['deferred_conviction_exits']} | "
                   f"{statistics.median(held) if held else 'n/a'} | {f(s.get('gross_pnl_frictionless_inr'))} | "
                   f"{f(s.get('total_friction_inr'))} | {f(s.get('net_pnl_inr'))} | "
                   f"{f(s.get('mean_gross_r_frictionless'))} | {f(s.get('mean_net_r'))} |")
    out += ["", "## Exit quality (15 bars after exit; same session, scored sessions only)", "",
            "DEFENSIVE_SAVE = original stop breached within 15 bars · PREMATURE_CHOKE = original target reached · "
            "NOISE_CHURN = neither, full 15 bars observed · CENSORED = fewer than 15 same-session bars left and no "
            "touch yet.  A bar touching both levels counts as a stop first.  Mechanical exits (stop/target/"
            "force-close/max-hold) are tagged separately because their classification is partly definitional.", "",
            "| arm | exit kind | DEFENSIVE_SAVE | PREMATURE_CHOKE | NOISE_CHURN | CENSORED |", "|---|---|---|---|---|---|"]
    for arm, r in runs.items():
        for kind, c in sorted(r["exit_quality_summary"]["by_exit_kind"].items()):
            out.append(f"| {ARM_LABEL[arm]} | {kind} | {c['DEFENSIVE_SAVE']} | {c['PREMATURE_CHOKE']} | "
                       f"{c['NOISE_CHURN']} | {c['CENSORED']} |")
    out += ["", "## Friction deconstruction (totals, Rs)", "",
            "| arm | brokerage | STT (sell leg) | turnover charge | slippage | total friction | bps of entry notional |",
            "|---|---|---|---|---|---|---|"]
    for arm, r in runs.items():
        s = r["ledger_summary"]
        if s["trades"]:
            out.append(f"| {ARM_LABEL[arm]} | {s['brokerage_inr']:,.2f} | {s['stt_inr']:,.2f} | "
                       f"{s['turnover_charge_inr']:,.2f} | {s['slippage_inr']:,.2f} | {s['total_friction_inr']:,.2f} | "
                       f"{s['total_friction_bps_of_entry_notional']:.2f} |")
    ns = null["ledger_summary"]
    out += ["", "## Zero-alpha null (random side and entry bar, same stop/target geometry and frozen friction)", "",
            f"Seed {null['seed']} · {ns['trades']} trades · stop {geometry['stop']:.3f} ATR, target {geometry['target']:.3f} ATR "
            f"(medians of the control arm) · risk budget Rs {geometry['risk_budget']:.0f} · "
            f"max hold {null['geometry']['max_hold_bars']} bars.", "",
            f"Mean gross R (frictionless) {ns['mean_gross_r_frictionless']:.4f} · mean net R {ns['mean_net_r']:.4f} · "
            f"mean friction {ns['mean_friction_r']:.4f} R · total friction {ns['total_friction_bps_of_entry_notional']:.2f} bps of entry "
            f"notional · net Rs {ns['net_pnl_inr']:,.2f}.", "",
            "Caveat: the null reuses the engine's frozen cost and slippage functions and the control arm's measured geometry, "
            "but it is a standalone simulator, not the orchestrator's candidate/governor/broker pipeline.", ""]
    return "\n".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--params", type=Path, required=True)
    ap.add_argument("--data-root-stock", type=Path, default=ROOT, help="checkout holding revision2/ manifest")
    ap.add_argument("--data-root-grid", type=Path, required=True, help="checkout holding local_workspace/ grid manifest")
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--arms", default="00,10,01,11")
    ap.add_argument("--symbols", default="TITAN")
    ap.add_argument("--block", type=int, default=1)
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--null-entries", type=int, default=500)
    ap.add_argument("--null-max-hold-bars", type=int, default=None, help="default: max_hold_bars from --params")
    ap.add_argument("--seed", type=int, default=20241005)
    args = ap.parse_args(argv)
    out_dir = args.output_dir.resolve()
    if out_dir.exists() and any(out_dir.iterdir()):
        ap.error("output directory must be new or empty")
    if "stage_a" in str(out_dir) or "stage_c" in str(out_dir).lower():
        ap.error("refusing to write inside sealed Stage state")
    out_dir.mkdir(parents=True, exist_ok=True)
    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    if arms[0] != "00":
        ap.error("control arm 00 must run first (the null takes its geometry from it)")
    data_root = make_data_root(args.data_root_stock.resolve(), args.data_root_grid.resolve())

    runs, determinism = {}, {}
    for arm in arms:
        repeats = []
        for k in range(args.repeats):
            print(f"arm {arm} repeat {k + 1}/{args.repeats} (fresh process) ...", flush=True)
            repeats.append(run_worker(arm, out_dir / f"arm{arm}_rep{k + 1}.json", data_root, args))
        runs[arm] = repeats[0]
        determinism[arm] = {
            "repeats": len(repeats),
            "ledger_identical": len({r["trade_ledger_sha256"] for r in repeats}) == 1,
            "trace_identical": len({r["controller_trace_sha256"] for r in repeats}) == 1,
            "distinct_pids": len({r["pid"] for r in repeats}),
            "zero_state_checks": len(repeats[0]["zero_state_at_start"]),
            "trade_ledger_sha256": repeats[0]["trade_ledger_sha256"],
        }
        write_ledger_csv(out_dir / f"arm{arm}_ledger.csv", repeats[0]["ledger"])

    geometry = control_geometry(runs["00"])
    params = json.loads(args.params.read_text())
    max_hold = args.null_max_hold_bars or int(params["max_hold_bars"])
    print("null baseline (fresh process) ...", flush=True)
    null = run_worker("null", out_dir / "null.json", data_root, args, extra=[
        "--seed", str(args.seed), "--null-entries", str(args.null_entries),
        "--slippage-fraction", repr(runs["00"]["slippage_fraction"]),
        "--null-stop-atr-mult", repr(geometry["stop"]), "--null-target-atr-mult", repr(geometry["target"]),
        "--null-max-hold-bars", str(max_hold), "--null-risk-budget-inr", repr(geometry["risk_budget"])])
    write_ledger_csv(out_dir / "null_ledger.csv", null["ledger"])

    summary = {"determinism": determinism, "geometry": geometry,
               "arms": {a: {k: r[k] for k in ("ledger_summary", "exit_quality_summary", "exits_before_min_hold",
                                              "fsrn_exits_before_min_hold", "deferred_conviction_exits",
                                              "trade_ledger_sha256", "controller_trace_sha256", "slice_sha256")}
                        for a, r in runs.items()},
               "null": {"ledger_summary": null["ledger_summary"], "seed": null["seed"]}}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=str))
    (out_dir / "REPORT.md").write_text(markdown_report(args, runs, determinism, null, geometry))
    print(f"report: {out_dir / 'REPORT.md'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
