"""
Revision 5 controller parameter authority.

Single source of every tuning value consumed by the native R5 controllers that previously lived as
code literals: the five bay governors (outer loop, entry comparator, inner loop), the native
dynamic-parameter schedule, the bay AVRs, SEL-300G unit protection, MiCOM grid protection, the
merit-order bay dispatcher, the HRSG thermal/capital model, bay cooldown and the Protocol V3
inner loop.

* ``CONTROLLER_PARAMETER_SPECS`` is the table.  canonical_parameter_registry.py registers every row
  (it is part of the registry identity), so an EffectiveConfig built from the registry carries
  every value.
* ``R5ControllerParameters.from_config(config)`` is the only runtime read path.  It reads every
  name through ``require`` (no fallback to an undocumented default), validates bounds and the
  cross-parameter relationships, and returns immutable groups that are passed explicitly into the
  controller constructors / configuration methods.
* Defaults equal the former literals exactly, so the default engine is decision-identical.

Safety classes
    TUNABLE        active tuning value; optimizer-eligible in principle (NOT_CALIBRATED)
    FIXED_SAFETY   protection / safety envelope; registered and consumed, never optimizer-eligible
    ADVISORY       consumed, but only by an output no engine actuates (the plant admission's own
                   stop/target suggestion; the MPC plan owns the order stop and target)
    INACTIVE       registered for completeness of a structure but no runtime path can reach it
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from math import isfinite
from typing import Any, Dict, List, Mapping, Optional, Tuple

from revision5.topology import (
    BAY_IDS,
    BPSTG_HEALTHCARE,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
)

TUNABLE = "TUNABLE"
FIXED_SAFETY = "FIXED_SAFETY"
ADVISORY = "ADVISORY"
INACTIVE = "INACTIVE"
SAFETY_CLASSES = (TUNABLE, FIXED_SAFETY, ADVISORY, INACTIVE)
NOT_CALIBRATED = "ENGINEERING_INITIAL_VALUE; NOT_CALIBRATED"

BAY_TOKENS: Dict[str, str] = {
    GTG1_HEAVY_INDUSTRY: "gtg1",
    GTG2_TECH_TELECOM: "gtg2",
    CSTG1_BFSI: "cstg1",
    CSTG2_CONSUMER_AUTO: "cstg2",
    BPSTG_HEALTHCARE: "bpstg",
}


@dataclass(frozen=True)
class ControllerParameterSpec:
    name: str
    param_type: str
    default: Any
    minimum: Any
    maximum: Any
    safety_class: str
    owner: str
    consumer: str
    description: str
    applicability: str = "EXTERNAL"
    calibration_status: str = NOT_CALIBRATED

    @property
    def calibratable(self) -> bool:
        return self.safety_class == TUNABLE


_SPECS: List[ControllerParameterSpec] = []


def _add(name, kind, default, lo, hi, cls, owner, consumer, description):
    _SPECS.append(ControllerParameterSpec(name, kind, default, lo, hi, cls, owner, consumer, description))


# --------------------------------------------------------------------------- governors
# Former literal table revision5/governor.py:59 (BAY_GOVERNOR_SPECS) and the BayGovernorSpec
# dataclass defaults (target_r .. grid_droop_max) plus the dynamic_z feedback gain (-0.25 * u).
_GOVERNOR_BAY_DEFAULTS: Dict[str, Dict[str, float]] = {
    GTG1_HEAVY_INDUSTRY: dict(droop_r=0.040, kp=1.20, ki=0.05, kd=0.30, base_z=-1.90, z_exit=0.35,
                              atr_barrier=1.80, target_m=3.0, capital_weight=0.25),
    GTG2_TECH_TELECOM: dict(droop_r=0.040, kp=1.50, ki=0.08, kd=0.60, base_z=-2.00, z_exit=0.50,
                            atr_barrier=2.10, target_m=3.2, capital_weight=0.20),
    CSTG1_BFSI: dict(droop_r=0.055, kp=0.90, ki=0.12, kd=0.15, base_z=-1.85, z_exit=0.30,
                     atr_barrier=1.50, target_m=2.8, capital_weight=0.25),
    CSTG2_CONSUMER_AUTO: dict(droop_r=0.055, kp=1.00, ki=0.07, kd=0.25, base_z=-1.95, z_exit=0.40,
                              atr_barrier=1.65, target_m=2.9, capital_weight=0.18),
    BPSTG_HEALTHCARE: dict(droop_r=0.075, kp=0.80, ki=0.04, kd=0.20, base_z=-1.80, z_exit=0.40,
                           atr_barrier=1.40, target_m=2.5, capital_weight=0.12),
}
GOVERNOR_COMMON_DEFAULTS: Dict[str, float] = dict(
    target_r=0.30, outcome_window=6, integral_clamp=3.0, dynamic_offset_min=-0.45,
    dynamic_offset_max=0.15, grid_droop_gain=0.05, grid_droop_max=0.25, feedback_gain=0.25,
)

# (field, type, (lo, hi) as absolute bounds, class, description).  Bounds are an engineering
# stability envelope around the frozen value; they are NOT calibration evidence.
_GOVERNOR_FIELDS = (
    ("droop_r", "float", (0.01, 0.20), TUNABLE, "grid droop reference: adverse grid return per unit droop"),
    ("kp", "float", (0.10, 4.0), TUNABLE, "outer/inner loop proportional gain (R error)"),
    ("ki", "float", (0.0, 0.50), TUNABLE, "outer/inner loop integral gain"),
    ("kd", "float", (0.0, 2.0), TUNABLE, "outer/inner loop derivative gain"),
    ("base_z", "float", (-4.0, -0.5), TUNABLE, "entry comparator base z threshold (BUY side)"),
    ("z_exit", "float", (0.0, 2.0), TUNABLE, "mean-reversion release: sigma distance below the mean at which the governor releases a position; exit when side-signed z >= -z_exit after entry below it (governor_authority.position_decision; after protection / min-hold)"),
    ("atr_barrier", "float", (0.5, 4.0), TUNABLE, "plant ATR stop-distance constraint; actuates Box6 plan before sizing in full adaptation"),
    ("target_m", "float", (0.5, 6.0), TUNABLE, "plant ATR target-distance constraint; actuates Box6 plan before sizing in full adaptation"),
    ("capital_weight", "float", (0.05, 0.50), TUNABLE, "initial bay capital weight (dispatcher seed and initial AVR capital); the five must sum to 1"),
    ("target_r", "float", (0.05, 1.0), TUNABLE, "outer-loop realized-R setpoint"),
    ("outcome_window", "int", (2, 30), TUNABLE, "outer-loop realized-R moving-average window (trades)"),
    ("integral_clamp", "float", (0.25, 10.0), TUNABLE, "outer/inner loop integral anti-windup clamp"),
    ("dynamic_offset_min", "float", (-2.0, -0.01), TUNABLE, "lower clip of the outer-loop z feedback offset (also inner trailing-gap reference)"),
    ("dynamic_offset_max", "float", (0.0, 1.0), TUNABLE, "upper clip of the outer-loop z feedback offset"),
    ("grid_droop_gain", "float", (0.0, 0.50), TUNABLE, "grid droop penalty gain on dynamic_z"),
    ("grid_droop_max", "float", (0.0, 1.0), TUNABLE, "grid droop penalty ceiling"),
    ("feedback_gain", "float", (0.0, 2.0), TUNABLE, "dynamic_z feedback offset gain: offset = clip(-feedback_gain * u, min, max)"),
)
GOVERNOR_FIELD_NAMES = tuple(f[0] for f in _GOVERNOR_FIELDS)
_GOVERNOR_INT_FIELDS = frozenset(f[0] for f in _GOVERNOR_FIELDS if f[1] == "int")


def governor_parameter_name(bay_id: str, field_name: str) -> str:
    return f"gov_{BAY_TOKENS[bay_id]}_{field_name}"


for _bay in BAY_IDS:
    for _field, _kind, (_lo, _hi), _cls, _desc in _GOVERNOR_FIELDS:
        _value = _GOVERNOR_BAY_DEFAULTS[_bay].get(_field, GOVERNOR_COMMON_DEFAULTS.get(_field))
        _add(governor_parameter_name(_bay, _field), _kind, _value, _lo, _hi, _cls,
             f"BayTurbineClosedLoopGovernor[{_bay}]",
             "revision5/governor.py:BayGovernorSpec; revision5/dynamic_parameters.py:evaluate (schedule base)",
             f"{_bay} {_desc}")

# ---------------------------------------------------------------- governor runtime schedule
# Former literals revision5/dynamic_parameters.py:613-758 (identical for every bay: one schedule law
# applied to each bay's own base values, so one plant-wide coefficient set is the faithful owner).
_SCHEDULE = (
    ("gsch_target_trend_gain", 0.15, (0.0, 0.5), "target_r *= 1 + gain * trend_bias - stress_gain * stress"),
    ("gsch_target_stress_gain", 0.15, (0.0, 0.5), "target_r stress reduction gain"),
    ("gsch_target_min_scale", 0.70, (0.3, 1.0), "target_r lower clip as a fraction of the bay base"),
    ("gsch_target_max_scale", 1.25, (1.0, 2.0), "target_r upper clip as a fraction of the bay base"),
    ("gsch_window_stress_gain", 0.80, (0.0, 2.0), "outcome_window *= 1 + gain * stress"),
    ("gsch_window_min", 3, (1, 12), "outcome_window lower clip (trades)"),
    ("gsch_window_max", 12, (3, 60), "outcome_window upper clip (trades)"),
    ("gsch_integral_stress_gain", 0.80, (0.0, 2.0), "integral_clamp /= 1 + stress_gain * stress + error_gain * |e|"),
    ("gsch_integral_error_gain", 0.50, (0.0, 2.0), "integral_clamp error divisor gain"),
    ("gsch_integral_min_scale", 0.50, (0.1, 1.0), "integral_clamp lower clip as a fraction of the bay base"),
    ("gsch_integral_max_scale", 1.25, (0.1, 2.0), "integral_clamp upper clip as a fraction of the bay base; the schedule never exceeds the base, so the clip binds only below 1 (admissible down to integral_min_scale, validated)"),
    ("gsch_droop_gain_stress_gain", 0.50, (0.0, 2.0), "grid_droop_gain *= vol * (1 + gain * stress)"),
    ("gsch_droop_gain_min_scale", 0.50, (0.1, 1.0), "grid_droop_gain lower clip (fraction of base)"),
    ("gsch_droop_gain_max_scale", 2.00, (1.0, 4.0), "grid_droop_gain upper clip (fraction of base)"),
    ("gsch_droop_max_stress_gain", 0.60, (0.0, 2.0), "grid_droop_max *= 1 + gain * stress"),
    ("gsch_droop_max_min_scale", 0.80, (0.1, 4.0), "grid_droop_max lower clip (fraction of base); the schedule never falls below the base, so the clip binds only above 1 (admissible up to droop_max_max_scale, validated)"),
    ("gsch_droop_max_max_scale", 1.60, (1.0, 4.0), "grid_droop_max upper clip (fraction of base)"),
    ("gsch_kp_error_gain", 1.00, (0.0, 3.0), "Kp *= clip(1 + error_gain * |e| + stress_gain * stress, min, max)"),
    ("gsch_kp_stress_gain", 0.25, (0.0, 2.0), "Kp schedule stress gain"),
    ("gsch_kp_scale_min", 0.60, (0.1, 5.0), "Kp schedule lower clip; the scale argument is >= 1, so the clip binds only above 1 (a Kp floor multiplier; admissible up to kp_scale_max, validated)"),
    ("gsch_kp_scale_max", 2.20, (1.0, 5.0), "Kp schedule upper clip"),
    ("gsch_ki_error_gain", 1.50, (0.0, 4.0), "Ki /= clip(1 + error_gain * |e| + stress_gain * stress, min, max)"),
    ("gsch_ki_stress_gain", 1.00, (0.0, 4.0), "Ki schedule stress gain"),
    ("gsch_ki_divisor_min", 1.00, (1.0, 2.0), "Ki divisor lower clip (>= 1: the schedule can only bleed Ki)"),
    ("gsch_ki_divisor_max", 3.50, (1.0, 10.0), "Ki divisor upper clip"),
    ("gsch_kd_error_gain", 0.70, (0.0, 3.0), "Kd *= clip(1 + error_gain * |e| + stress_gain * stress, min, max)"),
    ("gsch_kd_stress_gain", 0.50, (0.0, 2.0), "Kd schedule stress gain"),
    ("gsch_kd_scale_min", 0.60, (0.1, 5.0), "Kd schedule lower clip; the scale argument is >= 1, so the clip binds only above 1 (a Kd floor multiplier; admissible up to kd_scale_max, validated)"),
    ("gsch_kd_scale_max", 2.00, (1.0, 5.0), "Kd schedule upper clip"),
    ("gsch_base_z_trend_gain", 0.10, (0.0, 0.5), "base_z += trend_gain * trend_bias - stress_gain * stress"),
    ("gsch_base_z_stress_gain", 0.25, (0.0, 1.0), "base_z stress gain"),
    ("gsch_base_z_lower_offset", 0.50, (0.0, 2.0), "base_z lower clip: base - offset"),
    ("gsch_base_z_upper_offset", 0.10, (0.0, 0.5), "base_z upper clip: base + offset"),
    ("gsch_droop_r_stress_gain", 0.25, (0.0, 0.9), "droop_r *= 1 - gain * stress"),
    ("gsch_droop_r_min_scale", 0.60, (0.1, 1.0), "droop_r lower clip (fraction of base); upper clip is the base (never widened)"),
    ("gsch_offset_min_stress_gain", 0.25, (0.0, 1.0), "dynamic_offset_min *= 1 + gain * stress"),
    ("gsch_offset_min_lower_scale", 1.50, (1.0, 3.0), "dynamic_offset_min clip: base * lower_scale (more negative)"),
    ("gsch_offset_min_upper_scale", 0.75, (0.1, 3.0), "dynamic_offset_min clip: base * upper_scale (the less negative bound); the schedule is never less negative than the base, so the clip binds only above 1 (admissible up to offset_min_lower_scale, validated)"),
)
for _name, _default, (_lo, _hi), _desc in _SCHEDULE:
    _add(_name, "int" if isinstance(_default, int) else "float", _default, _lo, _hi, TUNABLE,
         "Revision5DynamicParameterController (governor schedule)",
         "revision5/dynamic_parameters.py:evaluate", _desc)

# ------------------------------------------------------------- supervisory stress index
_STRESS = (
    ("dyn_vol_min", 0.50, (0.1, 1.0), FIXED_SAFETY, "normalized volatility lower clip"),
    ("dyn_vol_max", 2.50, (1.0, 10.0), FIXED_SAFETY, "normalized volatility upper clip"),
    ("dyn_stress_turbulent_weight", 0.55, (0.0, 1.0), TUNABLE, "stress = clip(w_t * turbulent + w_c * chop + w_v * excess_vol, 0, 1)"),
    ("dyn_stress_chop_weight", 0.25, (0.0, 1.0), TUNABLE, "stress chop weight"),
    ("dyn_stress_excess_vol_weight", 0.20, (0.0, 1.0), TUNABLE, "stress excess-volatility weight"),
    ("dyn_risk_derate_stress_gain", 0.45, (0.0, 1.0), TUNABLE, "risk_derate = clip(1 - g_s * stress - g_d * drawdown, floor, 1)"),
    ("dyn_risk_derate_drawdown_gain", 0.25, (0.0, 2.0), TUNABLE, "risk_derate drawdown gain"),
    ("dyn_risk_derate_lower_bound", 0.40, (0.1, 1.0), FIXED_SAFETY, "risk_derate floor"),
)
for _name, _default, (_lo, _hi), _cls, _desc in _STRESS:
    _add(_name, "float", _default, _lo, _hi, _cls, "Revision5DynamicParameterController (common state)",
         "revision5/dynamic_parameters.py:_normalized_state/evaluate", _desc)

# ------------------------------------------------------------------- bay cooldown / lockout
_COOLDOWN = (
    ("plant_loss_cooldown_base_bars", "int", 15, (1, 60), TUNABLE, "bay loss cooldown base (former LOSS_COOLDOWN_BARS); also the value before the first snapshot"),
    ("plant_loss_cooldown_stress_offset", "float", 0.80, (0.1, 2.0), TUNABLE, "loss cooldown = base * (offset + gain * stress)"),
    ("plant_loss_cooldown_stress_gain", "float", 0.90, (0.0, 3.0), TUNABLE, "loss cooldown stress gain"),
    ("plant_loss_cooldown_min_bars", "int", 8, (1, 60), TUNABLE, "loss cooldown lower clip"),
    ("plant_loss_cooldown_max_bars", "int", 30, (1, 120), TUNABLE, "loss cooldown upper clip"),
    ("plant_target_cooldown_base_bars", "int", 3, (0, 30), TUNABLE, "bay target cooldown base (former TARGET_COOLDOWN_BARS)"),
    ("plant_target_cooldown_stress_offset", "float", 0.80, (0.1, 2.0), TUNABLE, "target cooldown = base * (offset + gain * stress)"),
    ("plant_target_cooldown_stress_gain", "float", 0.70, (0.0, 3.0), TUNABLE, "target cooldown stress gain"),
    ("plant_target_cooldown_min_bars", "int", 2, (0, 30), TUNABLE, "target cooldown lower clip"),
    ("plant_target_cooldown_max_bars", "int", 8, (1, 60), TUNABLE, "target cooldown upper clip"),
    ("plant_bay_lockout_consecutive_stops", "int", 2, (1, 10), FIXED_SAFETY, "consecutive stopped/losing outcomes that latch the ANSI 86 bay lockout for the session"),
    ("plant_stop_floor_price_fraction", "float", 0.0065, (0.0, 0.05), TUNABLE, "plant admission stop floor as a price fraction (advisory output; replay uses the MPC plan stop)"),
    ("plant_target_min_reward_risk", "float", 1.5, (1.0, 5.0), TUNABLE, "plant admission target floor as a multiple of the stop distance (advisory output)"),
    ("symbol_loss_cooldown_bars", "int", 15, (0, 120), FIXED_SAFETY, "orchestrator per-symbol entry block after a net losing exit (former BAY_LOSS_COOLDOWN_BARS)"),
    ("symbol_trip_consecutive_losses", "int", 2, (1, 10), FIXED_SAFETY, "orchestrator: consecutive net losing exits that trip a symbol for the session (former literal 2)"),
)
for _name, _kind, _default, (_lo, _hi), _cls, _desc in _COOLDOWN:
    _add(_name, _kind, _default, _lo, _hi, _cls, "TurbineBayPanel cooldown / lockout",
         "revision5/ccpp_unified_plant.py:TurbineBayPanel; revision5/dynamic_parameters.py:evaluate; revision2_external/orchestrator.py",
         _desc)

# ----------------------------------------------------------------------------------- AVR
# Former literals: BayExcitationAVR.__init__ (ccpp_protection_cubicles.py:157), TurbineBayPanel
# AVR construction (15,000 / 0.40) and AVRRuntimeProfile (dynamic_parameters.py:79-181, 775-824).
_AVR = (
    ("avr_uel_min_notional_inr", 15000.0, (0.0, 200000.0), FIXED_SAFETY, "UEL minimum commercial notional (rupees); smaller lots lock out"),
    ("avr_oel_capital_fraction", 0.40, (0.05, 1.0), FIXED_SAFETY, "OEL notional cap as a fraction of bay capital (envelope ceiling of the runtime OEL fraction)"),
    ("avr_loading_fraction_base", 0.15, (0.01, 0.50), FIXED_SAFETY, "per-entry loading fraction envelope ceiling; also the control-stress reference: stress = 1 - loading / base"),
    ("avr_loading_fraction_min", 0.05, (0.0, 0.50), TUNABLE, "loading = clip(base * risk_derate, min, base)"),
    ("avr_uel_multiplier_stress_gain", 0.75, (0.0, 3.0), TUNABLE, "UEL multiplier = clip(1 + gain * stress, 1, max)"),
    ("avr_uel_multiplier_max", 1.75, (1.0, 5.0), TUNABLE, "UEL multiplier upper clip"),
    ("avr_oel_fraction_min", 0.15, (0.01, 1.0), TUNABLE, "OEL fraction = clip(base * risk_derate, min, base)"),
    ("avr_vol_scalar_min", 0.20, (0.01, 1.0), TUNABLE, "lot volatility scalar lower clip"),
    ("avr_vol_scalar_max_base", 2.0, (0.5, 5.0), TUNABLE, "lot volatility scalar upper clip = clip(base * risk_derate, min, base)"),
    ("avr_vol_scalar_max_min", 0.75, (0.1, 5.0), TUNABLE, "vol scalar upper clip lower bound"),
    ("avr_z_strength_denominator_base", 1.80, (0.5, 5.0), TUNABLE, "z strength = min(cap, |z| / denominator); denominator = base * (1 + gain * stress)"),
    ("avr_z_strength_denominator_stress_gain", 0.50, (0.0, 2.0), TUNABLE, "z strength denominator stress gain"),
    ("avr_z_strength_denominator_max", 2.70, (0.5, 10.0), TUNABLE, "z strength denominator upper clip"),
    ("avr_z_strength_cap_base", 1.50, (0.1, 5.0), TUNABLE, "z strength cap = clip(base * risk_derate, min, base)"),
    ("avr_z_strength_cap_min", 0.75, (0.05, 5.0), TUNABLE, "z strength cap lower clip"),
    ("avr_kp_base", 0.80, (0.0, 5.0), TUNABLE, "AVR Kp = clip(base + gain * stress, base, max)"),
    ("avr_kp_stress_gain", 0.60, (0.0, 5.0), TUNABLE, "AVR Kp stress gain"),
    ("avr_kp_max", 1.40, (0.0, 10.0), TUNABLE, "AVR Kp upper clip"),
    ("avr_ki_base", 0.15, (0.0, 2.0), TUNABLE, "AVR Ki = clip(base / (1 + gain * stress), min, base)"),
    ("avr_ki_stress_gain", 1.00, (0.0, 5.0), TUNABLE, "AVR Ki stress divisor gain"),
    ("avr_ki_min", 0.075, (0.0, 2.0), TUNABLE, "AVR Ki lower clip"),
    ("avr_kd_base", 0.10, (0.0, 2.0), TUNABLE, "AVR Kd = clip(base + gain * stress, base, max)"),
    ("avr_kd_stress_gain", 0.10, (0.0, 2.0), TUNABLE, "AVR Kd stress gain"),
    ("avr_kd_max", 0.20, (0.0, 5.0), TUNABLE, "AVR Kd upper clip"),
    ("avr_integral_clamp_base", 0.50, (0.01, 5.0), TUNABLE, "AVR integral clamp = clip(base - gain * stress, min, base)"),
    ("avr_integral_clamp_stress_gain", 0.20, (0.0, 5.0), TUNABLE, "AVR integral clamp stress gain"),
    ("avr_integral_clamp_min", 0.30, (0.01, 5.0), TUNABLE, "AVR integral clamp lower clip"),
    ("avr_undervoltage_trip_base_pu", 0.90, (0.5, 0.99), FIXED_SAFETY, "ANSI 27 trip = clip(base + gain * stress, base, max)"),
    ("avr_undervoltage_trip_stress_gain", 0.05, (0.0, 0.2), FIXED_SAFETY, "ANSI 27 stress tightening gain"),
    ("avr_undervoltage_trip_max_pu", 0.95, (0.5, 0.999), FIXED_SAFETY, "ANSI 27 trip upper clip"),
    ("avr_overvoltage_trip_base_pu", 1.10, (1.001, 1.5), FIXED_SAFETY, "ANSI 59 trip = clip(base - gain * stress, min, base)"),
    ("avr_overvoltage_trip_stress_gain", 0.05, (0.0, 0.2), FIXED_SAFETY, "ANSI 59 stress tightening gain"),
    ("avr_overvoltage_trip_min_pu", 1.05, (1.001, 1.5), FIXED_SAFETY, "ANSI 59 trip lower clip"),
    ("avr_mvar_limit_base_pu", 0.90, (0.1, 2.0), FIXED_SAFETY, "MVAR limiter |Q| limit (per unit) = clip(base - gain * stress, min, base); acts on an explicit signed per-unit MEASURED reactive power only (avr_mvar_limiter_mode='required' with a supplied measurement source); no measurement is ever synthesized and absence is not zero"),
    ("avr_mvar_limit_stress_gain", 0.30, (0.0, 2.0), FIXED_SAFETY, "MVAR limiter stress tightening gain (live only with avr_mvar_limiter_mode='required')"),
    ("avr_mvar_limit_min_pu", 0.60, (0.1, 2.0), FIXED_SAFETY, "MVAR limiter lower clip (live only with avr_mvar_limiter_mode='required')"),
    ("avr_excitation_min_base", 0.10, (0.0, 1.0), FIXED_SAFETY, "UEL excitation floor = clip(base + gain * stress, base, max)"),
    ("avr_excitation_min_stress_gain", 0.10, (0.0, 1.0), FIXED_SAFETY, "UEL excitation floor stress gain"),
    ("avr_excitation_min_max", 0.20, (0.0, 1.0), FIXED_SAFETY, "UEL excitation floor upper clip"),
    ("avr_excitation_max_base", 1.00, (0.1, 1.0), FIXED_SAFETY, "OEL excitation ceiling = clip(base - gain * stress, min, base); never above 1 (no AVR leverage)"),
    ("avr_excitation_max_stress_gain", 0.25, (0.0, 1.0), FIXED_SAFETY, "OEL excitation ceiling stress gain"),
    ("avr_excitation_max_min", 0.75, (0.1, 1.0), FIXED_SAFETY, "OEL excitation ceiling lower clip"),
    # Measured terminal-voltage path (avr_voltage_mode="measured" only; see BayTerminalVoltageMeter).
    ("avr_voltage_reference_pu", 1.0, (0.9, 1.1), FIXED_SAFETY, "AVR voltage reference (per unit of the synchronized bus design point)"),
    ("avr_synchronous_reactance_pu", 0.05, (0.0, 0.5), TUNABLE, "terminal voltage sag per unit of bay loading: V_t = V_bus - X * I_bay"),
    ("avr_voltage_filter_alpha", 1.0, (0.01, 1.0), TUNABLE, "first-order PT filter on V_t (1.0 = unfiltered)"),
)
for _name, _default, (_lo, _hi), _cls, _desc in _AVR:
    _add(_name, "float", _default, _lo, _hi, _cls, "BayExcitationAVR",
         "revision5/ccpp_protection_cubicles.py:BayExcitationAVR; revision5/dynamic_parameters.py:AVRRuntimeProfile",
         _desc)

# ------------------------------------------------------------------ SEL-300G unit protection
# ANSI 87G / 32R (check_in_flight_protection) are invoked on every held bar when the orchestrator
# runs unit_protection_mode="pre_sync_and_in_flight" (opt-in); in the default mode they are not called.
_SEL_INFLIGHT = " (live only with unit_protection_mode='pre_sync_and_in_flight')"
_SEL = (
    ("sel_stale_tick_limit_seconds", 120.0, (1.0, 600.0), FIXED_SAFETY, "ANSI 60FL stale-data envelope (runtime value tightens only)"),
    ("sel_stale_tick_stress_gain", 0.50, (0.0, 0.95), FIXED_SAFETY, "stale limit = clip(base * (1 - gain * stress), min, base)"),
    ("sel_stale_tick_min_seconds", 45.0, (1.0, 600.0), FIXED_SAFETY, "stale limit lower clip"),
    ("sel_max_spread_fraction", 0.005, (0.0001, 0.05), FIXED_SAFETY, "ANSI 40 spread envelope"),
    ("sel_spread_stress_gain", 0.40, (0.0, 0.95), FIXED_SAFETY, "spread limit stress tightening gain"),
    ("sel_spread_min_fraction", 0.0025, (0.0001, 0.05), FIXED_SAFETY, "spread limit lower clip"),
    ("sel_vol_surge_mult", 3.5, (1.0, 10.0), FIXED_SAFETY, "ANSI 24 bar range / ATR envelope"),
    ("sel_vol_surge_stress_gain", 0.30, (0.0, 0.95), FIXED_SAFETY, "vol surge stress tightening gain"),
    ("sel_vol_surge_min_mult", 2.0, (1.0, 10.0), FIXED_SAFETY, "vol surge lower clip"),
    ("sel_max_slippage_fraction", 0.0035, (0.0001, 0.05), FIXED_SAFETY, "ANSI 87G slippage envelope" + _SEL_INFLIGHT),
    ("sel_slippage_stress_gain", 0.30, (0.0, 0.95), FIXED_SAFETY, "slippage stress tightening gain" + _SEL_INFLIGHT),
    ("sel_slippage_min_fraction", 0.0020, (0.0001, 0.05), FIXED_SAFETY, "slippage limit lower clip" + _SEL_INFLIGHT),
    ("sel_max_adverse_bars", 4, (1, 60), FIXED_SAFETY, "ANSI 32R adverse-drift bars" + _SEL_INFLIGHT),
    ("sel_adverse_stress_gain", 0.50, (0.0, 0.95), FIXED_SAFETY, "adverse bars stress tightening gain" + _SEL_INFLIGHT),
    ("sel_adverse_min_bars", 2, (1, 60), FIXED_SAFETY, "adverse bars lower clip" + _SEL_INFLIGHT),
)
for _name, _default, (_lo, _hi), _cls, _desc in _SEL:
    _add(_name, "int" if isinstance(_default, int) else "float", _default, _lo, _hi, _cls,
         "BayUnitProtectionSEL300G", "revision5/ccpp_protection_cubicles.py:BayUnitProtectionSEL300G; revision5/dynamic_parameters.py:evaluate", _desc)

# ------------------------------------------------------------------ MiCOM grid protection
_MICOM = (
    ("micom_max_daily_fleet_drawdown_pct", 0.020, (0.001, 0.20), "ANSI 67 fleet drawdown envelope"),
    ("micom_drawdown_stress_gain", 0.35, (0.0, 0.95), "drawdown limit = clip(base * (1 - gain * stress), min, base)"),
    ("micom_drawdown_min_pct", 0.010, (0.001, 0.20), "drawdown limit lower clip"),
    ("micom_nifty_crash_rate_pct", 0.015, (0.001, 0.20), "ANSI 81U 15-minute Nifty move envelope"),
    ("micom_crash_stress_gain", 0.25, (0.0, 0.95), "crash limit stress tightening gain"),
    ("micom_crash_min_pct", 0.008, (0.001, 0.20), "crash limit lower clip"),
    ("micom_max_regime_vol_z", 3.0, (0.5, 10.0), "ANSI 21 Nifty volatility z envelope"),
    ("micom_vol_z_stress_gain", 0.30, (0.0, 0.95), "vol z limit stress tightening gain"),
    ("micom_vol_z_min", 1.80, (0.5, 10.0), "vol z limit lower clip"),
    ("micom_overfrequency_ratio", 1.5, (1.0, 5.0), "ANSI 81O melt-up trip = ratio * crash limit"),
)
for _name, _default, (_lo, _hi), _desc in _MICOM:
    _add(_name, "float", _default, _lo, _hi, FIXED_SAFETY, "MasterGridProtectionMiCOM",
         "revision5/ccpp_protection_cubicles.py:MasterGridProtectionMiCOM; revision5/dynamic_parameters.py:evaluate", _desc)

# ---------------------------------------------------------------------------- dispatcher
_DISPATCH = (
    ("disp_ema_retention", "float", 0.85, (0.0, 0.99), TUNABLE, "EMA alpha: weight retained from the previous allocation"),
    ("disp_ema_update", "float", 0.15, (0.01, 1.0), TUNABLE, "EMA beta: weight of the new merit target (alpha + beta = 1)"),
    ("disp_merit_window", "int", 20, (3, 200), TUNABLE, "realized-R outcomes kept per bay"),
    ("disp_min_samples", "int", 3, (1, 50), TUNABLE, "outcomes before a bay's merit departs from neutral"),
    ("disp_allocation_floor", "float", 0.08, (0.0, 0.20), FIXED_SAFETY, "minimum bay allocation fraction"),
    ("disp_allocation_ceiling", "float", 0.35, (0.20, 1.0), FIXED_SAFETY, "maximum bay allocation fraction; also the sector-dispatch per-bay cap"),
    ("disp_neutral_score", "float", 1.0, (0.1, 10.0), TUNABLE, "merit score of a bay below min_samples"),
    ("disp_score_base", "float", 1.0, (0.0, 10.0), TUNABLE, "merit = base + mean_R / max(downside_floor, downside_dev)"),
    ("disp_score_floor", "float", 0.1, (0.0, 10.0), TUNABLE, "merit score lower clip"),
    ("disp_downside_dev_default", "float", 0.5, (0.01, 5.0), TUNABLE, "downside deviation when fewer than downside_min_count losing outcomes"),
    ("disp_downside_dev_floor", "float", 0.2, (0.01, 5.0), TUNABLE, "downside deviation floor in the merit ratio"),
    ("disp_downside_min_count", "int", 2, (2, 50), TUNABLE, "losing outcomes needed to measure downside deviation"),
)
for _name, _kind, _default, (_lo, _hi), _cls, _desc in _DISPATCH:
    _add(_name, _kind, _default, _lo, _hi, _cls, "DynamicBayLoadDispatcher",
         "revision5/ccpp_unified_plant.py:DynamicBayLoadDispatcher; revision5/plant_control.py:SectorDispatchController", _desc)

# ---------------------------------------------------------------------------------- HRSG
_HRSG = (
    ("hrsg_recovery_fraction", "float", 0.60, (0.0, 1.0), TUNABLE, "fraction of curtailed GTG capital routed to healthy steam bays"),
    ("hrsg_rho_crit", "float", 0.65, (-0.99, 0.99), TUNABLE, "pairwise bay return correlation at which economizer damping begins"),
    ("hrsg_min_penalty_factor", "float", 0.70, (0.01, 1.0), FIXED_SAFETY, "deepest correlation derate of a bay allocation"),
    ("hrsg_correlation_window", "int", 30, (2, 1000), TUNABLE, "synchronous return snapshots kept for correlation"),
    ("hrsg_min_correlation_samples", "int", 6, (2, 1000), TUNABLE, "snapshots before a pair correlation is measured"),
    ("hrsg_pressure_time_constant_bars", "float", 5.0, (1.0, 200.0), TUNABLE, "drum pressure first-order lag (bars)"),
    ("hrsg_min_steam_pressure_pu", "float", 0.35, (0.01, 0.99), FIXED_SAFETY, "pressure-collapse threshold (pu); steam bays have zero capacity below it and exit"),
    ("hrsg_max_steam_pressure_pu", "float", 1.50, (1.01, 5.0), FIXED_SAFETY, "drum pressure ceiling (pu)"),
)
for _name, _kind, _default, (_lo, _hi), _cls, _desc in _HRSG:
    _add(_name, _kind, _default, _lo, _hi, _cls, "HeatRecoverySteamGenerator",
         "revision5/hrsg.py:HeatRecoverySteamGenerator", _desc)

# ---------------------------------------------- strategy / execution / safety / dispatch profiles
# Former literals revision5/dynamic_parameters.py:404-603.  Each profile value is a per-bar schedule
# of the run's OWN base value (registry parameter or safety-contract value, supplied at runtime;
# not a second copy):  strategy bases entry_confidence_threshold, min_risk_reward_ratio,
# min_hold_bars, max_hold_bars; execution / safety bases from the safety contract; dispatch bases
# disp_allocation_floor / disp_allocation_ceiling.  Consumed only with
# dynamic_profile_mode="consume" (safety and execution values tighten only).
_PROFILE = (
    ("psch_entry_conf_stress_gain", 0.10, (0.0, 0.5), TUNABLE, "entry confidence = clip(base + gain*stress + chop_gain*chop, min, max)"),
    ("psch_entry_conf_chop_gain", 0.025, (0.0, 0.2), TUNABLE, "entry confidence chop gain"),
    ("psch_entry_conf_min", 0.02, (0.0, 0.5), TUNABLE, "entry confidence lower clip"),
    ("psch_entry_conf_max", 0.35, (0.0, 1.0), TUNABLE, "entry confidence upper clip"),
    ("psch_min_rr_stress_gain", 0.60, (0.0, 3.0), TUNABLE, "min reward:risk = clip(base + gain*stress, min, max)"),
    ("psch_min_rr_min", 1.0, (0.5, 5.0), TUNABLE, "min reward:risk lower clip"),
    ("psch_min_rr_max", 3.0, (0.5, 10.0), TUNABLE, "min reward:risk upper clip"),
    ("psch_min_hold_chop_gain", 2.0, (0.0, 20.0), TUNABLE, "min hold = round(clip(base + chop_gain*chop + turb_gain*turbulent, min, max))"),
    ("psch_min_hold_turbulent_gain", 3.0, (0.0, 20.0), TUNABLE, "min hold turbulent gain"),
    ("psch_min_hold_min", 1.0, (0.0, 60.0), TUNABLE, "min hold lower clip (bars)"),
    ("psch_min_hold_max", 8.0, (1.0, 120.0), TUNABLE, "min hold upper clip (bars); the registry bounds of min_hold_bars also apply"),
    ("psch_max_hold_stress_gain", 20.0, (0.0, 120.0), TUNABLE, "max hold = round(clip(base - stress_gain*stress + trend_gain*trend, min, max))"),
    ("psch_max_hold_trend_gain", 10.0, (0.0, 120.0), TUNABLE, "max hold trend gain"),
    ("psch_max_hold_min", 20.0, (1.0, 240.0), TUNABLE, "max hold lower clip (bars)"),
    ("psch_max_hold_max", 120.0, (1.0, 480.0), TUNABLE, "max hold upper clip (bars)"),
    ("psch_slippage_vol_gain", 0.75, (0.0, 5.0), FIXED_SAFETY, "slippage tolerance = clip(base*(1 + gain*(vol - 1)), min, max), applied tighten-only"),
    ("psch_slippage_min_fraction", 0.0005, (0.0, 0.05), FIXED_SAFETY, "slippage tolerance lower clip"),
    ("psch_slippage_max_fraction", 0.0020, (0.0, 0.05), FIXED_SAFETY, "slippage tolerance upper clip; at its default it lies above the contract base (inert), set below the base it tightens the gate"),
    ("psch_data_age_stress_gain", 0.75, (0.0, 5.0), FIXED_SAFETY, "max market-data age = clip(base/(1 + gain*stress), min, base)"),
    ("psch_data_age_min_seconds", 10.0, (1.0, 600.0), FIXED_SAFETY, "max market-data age lower clip"),
    ("psch_order_timeout_stress_gain", 0.50, (0.0, 5.0), FIXED_SAFETY, "order timeout = clip(base/(1 + gain*stress), min(min_seconds, upper), upper) with upper = min(max_seconds, base): shortens with stress, applied tighten-only (replaces base*(1 + gain*stress), which could never tighten)"),
    ("psch_order_timeout_min_seconds", 15.0, (1.0, 600.0), FIXED_SAFETY, "order timeout lower clip (binds when base/(1 + gain*stress) falls below it; never raises the timeout above the contract base)"),
    ("psch_order_timeout_max_seconds", 60.0, (1.0, 600.0), FIXED_SAFETY, "order timeout upper clip; effective upper = min(max_seconds, contract base); at its default it lies above the contract base (inert), set below the base it caps the timeout at every stress level"),
    ("psch_drawdown_halt_stress_gain", 0.30, (0.0, 0.95), FIXED_SAFETY, "drawdown halt = clip(base*(1 - gain*stress), min, base)"),
    ("psch_drawdown_halt_min", 0.10, (0.0, 1.0), FIXED_SAFETY, "drawdown halt lower clip"),
    ("psch_gross_exposure_min", 0.20, (0.0, 1.0), FIXED_SAFETY, "gross exposure = clip(base*risk_derate, min, base)"),
    ("psch_symbol_exposure_min", 0.05, (0.0, 1.0), FIXED_SAFETY, "symbol exposure = clip(base*risk_derate, min, base)"),
    ("psch_concurrent_positions_min", 1.0, (0.0, 50.0), FIXED_SAFETY, "concurrent positions = round(clip(base*risk_derate, min, base))"),
    ("psch_daily_loss_multiplier_min", 0.40, (0.0, 1.0), FIXED_SAFETY, "daily loss limit multiplier = clip(risk_derate, min, 1)"),
    ("psch_dispatch_floor_stress_gain", 0.20, (0.0, 0.95), FIXED_SAFETY, "dispatcher floor = clip(base*(1 - gain*stress), min, max); consumed by the sector-dispatch bounded projection (per-bay reference floor)"),
    ("psch_dispatch_floor_min", 0.05, (0.0, 0.2), FIXED_SAFETY, "dispatcher floor lower clip"),
    ("psch_dispatch_floor_max", 0.10, (0.0, 0.2), FIXED_SAFETY, "dispatcher floor upper clip"),
    ("psch_dispatch_ceiling_min", 0.15, (0.0, 1.0), FIXED_SAFETY, "dispatch ceiling = clip(base*risk_derate, min, base); consumed as the sector-dispatch per-bay cap"),
    ("psch_dispatch_floor_ceiling_ratio", 0.50, (0.0, 1.0), FIXED_SAFETY, "if floor >= ceiling: floor = min(floor, ratio*ceiling) (keeps the floor strictly feasible below the runtime ceiling)"),
)
PROFILE_SCHEDULE_PARAMETERS = tuple(name for name, *_ in _PROFILE)
for _name, _default, (_lo, _hi), _cls, _desc in _PROFILE:
    _add(_name, "float", _default, _lo, _hi, _cls, "Revision5DynamicParameterController (runtime profiles)",
         "revision5/dynamic_parameters.py:evaluate; revision2_external/orchestrator.py dynamic_profile_mode='consume'", _desc)

# ------------------------------------------------------- Box 6 Tier-1 / Tier-3 schedule envelopes
# Former literals revision2_external/dynamic_parameter_controller.py:13-18.  They bound the per-plan
# Tier-3 Box 6 gain schedule and the Tier-1 ATR floor / slippage schedule (FIXED_SAFETY envelopes).
_TIER = (
    ("tier1_normalized_vol_min", 0.5, (0.05, 1.0), "Tier-1 normalized volatility lower clip"),
    ("tier1_normalized_vol_max", 2.5, (1.0, 10.0), "Tier-1 normalized volatility upper clip"),
    ("tier1_atr_floor_min", 0.0025, (0.0, 0.05), "Tier-1 ATR floor multiplier lower clip"),
    ("tier1_atr_floor_max", 0.0120, (0.0, 0.10), "Tier-1 ATR floor multiplier upper clip"),
    ("tier1_slippage_scale_min", 0.6, (0.0, 1.0), "Tier-1 slippage multiplier lower clip"),
    ("tier1_slippage_scale_max", 3.0, (1.0, 10.0), "Tier-1 slippage multiplier upper clip"),
    ("tier3_kp_scale_min", 0.5, (0.0, 10.0), "Tier-3 Box 6 Kp schedule lower clip; the scale argument 1 + gain*|error| is >= 1, so the clip binds only above 1 (admissible up to tier3_kp_scale_max, validated)"),
    ("tier3_kp_scale_max", 2.5, (1.0, 10.0), "Tier-3 Box 6 Kp schedule upper clip"),
    ("tier3_ki_divisor_min", 1.0, (1.0, 2.0), "Tier-3 Box 6 Ki divisor lower clip (>= 1: the schedule only bleeds Ki)"),
    ("tier3_ki_divisor_max", 4.0, (1.0, 20.0), "Tier-3 Box 6 Ki divisor upper clip"),
    ("tier3_kd_scale_min", 0.5, (0.0, 10.0), "Tier-3 Box 6 Kd schedule lower clip; the scale argument 1 + gain*|error| is >= 1, so the clip binds only above 1 (admissible up to tier3_kd_scale_max, validated)"),
    ("tier3_kd_scale_max", 2.0, (1.0, 10.0), "Tier-3 Box 6 Kd schedule upper clip"),
)
for _name, _default, (_lo, _hi), _desc in _TIER:
    _add(_name, "float", _default, _lo, _hi, FIXED_SAFETY, "DynamicParameterController (Box 6 Tier-1 / Tier-3)",
         "revision2_external/dynamic_parameter_controller.py", _desc)
TIER_ENVELOPE_PARAMETERS = tuple(name for name, *_ in _TIER)

# ------------------------------------------- closed-loop / studies-weight safety envelopes
# Former literals revision2_external/closed_loop_control.py (_ENTRY_DERATE_BOUNDS, _PERSISTENCE_BOUNDS,
# _GAIN_SCALE_BOUNDS) and revision2_external/composite_study_signal.py (MIN_WEIGHT, MAX_WEIGHT).  They
# are ADDED to the 312 audited rows (the original classification table is unchanged); defaults equal
# the former literals.  Consumers read them from the run's EffectiveConfig on every configure().
STUDY_COUNT = 4          # len(revision2_external.composite_study_signal.STUDY_NAMES); asserted by a test
_ENVELOPE = (
    ("cl_entry_derate_min", 0.5, (0.1, 1.0), FIXED_SAFETY, "ClosedLoopSupervisor / CausalOutcomeLedger", "entry-quality derate = clip(0.5 + pooled_win_rate, min, max): lower bound (the derate scales entry size in closed_loop_mode='active_paper')"),
    ("cl_entry_derate_max", 1.0, (0.5, 1.0), FIXED_SAFETY, "ClosedLoopSupervisor / CausalOutcomeLedger", "entry-quality derate upper bound; never above 1 (a supervisory derate only reduces risk)"),
    ("cl_persistence_min", 0.05, (0.001, 0.95), FIXED_SAFETY, "ClosedLoopSupervisor / SymbolDynamicsProfiler", "deviation-persistence phi lower clip (0 < phi < 1 for a finite half-life) -> reference-path response time; at its default the response-time floor (cl_response_time_min_bars) dominates, the clip is observable once phi_min > 0.5 ** (1 / response_time_min)"),
    ("cl_persistence_max", 0.99, (0.5, 0.999), FIXED_SAFETY, "ClosedLoopSupervisor / SymbolDynamicsProfiler", "deviation-persistence phi upper clip (strictly below 1) -> reference-path response time; at its default the response-time ceiling dominates, the clip is observable once phi_max < 0.5 ** (1 / response_time_max)"),
    ("cl_gain_scale_min", 0.5, (0.1, 1.0), ADVISORY, "ClosedLoopSupervisor / SymbolDynamicsProfiler", "suggested_pid_gain_scale = clip(default_response_time / response_time, min, max) lower clip [ADVISORY: the suggestion feeds no actuator]"),
    ("cl_gain_scale_max", 1.5, (1.0, 3.0), ADVISORY, "ClosedLoopSupervisor / SymbolDynamicsProfiler", "suggested_pid_gain_scale upper clip [ADVISORY: the suggestion feeds no actuator]"),
    ("studies_weight_min", 0.05, (0.01, 0.25), FIXED_SAFETY, "CompositeStudySignal", "chart-studies weight = clip(1/4 + PID adjustment, min, max): lower bound (a study is down-weighted, never deleted); requires 4*min <= 1"),
    ("studies_weight_max", 0.60, (0.25, 1.0), FIXED_SAFETY, "CompositeStudySignal", "chart-studies weight upper bound (no study dominates); requires 4*max >= 1"),
)
for _name, _default, (_lo, _hi), _cls, _owner, _desc in _ENVELOPE:
    _add(_name, "float", _default, _lo, _hi, _cls, _owner,
         "revision2_external/closed_loop_control.py; revision2_external/composite_study_signal.py (configure(config))", _desc)
ENVELOPE_PARAMETER_NAMES = tuple(name for name, *_ in _ENVELOPE)

# ------------------------------------------------------------------- Governor V3 inner loop
# Ported from a695913 (research/r5-step0-correctness); same names, defaults and classes.
_V3 = (
    ("gov_v3_mfe_activation_r", "float", 0.30, (0.10, 0.30), TUNABLE, "favourable excursion (R) at which the PID-modulated trailing stop becomes eligible"),
    ("gov_v3_kappa", "float", 0.25, (0.10, 0.40), TUNABLE, "trailing-gap modulation depth: gap = base_gap * (1 - kappa * tanh(u))"),
    ("gov_v3_gamma_fast", "float", 0.85, (0.50, 0.90), TUNABLE, "inner integral leak when |error| > tau_error"),
    ("gov_v3_tau_error_multiplier", "float", 1.0, (0.5, 3.0), TUNABLE, "fast-leak error boundary in bars of noise (ATR / initial risk)"),
    ("gov_v3_gamma_slow", "float", 0.98, (0.95, 0.999), FIXED_SAFETY, "inner integral leak when |error| <= tau_error"),
    ("gov_v3_base_gap_r", "float", 0.45, (0.20, 0.80), FIXED_SAFETY, "unmodulated trailing gap behind the favourable extreme (R)"),
    ("gov_v3_minimum_gap_r", "float", 0.15, (0.05, 0.40), FIXED_SAFETY, "smallest commanded trailing gap (R)"),
    ("gov_v3_noise_floor_mult", "float", 1.0, (0.5, 2.0), FIXED_SAFETY, "trailing gap noise floor in bars of noise"),
    ("gov_v3_grace_bars", "int", 2, (0, 10), FIXED_SAFETY, "bars after the fill before the trailing stop may engage"),
)
for _name, _kind, _default, (_lo, _hi), _cls, _desc in _V3:
    _add(_name, _kind, _default, _lo, _hi, _cls, "PositionControlV3 (governor inner loop, governor_position_mode='v3')",
         "revision5/governor.py:BayTurbineClosedLoopGovernor._position_control_v3", _desc)

# Finite immutable adaptation policy, not recursively adapted meta-coefficients.
_ADAPTATION = (
    ("adapt_active", 0.0, 0.0, 1.0),
    ("adapt_fraction", 0.20, 0.0, 1.0),
    ("adapt_stress_weight", 1.0, 0.0, 5.0),
    ("adapt_trend_weight", 0.5, 0.0, 5.0),
    ("adapt_error_weight", 0.5, 0.0, 5.0),
    ("adapt_signed_relief", 2.0, 0.0, 5.0),
    ("adapt_proxy_scale", 1.5, 0.0, 5.0),
    ("adapt_proxy_max", 2.0, 0.1, 5.0),
    ("adapt_proxy_derate_floor", 0.1, 0.0, 1.0),
)
for _name, _default, _lo, _hi in _ADAPTATION:
    _add(_name, "float", _default, _lo, _hi, FIXED_SAFETY,
         "Full R5 adaptation policy", "revision5/full_adaptation.py",
         "Immutable registered adaptation policy; engineering initial value, NOT_CALIBRATED")
ADAPTATION_POLICY_NAMES = tuple(n for n, *_ in _ADAPTATION)

CONTROLLER_PARAMETER_SPECS: Tuple[ControllerParameterSpec, ...] = tuple(_SPECS)
CONTROLLER_PARAMETERS: Dict[str, ControllerParameterSpec] = {s.name: s for s in CONTROLLER_PARAMETER_SPECS}
if len(CONTROLLER_PARAMETERS) != len(CONTROLLER_PARAMETER_SPECS):
    raise RuntimeError("duplicate controller parameter names")
CONTROLLER_PARAMETER_NAMES: Tuple[str, ...] = tuple(sorted(CONTROLLER_PARAMETERS))
POSITION_CONTROL_V3_PARAMETERS = {name[len("gov_v3_"):]: name for name, *_ in _V3}


def controller_parameter_defaults() -> Dict[str, Any]:
    return {s.name: s.default for s in CONTROLLER_PARAMETER_SPECS}


# =========================================================================== runtime groups

class ControllerParameterError(ValueError):
    """A controller parameter is missing, mistyped, out of bounds or inconsistent."""


def _read(values: Mapping[str, Any], name: str):
    spec = CONTROLLER_PARAMETERS[name]
    if name not in values:
        raise ControllerParameterError(f"missing controller parameter {name}")
    value = values[name]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ControllerParameterError(f"{name} must be numeric")
    if spec.param_type == "int":
        if int(value) != value:
            raise ControllerParameterError(f"{name} must be an integer")
        value = int(value)
    else:
        value = float(value)
    if not isfinite(float(value)):
        raise ControllerParameterError(f"{name} must be finite")
    if not spec.minimum <= value <= spec.maximum:
        raise ControllerParameterError(f"{name}={value} outside [{spec.minimum}, {spec.maximum}]")
    return value


def _group(cls, values: Mapping[str, Any], prefix: str = ""):
    return cls(**{f.name: _read(values, prefix + f.name) for f in fields(cls)})


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ControllerParameterError(message)


@dataclass(frozen=True)
class GovernorScheduleParameters:
    target_trend_gain: float
    target_stress_gain: float
    target_min_scale: float
    target_max_scale: float
    window_stress_gain: float
    window_min: int
    window_max: int
    integral_stress_gain: float
    integral_error_gain: float
    integral_min_scale: float
    integral_max_scale: float
    droop_gain_stress_gain: float
    droop_gain_min_scale: float
    droop_gain_max_scale: float
    droop_max_stress_gain: float
    droop_max_min_scale: float
    droop_max_max_scale: float
    kp_error_gain: float
    kp_stress_gain: float
    kp_scale_min: float
    kp_scale_max: float
    ki_error_gain: float
    ki_stress_gain: float
    ki_divisor_min: float
    ki_divisor_max: float
    kd_error_gain: float
    kd_stress_gain: float
    kd_scale_min: float
    kd_scale_max: float
    base_z_trend_gain: float
    base_z_stress_gain: float
    base_z_lower_offset: float
    base_z_upper_offset: float
    droop_r_stress_gain: float
    droop_r_min_scale: float
    offset_min_stress_gain: float
    offset_min_lower_scale: float
    offset_min_upper_scale: float

    def validate(self) -> None:
        for lo, hi in (("target_min_scale", "target_max_scale"), ("window_min", "window_max"),
                       ("integral_min_scale", "integral_max_scale"), ("droop_gain_min_scale", "droop_gain_max_scale"),
                       ("droop_max_min_scale", "droop_max_max_scale"), ("kp_scale_min", "kp_scale_max"),
                       ("ki_divisor_min", "ki_divisor_max"), ("kd_scale_min", "kd_scale_max")):
            _require(getattr(self, lo) <= getattr(self, hi), f"governor schedule requires {lo} <= {hi}")
        _require(self.offset_min_upper_scale <= self.offset_min_lower_scale,
                 "governor schedule requires offset_min_upper_scale <= offset_min_lower_scale")
        # Reachable-clip domains: a clip that cannot bind the schedule is still ordered against its partner.
        _require(self.integral_max_scale >= self.integral_min_scale, "governor schedule requires integral_max_scale >= integral_min_scale")


@dataclass(frozen=True)
class ProfileScheduleParameters:
    entry_conf_stress_gain: float
    entry_conf_chop_gain: float
    entry_conf_min: float
    entry_conf_max: float
    min_rr_stress_gain: float
    min_rr_min: float
    min_rr_max: float
    min_hold_chop_gain: float
    min_hold_turbulent_gain: float
    min_hold_min: float
    min_hold_max: float
    max_hold_stress_gain: float
    max_hold_trend_gain: float
    max_hold_min: float
    max_hold_max: float
    slippage_vol_gain: float
    slippage_min_fraction: float
    slippage_max_fraction: float
    data_age_stress_gain: float
    data_age_min_seconds: float
    order_timeout_stress_gain: float
    order_timeout_min_seconds: float
    order_timeout_max_seconds: float
    drawdown_halt_stress_gain: float
    drawdown_halt_min: float
    gross_exposure_min: float
    symbol_exposure_min: float
    concurrent_positions_min: float
    daily_loss_multiplier_min: float
    dispatch_floor_stress_gain: float
    dispatch_floor_min: float
    dispatch_floor_max: float
    dispatch_ceiling_min: float
    dispatch_floor_ceiling_ratio: float

    def validate(self) -> None:
        for lo, hi in (("entry_conf_min", "entry_conf_max"), ("min_rr_min", "min_rr_max"), ("min_hold_min", "min_hold_max"),
                       ("max_hold_min", "max_hold_max"), ("slippage_min_fraction", "slippage_max_fraction"),
                       ("order_timeout_min_seconds", "order_timeout_max_seconds"), ("dispatch_floor_min", "dispatch_floor_max")):
            _require(getattr(self, lo) <= getattr(self, hi), f"profile schedule requires {lo} <= {hi}")
        # The dispatch floor is projected below the runtime ceiling by the consumer (priority: ceiling,
        # then total budget, then floor); every admissible floor must itself fit n bays in the budget.
        _require(len(BAY_IDS) * self.dispatch_floor_max <= 1.0 + 1e-12,
                 "profile schedule requires n_bays * dispatch_floor_max <= 1 (a floor must fit the total budget)")


# Bases of the runtime profiles: the run's own registry parameters / safety-contract values.
PROFILE_BASE_PARAMETERS = ("entry_confidence_threshold", "min_risk_reward_ratio", "min_hold_bars", "max_hold_bars")
PROFILE_BASE_SAFETY = ("max_slippage_fraction", "max_market_data_age_seconds", "order_timeout_seconds_execution",
                       "safety_drawdown_halt_threshold", "max_gross_exposure_fraction",
                       "max_exposure_per_symbol_fraction", "max_concurrent_positions")


@dataclass(frozen=True)
class SupervisoryStressParameters:
    vol_min: float
    vol_max: float
    stress_turbulent_weight: float
    stress_chop_weight: float
    stress_excess_vol_weight: float
    risk_derate_stress_gain: float
    risk_derate_drawdown_gain: float
    risk_derate_lower_bound: float

    def validate(self) -> None:
        _require(0.0 < self.vol_min < 1.0 < self.vol_max, "require 0 < dyn_vol_min < 1 < dyn_vol_max")


@dataclass(frozen=True)
class PlantCooldownParameters:
    loss_cooldown_base_bars: int
    loss_cooldown_stress_offset: float
    loss_cooldown_stress_gain: float
    loss_cooldown_min_bars: int
    loss_cooldown_max_bars: int
    target_cooldown_base_bars: int
    target_cooldown_stress_offset: float
    target_cooldown_stress_gain: float
    target_cooldown_min_bars: int
    target_cooldown_max_bars: int
    bay_lockout_consecutive_stops: int
    stop_floor_price_fraction: float
    target_min_reward_risk: float

    def validate(self) -> None:
        _require(self.loss_cooldown_min_bars <= self.loss_cooldown_max_bars, "loss cooldown min > max")
        _require(self.target_cooldown_min_bars <= self.target_cooldown_max_bars, "target cooldown min > max")


@dataclass(frozen=True)
class AVRParameters:
    uel_min_notional_inr: float
    oel_capital_fraction: float
    loading_fraction_base: float
    loading_fraction_min: float
    uel_multiplier_stress_gain: float
    uel_multiplier_max: float
    oel_fraction_min: float
    vol_scalar_min: float
    vol_scalar_max_base: float
    vol_scalar_max_min: float
    z_strength_denominator_base: float
    z_strength_denominator_stress_gain: float
    z_strength_denominator_max: float
    z_strength_cap_base: float
    z_strength_cap_min: float
    kp_base: float
    kp_stress_gain: float
    kp_max: float
    ki_base: float
    ki_stress_gain: float
    ki_min: float
    kd_base: float
    kd_stress_gain: float
    kd_max: float
    integral_clamp_base: float
    integral_clamp_stress_gain: float
    integral_clamp_min: float
    undervoltage_trip_base_pu: float
    undervoltage_trip_stress_gain: float
    undervoltage_trip_max_pu: float
    overvoltage_trip_base_pu: float
    overvoltage_trip_stress_gain: float
    overvoltage_trip_min_pu: float
    mvar_limit_base_pu: float
    mvar_limit_stress_gain: float
    mvar_limit_min_pu: float
    excitation_min_base: float
    excitation_min_stress_gain: float
    excitation_min_max: float
    excitation_max_base: float
    excitation_max_stress_gain: float
    excitation_max_min: float
    voltage_reference_pu: float
    synchronous_reactance_pu: float
    voltage_filter_alpha: float

    def validate(self) -> None:
        pairs = (("loading_fraction_min", "loading_fraction_base"), ("oel_fraction_min", "oel_capital_fraction"),
                 ("vol_scalar_min", "vol_scalar_max_min"), ("vol_scalar_max_min", "vol_scalar_max_base"),
                 ("z_strength_denominator_base", "z_strength_denominator_max"),
                 ("z_strength_cap_min", "z_strength_cap_base"), ("kp_base", "kp_max"), ("ki_min", "ki_base"),
                 ("kd_base", "kd_max"), ("integral_clamp_min", "integral_clamp_base"),
                 ("undervoltage_trip_base_pu", "undervoltage_trip_max_pu"),
                 ("overvoltage_trip_min_pu", "overvoltage_trip_base_pu"), ("mvar_limit_min_pu", "mvar_limit_base_pu"),
                 ("excitation_min_base", "excitation_min_max"), ("excitation_max_min", "excitation_max_base"))
        for lo, hi in pairs:
            _require(getattr(self, lo) <= getattr(self, hi), f"AVR requires {lo} <= {hi}")
        _require(self.excitation_min_max < self.excitation_max_min, "AVR excitation floor must stay below its ceiling")
        _require(self.undervoltage_trip_max_pu < self.voltage_reference_pu < self.overvoltage_trip_min_pu,
                 "AVR voltage reference must lie strictly inside the ANSI 27/59 corridor")


@dataclass(frozen=True)
class UnitProtectionParameters:
    stale_tick_limit_seconds: float
    stale_tick_stress_gain: float
    stale_tick_min_seconds: float
    max_spread_fraction: float
    spread_stress_gain: float
    spread_min_fraction: float
    vol_surge_mult: float
    vol_surge_stress_gain: float
    vol_surge_min_mult: float
    max_slippage_fraction: float
    slippage_stress_gain: float
    slippage_min_fraction: float
    max_adverse_bars: int
    adverse_stress_gain: float
    adverse_min_bars: int

    def validate(self) -> None:
        for lo, hi in (("stale_tick_min_seconds", "stale_tick_limit_seconds"), ("spread_min_fraction", "max_spread_fraction"),
                       ("vol_surge_min_mult", "vol_surge_mult"), ("slippage_min_fraction", "max_slippage_fraction"),
                       ("adverse_min_bars", "max_adverse_bars")):
            _require(getattr(self, lo) <= getattr(self, hi), f"SEL-300G requires {lo} <= {hi}")


@dataclass(frozen=True)
class GridProtectionParameters:
    max_daily_fleet_drawdown_pct: float
    drawdown_stress_gain: float
    drawdown_min_pct: float
    nifty_crash_rate_pct: float
    crash_stress_gain: float
    crash_min_pct: float
    max_regime_vol_z: float
    vol_z_stress_gain: float
    vol_z_min: float
    overfrequency_ratio: float

    def validate(self) -> None:
        for lo, hi in (("drawdown_min_pct", "max_daily_fleet_drawdown_pct"), ("crash_min_pct", "nifty_crash_rate_pct"),
                       ("vol_z_min", "max_regime_vol_z")):
            _require(getattr(self, lo) <= getattr(self, hi), f"MiCOM requires {lo} <= {hi}")


@dataclass(frozen=True)
class DispatcherParameters:
    ema_retention: float
    ema_update: float
    merit_window: int
    min_samples: int
    allocation_floor: float
    allocation_ceiling: float
    neutral_score: float
    score_base: float
    score_floor: float
    downside_dev_default: float
    downside_dev_floor: float
    downside_min_count: int

    def validate(self) -> None:
        _require(abs(self.ema_retention + self.ema_update - 1.0) <= 1e-12,
                 "dispatcher EMA requires disp_ema_retention + disp_ema_update == 1")
        _require(0.0 < self.allocation_floor < self.allocation_ceiling <= 1.0, "dispatcher requires 0 < floor < ceiling <= 1")
        _require(len(BAY_IDS) * self.allocation_floor <= 1.0 <= len(BAY_IDS) * self.allocation_ceiling,
                 "dispatcher floor/ceiling must admit a full allocation (n*floor <= 1 <= n*ceiling)")
        _require(self.min_samples <= self.merit_window, "dispatcher requires min_samples <= merit_window")


@dataclass(frozen=True)
class HRSGParameters:
    recovery_fraction: float
    rho_crit: float
    min_penalty_factor: float
    correlation_window: int
    min_correlation_samples: int
    pressure_time_constant_bars: float
    min_steam_pressure_pu: float
    max_steam_pressure_pu: float

    def validate(self) -> None:
        _require(self.min_correlation_samples <= self.correlation_window, "HRSG requires min samples <= window")
        _require(self.min_steam_pressure_pu < 1.0 < self.max_steam_pressure_pu, "HRSG requires min < 1 < max pressure")


@dataclass(frozen=True)
class R5ControllerParameters:
    """Immutable, validated controller parameter image built from one EffectiveConfig."""

    governor_specs: Mapping[str, Any]
    governor_schedule: GovernorScheduleParameters
    stress: SupervisoryStressParameters
    cooldown: PlantCooldownParameters
    avr: AVRParameters
    unit_protection: UnitProtectionParameters
    grid_protection: GridProtectionParameters
    dispatcher: DispatcherParameters
    hrsg: HRSGParameters
    symbol_loss_cooldown_bars: int
    symbol_trip_consecutive_losses: int
    profile: ProfileScheduleParameters
    profile_bases: Mapping[str, float]
    v3_values: Mapping[str, Any]
    values: Mapping[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def from_values(cls, values: Mapping[str, Any],
                    safety_bases: Optional[Mapping[str, Any]] = None) -> "R5ControllerParameters":
        from revision5.governor import BayGovernorSpec
        specs = {}
        for bay in BAY_IDS:
            kwargs = {name: _read(values, governor_parameter_name(bay, name)) for name in GOVERNOR_FIELD_NAMES}
            specs[bay] = BayGovernorSpec(bay_id=bay, **kwargs)
            _require(specs[bay].dynamic_offset_min < 0.0 <= specs[bay].dynamic_offset_max,
                     f"{bay}: governor requires dynamic_offset_min < 0 <= dynamic_offset_max")
        _require(abs(sum(s.capital_weight for s in specs.values()) - 1.0) <= 1e-12,
                 "governor capital weights must sum to 1.0")
        built = cls(
            governor_specs=specs,
            governor_schedule=_group(GovernorScheduleParameters, values, "gsch_"),
            stress=_group(SupervisoryStressParameters, values, "dyn_"),
            cooldown=_group(PlantCooldownParameters, values, "plant_"),
            avr=_group(AVRParameters, values, "avr_"),
            unit_protection=_group(UnitProtectionParameters, values, "sel_"),
            grid_protection=_group(GridProtectionParameters, values, "micom_"),
            dispatcher=_group(DispatcherParameters, values, "disp_"),
            hrsg=_group(HRSGParameters, values, "hrsg_"),
            symbol_loss_cooldown_bars=_read(values, "symbol_loss_cooldown_bars"),
            symbol_trip_consecutive_losses=_read(values, "symbol_trip_consecutive_losses"),
            profile=_group(ProfileScheduleParameters, values, "psch_"),
            profile_bases=_profile_bases(values, safety_bases),
            v3_values={key: _read(values, name) for key, name in POSITION_CONTROL_V3_PARAMETERS.items()},
            values={name: _read(values, name) for name in CONTROLLER_PARAMETER_NAMES},
        )
        for group in (built.governor_schedule, built.stress, built.cooldown, built.avr, built.unit_protection,
                      built.grid_protection, built.dispatcher, built.hrsg, built.profile):
            group.validate()
        _validate_envelopes(built.values, built.profile, built.dispatcher)
        return built

    @classmethod
    def from_config(cls, config, safety_bases: Optional[Mapping[str, Any]] = None) -> "R5ControllerParameters":
        """Read every controller parameter (and the profile bases) from an EffectiveConfig through
        ``require``; ``safety_bases`` are the run's safety-contract values."""
        names = CONTROLLER_PARAMETER_NAMES + PROFILE_BASE_PARAMETERS
        return cls.from_values({name: config.require(name) for name in names}, safety_bases)

    @classmethod
    def defaults(cls) -> "R5ControllerParameters":
        """The registry default image (the documented defaults, not a hidden fallback)."""
        return cls.from_values(controller_parameter_defaults())

    def position_control_v3(self):
        from revision5.governor import PositionControlV3
        return PositionControlV3(**self.v3_values)

    def startup_report(self) -> Dict[str, Any]:
        import hashlib
        import json
        payload = {name: self.values[name] for name in CONTROLLER_PARAMETER_NAMES}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        return {"parameter_count": len(payload), "values_sha256": digest,
                "non_default": {k: v for k, v in payload.items() if v != CONTROLLER_PARAMETERS[k].default},
                "effective_values": payload}


