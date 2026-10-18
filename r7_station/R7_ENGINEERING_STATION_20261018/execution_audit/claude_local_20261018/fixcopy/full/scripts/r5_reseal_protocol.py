"""Phase 11: re-seal the Step-5 calibration protocol against the parameter-authority engine.

The V2 protocol (revision5/step5_sealed_calibration_protocol_v2.json, sha256 6861cdd2...) is pinned to
engine c0b4672 and registry 12b700d6...; both are stale.  This tool derives the successor protocol from
V2 WITHOUT changing V2 (a sealed file keeps its hash):

* frozen_parent.commit         = the engine commit (HEAD; the tree must be clean)
* registry_identity_sha256     = the current CanonicalParameterRegistry identity
* engine.controller_modes      = the default (legacy) modes; the opt-in modes are listed as NOT sealed
* parameter_authority          = every controller parameter name with its safety class and eligibility
* supersedes                   = V2 (protocol id + sha256); V2 is marked historical in a sidecar note
* search_space                 = unchanged (calibration has not begun; the reseal only re-pins identity)

and writes PROTOCOL_RESEAL_MANIFEST.json (commit, registry identity, protocol sha256, changed files vs
cb10fac, parameter names, controller modes, test evidence).

    PYTHONPATH="$PWD" python scripts/r5_reseal_protocol.py --tests docs/r5_parameter_authority/TEST_RESULTS.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from canonical_parameter_registry import CanonicalParameterRegistry  # noqa: E402
from revision5.controller_parameters import CONTROLLER_PARAMETER_SPECS  # noqa: E402

V2 = ROOT / "revision5/step5_sealed_calibration_protocol_v2.json"
OUT = ROOT / "revision5/step5_sealed_calibration_protocol_v4_parameter_authority.json"
HISTORICAL_NOTE = ROOT / "revision5/step5_sealed_calibration_protocol_v2.HISTORICAL.md"
MANIFEST = ROOT / "docs/r5_parameter_authority/PROTOCOL_RESEAL_MANIFEST.json"
BASE = "cb10fac5be123f450ede9bb3e8c11ce2ec27817b"

DEFAULT_MODES = {"governor_authority": "advisory", "governor_position_mode": "legacy", "avr_voltage_mode": "legacy",
                 "dispatcher_allocation_mode": "legacy", "hrsg_correlation_mode": "off", "final_exit_authority": False,
                 "authority_telemetry": False, "entry_fill_anchor_mode": "reanchor_distances",
                 "unit_protection_mode": "pre_synchronization", "dynamic_profile_mode": "off",
                 "avr_mvar_limiter_mode": "off"}
OPT_IN_MODES = {"governor_authority": ["full"], "governor_position_mode": ["v3"], "avr_voltage_mode": ["measured"],
                "dispatcher_allocation_mode": ["bounded"], "hrsg_correlation_mode": ["causal_bay_returns"],
                "final_exit_authority": [True], "entry_fill_anchor_mode": ["box6_levels"],
                "unit_protection_mode": ["pre_sync_and_in_flight"], "dynamic_profile_mode": ["consume"],
                "avr_mvar_limiter_mode": ["required"]}


def git(*args) -> str:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tests", type=Path, required=True, help="JSON test evidence to record")
    parser.add_argument("--allow-dirty", action="store_true", help="dry run only")
    args = parser.parse_args()
    dirty = git("status", "--porcelain", "--untracked-files=no")
    if dirty and not args.allow_dirty:
        raise SystemExit(f"refusing to seal a dirty tree:\n{dirty}")
    commit = git("rev-parse", "HEAD")
    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()
    v2 = json.loads(V2.read_text())

    protocol = dict(v2)
    protocol["protocol_id"] = "R5_STEP5_SEALED_CALIBRATION_V4_PARAMETER_AUTHORITY"
    protocol["created_date"] = "2026-10-16"
    protocol["frozen_parent"] = {"commit": commit, "branch": "feature/r5-parameter-authority",
                                 "base": BASE, "note": "engine with R5 controller parameter authority"}
    protocol["registry_identity_sha256"] = registry.FROZEN_IDENTITY_SHA256
    protocol["supersedes"] = {"protocol_id": v2["protocol_id"], "sha256": sha256(V2),
                              "reason": "V2 is pinned to engine c0b4672 and registry 12b700d6...; the engine now owns every "
                                        "native R5 controller value in the registry (identity changed)."}
    engine = dict(protocol.get("engine", {}))
    engine["controller_modes"] = DEFAULT_MODES
    engine["opt_in_modes_not_sealed"] = OPT_IN_MODES
    protocol["engine"] = engine
    protocol["parameter_authority"] = {
        "module": "revision5/controller_parameters.py",
        "parameters": {s.name: {"class": s.safety_class, "optimizer_eligible": registry.is_calibratable(s.name, "EXTERNAL"), "default": s.default}
                       for s in sorted(CONTROLLER_PARAMETER_SPECS, key=lambda s: s.name)},
        "search_space_unchanged": False,
        "search_space_change": "Formerly inactive controls are now eligible where TUNABLE; ranges and cross-bound validation changed. Recalibration is not authorized by resealing.",
        "mvar_input_contract": "required mode needs measured AVR plus a caller-supplied causal signed per-unit measurement per bay/bar; absent or invalid values fail closed; no physical MVAR inferred from OHLC",
        "calibration_status": "NOT_CALIBRATED: no calibration has run against this identity",
    }
    if "distributed_execution" in protocol and isinstance(protocol["distributed_execution"], dict):
        protocol["distributed_execution"] = {**protocol["distributed_execution"], "engine_parent_commit": commit}
    OUT.write_text(json.dumps(protocol, indent=2, sort_keys=True) + "\n")

    HISTORICAL_NOTE.write_text(
        "# HISTORICAL: step5_sealed_calibration_protocol_v2.json\n\n"
        f"`step5_sealed_calibration_protocol_v2.json` (sha256 `{sha256(V2)}`) is pinned to engine `c0b4672` and "
        "registry `12b700d6caa88b7689daf825ff4c512e0513dcb59de1a3c96cf693a820aa277d`. It is kept unchanged as a "
        "historical record and is superseded by "
        f"`{OUT.name}` (sha256 `{sha256(OUT)}`, engine `{commit}`, registry `{registry.FROZEN_IDENTITY_SHA256}`). "
        "No calibration may be run against V2 on this engine.\n")

    changed = git("diff", "--name-only", BASE, commit).split()
    evidence = json.loads(args.tests.read_text())
    manifest = {
        "manifest": "R5_PROTOCOL_RESEAL_PARAMETER_AUTHORITY",
        "engine_commit": commit, "base_commit": BASE,
        "registry_identity_sha256": registry.FROZEN_IDENTITY_SHA256,
        "superseded_registry_identity_sha256": "12b700d6caa88b7689daf825ff4c512e0513dcb59de1a3c96cf693a820aa277d",
        "protocol": {"file": str(OUT.relative_to(ROOT)), "sha256": sha256(OUT), "protocol_id": protocol["protocol_id"]},
        "superseded_protocol": {"file": str(V2.relative_to(ROOT)), "sha256": sha256(V2), "frozen_parent": v2["frozen_parent"],
                                "historical_note": str(HISTORICAL_NOTE.relative_to(ROOT))},
        "surface_counts": registry.surface_counts(),
        "changed_files": changed,
        "parameter_names": sorted(s.name for s in CONTROLLER_PARAMETER_SPECS),
        "controller_modes": {"defaults": DEFAULT_MODES, "opt_in_not_sealed": OPT_IN_MODES},
        "tests": evidence,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"protocol {OUT.name} sha256 {sha256(OUT)}  engine {commit}  registry {registry.FROZEN_IDENTITY_SHA256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
