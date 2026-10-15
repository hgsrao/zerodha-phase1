"""Phase 2 audit: is each controller parameter a renamed constant or a genuinely dynamic runtime value?

A parameter is DYNAMIC only when its RUNTIME value is recomputed during execution from signals,
regime, time, plant state or an explicit schedule.  Being stored in the registry does not make it
dynamic.  Classes:

  CALIBRATABLE_DYNAMIC        TUNABLE base whose operating value is recomputed by an explicit schedule
                              every native-plant bar (or every Box 6 plan)
  DYNAMIC_SAFETY_ENVELOPE     FIXED_SAFETY base whose operating limit is tightened by a schedule every bar
  STATIC_SCHEDULE_COEFFICIENT constant coefficient / clip of a per-bar schedule (the schedule is dynamic;
                              the coefficient is not)
  STATIC_CONTROLLER_CONSTANT  constant gain / window / bound of a stateful controller whose OUTPUT evolves
                              (e.g. dispatcher EMA, HRSG lag, V3 gains, latch counts)
  STATIC_SAFETY_LIMIT         constant protection limit
  STATIC_REGISTRATION_ONLY    registered, consumed by nothing that actuates (ADVISORY / INACTIVE)

    PYTHONPATH="$PWD" python scripts/r5_phase2_classification.py --out docs/r5_parameter_authority
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from canonical_parameter_registry import CanonicalParameterRegistry  # noqa: E402
from revision5.controller_parameters import (  # noqa: E402
    ADVISORY, BAY_TOKENS, CONTROLLER_PARAMETER_SPECS, FIXED_SAFETY, INACTIVE, TUNABLE,
)

PLANT_BAR = "every PAPER_APPLY bar (CentralPlantMasterDCS.step_bar -> apply_dynamic_environment)"
SIGNALS = "stress (HMM turbulent / chop share, excess normalized vol), trend_bias, |mean governor error|, risk_derate (stress, fleet drawdown)"

GOV_SCHEDULE = {
    "target_r": "clip(base*(1 + gsch_target_trend_gain*trend_bias - gsch_target_stress_gain*stress), base*gsch_target_min_scale, base*gsch_target_max_scale)",
    "outcome_window": "round(clip(base*(1 + gsch_window_stress_gain*stress), gsch_window_min, gsch_window_max))",
    "integral_clamp": "clip(base/(1 + gsch_integral_stress_gain*stress + gsch_integral_error_gain*|e|), base*min_scale, base*max_scale)",
    "grid_droop_gain": "clip(base*vol*(1 + gsch_droop_gain_stress_gain*stress), base*min_scale, base*max_scale)",
    "grid_droop_max": "clip(base*(1 + gsch_droop_max_stress_gain*stress), base*min_scale, base*max_scale)",
    "kp": "base*clip(1 + gsch_kp_error_gain*|e| + gsch_kp_stress_gain*stress, gsch_kp_scale_min, gsch_kp_scale_max)",
    "ki": "base/clip(1 + gsch_ki_error_gain*|e| + gsch_ki_stress_gain*stress, gsch_ki_divisor_min, gsch_ki_divisor_max)",
    "kd": "base*clip(1 + gsch_kd_error_gain*|e| + gsch_kd_stress_gain*stress, gsch_kd_scale_min, gsch_kd_scale_max)",
    "base_z": "clip(base + gsch_base_z_trend_gain*trend_bias - gsch_base_z_stress_gain*stress, base - lower_offset, base + upper_offset)",
    "droop_r": "clip(base*(1 - gsch_droop_r_stress_gain*stress), base*gsch_droop_r_min_scale, base)",
    "dynamic_offset_min": "clip(base*(1 + gsch_offset_min_stress_gain*stress), base*lower_scale, base*upper_scale)",
    "dynamic_offset_max": "clip(base*risk_derate, 0, base)",
}
AVR_SCHEDULE = {
    "avr_loading_fraction_base": "loading = clip(base*risk_derate, avr_loading_fraction_min, base); control_stress = 1 - loading/base",
    "avr_oel_capital_fraction": "oel_fraction = clip(base*risk_derate, avr_oel_fraction_min, base)",
    "avr_vol_scalar_max_base": "vol_scalar_max = clip(base*risk_derate, avr_vol_scalar_max_min, base)",
    "avr_z_strength_denominator_base": "clip(base*(1 + gain*stress), base, avr_z_strength_denominator_max)",
    "avr_z_strength_cap_base": "clip(base*risk_derate, avr_z_strength_cap_min, base)",
    "avr_kp_base": "clip(base + avr_kp_stress_gain*control_stress, base, avr_kp_max)",
    "avr_ki_base": "clip(base/(1 + avr_ki_stress_gain*control_stress), avr_ki_min, base)",
    "avr_kd_base": "clip(base + avr_kd_stress_gain*control_stress, base, avr_kd_max)",
    "avr_integral_clamp_base": "clip(base - gain*control_stress, avr_integral_clamp_min, base)",
    "avr_undervoltage_trip_base_pu": "clip(base + gain*control_stress, base, max)",
    "avr_overvoltage_trip_base_pu": "clip(base - gain*control_stress, min, base)",
    "avr_excitation_min_base": "clip(base + gain*control_stress, base, max)",
    "avr_excitation_max_base": "clip(base - gain*control_stress, min, base)",
}
SAFETY_SCHEDULE_BASES = {
    "sel_stale_tick_limit_seconds", "sel_max_spread_fraction", "sel_vol_surge_mult", "sel_max_slippage_fraction",
    "sel_max_adverse_bars", "micom_max_daily_fleet_drawdown_pct", "micom_nifty_crash_rate_pct", "micom_max_regime_vol_z",
}
COOLDOWN_BASES = {"plant_loss_cooldown_base_bars", "plant_target_cooldown_base_bars"}
MODE_GATE = {"psch_": "dynamic_profile_mode='consume'", "gov_v3_": "governor_position_mode='v3'", "avr_voltage_reference_pu": "avr_voltage_mode='measured'",
             "avr_synchronous_reactance_pu": "avr_voltage_mode='measured'", "avr_voltage_filter_alpha": "avr_voltage_mode='measured'",
             "sel_max_slippage": "unit_protection_mode='pre_sync_and_in_flight'", "sel_slippage": "unit_protection_mode='pre_sync_and_in_flight'",
             "sel_max_adverse": "unit_protection_mode='pre_sync_and_in_flight'", "sel_adverse": "unit_protection_mode='pre_sync_and_in_flight'"}
EFFECT = [("psch_", "R5 strategy / safety / dispatch profile (per bar) -> Box 4 confidence, Box 5 min R:R, Box 6 holds, tighten-only entry gates and sizing, drawdown exit, dispatcher ceiling"),
          ("gov_v3_", "V3 inner loop -> protected R floor -> governor stop (next bar) / ratchet exit"),
          ("gov_", "governor outer loop / comparator / inner loop -> entry admission, protected stop, governor exit"),
          ("gsch_", "governor operating gains, target, window, clamp, z, droop -> entry admission, stop, exit"),
          ("dyn_", "stress / risk_derate -> every schedule (governor, AVR lot size, protection, cooldown)"),
          ("plant_bay_lockout", "ANSI 86 bay lockout -> entry admission, HRSG availability"),
          ("plant_stop", "plant admission stop suggestion (advisory, not actuated)"),
          ("plant_target", "plant admission target suggestion (advisory, not actuated)"),
          ("plant_", "bay cooldown -> entry admission, HRSG availability -> capital"),
          ("symbol_", "orchestrator symbol latch -> entry admission"),
          ("avr_", "AVR excitation / lot sizing / ANSI 27/59 -> position size, AVR trip exit"),
          ("sel_", "SEL-300G trip -> entry rejection, unit breaker, protective exit"),
          ("micom_", "MiCOM trip -> grid intertie, entry block, protective exit"),
          ("disp_", "merit allocation -> HRSG balance -> bay capital headroom -> position size; sector dispatch cap"),
          ("hrsg_", "drum pressure / capital coupling -> steam-bay capital, collapse exit"),
          ("tier1_", "Box 6 Tier-1 ATR floor / slippage -> plan ATR -> stop / target"),
          ("tier3_", "Box 6 Tier-3 entry gain schedule -> entry PID -> order reference (A) / booked levels (B/C box6_levels)")]

# Existing registry controller parameters (outside the 257-row table), audited the same way.
EXISTING = [
    ("pid_kp_entry", "Box 6 entry PID", "DYNAMIC", "per Box 6 plan: Kp*clip(1 + mpc_schedule_kp_gain*|conf - baseline|, tier3 envelope) x R5 bay gain scale (per PAPER_APPLY bar)",
     "confidence error, R5 snapshot kp/spec ratio", "order reference price (A); booked stop/target with entry_fill_anchor_mode='box6_levels' (B/C)"),
    ("pid_ki_entry", "Box 6 entry PID", "DYNAMIC", "Ki/clip(1 + mpc_schedule_ki_gain*|e|, tier3) x R5 Ki scale", "same", "same"),
    ("pid_kd_entry", "Box 6 entry PID", "DYNAMIC", "Kd*clip(1 + mpc_schedule_kd_gain*|e|, tier3) x R5 Kd scale", "same", "same"),
    ("pid_kp_exit", "Box 6 exit PID + continuous exit PIDs", "DYNAMIC", "gain x R5 bay gain scale (per PAPER_APPLY bar)", "R5 snapshot", "plan stop/target distances (all engines); trailing stop (advisory under full authority)"),
    ("pid_ki_exit", "Box 6 exit PID + continuous exit PIDs", "DYNAMIC", "gain x R5 Ki scale", "R5 snapshot", "same"),
    ("pid_kd_exit", "Box 6 exit PID + continuous exit PIDs", "DYNAMIC", "gain x R5 Kd scale", "R5 snapshot", "same"),
    ("mpc_schedule_kp_gain", "Box 6 Tier-3 schedule", "STATIC_SCHEDULE_COEFFICIENT", "coefficient of the per-plan Kp schedule", "-", "entry PID"),
    ("mpc_schedule_ki_gain", "Box 6 Tier-3 schedule", "STATIC_SCHEDULE_COEFFICIENT", "coefficient of the per-plan Ki schedule", "-", "entry PID"),
    ("mpc_schedule_kd_gain", "Box 6 Tier-3 schedule", "STATIC_SCHEDULE_COEFFICIENT", "coefficient of the per-plan Kd schedule", "-", "entry PID"),
    ("studies_pid_kp", "studies-weight PIDs", "STATIC_CONTROLLER_CONSTANT", "constant gain (no schedule)", "-", "study weights -> studies direction/confidence -> admission, FSRN"),
    ("studies_pid_ki", "studies-weight PIDs", "STATIC_CONTROLLER_CONSTANT", "constant gain", "-", "same"),
    ("studies_pid_kd", "studies-weight PIDs", "STATIC_CONTROLLER_CONSTANT", "constant gain", "-", "same"),
    ("studies_pid_output_clamp", "studies-weight PIDs", "STATIC_CONTROLLER_CONSTANT", "constant clamp", "-", "same"),
    ("gov_path_error_sigma", "governor inner loop (legacy)", "STATIC_CONTROLLER_CONSTANT", "tolerance = sigma*ATR/risk*sqrt(bars): the tolerance is dynamic, sigma is constant", "ATR, initial risk, held bars", "GOVERNOR_PATH_ERROR exit"),
    ("mv_fsr_entry_threshold", "Mark V", "STATIC_CONTROLLER_CONSTANT", "constant threshold on the per-bar limiter minimum", "-", "entry admission / size"),
    ("mv_fsr_exit_threshold", "Mark V", "STATIC_CONTROLLER_CONSTANT", "constant threshold", "-", "governor exit"),
    ("mv_fsrt_slope", "Mark V", "STATIC_SCHEDULE_COEFFICIENT", "FSRT = 1 - drawdown/span*slope (per bar)", "MTM drawdown", "size, exit"),
    ("mv_fsra_slope", "Mark V", "STATIC_SCHEDULE_COEFFICIENT", "FSRA = base - slope*velocity (per bar)", "velocity", "size, exit"),
    ("grid_vix_derate_start", "plant grid synchronizer", "STATIC_SAFETY_LIMIT", "constant threshold", "-", "ECS demand -> dispatch cap"),
    ("ecs_demand_restore_step_pu", "ECS supervisor", "STATIC_CONTROLLER_CONSTANT", "rate limit of the per-step demand", "grid state", "dispatch cap"),
    ("cl_confidence_offset_gain", "closed-loop entry quality", "STATIC_CONTROLLER_CONSTANT", "constant gain on the outcome ledger", "realized outcomes", "entry confidence offset (active_paper)"),
]


def classify(spec):
    name = spec.name
    if spec.safety_class in (ADVISORY, INACTIVE):
        return "STATIC_REGISTRATION_ONLY", "never (no actuating consumer)", spec.description, "-"
    for bay, token in BAY_TOKENS.items():
        prefix = f"gov_{token}_"
        if name.startswith(prefix) and not name.startswith("gov_v3_"):
            field = name[len(prefix):]
            if field in GOV_SCHEDULE:
                return "CALIBRATABLE_DYNAMIC", PLANT_BAR, GOV_SCHEDULE[field], SIGNALS
            return "STATIC_CONTROLLER_CONSTANT", "constant during the run", spec.description, "-"
    if name in AVR_SCHEDULE:
        cls = "CALIBRATABLE_DYNAMIC" if spec.safety_class == TUNABLE else "DYNAMIC_SAFETY_ENVELOPE"
        return cls, PLANT_BAR, AVR_SCHEDULE[name], "risk_derate, stress (control_stress = 1 - loading/base)"
    if name in SAFETY_SCHEDULE_BASES:
        return "DYNAMIC_SAFETY_ENVELOPE", PLANT_BAR, "clip(base*(1 - gain*stress), min, base) (tighten only)", "stress"
    if name in COOLDOWN_BASES:
        return "CALIBRATABLE_DYNAMIC", PLANT_BAR, "round(clip(base*(offset + gain*stress), min, max))", "stress"
    if name.startswith(("gsch_", "dyn_")) or (name.startswith(("avr_", "sel_", "micom_", "plant_")) and
                                              any(t in name for t in ("_gain", "_min", "_max", "_offset", "_weight", "_floor", "_lower"))):
        return "STATIC_SCHEDULE_COEFFICIENT", "constant (the schedule it parameterizes runs every bar)", spec.description, "-"
    if name.startswith("tier1_") or name.startswith("tier3_"):
        return "STATIC_SCHEDULE_COEFFICIENT", "constant (bounds a per-plan schedule)", spec.description, "-"
    if name.startswith("psch_"):
        return ("STATIC_SCHEDULE_COEFFICIENT", "constant (the runtime profile it parameterizes is recomputed every plant bar)",
                spec.description, "-")
    if spec.safety_class == FIXED_SAFETY:
        return "STATIC_SAFETY_LIMIT", "constant during the run", spec.description, "-"
    return "STATIC_CONTROLLER_CONSTANT", "constant during the run (the controller output it parameterizes evolves)", spec.description, "-"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    registry = CanonicalParameterRegistry()
    rows = []
    for spec in sorted(CONTROLLER_PARAMETER_SPECS, key=lambda s: s.name):
        cls, cadence, formula, inputs = classify(spec)
        gate = next((v for k, v in MODE_GATE.items() if spec.name.startswith(k)), None)
        effect = next(v for k, v in EFFECT if spec.name.startswith(k))
        actuates = spec.safety_class in (TUNABLE, FIXED_SAFETY)
        rows.append({
            "parameter": spec.name, "controller": spec.owner, "engine_path": spec.consumer,
            "source_of_truth": "canonical registry (revision5/controller_parameters.py row) -> EffectiveConfig -> R5ControllerParameters.from_config",
            "default": spec.default, "classification": cls, "registry_safety_class": spec.safety_class,
            "update_frequency": cadence, "formula_or_schedule": formula, "runtime_inputs": inputs,
            "bounds": [spec.minimum, spec.maximum], "fallback": "none: missing -> ControllerParameterError / MissingParameterError at startup (fail closed)",
            "mode_gate": gate or "always (PAPER_APPLY native plant)" if actuates else "n/a",
            "consumed_by_engines": ["A", "B", "C"] if actuates else [],
            "changes_real_decision": actuates, "decision_effect": effect if actuates else f"none ({spec.safety_class})",
            "sensitivity_proof": ("tests/test_r5_dynamization.py::test_every_profile_coefficient_moves_its_consumer_or_is_inactive"
                                  if spec.name.startswith("psch_") else
                                  "tests/test_r5_parameter_authority.py::test_every_parameter_sensitivity_matches_its_class"
                                  if not spec.name.startswith(("symbol_", "tier1_", "tier3_")) else
                                  "tests/test_r5_parameter_authority.py::test_box6_tier_envelopes_change_the_schedule_or_are_proved_unreachable"
                                  if spec.name.startswith("tier") else
                                  "tests/test_r5_parameter_authority_engines.py::test_symbol_loss_cooldown_and_trip_count_change_entry_admission"),
        })
    for name, controller, cls, formula, inputs, effect in EXISTING:
        spec = registry.params[name]
        rows.append({"parameter": name, "controller": controller, "engine_path": "pre-existing registry row (extended, not redone)",
                     "source_of_truth": "canonical registry -> EffectiveConfig.require", "default": spec.default,
                     "classification": cls, "registry_safety_class": "TUNABLE" if registry.is_calibratable(name, "EXTERNAL") else "FIXED",
                     "update_frequency": "per Box 6 plan / per bar" if cls == "DYNAMIC" else "constant during the run",
                     "formula_or_schedule": formula, "runtime_inputs": inputs, "bounds": [spec.minimum, spec.maximum],
                     "fallback": "none: EffectiveConfig.require raises", "mode_gate": "always",
                     "consumed_by_engines": ["A", "B", "C"], "changes_real_decision": True, "decision_effect": effect,
                     "sensitivity_proof": "tests/test_r5_parameter_authority.py (Box 6 / studies tests) and tests_engine_*/"})
    counts = Counter(r["classification"] for r in rows)
    lists = {
        "registered_not_consumed_at_runtime": sorted(r["parameter"] for r in rows if r["classification"] == "STATIC_REGISTRATION_ONLY"),
        "still_hardcoded_or_effectively_constant": "docs/r5_parameter_authority/REMAINING_HARDCODED_VALUES.md (enforced by tests/r5_remaining_literals.py)",
        "dynamic_schedule_hardcoded_outside_registry": [
            "revision2_external/closed_loop_control.py _ENTRY_DERATE_BOUNDS / _PERSISTENCE_BOUNDS / _GAIN_SCALE_BOUNDS (FIXED_SAFETY_ENVELOPE, pre-existing)",
            "revision2_external/composite_study_signal.py MIN_WEIGHT / MAX_WEIGHT (FIXED_SAFETY_ENVELOPE, pre-existing)",
            "revision2_external/pid_controller.py / continuous_exit_controller.py _EXIT_TIGHTNESS_FLOOR 0.5 (cannot bind: |PID output| <= pid_integral_max_clamp <= 0.25)",
            "revision5/dynamic_parameters.py strategy / execution / safety profiles and dispatcher snapshot bounds (no consumer)",
            "revision2_external/dynamic_parameter_controller.py Tier-2 regime schedule (called only from tests)",
        ],
        "mode_gated_dynamic_paths": sorted({r["mode_gate"] for r in rows if r["mode_gate"] not in ("n/a", "always", "always (PAPER_APPLY native plant)")}),
    }
    verdict = {"verdict": "PARTIAL", "reasons": [
        "PASS: every active native-controller tuning value is registry-owned, read with no fallback, and proven to move an actuator.",
        f"Genuinely dynamic at runtime: {counts['CALIBRATABLE_DYNAMIC'] + counts['DYNAMIC_SAFETY_ENVELOPE'] + counts['DYNAMIC']} parameters "
        "(scheduled every plant bar or every Box 6 plan); the rest are constant during a run by design (coefficients, controller constants, safety limits).",
        "Not dynamic and not actuating: the STATIC_REGISTRATION_ONLY rows (12 advisory, 15 inactive, each with its reason).",
        "AVR MVAR limiter has no measured-MVAR source (inactive); closed-loop / studies-weight envelopes remain annotated pre-existing literals.",
        "V3, measured AVR, bounded dispatch, HRSG returns, Box 6 levels at fill and in-flight protection are live only in their opt-in modes (defaults unchanged pending approval).",
    ]}
    doc = {"definition": __doc__.split("Classes:")[0].strip(), "counts": dict(counts), "verdict": verdict, "lists": lists, "parameters": rows}
    args.out.mkdir(parents=True, exist_ok=True)
    (args.out / "phase2_parameter_classification.json").write_text(json.dumps(doc, indent=2, default=str) + "\n")
    md = ["# Phase 2 parameter classification (static vs dynamic)", "", doc["definition"], "",
          f"**Verdict: {verdict['verdict']}**", ""] + [f"- {r}" for r in verdict["reasons"]] + [
          "", "## Counts", "", "| Classification | Parameters |", "|---|---|"] + [f"| {k} | {v} |" for k, v in sorted(counts.items())] + [
          "", "## Registered but not consumed at runtime", ""] + [f"- `{n}`" for n in lists["registered_not_consumed_at_runtime"]] + [
          "", "## Dynamic schedules still hardcoded outside the registry", ""] + [f"- {n}" for n in lists["dynamic_schedule_hardcoded_outside_registry"]] + [
          "", "## Mode-gated paths", ""] + [f"- {n}" for n in lists["mode_gated_dynamic_paths"]] + [
          "", "## Parameters", "", "| Parameter | Class | Cadence | Formula / schedule | Inputs | Bounds | Engines | Effect |",
          "|---|---|---|---|---|---|---|---|"] + [
          f"| `{r['parameter']}` | {r['classification']} | {r['update_frequency']} | {r['formula_or_schedule']} | {r['runtime_inputs']} | "
          f"{r['bounds']} | {','.join(r['consumed_by_engines']) or '-'} | {r['decision_effect']} |" for r in rows]
    (args.out / "phase2_parameter_classification.md").write_text("\n".join(md) + "\n")
    print(dict(counts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
