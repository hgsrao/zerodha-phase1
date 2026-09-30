#!/usr/bin/env python3
"""Build revision5/step5_sealed_calibration_protocol_v3.json from the sealed V2 protocol.

V3 is an incremental experiment: V2's data, blocks, seed, trial count, scheduler, objective,
gates and tie-breaks are copied unchanged.  Only the engine's position control changes
(``closed_loop_v3``), the five V2 trading parameters are frozen to the V2 Stage-B winner, and the
optimizer searches the four V3 controller parameters.

Identity chain (recorded here, verified by the worker):
    PROTOCOL_V3_SHA   = SHA-256 of the written protocol file (printed; a file cannot hold its own hash)
    ENGINE_V3         = frozen Git commit (``engine_parent_commit``) + the worker's clean-tree drift check
    REGISTRY_V3       = canonical registry identity (``registry_identity_sha256``)
    WORKER_V3_SHA     = SHA-256 of scripts/run_r5_step5_candidate.py at the engine commit

    python scripts/protocol_v3/build_protocol_v3.py --engine-commit <sha>
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

V2 = ROOT / "revision5/step5_sealed_calibration_protocol_v2.json"
V3 = ROOT / "revision5/step5_sealed_calibration_protocol_v3.json"

# V2 Stage-B winner (Stage A trial 7; Stage B score -37,611.2427 over 6 blocks).
V2_STAGE_B_WINNER = {
    "entry_confidence_threshold": 0.2800762797438398,
    "max_hold_bars": 89,
    "minimum_profit_margin_over_cost": 1.975907898675508,
    "profit_target_atr_mult": 1.2468995744109899,
    "stop_loss_atr_mult": 1.183652189381975,
}
V3_SURFACE = ("gov_v3_mfe_activation_r", "gov_v3_kappa", "gov_v3_gamma_fast", "gov_v3_tau_error_multiplier")
V3_FROZEN_CONTROLLER = ("gov_v3_gamma_slow", "gov_v3_base_gap_r", "gov_v3_minimum_gap_r",
                        "gov_v3_noise_floor_mult", "gov_v3_grace_bars")


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(*args) -> str:
    return subprocess.check_output(["git", "-C", str(ROOT), *args], text=True).strip()


def build(engine_commit: str) -> dict:
    from canonical_parameter_registry import CanonicalParameterRegistry
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    full = _git("rev-parse", engine_commit)
    worker_sha = hashlib.sha256(subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{full}:scripts/run_r5_step5_candidate.py"])).hexdigest()
    executor_sha = hashlib.sha256(subprocess.check_output(
        ["git", "-C", str(ROOT), "show", f"{full}:scripts/run_r5_step5_stage_a_executor.py"])).hexdigest()
    v2_sha = _sha(V2)
    p = copy.deepcopy(json.loads(V2.read_text()))
    p["protocol_id"] = "R5_STEP5_SEALED_CALIBRATION_V3"
    p["created_date"] = "2026-09-30"
    p["supersedes"] = {
        "protocol_id": "R5_STEP5_SEALED_CALIBRATION_V2", "protocol_sha256": v2_sha,
        "reason": ("Incremental controller experiment: identical V2 data, blocks, seed, trial count, scheduler, "
                   "objective and gates; only the governor's position control changes (closed_loop_v3)."),
    }
    p["engine"]["governor_authority"] = "full"
    p["engine"]["governor_position_control"] = "closed_loop_v3"
    p["engine"]["governor_position_control_note"] = (
        "revision5.governor.PositionControlV3: two-rate leaky inner integral (Ki applied once, clamped at the "
        "bay integral clamp), no derivative kick on the first or activation sample, per-bar trailing gap "
        "base_gap*(1-kappa*tanh(u)) floored by one bar of noise and minimum_gap, trailing eligible after "
        "grace_bars and mfe_activation_r, one-way floor armed for the next bar, no binary path-error exit, "
        "no order-flow input.  Reference path applies curve_gamma once.  FSRN and every other exit unchanged.")
    for key in ("distributed_execution",):
        p[key]["engine_parent_commit"] = full
        p[key]["cross_machine_equivalence"] = {
            "status": "PENDING", "candidate_a_sha256": None, "candidate_b_sha256": None,
            "paper_apply_reference_sha256": None,
            "note": "V2: desktop reproduced laptop trial 1 exactly (score to 14 digits); re-establish for V3."}
    p["frozen_parent"]["commit"] = full
    p["registry_identity_sha256"] = registry.identity_sha256()
    p["identity"] = {
        "engine_commit": full, "registry_identity_sha256": registry.identity_sha256(),
        "worker_sha256": worker_sha, "stage_a_executor_sha256": executor_sha,
        "protocol_sha256": "SHA-256 of this file; printed by build_protocol_v3.py and recorded in the commit",
    }
    p["fixed_parameters"] = dict(V2_STAGE_B_WINNER)
    p["fixed_parameters_source"] = ("V2 Stage-B winner: Stage A trial 7 (score -24,907.96), Stage B score "
                                    "-37,611.24 over 6 blocks, 271 trades, 0 safety violations.")
    p["search_surface"] = list(V3_SURFACE)
    p["search_space"] = {
        name: {"default": registry.params[name].default, "minimum": registry.params[name].minimum,
               "maximum": registry.params[name].maximum, "type": registry.params[name].param_type}
        for name in V3_SURFACE}
    p["immutable_during_step5"] = [
        "all plant-control parameters except the four V3 search parameters (search_surface)"
        if item == "all plant-control parameters" else item for item in p["immutable_during_step5"]] + [
        "V2 trading parameters (fixed_parameters)", "V3 frozen controller constants: " + ", ".join(V3_FROZEN_CONTROLLER),
        "bay Kp/Ki/Kd, droop and integral clamps"]
    p["optimizer"]["trial_zero"] = "canonical current defaults (V3 registry defaults on the frozen V2 trading parameters)"
    p["pre_search_bridge"] = {
        "tool": "scripts/protocol_v3/paired_v2_v3_bridge.py",
        "required_before_stage_a": True,
        "cases": ["V2 Stage-A trial 0 parameters", "V2 Stage-A trial 7 parameters"],
        "blocks": "the same Stage-A blocks",
        "gate": "the legacy shadow must reproduce every real V2 exit exactly and the real run must match the V2 result",
        "design": "exit-only: identical entries; V3 differs only in the exit policy",
    }
    p["validation"]["holdout_eligibility"] = (
        "The 2025-07-01..2026-01-01 window may serve as the V3 holdout only if the V2 Step-6 validation was never "
        "executed or observed. Otherwise the V3 verdict uses the fresh post-freeze OOS period (periods.fresh_oos_rule).")
    p["contamination_rules"]["v2_step6_outputs_used_in_v3_design"] = False
    return p


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--engine-commit", required=True)
    args = parser.parse_args(argv)
    protocol = build(args.engine_commit)
    V3.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n")
    print("PROTOCOL_V3_SHA", _sha(V3))
    print("ENGINE_V3_COMMIT", protocol["identity"]["engine_commit"])
    print("REGISTRY_V3_IDENTITY", protocol["identity"]["registry_identity_sha256"])
    print("WORKER_V3_SHA", protocol["identity"]["worker_sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
