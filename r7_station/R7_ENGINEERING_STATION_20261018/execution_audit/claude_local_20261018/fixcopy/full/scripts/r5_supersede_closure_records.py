"""Phase 8: write SUPERSEDING copies of the stale closure records (originals are never edited).

Originals (read-only inputs):
  * ~/projects/zerodha-r5-governor-refactor/docs/r5_remediation_project/R5_FINAL_PARAMETER_CLOSURE.csv
    and R5_PARAMETER_REGISTER.json (branch feature/ir02a-parameter-closure @ 59371ff)
  * ~/R5_ENGINEERING_PLATFORM/audit_bundle/WP-R5-001/UPDATED_PARAMETER_REGISTER.json

Output (this branch): docs/r5_parameter_authority/superseding/ -- every row this programme changes is
re-issued with its current classification, its registry owner(s), its runtime consumer and its
sensitivity proof, and links back to the superseded original row (path, commit, sha256).

    python scripts/r5_supersede_closure_records.py --out docs/r5_parameter_authority/superseding
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

HOME = Path.home()
CLOSURE_DIR = HOME / "projects/zerodha-r5-governor-refactor/docs/r5_remediation_project"
WP = HOME / "R5_ENGINEERING_PLATFORM/audit_bundle/WP-R5-001/UPDATED_PARAMETER_REGISTER.json"
ORIGINAL_COMMIT = "59371ff (feature/ir02a-parameter-closure)"
SWEEP = "tests/test_r5_parameter_authority.py::test_every_parameter_sensitivity_matches_its_class"

# id -> (runtime path, active, disposition, registry prefixes / names, consumer, proof, note)
UPDATES = {
    "GV-01": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED",
              "gov_<gtg1|gtg2|cstg1|cstg2|bpstg>_{droop_r,kp,ki,kd,base_z,capital_weight} (TUNABLE); _{atr_barrier,target_m} (ADVISORY); _z_exit (INACTIVE)",
              "revision5/governor.py BayGovernorSpec via R5ControllerParameters.from_config; CentralPlantMasterDCS.apply_controller_parameters",
              SWEEP + " + test_governor_kp_moves_the_entry_threshold_and_admission + tests/test_r5_parameter_authority_engines.py", ""),
    "GV-02": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED",
              "gov_<bay>_{target_r,outcome_window,integral_clamp,dynamic_offset_min,dynamic_offset_max,grid_droop_gain,grid_droop_max}",
              "revision5/governor.py; revision5/dynamic_parameters.py schedule base", SWEEP + " + test_governor_target_and_window_change_decisions", ""),
    "GV-03": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED", "gov_<bay>_feedback_gain",
              "revision5/governor.py BayTurbineClosedLoopGovernor.dynamic_z", SWEEP, ""),
    "PL-01": ("ACTIVE_TRADING_PATH (computed every admission)", "ADVISORY", "REGISTRY_OWNED_ADVISORY",
              "plant_stop_floor_price_fraction, plant_target_min_reward_risk",
              "revision5/ccpp_unified_plant.py TurbineBayPanel.evaluate_admission (stop_loss / take_profit outputs are discarded by Engines A/B/C: the MPC plan owns the order stop and target)",
              SWEEP + " (ADVISORY branch: moves the advisory output, never an actuator)", "was 'never executed by the paper replay loop': false; it executes but is not actuated"),
    "PL-02": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_FIXED_SAFETY", "avr_uel_min_notional_inr, avr_oel_capital_fraction",
              "revision5/ccpp_protection_cubicles.py BayExcitationAVR.calculate_lot_size -> plant quantity -> order quantity", SWEEP,
              "was SIMULATION_ONLY: the AVR lot size is live in PAPER_APPLY (_plant_entry_gate)"),
    "PL-03": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED (floor/ceiling FIXED_SAFETY)", "disp_*",
              "revision5/ccpp_unified_plant.py DynamicBayLoadDispatcher -> HRSG -> bay effective allocation -> order headroom; SectorDispatchController per-bay cap",
              SWEEP + " + test_legacy_dispatcher_can_exceed_the_ceiling_and_bounded_mode_never_does + test_bounded_dispatch_reaches_the_bay_capital_actuator",
              "was SIMULATION_ONLY: active in Engine A/B/C PAPER_APPLY; legacy allocation can exceed the ceiling (opt-in dispatcher_allocation_mode='bounded' fixes it)"),
    "PL-04": ("TESTS_ONLY", "NO", "DEAD_NOT_REGISTERED", "none (MarkVExecutiveController superseded by revision5/governor_authority.py, whose values are registered)",
              "none", "tests/r5_remaining_literals.py register", "classification unchanged (correct): no runtime caller"),
    "PL-05": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED", "plant_loss_cooldown_*, plant_target_cooldown_*",
              "revision5/ccpp_unified_plant.py TurbineBayPanel; revision5/dynamic_parameters.py", SWEEP, ""),
    "ORC-01": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_FIXED_SAFETY", "symbol_loss_cooldown_bars",
               "revision2_external/orchestrator.py authoritative close bookkeeping", "tests/test_r5_parameter_authority_engines.py::test_symbol_loss_cooldown_and_trip_count_change_entry_admission", ""),
    "ORC-02": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_FIXED_SAFETY", "symbol_trip_consecutive_losses",
               "revision2_external/orchestrator.py authoritative close bookkeeping", "tests/test_r5_parameter_authority_engines.py::test_symbol_loss_cooldown_and_trip_count_change_entry_admission", ""),
    "ORC-03": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_FIXED_SAFETY", "plant_bay_lockout_consecutive_stops",
               "revision5/ccpp_unified_plant.py TurbineBayPanel.register_outcome", SWEEP, ""),
    "MI-02": ("ACTIVE_PROTECTION_PATH (ANSI 60FL/40/24); NO RUNTIME CALLER (ANSI 87G/32R)", "PARTIAL", "REGISTRY_OWNED_FIXED_SAFETY / INACTIVE",
              "sel_stale_tick_*, sel_*spread*, sel_vol_surge_* (FIXED_SAFETY); sel_*slippage*, sel_*adverse* (INACTIVE)",
              "revision5/ccpp_protection_cubicles.py BayUnitProtectionSEL300G.check_pre_synchronization (admission + position control); check_in_flight_protection has no runtime caller",
              SWEEP, "was SIMULATION_ONLY: pre-synchronization checks trip live admissions and positions"),
    "MI-03": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED (TUNABLE / FIXED_SAFETY; MVAR limiter INACTIVE)", "avr_*",
              "revision5/ccpp_protection_cubicles.py BayExcitationAVR; revision5/dynamic_parameters.py AVRRuntimeProfile",
              SWEEP + " + test_avr_kp_ki_kd_change_excitation_and_limits_clamp_the_command + test_nonzero_measured_voltage_error_changes_excitation_and_reaches_order_quantity",
              "was SIMULATION_ONLY: the lot size is live; the PID is effective only with avr_voltage_mode='measured' (legacy error is always 0)"),
    "HR-01": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED (collapse / max pressure / min penalty FIXED_SAFETY)", "hrsg_*",
              "revision5/hrsg.py HeatRecoverySteamGenerator.step_bar / balance_capital (every PAPER_APPLY bar); superheat_headroom_factor has no caller",
              SWEEP + " + test_hrsg_lag_and_collapse_threshold_change_pressure_allocation_and_exit",
              "was SIMULATION_ONLY: runs every bar; correlation damper inert unless hrsg_correlation_mode='causal_bay_returns'"),
    "DP-01": ("ACTIVE_TRADING_PATH", "YES", "REGISTRY_OWNED_NOT_CALIBRATED", "gsch_*, dyn_*, avr_* schedule, sel_* / micom_* schedule, plant_*_cooldown_*",
              "revision5/dynamic_parameters.py Revision5DynamicParameterController.evaluate (every PAPER_APPLY bar)", SWEEP,
              "was SIMULATION_ONLY: drives every bay's governor, AVR, relays and cooldowns; strategy/execution/safety/dispatcher snapshot outputs have NO consumer and are not registered"),
    "CC-07": ("OFFLINE_EXPERIMENT", "NO", "SUPERSEDED_BY_V3", "gov_v3_* (Protocol V3 inner loop, governor_position_mode='v3')",
              "revision5/governor.py PositionControlV3", "tests/test_r5_parameter_authority.py::test_each_v3_parameter_changes_the_inner_loop_floor",
              "GovernorPositionPolicy is inactive by default (orchestrator.governor_position_policy is None)"),
}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    closure = CLOSURE_DIR / "R5_FINAL_PARAMETER_CLOSURE.csv"
    original_rows = {r["ID"]: r for r in csv.DictReader(open(closure))}
    link = f"{closure} @ {ORIGINAL_COMMIT} sha256={sha(closure)}"
    out_rows = []
    for rid, (path, active, disposition, owners, consumer, test, note) in UPDATES.items():
        original = original_rows[rid]
        row = dict(original)
        row.update({"RUNTIME_PATH": path, "ACTIVE_IN_PRODUCTION": active, "FINAL_DISPOSITION": disposition,
                    "TEST": test, "CODE_LOCATION": consumer, "CLOSED_YES_NO": "REGISTRY_OWNED (calibration pending reseal)",
                    "REASON": note or original["REASON"]})
        row["REGISTRY_OWNERS"] = owners
        row["SUPERSEDES"] = f"{rid} in {link}"
        row["SUPERSEDED_ROW_DISPOSITION"] = f"{original['RUNTIME_PATH']} / {original['ACTIVE_IN_PRODUCTION']} / {original['FINAL_DISPOSITION']}"
        out_rows.append(row)
    fields = list(out_rows[0])
    with open(args.out / "R5_FINAL_PARAMETER_CLOSURE.SUPERSEDING.csv", "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(out_rows)

    wp = json.loads(WP.read_text())
    superseding = []
    for item in wp["parameters"]:
        rid = item.get("id")
        if rid in UPDATES:
            path, active, disposition, owners, consumer, test, note = UPDATES[rid]
            superseding.append({**item, "runtime_path": path, "active_in_production": active,
                                "final_disposition": disposition, "registry_owners": owners, "consumer": consumer,
                                "sensitivity_proof": test, "note": note,
                                "supersedes": f"{rid} in {WP} sha256={sha(WP)}"})
    (args.out / "WP-R5-001_UPDATED_PARAMETER_REGISTER.SUPERSEDING.json").write_text(json.dumps(
        {"supersedes": str(WP), "supersedes_sha256": sha(WP), "parameters": superseding}, indent=2) + "\n")

    register = CLOSURE_DIR / "R5_PARAMETER_REGISTER.json"
    (args.out / "SUPERSEDED_ORIGINALS.json").write_text(json.dumps({
        "note": "Originals are unmodified historical records; the rows listed are superseded by the files in this folder.",
        "originals": {str(closure): sha(closure), str(register): sha(register), str(WP): sha(WP)},
        "original_commit": ORIGINAL_COMMIT, "superseded_ids": sorted(UPDATES)}, indent=2) + "\n")
    print(f"{len(out_rows)} closure rows and {len(superseding)} WP-R5-001 rows superseded -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
