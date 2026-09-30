#!/usr/bin/env python3
"""Exit-only paired bridge: the same V2 entries, exited by the V2 (legacy) and V3 controllers.

For each sealed training block the V2 engine runs normally (legacy position control) with the
given V2 trading parameters, so every entry -- time, side, fill, quantity, initial stop, target --
is the V2 entry.  ``revision5.exit_shadow.ShadowExitBridge`` mirrors each fill and exits it
under two policies on the engine's own per-bar inputs:

    legacy  must reproduce every real V2 exit exactly (the bridge refuses to report otherwise)
    v3      the Protocol V3 closed-loop controller (registry defaults, or --v3-params)

Per trade:  delta_R = R_v3 - R_v2,  giveback = max(0, MFE - R),  MFE capture = R / MFE (MFE > 0),
MAE delta, exit attribution, and the inner-loop state (integral I_t, error, u, gap) on the bar V3
trailing latches and on the first stop move (bumpless-transfer diagnostic).  Output is strict JSON.

Qualification gates -- bridge.json is written only if every one holds for every block:
    V2_REFERENCE_*                     --v2-reference is required: a sealed V2 candidate result for
                                       the same stage and parameters; the observed run's trade ledger
                                       must hash identically to it (engine neutrality)
    LEGACY_SHADOW_MISMATCH             the legacy shadow reproduces every real exit exactly
    SENTINEL_RECONCILIATION_VIOLATION  no trade (real, legacy or V3) is closed by end-of-run
                                       reconciliation at the block's final row, which is a
                                       non-tradeable boundary sentinel

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

from revision5.exit_shadow import ACTIVATION_FIELDS  # noqa: E402

OUTPUT_ROOT = ROOT / "outputs/protocol_v3_state"
DEFAULT_PROTOCOL = ROOT / "revision5/step5_sealed_calibration_protocol_v3.json"
V2_SURFACE = ("entry_confidence_threshold", "profit_target_atr_mult", "stop_loss_atr_mult",
              "minimum_profit_margin_over_cost", "max_hold_bars")
SENTINEL_REASON = "end_of_run_reconciliation"


def ledger_sha256(trades: list) -> str:
    """Hash of a trade ledger in its JSON form (the sealed worker writes with default=str)."""
    canonical = json.loads(json.dumps(trades, default=str))
    return hashlib.sha256(json.dumps(canonical, sort_keys=True).encode()).hexdigest()


def sentinel_violations(ledgers: dict) -> list:
    return [{"ledger": name, "trade_id": t["trade_id"], "symbol": t.get("symbol"),
             "exit_timestamp": str(t.get("exit_timestamp"))}
            for name, trades in ledgers.items() for t in trades if t.get("reason") == SENTINEL_REASON]


def validate_reference(reference: dict, protocol: dict, stage: str, v2_params: dict, blocks: list) -> None:
    """The reference must be the sealed V2 worker's result for exactly these parameters and blocks."""
    if reference.get("mode") != "CANDIDATE_EVALUATION":
        raise SystemExit(f"V2_REFERENCE_MODE: {reference.get('mode')!r}")
    v2_sha = protocol["supersedes"]["protocol_sha256"]
    if reference.get("protocol_sha256") != v2_sha:
        raise SystemExit(f"V2_REFERENCE_PROTOCOL: {reference.get('protocol_sha256')} != V2 {v2_sha}")
    if reference.get("stage") != stage:
        raise SystemExit(f"V2_REFERENCE_STAGE: {reference.get('stage')!r} != {stage!r}")
    if reference.get("params") != v2_params:
        raise SystemExit("V2_REFERENCE_PARAMS: the reference was produced with different parameters")
    have = {int(b["block"]): b for b in reference.get("blocks", [])}
    for block in blocks:
        ref = have.get(int(block["block"]))
        if ref is None or "trades" not in ref:
            raise SystemExit(f"V2_REFERENCE_BLOCK_MISSING: block {block['block']}")
        if list(ref["sessions"]) != list(block["sessions"]):
            raise SystemExit(f"V2_REFERENCE_SESSIONS: block {block['block']}")