def _validate_envelopes(v: Mapping[str, Any], profile: "ProfileScheduleParameters", dispatcher: "DispatcherParameters") -> None:
    """Cross-parameter ordering / feasibility of the envelope rows (Tier 1/3, closed-loop, studies, profile)."""
    for stem in ("tier1_normalized_vol", "tier1_atr_floor", "tier1_slippage_scale", "tier3_kp_scale", "tier3_ki_divisor",
                 "tier3_kd_scale", "cl_entry_derate", "cl_persistence", "cl_gain_scale", "studies_weight"):
        _require(v[f"{stem}_min"] <= v[f"{stem}_max"], f"{stem} envelope requires min <= max")
    _require(0.0 < v["cl_persistence_min"] < v["cl_persistence_max"] < 1.0, "persistence envelope must lie strictly inside (0, 1)")
    # Study weights start at 1/n and are renormalized: the bounds must contain the neutral weight, so
    # n * min <= 1 <= n * max (the weights can sum to one under the bounds).
    _require(STUDY_COUNT * v["studies_weight_min"] <= 1.0 + 1e-12 and STUDY_COUNT * v["studies_weight_max"] >= 1.0 - 1e-12,
             "studies weight bounds must admit a unit sum (n*min <= 1 <= n*max)")
    _require(profile.dispatch_ceiling_min <= dispatcher.allocation_ceiling,
             "psch_dispatch_ceiling_min must not exceed disp_allocation_ceiling")


