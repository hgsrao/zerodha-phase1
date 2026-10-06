#!/usr/bin/env python3
"""One isolated Block 1 arm run.  Always executed in its own fresh Python process.

Arms (isolation plan, r5-isolation-plan.md):
  00 control      position authority as at d1b71eb (hold fix absent), PA unchanged
  10 hold-only    this branch's position authority (PR #7), PA unchanged
  01 pa-only      d1b71eb authority + PA directional-symmetry delta only
  11 interaction  PR #7 authority + the same PA delta
  null            single-pass random-entry zero-alpha baseline (no engine run)

No engine module is edited.  Arm differences are applied at runtime, inside this process only:
  * hold authority absent  -> ``position_decision`` from the pinned d1b71eb blob replaces the
    orchestrator's reference to it;
  * PA symmetry            -> ``TALibPredictiveAnalyticsBox`` is replaced by a copy of
    ``indicators_talib.py`` with ``data/pa_symmetry_indicators_talib.patch`` applied and
    ``symmetric_direction=True``.  The patch also adds an unused ``reset_session`` method.

Diagnostic only; reads the sealed Block 1 slice through the existing causal ``prepare_block``.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

BASELINE_AUTHORITY_COMMIT = "d1b71eb042622ee49b884c20cf192784b915540c"
PA_PATCH = Path(__file__).resolve().parent / "data" / "pa_symmetry_indicators_talib.patch"
ARMS = {"00": (False, False), "10": (True, False), "01": (False, True), "11": (True, True)}  # (hold fix, PA delta)


def sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


ARTIFACT_DIR = None   # set from --artifact-dir; generated sources are copied here for hashing


def _load_module_from_source(name: str, source: str):
    tmp = Path(tempfile.mkdtemp(prefix="arm_mod_")) / f"{name}.py"
    tmp.write_text(source)
    if ARTIFACT_DIR is not None:
        (ARTIFACT_DIR / f"{name}.py").write_text(source)
    spec = importlib.util.spec_from_file_location(name, tmp)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def baseline_position_decision():
    src = subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{BASELINE_AUTHORITY_COMMIT}:revision5/governor_authority.py"], text=True)
    return _load_module_from_source("governor_authority_d1b71eb", src).position_decision


def symmetric_pa_class():
    src = (ROOT / "revision2_external/indicators_talib.py").read_text()
    work = Path(tempfile.mkdtemp(prefix="pa_patch_"))
    (work / "revision2_external").mkdir()
    (work / "revision2_external/indicators_talib.py").write_text(src)
    subprocess.check_call(["patch", "-p1", "-s", "-i", str(PA_PATCH)], cwd=work)
    patched = (work / "revision2_external/indicators_talib.py").read_text()
    return _load_module_from_source("indicators_talib_pa_symmetric", patched).TALibPredictiveAnalyticsBox


def load_step5_worker():
    spec = importlib.util.spec_from_file_location("r5_step5_worker", ROOT / "scripts/run_r5_step5_candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def verify_zero_state(orch, plant, equity: float) -> dict:
    """Every controller accumulator must start at zero; raises if any does not."""
    checks = {}
    for bay_id, bay in plant.bays.items():
        gov = bay.governor
        checks[f"{bay_id}.integral_error"] = float(gov.integral_error)
        checks[f"{bay_id}.last_error"] = float(gov.last_error)
        checks[f"{bay_id}.last_control_u"] = float(gov.last_control_u)
        checks[f"{bay_id}.inner_states"] = len(gov._inner_states)
        checks[f"{bay_id}.consecutive_stops"] = int(bay.consecutive_stops)
    checks["orch.open_trades"] = len(orch.open_trades)
    # The curve is seeded with the single starting-equity point; any other content is carried state.
    checks["orch.equity_curve_deviation_from_seed"] = float(
        sum(abs(float(v) - equity) for v in orch._equity_curve) + max(0, len(orch._equity_curve) - 1))
    checks["orch.mtm_max_drawdown_fraction"] = float(orch._mtm_max_drawdown_fraction)
    checks["orch.symbol_consecutive_losses"] = len(orch.symbol_consecutive_losses)
    checks["orch.mtm_peak_minus_start_equity"] = float(orch._mtm_peak) - equity
    nonzero = {k: v for k, v in checks.items() if v != 0}
    if nonzero:
        raise RuntimeError(f"controller state not zero at session start: {nonzero}")
    return checks


def session_bars(frames, symbols, sessions):
    """Bars of the scored target sessions only (never warmup, never the post-block sentinel)."""
    wanted = set(sessions)
    out = {}
    for sym in symbols:
        df = frames[sym]
        per_day = {}
        for rec in df.to_dict("records"):
            day = str(rec["timestamp"])[:10]
            if day in wanted:
                per_day.setdefault(day, []).append(rec)
        out[sym] = [per_day[d] for d in sorted(per_day)]
    return out


def forward_bars_after(bars_by_day, symbol, exit_ts: str, limit: int, with_remaining: bool = False):
    day = str(exit_ts)[:10]
    for bars in bars_by_day[symbol]:
        if str(bars[0]["timestamp"])[:10] == day:
            later = [b for b in bars if str(b["timestamp"]) > str(exit_ts)]
            return (later[:limit], len(later)) if with_remaining else later[:limit]
    return ([], 0) if with_remaining else []


def run_arm(arm, protocol, params, frames, feeds, symbols, block_number):
    import revision2_external.orchestrator as orch_module
    from canonical_parameter_registry import CanonicalParameterRegistry
    from revision2_external.grid_context import SealedGridContextProvider
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    import isolation_lib as lib

    hold_fix, pa_delta = ARMS[arm]
    inner_decision = orch_module.governor_position_decision if hold_fix else baseline_position_decision()
    decisions = []

    def recording_decision(governor, cfg, **kw):          # pass-through: records, never alters
        result = inner_decision(governor, cfg, **kw)
        inner = result.get("inner") or {}
        decisions.append({
            "position_id": kw.get("position_id"), "elapsed_bars": int(kw["elapsed_bars"]),
            "min_hold_bars": int(kw["min_hold_bars"]), "measured_r": kw["measured_r"],
            "reference_r": kw["reference_r"], "conviction": kw["conviction"],
            "action": result["action"], "reason": result["reason"],
            "deferred": bool(result.get("conviction_exit_deferred", False)),
            "inner_action": inner.get("action"), "error": inner.get("error"),
            "integral": inner.get("integral_error"), "derivative": inner.get("derivative"),
            "control_u": inner.get("control_u"), "protected_r_floor": result.get("protected_r_floor"),
            "controlling_limiter": result.get("controlling_limiter"), "fsr_selected": result.get("fsr_selected"),
            "limiters": result.get("limiters")})
        return result

    orch_module.governor_position_decision = recording_decision
    if pa_delta:
        pa_cls = symmetric_pa_class()
        orch_module.TALibPredictiveAnalyticsBox = lambda: pa_cls(symmetric_direction=True)

    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    errors = registry.validate_calibration_payload(params, engine="EXTERNAL")
    if errors:
        raise ValueError("invalid calibration payload: " + "; ".join(errors))
    equity = float(protocol["block_execution_contract"]["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    orch = orch_module.Revision2ExternalEngineOrchestrator(
        symbols, registry, calibration_overrides=params, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]),
        real_plant_dcs=plant, plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
        telemetry_mode="compact", governor_authority="full")
    zero_state = verify_zero_state(orch, plant, equity)
    print(f"ZERO_STATE_RECEIPT arm={arm} pid={__import__('os').getpid()} t=0 (before first bar) "
          f"starting_equity={equity!r} mtm_peak={orch._mtm_peak!r} checks={len(zero_state)} all_zero=True", flush=True)
    for key, value in zero_state.items():
        print(f"ZERO_STATE_RECEIPT arm={arm} {key} = {value!r}", flush=True)
    started = time.time()
    report = orch.run({s: frames[s] for s in symbols},
                      warmup=int(protocol["block_execution_contract"]["stock_warmup_bars_per_symbol"]))
    trades = report.get("trades", [])
    slip = float(orch.broker.slippage_fraction)
    block = next(b for b in protocol["sampling_plan"]["stage_a"] if int(b["block"]) == block_number)
    bars_by_day = session_bars(frames, symbols, block["sessions"])
    ledger, quality = [], []
    for t in trades:
        row = lib.trade_ledger_row(t, slip)
        fwd, remaining = forward_bars_after(bars_by_day, t["symbol"], str(t["exit_timestamp"]), lib.FORWARD_BARS, True)
        q = lib.classify_exit(t["side"], float(t["planned_stop_price"]), float(t["planned_target_price"]), fwd)
        q.update({"trade_id": t.get("trade_id"), "session_bars_remaining_after_exit": remaining, "forward_bars": [
                      {k: (str(b[k]) if k == "timestamp" else float(b[k])) for k in ("timestamp", "open", "high", "low", "close")}
                      for b in fwd], "exit_reason": t.get("reason"),
                  "exit_kind": lib.exit_kind(t.get("reason")), "bars_held": t.get("bars_held")})
        row.update({"exit_class": q["exit_class"], "exit_kind": q["exit_kind"],
                    "bars_to_event": q["bars_to_event"], "forward_bars_available": q["forward_bars_available"]})
        ledger.append(row)
        quality.append(q)
    return {
        "arm": arm, "hold_fix": hold_fix, "pa_symmetry": pa_delta, "symbols": symbols, "block": block_number,
        "zero_state_at_start": zero_state, "slippage_fraction": slip,
        "runtime_seconds": round(time.time() - started, 1),
        "trade_ledger_sha256": sha(trades), "controller_trace_sha256": sha(decisions),
        "position_decisions": decisions,
        "exits_before_min_hold": sum(d["action"] == "EXIT" and d["elapsed_bars"] < d["min_hold_bars"] for d in decisions),
        "fsrn_exits_before_min_hold": sum(d["action"] == "EXIT" and d["elapsed_bars"] < d["min_hold_bars"]
                                          and str(d["reason"]).endswith(":FSRN") for d in decisions),
        "deferred_conviction_exits": sum(d["deferred"] for d in decisions),
        "governor_authority": report.get("governor_authority"),
        "trades": trades, "ledger": ledger, "ledger_summary": lib.summarize_ledger(ledger),
        "exit_quality": quality, "exit_quality_summary": lib.summarize_exit_quality(quality),
    }


def run_null(protocol, frames, symbols, block_number, args, control_summary):
    import isolation_lib as lib
    block = next(b for b in protocol["sampling_plan"]["stage_a"] if int(b["block"]) == block_number)
    sessions = session_bars(frames, symbols, block["sessions"])
    rows = lib.random_entry_null(
        sessions, n_entries=args.null_entries, seed=args.seed, stop_atr_mult=args.null_stop_atr_mult,
        target_atr_mult=args.null_target_atr_mult, max_hold_bars=args.null_max_hold_bars,
        risk_budget_inr=args.null_risk_budget_inr, slippage_fraction=args.slippage_fraction,
        entry_window=(args.null_first_bar, args.null_last_bar))
    return {"arm": "null", "symbols": symbols, "block": block_number, "seed": args.seed, "ledger": rows,
            "ledger_summary": lib.summarize_ledger(rows),
            "geometry": {"stop_atr_mult": args.null_stop_atr_mult, "target_atr_mult": args.null_target_atr_mult,
                         "max_hold_bars": args.null_max_hold_bars, "risk_budget_inr": args.null_risk_budget_inr}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--artifact-dir", type=Path, default=None)
    ap.add_argument("--arm", required=True, choices=[*ARMS, "null"])
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--data-root", type=Path, required=True)
    ap.add_argument("--protocol", type=Path, default=ROOT / "revision5/step5_sealed_calibration_protocol_v2.json")
    ap.add_argument("--params", type=Path, required=True)
    ap.add_argument("--symbols", default="TITAN")
    ap.add_argument("--block", type=int, default=1)
    ap.add_argument("--seed", type=int, default=20241005)
    ap.add_argument("--slippage-fraction", type=float, default=None)
    ap.add_argument("--null-entries", type=int, default=500)
    ap.add_argument("--null-stop-atr-mult", type=float, default=None)
    ap.add_argument("--null-target-atr-mult", type=float, default=None)
    ap.add_argument("--null-max-hold-bars", type=int, default=None)
    ap.add_argument("--null-risk-budget-inr", type=float, default=None)
    ap.add_argument("--null-first-bar", type=int, default=15)
    ap.add_argument("--null-last-bar", type=int, default=300)
    args = ap.parse_args(argv)
    if args.arm == "null" and None in (args.slippage_fraction, args.null_stop_atr_mult, args.null_target_atr_mult,
                                       args.null_max_hold_bars, args.null_risk_budget_inr):
        ap.error("null arm needs --slippage-fraction and the --null-* geometry (taken from the control arm)")

    global ARTIFACT_DIR
    if args.artifact_dir is not None:
        args.artifact_dir.mkdir(parents=True, exist_ok=True)
        ARTIFACT_DIR = args.artifact_dir
    protocol = json.loads(args.protocol.read_text())
    params = json.loads(args.params.read_text())
    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    block = next(b for b in protocol["sampling_plan"]["stage_a"] if int(b["block"]) == args.block)
    worker = load_step5_worker()
    frames, feeds, audit = worker.prepare_block(args.data_root, protocol, block)
    missing = [s for s in symbols if s not in frames]
    if missing:
        raise SystemExit(f"symbols not in block frames: {missing}")
    if args.arm == "null":
        result = run_null(protocol, frames, symbols, args.block, args, None)
    else:
        result = run_arm(args.arm, protocol, params, frames, feeds, symbols, args.block)
    result["slice_sha256"] = audit.get("slice_sha256")
    result["pid"] = __import__("os").getpid()
    args.out.write_text(json.dumps(result, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
