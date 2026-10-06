#!/usr/bin/env python3
"""Generate the Block 1 verification report from a harness output directory.

Everything printed is read from the run's artifacts or recomputed from the source files; nothing
is typed by hand.  Items the engine does not model are printed as NOT MODELED, never invented.

    python scripts/diagnostics/block1_audit_report.py --run-dir outputs/block1_isolation_audit \
        --stock-root . --grid-root ~/projects/zerodha-phase1 --params <trial_007.json> --compare-dir outputs/block1_isolation
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(HERE))
import isolation_lib as lib  # noqa: E402

ARMS = ("00", "10", "01", "11")
BASELINE_COMMIT = "d1b71eb042622ee49b884c20cf192784b915540c"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git(*args) -> str:
    return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.rstrip()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--stock-root", type=Path, default=ROOT)
    ap.add_argument("--grid-root", type=Path, required=True)
    ap.add_argument("--params", type=Path, required=True)
    ap.add_argument("--compare-dir", type=Path, default=None, help="earlier run to compare ledger hashes against")
    ap.add_argument("--symbols", default="TITAN")
    args = ap.parse_args(argv)
    run = args.run_dir.resolve()
    out: list[str] = []
    P = out.append
    runs = {a: json.loads((run / f"arm{a}_rep1.json").read_text()) for a in ARMS}
    procs = json.loads((run / "process_manifest.json").read_text())
    bad = []

    DESC = {"00": "Control (d1b71eb baseline blob)", "10": "Hold-only (PR #7 logic)",
            "01": "PA-only (PA symmetric_direction patch; ID RR NOT patched)", "11": "Combined (Hold fix + PA patch)"}
    fmt_ts = lambda v: datetime.fromisoformat(str(v)).strftime("%Y-%m-%d %H:%M:%S")

    # ------------------------------------------------------------------ 1
    P("# SECTION 1: SUBPROCESS EXECUTION & STATE VERIFICATION\n")
    P("| Arm ID | Configuration Description | OS Process PID | Start Time (ISO-8601 UTC) | End Time (ISO-8601 UTC) | Elapsed Runtime (s) | Exit Code |")
    P("|:---|:---|:---|:---|:---|:---|:---|")
    for p in procs:
        if p["arm"] == "null":
            continue
        P(f"| Arm {p['arm']} | {DESC[p['arm']]} | {p['spawned_pid']} (worker reports {runs[p['arm']]['pid']}) | {p['started_utc']} | "
          f"{p['ended_utc']} | {p['elapsed_seconds']} (in-process {runs[p['arm']]['runtime_seconds']}) | {p['exit_status']} |")
    pids = [p["spawned_pid"] for p in procs]
    ordered = sorted(procs, key=lambda p: p["started_utc"])
    P(f"\nPIDs distinct (incl. null process): {len(set(pids)) == len(pids)} · all exit codes 0: {all(p['exit_status'] == 0 for p in procs)} · "
      f"runs strictly sequential: {all(a['ended_utc'] <= b['started_utc'] for a, b in zip(ordered, ordered[1:]))}\n")
    P("## Clean-state initialization receipt (verbatim lines from each worker's console log, evaluated at t = 0, before the first bar)\n")
    for a in ARMS:
        log = (run / f"arm{a}_rep1.log").read_text()
        P(f"### Arm {a}  ({run / f'arm{a}_rep1.log'})\n```")
        P("\n".join(l for l in log.splitlines() if l.startswith("ZERO_STATE_RECEIPT")) or "(NO RECEIPT LINES IN LOG)")
        P("```")
        bad += [] if "ZERO_STATE_RECEIPT" in log else [f"arm {a} log has no receipt"]
        bad += [f"arm {a} {k}" for k, v in runs[a]["zero_state_at_start"].items() if v != 0]
    P("\nMapping of the five requested accumulators to checked fields: PID integral = `*.integral_error`; last error = `*.last_error`; "
      "control output u(t) = `*.last_control_u`; consecutive stop counter = `*.consecutive_stops`; drawdown high-water mark = "
      "`orch.mtm_peak_minus_start_equity` and `orch.mtm_max_drawdown_fraction`.  Also checked: per-position inner states, open trades, "
      "per-symbol loss counters, equity-curve seed.  No separate EMA accumulator exists on these objects.")

    # ------------------------------------------------------------------ 2
    P("\n# SECTION 2: CRYPTOGRAPHIC HASHES & PROVENANCE (sha256 recomputed when this report ran)\n")
    stock_manifest = args.stock_root / "revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json"
    sm = json.loads(stock_manifest.read_text())
    P("## Input market data (the engine loads CSV, not parquet)\n")
    P("| Path | sha256 | Manifest sha256 | Match |\n|:---|:---|:---|:---|")
    for sym in args.symbols.split(","):
        entry = next(f for f in sm["files"] if f["symbol"] == sym)
        path = Path(sm["data_dir"]) / entry["filename"]
        got = sha256(path)
        P(f"| {path} | {got} | {entry['sha256']} | {got == entry['sha256']} |")
        bad += [] if got == entry["sha256"] else [f"data hash {sym}"]
    gm = json.loads((args.grid_root / "local_workspace/records/grid-manifest-local.json").read_text())
    for f in gm["files"]:
        got = sha256(Path(f["path"]))
        P(f"| {f['path']} | {got} | {f['sha256']} | {got == f['sha256']} |")
        bad += [] if got == f["sha256"] else [f"grid hash {f['name']}"]
    P(f"\nStock manifest {stock_manifest} sha256 {sha256(stock_manifest)}; manifest_hash field {sm['manifest_hash']}.")
    P(f"In-run causal slice hash (from prepare_block) {runs['00']['slice_sha256']}; identical across all 4 arms: "
      f"{len({runs[a]['slice_sha256'] for a in ARMS}) == 1}.")
    P("\n## Frozen parameter files (outputs/r5_step5_stage_a_v2_state/params/ of the zerodha-phase1 checkout)\n")
    P("| Path | sha256 | Used by this run |\n|:---|:---|:---|")
    for f in sorted(args.params.parent.glob("*")):
        P(f"| {f} | {sha256(f)} | {'YES' if f.resolve() == args.params.resolve() else 'no'} |")
    P(f"\nProtocol: {ROOT / 'revision5/step5_sealed_calibration_protocol_v2.json'} sha256 {sha256(ROOT / 'revision5/step5_sealed_calibration_protocol_v2.json')}")
    P("\n## Code blobs\n")
    gen = run / "generated_sources"
    blob = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASELINE_COMMIT}:revision5/governor_authority.py"], capture_output=True).stdout
    blob_sha = hashlib.sha256(blob).hexdigest()
    P("| Item | Path | sha256 |\n|:---|:---|:---|")
    P(f"| Baseline governor module as loaded (arms 00/01) | {gen / 'governor_authority_d1b71eb.py'} | {sha256(gen / 'governor_authority_d1b71eb.py')} |")
    P(f"| git blob {BASELINE_COMMIT[:8]}:revision5/governor_authority.py | (git object) | {blob_sha} |")
    P(f"| PA-symmetry patched module as loaded (arms 01/11) | {gen / 'indicators_talib_pa_symmetric.py'} | {sha256(gen / 'indicators_talib_pa_symmetric.py')} |")
    P(f"| PA patch file | {HERE / 'data/pa_symmetry_indicators_talib.patch'} | {sha256(HERE / 'data/pa_symmetry_indicators_talib.patch')} |")
    P(f"| Unpatched revision2_external/indicators_talib.py | {ROOT / 'revision2_external/indicators_talib.py'} | {sha256(ROOT / 'revision2_external/indicators_talib.py')} |")
    P(f"| PR #7 governor_authority.py (arms 10/11) | {ROOT / 'revision5/governor_authority.py'} | {sha256(ROOT / 'revision5/governor_authority.py')} |")
    same = sha256(gen / 'governor_authority_d1b71eb.py') == blob_sha
    bad += [] if same else ["baseline blob mismatch"]
    P(f"\nLoaded baseline module equals the git blob: {same}.")
    P("\n## Output files\n")
    P("Requested names `run_manifest.json` / `ledger.csv` / `summary.md` do not exist; the harness writes `process_manifest.json`, "
      "`arm<ID>_ledger.csv` and `REPORT.md` / `summary.json`.  Directory: " + str(run) + "\n")
    P("| Path | sha256 |\n|:---|:---|")
    for f in sorted(run.rglob("*")):
        if f.is_file():
            P(f"| {f} | {sha256(f)} |")
    P("\n## Determinism\n")
    P("| Arm | trade_ledger_sha256 | controller_trace_sha256 |\n|:---|:---|:---|")
    for a in ARMS:
        P(f"| {a} | {runs[a]['trade_ledger_sha256']} | {runs[a]['controller_trace_sha256']} |")
    if args.compare_dir:
        P(f"\nReproducibility vs earlier run {args.compare_dir.resolve()}:")
        for a in ARMS:
            prev = json.loads((args.compare_dir / f"arm{a}_rep1.json").read_text())
            P(f"- arm {a}: ledger hash equal={prev['trade_ledger_sha256'] == runs[a]['trade_ledger_sha256']}, "
              f"trace hash equal={prev['controller_trace_sha256'] == runs[a]['controller_trace_sha256']}")

    # ------------------------------------------------------------------ 3
    P("\n# SECTION 3: COMPLETE TRADE-BY-TRADE AUDIT LEDGER (unrounded, no aggregation)\n")
    P("| Arm | Trade | Symbol | Side | Entry ts | Entry fill (Rs) | Exit ts | Exit fill (Rs) | Qty | Notional (Rs) | Bars held | Clock seconds | Exit predicate | MFE (R) | MAE (R) | Gross P&L fill-to-fill (Rs) |")
    P("|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|")
    for a in ARMS:
        for t in runs[a]["trades"]:
            secs = (datetime.fromisoformat(str(t["exit_timestamp"])) - datetime.fromisoformat(str(t["entry_timestamp"]))).total_seconds()
            P(f"| {a} | {t['trade_id']} | {t['symbol']} | {t['side']} | {fmt_ts(t['entry_timestamp'])} | {t['entry_price']!r} | "
              f"{fmt_ts(t['exit_timestamp'])} | {t['exit_price']!r} | {t['quantity']} | {t['entry_price'] * t['quantity']!r} | "
              f"{t['bars_held']} | {secs:g} | {t['reason']} | {t['mfe_r']!r} | {t['mae_r']!r} | {t['pnl']!r} |")
    P("\nMFE/MAE are the engine's own `mfe_r`/`mae_r` (pre-exit-bar, in units of initial risk); trade-record fields are printed unmodified.")

    # ------------------------------------------------------------------ 4
    P("\n# SECTION 4: EXACT COST RECONCILIATION & BALANCE SHEET\n")
    P("Engine friction model (revision2/transaction_costs.py): brokerage = min(Rs 20, 0.03% turnover); exchange turnover charge = 0.00345% "
      "turnover; STT = 0.025% on SELL legs.  **Stamp duty and GST are not modeled by the engine** and are shown as 0.00 (NOT MODELED), not "
      "computed.  Slippage = frozen paper-fill adverse fraction, recovered from fills.  Gross here is frictionless "
      "(fill-to-fill P&L + slippage) because fill prices already contain slippage.  Net Realized is the engine's own `net_pnl` "
      "(independent of this harness's arithmetic), so Drift is a real check.\n")
    P("| Arm | Symbol | Gross P&L (Rs) | Brokerage (Rs) | STT (Rs) | Exch Turnover (Rs) | Stamp + GST (Rs) | Slippage (Rs) | Total Friction (Rs) | Total Friction (bps) | Net Realized P&L (Rs) | Drift (Gross - Friction - Net) | Drift unrounded |")
    P("|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|")
    for a in ARMS:
        for r in runs[a]["ledger"]:
            drift = r["gross_pnl_frictionless_inr"] - r["total_friction_inr"] - r["engine_net_pnl_inr"]
            ok = f"{drift:.2f}" in ("0.00", "-0.00") and abs(drift) < 5e-6
            bad += [] if ok else [f"arm {a} {r['trade_id']} drift {drift!r}"]
            P(f"| {a} | {r['symbol']} {r['trade_id']} | {r['gross_pnl_frictionless_inr']:.2f} | {r['brokerage_inr']:.2f} | {r['stt_inr']:.2f} | "
              f"{r['turnover_charge_inr']:.2f} | 0.00 (not modeled) | {r['slippage_inr']:.2f} | {r['total_friction_inr']:.2f} | "
              f"{r['total_friction_bps']:.2f} | {r['engine_net_pnl_inr']:.2f} | {drift:.2f} | {drift!r} |")
    P("\nItemized fees vs the engine's booked `costs`, per trade (Rs, unrounded error):")
    for a in ARMS:
        for r in runs[a]["ledger"]:
            P(f"- arm {a} {r['trade_id']}: itemized {r['fees_inr']!r} vs engine {r['engine_costs_inr']!r} -> error {r['fee_reconciliation_error_inr']!r}")

    # ------------------------------------------------------------------ 5
    P("\n# SECTION 5: 15-BAR COUNTERFACTUAL TRACKING PROOF\n")
    P("Forward bars = 1-minute bars of the same symbol and same scored session strictly after the exit bar; the post-block sentinel row and "
      "any later sessions are never used.  A bar touching both levels counts as stop first.\n")
    P("| Arm | Trade | Side | Planned stop (Rs) | Planned target (Rs) | Fwd bars used | Bars left in session after exit | 15-bar fwd extreme high (Rs) | 15-bar fwd extreme low (Rs) | Class | Justification |")
    P("|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|:---|")
    detail = []
    for a in ARMS:
        for t, q in zip(runs[a]["trades"], runs[a]["exit_quality"]):
            buy = t["side"] == "BUY"
            stop, tgt = float(t["planned_stop_price"]), float(t["planned_target_price"])
            fwd = q["forward_bars"]
            hi = max(b["high"] for b in fwd) if fwd else None
            lo = min(b["low"] for b in fwd) if fwd else None
            again = lib.classify_exit(t["side"], stop, tgt, fwd)
            first_stop = next((i for i, b in enumerate(fwd, 1) if (b["low"] <= stop if buy else b["high"] >= stop)), None)
            first_tgt = next((i for i, b in enumerate(fwd, 1) if (b["high"] >= tgt if buy else b["low"] <= tgt)), None)
            cls = q["exit_class"]
            if cls == "DEFENSIVE_SAVE":
                why = f"stop breached at forward bar +{first_stop}: {'low' if buy else 'high'} {fwd[first_stop - 1]['low' if buy else 'high']!r} vs stop {stop!r}"
            elif cls == "PREMATURE_CHOKE":
                why = f"target reached at forward bar +{first_tgt}: {'high' if buy else 'low'} {fwd[first_tgt - 1]['high' if buy else 'low']!r} vs target {tgt!r}"
            elif cls == "NOISE_CHURN":
                why = (f"neither level touched in {len(fwd)} bars: {'min low' if buy else 'max high'} "
                       f"{(lo if buy else hi)!r} {'>' if buy else '<'} stop {stop!r}; {'max high' if buy else 'min low'} "
                       f"{(hi if buy else lo)!r} {'<' if buy else '>'} target {tgt!r}")
            else:
                why = f"only {len(fwd)} bars available and no touch"
            ok = again["exit_class"] == cls and ((cls == "DEFENSIVE_SAVE") == (first_stop is not None))
            bad += [] if ok else [f"arm {a} {t['trade_id']} class re-derivation mismatch"]
            P(f"| {a} | {t['trade_id']} | {t['side']} | {stop!r} | {tgt!r} | {len(fwd)} | {q['session_bars_remaining_after_exit']} | {hi!r} | {lo!r} | {cls} | {why} |")
            detail.append((a, t, q))
    P("\n## Bar-by-bar forward trajectories\n")
    for a, t, q in detail:
        P(f"### arm {a} {t['trade_id']} {t['symbol']} {t['side']} (exit {fmt_ts(t['exit_timestamp'])} @ {t['exit_price']!r}, {t['reason']})\n")
        P("| +bar | timestamp | open | high | low | close |\n|:---|:---|:---|:---|:---|:---|")
        for i, b in enumerate(q["forward_bars"], 1):
            P(f"| {i} | {b['timestamp']} | {b['open']!r} | {b['high']!r} | {b['low']!r} | {b['close']!r} |")
        P("")

    # ------------------------------------------------------------------ 6
    P("\n# SECTION 6: GIT TREE BOUNDARY AUDIT\n")
    ref = "origin/diagnostic/pid-controller-authority-hierarchy"
    P(f"## `git status` (unedited)\n```\n{git('status')}\n```")
    P(f"## `git diff --stat {ref}` (unedited)\n```\n{git('diff', '--stat', ref)}\n```")
    P("(An empty diff --stat means no TRACKED file differs from the PR #7 head.)")
    engine = git('diff', '--name-only', ref, '--', 'revision2', 'revision2_external', 'revision3', 'revision3_external',
                 'revision4', 'revision4_production', 'revision5', 'blocks', 'runtime')
    P(f"\n`git diff --name-only {ref} -- <all engine dirs>`: {engine or '(empty)'}")
    untracked = [x for x in git('ls-files', '--others', '--exclude-standard').splitlines()]
    outside = [x for x in untracked if not (x.startswith(("scripts/diagnostics/", "tests/", "outputs/block1_isolation", "docs/experiment_outputs/steam/")))]
    P(f"Untracked paths outside scripts/diagnostics/, tests/, outputs/block1_isolation*/ and the pre-existing docs/experiment_outputs/steam/: {outside or 'none'}")
    local_head, remote = git('rev-parse', ref), git('ls-remote', 'origin', 'refs/heads/diagnostic/pid-controller-authority-hierarchy').split()[0]
    P(f"PR #7 head: local tracking ref {local_head}; remote now {remote}; unchanged from reported 440ca5fac80d935c16a8c45fd489f2d3f9153503: "
      f"{local_head == remote == '440ca5fac80d935c16a8c45fd489f2d3f9153503'}")
    bad += [] if (not engine and not outside and local_head == remote) else ["git boundary violated"]
    P(f"\n**SELF-CHECK FAILURES: {bad if bad else 'none'}**")
    text = "\n".join(out)
    (run / "AUDIT_REPORT.md").write_text(text + "\n")
    print(text)
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