def _profile_bases(values: Mapping[str, Any], safety_bases: Optional[Mapping[str, Any]]) -> Dict[str, float]:
    """Profile bases: registry parameters from ``values`` (EffectiveConfig) and safety-contract values
    from ``safety_bases``.  Absent inputs (direct construction of the registry-default image) are the
    canonical registry defaults -- the documented values, not a hidden copy."""
    from canonical_parameter_registry import CanonicalParameterRegistry
    registry = None
    bases: Dict[str, float] = {}
    for name in PROFILE_BASE_PARAMETERS:
        if name not in values:
            registry = registry or CanonicalParameterRegistry()
            bases[name] = float(registry.params[name].default)
        else:
            bases[name] = float(values[name])
    for name in PROFILE_BASE_SAFETY:
        if safety_bases is None or name not in safety_bases:
            registry = registry or CanonicalParameterRegistry()
            bases[name] = float(registry.safety_params[name].default)
        else:
            bases[name] = float(safety_bases[name])
    for name, value in bases.items():
        _require(isfinite(value) and value >= 0.0, f"profile base {name} must be finite and non-negative")
    return bases


_DEFAULTS_CACHE: Optional[R5ControllerParameters] = None


def default_controller_parameters() -> R5ControllerParameters:
    global _DEFAULTS_CACHE
    if _DEFAULTS_CACHE is None:
        _DEFAULTS_CACHE = R5ControllerParameters.defaults()
    return _DEFAULTS_CACHE
