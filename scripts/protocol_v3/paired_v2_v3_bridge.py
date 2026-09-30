#!/usr/bin/env python3
"""Exit-only paired bridge: the same V2 entries, exited by the V2 (legacy) and V3 controllers.

For each sealed training block the V2 engine runs normally (legacy position control) with the
given V2 trading parameters, so every entry -- time, side, fill, quantity, initial stop, target --
is the V2 entry.  ``revision5.exit_shadow.ShadowExitBridge`` mirrors each fill and exits it
under two policies on the engine's own per-bar inputs:

    legacy  must reproduce every real V2 exit exactly (the bridge refuses to report otherwise)
    v3      the Protocol V3 closed-loop controller (registry defaults, or --v3-params)

Per trade:  delta_R = R_v3 - R_v2,  giveback = max(0, MFE - R),  MFE capture = R / MFE (MFE > 0),
MAE delta, and exit attribution.  Output is strict JSON (NaN is refused).

Holdout quarantine: only Stage A/B training blocks can be selected, and output must go under
outputs/protocol_v3_state/.  Nothing from the validation window is read.

Usage (V3 worktree, desktop, after Stage C):
    python scripts/protocol_v3/paired_v2_v3_bridge.py --stage A \
        --params outputs/r5_step5_stage_b_state/candidates/finalist_1.json --label trial_007 \
        --v2-reference <V2 state>/results/trial_007.json
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import math
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

OUTPUT_ROOT = ROOT / "outputs/protocol_v3_state"
DEFAULT_PROTOCOL = ROOT / "revision5/step5_sealed_calibration_protocol_v3.json"
V2_SURFACE = ("entry_confidence_threshold", "profit_target_atr_mult", "stop_loss_atr_mult",
              "minimum_profit_margin_over_cost", "max_hold_bars")


def _worker():
    spec = importlib.util.spec_from_file_location("r5_step5_worker", ROOT / "scripts/run_r5_step5_candidate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _finite_or_none(value):
    if value is None:
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _mean(values):
    values = [v for v in values if v is not None]
    return sum(values) / len(values) if values else None


def exit_class(trade: dict) -> str:
    reason = str(trade["reason"])
    if reason.startswith("governor_exit:FSR"):
        return "FSR_EXIT"
    if reason.startswith("governor_exit:GOVERNOR_RATCHET_FLOOR"):
        return "PID_FLOOR_EXIT"
    if reason.startswith("governor_exit:"):
        return "GOVERNOR_OTHER"
    if reason in ("stop", "stop_gap"):
        return "PID_STOP" if trade.get("stop_moves", 0) > 0 else "PLAN_STOP"
    if reason in ("target", "target_gap"):
        return "TARGET"
    if reason.startswith("micom_trip"):
        return "MICOM"
    return "TIME_OR_SESSION"


def summarize(trades: list) -> dict:
    net = [t["net_pnl"] for t in trades]
    wins = [x for x in net if x > 0]
    losses = [x for x in net if x < 0]
    r = [t["realized_r"] for t in trades if t["realized_r"] is not None]
    capture = [t["realized_r"] / t["mfe_r"] for t in trades
               if t["realized_r"] is not None and t["mfe_r"] and t["mfe_r"] > 0]
    return {
        "trades": len(trades), "net_pnl": sum(net), "gross_pnl": sum(t["pnl"] for t in trades),
        "profit_factor": (sum(wins) / -sum(losses)) if losses else None,
        "win_rate": len(wins) / len(trades) if trades else None,
        "expectancy_r": _mean(r), "mean_bars_held": _mean([t["bars_held"] for t in trades]),
        "mean_mfe_r": _mean([t["mfe_r"] for t in trades]), "mean_mae_r": _mean([t["mae_r"] for t in trades]),
        "mean_giveback_r": _mean([max(0.0, t["mfe_r"] - t["realized_r"]) for t in trades
                                  if t["realized_r"] is not None]),
        "mfe_capture_fraction": _mean(capture), "mfe_capture_trades": len(capture),
        "exit_classes": dict(Counter(exit_class(t) for t in trades)),
    }


def pair(v2: list, v3: list) -> dict:
    by_id = {t["trade_id"]: t for t in v3}
    rows = []
    for a in v2:
        b = by_id[a["trade_id"]]
        rows.append({
            "trade_id": a["trade_id"], "symbol": a["symbol"], "side": a["side"],
            "entry_timestamp": a["entry_timestamp"],
            "v2": {k: a[k] for k in ("exit_timestamp", "reason", "realized_r", "net_pnl", "bars_held", "mfe_r", "mae_r")},
            "v3": {k: b[k] for k in ("exit_timestamp", "reason", "realized_r", "net_pnl", "bars_held", "mfe_r",
                                     "mae_r", "stop_moves")},
            "delta_r": (b["realized_r"] - a["realized_r"]) if None not in (a["realized_r"], b["realized_r"]) else None,
            "delta_net_pnl": b["net_pnl"] - a["net_pnl"],
            "delta_mae_r": b["mae_r"] - a["mae_r"],
            "v2_exit_class": exit_class(a), "v3_exit_class": exit_class(b),
        })
    deltas = [r["delta_r"] for r in rows if r["delta_r"] is not None]
    v2_losers = [r for r in rows if r["v2"]["net_pnl"] < 0]
    v2_winners = [r for r in rows if r["v2"]["net_pnl"] > 0]
    return {
        "rows": rows,
        "summary": {
            "paired_trades": len(rows), "sum_delta_r": sum(deltas), "mean_delta_r": _mean(deltas),
            "sum_delta_net_pnl": sum(r["delta_net_pnl"] for r in rows),
            "improved": sum(d > 1e-12 for d in deltas), "worsened": sum(d < -1e-12 for d in deltas),
            "unchanged": sum(abs(d) <= 1e-12 for d in deltas),
            "v2_losers_improved": sum((r["delta_r"] or 0) > 1e-12 for r in v2_losers),
            "v2_losers": len(v2_losers),
            "v2_winners_preserved": sum((r["delta_r"] or 0) >= -1e-12 for r in v2_winners),
            "v2_winners": len(v2_winners),
            "mean_delta_mae_r": _mean([r["delta_mae_r"] for r in rows]),
            "v3_stop_actuated_trades": sum(r["v3"]["stop_moves"] > 0 for r in rows),
            "exit_class_transitions": dict(Counter(f"{r['v2_exit_class']}->{r['v3_exit_class']}" for r in rows)),
        },
    }


def run_block(worker, protocol, block, v2_params, v3_overrides):
    from canonical_parameter_registry import CanonicalParameterRegistry
    from revision2_external.grid_context import SealedGridContextProvider
    from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
    from revision5.ccpp_unified_plant import CentralPlantMasterDCS
    from revision5.exit_shadow import ShadowExitBridge, validate_legacy
    from revision5.governor_authority import position_control_v3_from_config

    frames, feeds, audit = worker.prepare_block(ROOT, protocol, block)
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    payload = {**v2_params, **v3_overrides}
    errors = registry.validate_calibration_payload(payload, engine="EXTERNAL")
    if errors:
        raise SystemExit("invalid payload: " + "; ".join(errors))
    equity = float(protocol["block_execution_contract"]["starting_equity_per_block"])
    plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
    # Identical to the V2 worker's execute_block: the real run IS the V2 engine (legacy control).
    orch = Revision2ExternalEngineOrchestrator(
        sorted(frames), registry, calibration_overrides=payload, starting_equity=equity,
        grid_context_provider=SealedGridContextProvider(feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]),
        real_plant_dcs=plant, plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper",
        telemetry_mode="compact", governor_authority="full", governor_position_control="legacy")
    v3_policy = position_control_v3_from_config(orch.config)
    bridge = ShadowExitBridge({"legacy": None, "v3": v3_policy})
    orch.exit_shadow = bridge
    report = orch.run(frames, warmup=int(protocol["block_execution_contract"]["stock_warmup_bars_per_symbol"]))
    check = validate_legacy(report["trades"], bridge.closed["legacy"])
    return {
        "block": int(block["block"]), "sessions": block["sessions"], "slice_sha256": audit["slice_sha256"],
        "v2_real_metrics": worker.metrics(report),
        "v2_trade_list_sha256": hashlib.sha256(json.dumps(report["trades"], sort_keys=True,
                                                          default=str).encode()).hexdigest(),
        "v2_real_trades": report["trades"],
        "legacy_reproduction": check, "v3_policy": vars(v3_policy),
        "v2": summarize(bridge.closed["legacy"]), "v3": summarize(bridge.closed["v3"]),
        "paired": pair(bridge.closed["legacy"], bridge.closed["v3"]),
    }


def _sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    return obj


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--protocol", type=Path, default=DEFAULT_PROTOCOL)
    parser.add_argument("--stage", choices=["A", "B"], required=True)
    parser.add_argument("--block", type=int, action="append", help="block number (repeatable); default all")
    parser.add_argument("--params", type=Path, required=True, help="V2 trading parameters (5 keys)")
    parser.add_argument("--v3-params", type=Path, help="V3 controller overrides (gov_v3_* keys); default registry")
    parser.add_argument("--label", required=True)
    parser.add_argument("--v2-reference", type=Path,
                        help="V2 candidate result JSON for the same params: per-block metrics must match")
    args = parser.parse_args(argv)

    out_dir = (OUTPUT_ROOT / f"bridge_{args.label}_stage{args.stage}").resolve()
    if OUTPUT_ROOT.resolve() not in out_dir.parents:
        parser.error("output must stay under outputs/protocol_v3_state/")
    if "r5_step6" in str(args.params) or (args.v2_reference and "r5_step6" in str(args.v2_reference)):
        parser.error("holdout quarantine: Step-6 state may not be read")
    protocol = json.loads(args.protocol.read_text())
    v2_params = json.loads(args.params.read_text())
    if set(v2_params) != set(V2_SURFACE):
        parser.error(f"--params must contain exactly {sorted(V2_SURFACE)}")
    v3_overrides = json.loads(args.v3_params.read_text()) if args.v3_params else {}
    if not all(k.startswith("gov_v3_") for k in v3_overrides):
        parser.error("--v3-params may only contain gov_v3_* registry parameters")
    blocks = [b for b in protocol["sampling_plan"]["stage_a" if args.stage == "A" else "stage_b"]
              if not args.block or int(b["block"]) in args.block]
    reference = json.loads(args.v2_reference.read_text()) if args.v2_reference else None
    worker = _worker()
    results = []
    for block in blocks:
        started = time.time()
        print(f"block {block['block']} {block['sessions'][0]}..{block['sessions'][-1]} ...", flush=True)
        result = run_block(worker, protocol, block, v2_params, v3_overrides)
        result["runtime_seconds"] = round(time.time() - started, 1)
        if reference is not None:
            # Engine neutrality: the reference ledger was produced by the sealed V2 worker without any
            # shadow observer; the observed run's ledger must hash identically.
            ref = next(b for b in reference["blocks"] if int(b["block"]) == result["block"])
            keys = ("completed_trades", "net_pnl", "gross_pnl", "mtm_max_drawdown_fraction")
            ref_hash = hashlib.sha256(json.dumps(ref["trades"], sort_keys=True, default=str).encode()).hexdigest()
            result["v2_reference_trade_list_sha256"] = ref_hash
            result["v2_reference_match"] = (ref_hash == result["v2_trade_list_sha256"] and all(
                abs(float(ref["metrics"][k]) - float(result["v2_real_metrics"][k])) < 1e-6 for k in keys))
        if not result["legacy_reproduction"]["exact"]:
            raise SystemExit(f"LEGACY_SHADOW_MISMATCH block {result['block']}: "
                             f"{json.dumps(result['legacy_reproduction'], default=str)[:800]}")
        if reference is not None and not result["v2_reference_match"]:
            raise SystemExit(f"V2_REFERENCE_MISMATCH block {result['block']}")
        s = result["paired"]["summary"]
        print(f"  trades {s['paired_trades']}  V2 net {result['v2']['net_pnl']:.2f}  V3 net {result['v3']['net_pnl']:.2f}"
              f"  sum dR {s['sum_delta_r']:+.3f}  improved/worsened {s['improved']}/{s['worsened']}", flush=True)
        results.append(result)
    all_v2 = [r for res in results for r in res["paired"]["rows"]]
    overall = {
        "blocks": len(results),
        "paired_trades": len(all_v2),
        "v2_net_pnl": sum(r["v2"]["net_pnl"] for r in all_v2),
        "v3_net_pnl": sum(r["v3"]["net_pnl"] for r in all_v2),
        "sum_delta_r": sum(r["delta_r"] or 0.0 for r in all_v2),
        "improved": sum((r["delta_r"] or 0) > 1e-12 for r in all_v2),
        "worsened": sum((r["delta_r"] or 0) < -1e-12 for r in all_v2),
        "exit_class_transitions": dict(Counter(f"{r['v2_exit_class']}->{r['v3_exit_class']}" for r in all_v2)),
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    document = _sanitize({"protocol_id": protocol["protocol_id"], "stage": args.stage, "label": args.label,
                          "v2_params": v2_params, "v3_overrides": v3_overrides, "overall": overall,
                          "blocks": results})
    (out_dir / "bridge.json").write_text(json.dumps(document, indent=2, allow_nan=False, default=str))
    print(json.dumps(_sanitize(overall), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