def transfer_summary(trades: list, key: str) -> dict:
    states = [t[key] for t in trades if t.get(key)]
    integrals = sorted(float(x["integral_error"]) for x in states if x.get("integral_error") is not None)
    mid = len(integrals) // 2
    return {
        "trades": len(states),
        "integral_min": integrals[0] if integrals else None,
        "integral_median": ((integrals[mid] if len(integrals) % 2 else (integrals[mid - 1] + integrals[mid]) / 2)
                            if integrals else None),
        "integral_max": integrals[-1] if integrals else None,
        "mean_abs_integral": _mean([abs(x) for x in integrals]),
        "mean_held_bars": _mean([x["held_bars"] for x in states]),
        "mean_gap_r": _mean([x.get("gap_r") for x in states]),
    }


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
        "v3_activation": transfer_summary(trades, "v3_activation"),
        "first_stop_move": transfer_summary(trades, "first_stop_move"),
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
                                     "mae_r", "stop_moves", *ACTIVATION_FIELDS, "v3_activation",
                                     "first_stop_move")},
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
        "v2_trade_list_sha256": ledger_sha256(report["trades"]),
        "v2_real_trades": report["trades"],
        "sentinel_violations": sentinel_violations({"v2_real": report["trades"],
                                                    "legacy_shadow": bridge.closed["legacy"],
                                                    "v3_shadow": bridge.closed["v3"]}),
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
    parser.add_argument("--v2-reference", type=Path, required=True,
                        help=("sealed V2 candidate result JSON for the same stage and params (unshadowed); "
                              "the observed ledger must hash identically, block by block"))
    args = parser.parse_args(argv)

    out_dir = (OUTPUT_ROOT / f"bridge_{args.label}_stage{args.stage}").resolve()
    if OUTPUT_ROOT.resolve() not in out_dir.parents:
        parser.error("output must stay under outputs/protocol_v3_state/")
    if "r5_step6" in str(args.params) or "r5_step6" in str(args.v2_reference):
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
    reference = json.loads(args.v2_reference.read_text())
    validate_reference(reference, protocol, args.stage, v2_params, blocks)
    worker = _worker()
    results = []
    for block in blocks:
        started = time.time()
        print(f"block {block['block']} {block['sessions'][0]}..{block['sessions'][-1]} ...", flush=True)
        result = run_block(worker, protocol, block, v2_params, v3_overrides)
        result["runtime_seconds"] = round(time.time() - started, 1)
        # Engine neutrality: the reference ledger was produced by the sealed V2 worker without any
        # shadow observer; the observed run's ledger must hash identically.
        ref = next(b for b in reference["blocks"] if int(b["block"]) == result["block"])
        keys = ("completed_trades", "net_pnl", "gross_pnl", "mtm_max_drawdown_fraction")
        ref_hash = ledger_sha256(ref["trades"])
        result["v2_reference_trade_list_sha256"] = ref_hash
        result["v2_reference_match"] = (ref_hash == result["v2_trade_list_sha256"] and all(
            abs(float(ref["metrics"][k]) - float(result["v2_real_metrics"][k])) < 1e-6 for k in keys))
        if not result["v2_reference_match"]:
            raise SystemExit(f"V2_REFERENCE_MISMATCH block {result['block']}: "
                             f"ledger {result['v2_trade_list_sha256']} vs reference {ref_hash}")
        if not result["legacy_reproduction"]["exact"]:
            raise SystemExit(f"LEGACY_SHADOW_MISMATCH block {result['block']}: "
                             f"{json.dumps(result['legacy_reproduction'], default=str)[:800]}")
        if result["sentinel_violations"]:
            raise SystemExit(f"SENTINEL_RECONCILIATION_VIOLATION block {result['block']}: "
                             f"{json.dumps(result['sentinel_violations'][:20])}")
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
        "v3_activation": transfer_summary([r["v3"] for r in all_v2], "v3_activation"),
        "v3_first_stop_move": transfer_summary([r["v3"] for r in all_v2], "first_stop_move"),
        "qualified": True,
        "gates": ["V2_REFERENCE_MATCH", "LEGACY_SHADOW_EXACT", "NO_SENTINEL_RECONCILIATION"],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    document = _sanitize({"protocol_id": protocol["protocol_id"], "stage": args.stage, "label": args.label,
                          "v2_reference": {"path": str(args.v2_reference),
                                           "candidate_sha256": reference.get("candidate_sha256")},
                          "v2_params": v2_params, "v3_overrides": v3_overrides, "overall": overall,
                          "blocks": results})
    (out_dir / "bridge.json").write_text(json.dumps(document, indent=2, allow_nan=False, default=str))
    print(json.dumps(_sanitize(overall), indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
