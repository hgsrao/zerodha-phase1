"""R8 integrated-engine acceptance matrix, run on the frozen dataset (outputs stay local).

Runs revision8_avr/run_r8_replay.py for each engine (B = Nautilus, C = Backtrader) in R8 mode with
plant physics off / observe / enforce, and checks:

  1. observe leaves the trades byte-identical to off (trades_sha256 equal);
  2. in observe, every unit synchronized and the machine scan and relays ran;
  3. enforce runs complete and reconcile (broker vs ledger);
  4. no short entries while shorts are off (the default).

Usage (on the machine that holds the dataset; never commit --out):
    python scripts/r8_acceptance.py --data-dir <frozen 48-symbol dir> --out <local dir> [--bars 390]
    python scripts/r8_acceptance.py --synthetic --out <local dir>          # wiring smoke test only
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPLAY = ROOT / "revision8_avr" / "run_r8_replay.py"
ARMS = ["off", "observe", "enforce"]


def _run(engine: str, physics: str, a, out: Path) -> dict:
    arm = out / f"engine_{engine}_{physics}"
    cmd = [sys.executable, str(REPLAY), engine, "r8", "--out", str(arm), "--bars", str(a.bars),
           "--warmup", str(a.warmup), "--symbols", a.symbols, "--plant-mode", a.plant_mode,
           "--plant-physics", physics]
    if a.synthetic:
        cmd.append("--synthetic")
    elif a.data_dir:
        cmd += ["--data-dir", a.data_dir]
    env = {**os.environ, "LIVE_TRADING_ENABLED": "False"}
    print("RUN", " ".join(cmd[1:]), flush=True)
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True)
    (arm / "stderr.txt").parent.mkdir(parents=True, exist_ok=True)
    (arm / "stderr.txt").write_text(proc.stderr)
    if proc.returncode != 0:
        return {"error": f"exit {proc.returncode}", "stderr_tail": proc.stderr[-2000:]}
    return json.loads((arm / "summary.json").read_text())


def _reconciled(s: dict) -> bool:
    rec = s.get("reconciliation")
    if isinstance(rec, dict):
        return not rec.get("mismatches") and rec.get("ok", True) is not False
    return rec is None or bool(rec)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", required=True)
    p.add_argument("--data-dir", default=os.environ.get("DATA_DIR"))
    p.add_argument("--synthetic", action="store_true")
    p.add_argument("--bars", type=int, default=390)
    p.add_argument("--warmup", type=int, default=60)
    p.add_argument("--symbols", default="ALL")
    p.add_argument("--plant-mode", choices=["ENGINE_A", "ENGINE_B"], default="ENGINE_A")
    p.add_argument("--engines", default="B,C")
    a = p.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    results, checks = {}, []
    for engine in a.engines.split(","):
        runs = {ph: _run(engine, ph, a, out) for ph in ARMS}
        results[engine] = runs
        failed = [k for k, s in runs.items() if "error" in s]
        checks.append((f"{engine}: every arm completed", not failed, failed))
        if failed:
            continue
        off, obs = runs["off"], runs["observe"]
        checks.append((f"{engine}: observe trades identical to off",
                       off["trades_sha256"] == obs["trades_sha256"],
                       f"{off['trades_sha256'][:12]} vs {obs['trades_sha256'][:12]}"))
        counts = obs.get("plant_physics_counts") or {}
        missing = [k for k in ("52G_CLOSED_SYNCHRONIZED", "SYNC_STEP", "MACHINE_SCAN_STEP", "BAR")
                   if not counts.get(k)]
        checks.append((f"{engine}: physics ran in observe", not missing, missing or counts))
        for key, s in runs.items():
            checks.append((f"{engine} {key}: reconciled", _reconciled(s), s.get("reconciliation")))
            sells = (s.get("entry_sides") or {}).get("SELL", 0)
            checks.append((f"{engine} {key}: no short entries", sells == 0, s.get("entry_sides")))

    table = []
    for engine, runs in results.items():
        for key, s in runs.items():
            if "error" in s:
                table.append(f"{engine:2s} {key:22s} ERROR {s['error']}")
                continue
            table.append(f"{engine:2s} {key:22s} trades={len(s.get('trades') or []):5d} "
                         f"net_pnl={s.get('net_pnl')!s:>14s} "
                         f"trips={sum(v for k, v in (s.get('plant_physics_counts') or {}).items() if k.startswith('TRIP:'))}")
    passed = all(ok for _, ok, _ in checks)
    report = {"passed": passed, "checks": [{"check": c, "ok": ok, "detail": str(d)} for c, ok, d in checks],
              "table": table, "plant_mode": a.plant_mode, "synthetic": a.synthetic}
    (out / "acceptance.json").write_text(json.dumps(report, indent=2, default=str))
    print("\n".join(table))
    for c, ok, d in checks:
        print(("PASS " if ok else "FAIL ") + c + ("" if ok else f"  -> {d}"))
    print("ACCEPTANCE", "PASSED" if passed else "FAILED", "->", out / "acceptance.json")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
