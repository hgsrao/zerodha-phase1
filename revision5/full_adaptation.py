"""Causal operating images rebuilt from immutable registry bases on every R5 bar.

This is finite bounded gain scheduling, not Kalman estimation or calibration.
Hard registry domains and safety contract authority are never rewritten.
"""
from __future__ import annotations
import math
from dataclasses import replace
from revision5.controller_parameters import (
    CONTROLLER_PARAMETERS, ADAPTATION_POLICY_NAMES, ENVELOPE_PARAMETER_NAMES,
    R5ControllerParameters, ControllerParameterError, governor_parameter_name,
)
from revision5.topology import BAY_IDS


def bounded_weights(raw, lower, upper):
    """Euclidean projection onto sum=1 with individual hard bounds."""
    if sum(lower.values()) > 1.0 + 1e-12 or sum(upper.values()) < 1.0 - 1e-12:
        raise ValueError('capital bounds do not admit a unit allocation')
    lo = min(raw[k] - upper[k] for k in raw)
    hi = max(raw[k] - lower[k] for k in raw)
    for _ in range(64):  # numerical precision, not a tuning constant
        mid = (lo + hi) / 2
        if sum(max(lower[k], min(upper[k], raw[k] - mid)) for k in raw) > 1:
            lo = mid
        else:
            hi = mid
    result = {k: max(lower[k], min(upper[k], raw[k] - (lo + hi) / 2)) for k in raw}
    if abs(sum(result.values()) - 1) > 1e-10:
        raise ValueError('capital projection did not conserve the budget')
    return result


class FullR5Adaptation:
    """Stateless per-bar scheduling. Bases and policy are frozen for the run.

    Each generated image is checked as a whole before any consumer mutation.
    Constraint backtracking attenuates a request and is recorded explicitly.
    It is not evidence that every coefficient changes for every input.
    """
    def __init__(self, registry, config, safety):
        self.registry = registry
        self.base = config.as_dict()
        self.safety = dict(safety)
        self.last_report = None

    def evaluate(self, env, governors):
        probs = [float(env.trend_probability), float(env.chop_probability), float(env.turbulent_probability)]
        values = probs + [float(env.normalized_vol), float(env.control_error_delta), float(env.fleet_drawdown_fraction)]
        if not all(math.isfinite(v) for v in values) or min(probs) < 0 or sum(probs) <= 0 or values[3] <= 0:
            raise ValueError('invalid causal full-adaptation environment')
        trend, chop, turbulent = (v / sum(probs) for v in probs)
        b = self.base
        stress = min(1., max(0., b['dyn_stress_turbulent_weight'] * turbulent
                             + b['dyn_stress_chop_weight'] * chop
                             + b['dyn_stress_excess_vol_weight'] * max(0., env.normalized_vol - 1)))
        error = math.tanh(abs(env.control_error_delta))
        pressure = math.tanh(b['adapt_stress_weight'] * stress + b['adapt_error_weight'] * error
                             - b['adapt_trend_weight'] * (trend - turbulent))
        fraction = b['adapt_fraction'] * abs(pressure)
        target = dict(b)
        # Includes original native controls AND legacy Box6/study/MarkV operating coefficients.
        legacy = ('pid_kp_entry','pid_ki_entry','pid_kd_entry','pid_kp_exit','pid_ki_exit','pid_kd_exit',
                  'mpc_schedule_kp_gain','mpc_schedule_ki_gain','mpc_schedule_kd_gain',
                  'studies_pid_kp','studies_pid_ki','studies_pid_kd','studies_pid_output_clamp',
                  'gov_path_error_sigma','mv_fsr_entry_threshold','mv_fsr_exit_threshold',
                  'mv_fsrt_slope','mv_fsra_slope','grid_vix_derate_start',
                  'ecs_demand_restore_step_pu','cl_confidence_offset_gain')
        names = set(CONTROLLER_PARAMETERS) | set(legacy)
        immutable = set(ADAPTATION_POLICY_NAMES) | set(ENVELOPE_PARAMETER_NAMES)
        for name in sorted(names - immutable):
            spec = self.registry.params[name]
            base = b[name]
            # Safety upper limits only tighten. Lower-bound protections tighten upward.
            safe = name in CONTROLLER_PARAMETERS and CONTROLLER_PARAMETERS[name].safety_class == 'FIXED_SAFETY'
            lower_protection = ('undervoltage' in name or 'uel_' in name
                                or name.endswith('min_steam_pressure_pu') or 'cooldown' in name)
            upward = (pressure > 0) if not safe else lower_protection
            anchor = spec.maximum if upward else spec.minimum
            target[name] = base + fraction * (anchor - base)
            if safe:
                target[name] = max(base, target[name]) if lower_protection else min(base, target[name])
        # Explicit direction for inherited entry/grid/path protection policies.
        for name in ('mv_fsr_entry_threshold','mv_fsr_exit_threshold'):
            target[name] = b[name] + fraction * (self.registry.params[name].maximum-b[name])
        for name in ('grid_vix_derate_start','ecs_demand_restore_step_pu','gov_path_error_sigma'):
            target[name] = b[name] - fraction * (b[name]-self.registry.params[name].minimum)
        # Coupled controls have one independent operating degree of freedom.
        target['disp_ema_retention'] = 1.0 - target['disp_ema_update']
        # Capital tilt uses each bay's actual causal tracking error, never its dictionary index.
        raw = {bay: b[governor_parameter_name(bay,'capital_weight')] * math.exp(
            -b['adapt_fraction'] * stress * abs(float(governors[bay].last_error))) for bay in BAY_IDS}
        lows = {bay:self.registry.params[governor_parameter_name(bay,'capital_weight')].minimum for bay in BAY_IDS}
        highs = {bay:self.registry.params[governor_parameter_name(bay,'capital_weight')].maximum for bay in BAY_IDS}
        weights = bounded_weights(raw, lows, highs)
        for bay,w in weights.items(): target[governor_parameter_name(bay,'capital_weight')] = w
        # Explicit relational projections; validation below remains authoritative.
        for key in list(target):
            if key.endswith('_min') and key[:-4] + '_max' in target:
                other = key[:-4] + '_max'
                target[key] = min(target[key],target[other])
            if key.endswith('_min_scale') and key[:-10] + '_max_scale' in target:
                other = key[:-10] + '_max_scale'
                target[key] = min(target[key],target[other])
        target['disp_min_samples'] = min(target['disp_min_samples'],target['disp_merit_window'])
        target['hrsg_min_correlation_samples'] = min(target['hrsg_min_correlation_samples'],target['hrsg_correlation_window'])
        target['gov_v3_gamma_fast'] = min(target['gov_v3_gamma_fast'],target['gov_v3_gamma_slow'])
        # Backtracking is a feasibility projection, NOT a silent acceptance of invalid profiles.
        attenuation = 1.
        constraints = []
        for attempt in range(54):  # sufficient binary64 convergence
            operating = dict(b)
            for name in names:
                spec = self.registry.params[name]
                value = b[name] + attenuation * (target[name] - b[name])
                value = max(spec.minimum,min(spec.maximum,value))
                if spec.param_type == 'int':
                    safe = name in CONTROLLER_PARAMETERS and CONTROLLER_PARAMETERS[name].safety_class == 'FIXED_SAFETY'
                    # Discrete safety limits round toward tighter operation, never away from it.
                    operating[name] = int(math.floor(value) if safe and value < b[name]
                                          else math.ceil(value) if safe and value > b[name] else round(value))
                else:
                    operating[name] = float(value)
            try:
                operating["adapt_active"] = 1.0
                params = R5ControllerParameters.from_values(operating,self.safety)
                # Legacy refresh consumers enforce these inequalities too.
                if operating['mv_fsr_exit_threshold'] >= operating['mv_fsr_entry_threshold']:
                    raise ValueError('MarkV exit threshold must remain below entry threshold')
                break
            except (ValueError,ControllerParameterError) as exc:
                constraints.append(str(exc))
                attenuation /= 2
        else:
            raise ValueError('no feasible adaptive operating image: '+ '; '.join(sorted(set(constraints))))
        self.last_report = {'source':'causal_regime_error_schedule','stress':stress,'trend':trend,
            'chop':chop,'pressure':pressure,'requested_fraction':fraction,'attenuation':attenuation,
            'constraint_projections':sorted(set(constraints)),
            'parameters':{n:{'base':b[n],'operating':operating[n],
                             'changed':operating[n]!=b[n],
                             'role':'IMMUTABLE_POLICY' if n in immutable else 'ADAPTIVE_OPERATING_VALUE'}
                          for n in sorted(names)}}
        return operating, params


def constrain_entry_plan(plan, atr, spec, parameters):
    """Apply plant stop/target constraints BEFORE risk/cost sizing, for either side."""
    entry = float(plan.entry_price)
    sign = 1. if plan.side == 'BUY' else -1. if plan.side == 'SELL' else 0.
    if not sign or not all(math.isfinite(x) and x > 0 for x in (entry,float(atr))):
        raise ValueError('invalid plant-constrained entry inputs')
    old_risk = sign * (entry - float(plan.stop_price))
    old_reward = sign * (float(plan.target_price) - entry)
    if old_risk <= 0 or old_reward <= 0:
        raise ValueError('invalid order geometry before plant constraints')
    risk = max(old_risk,float(spec.atr_barrier)*atr,
               entry*parameters.cooldown.stop_floor_price_fraction)
    reward = max(old_reward,float(spec.target_m)*atr,risk*parameters.cooldown.target_min_reward_risk)
    constrained = replace(plan,stop_price=entry-sign*risk,target_price=entry+sign*reward)
    if constrained.stop_price <= 0 or constrained.target_price <= 0:
        raise ValueError('plant constraints produce non-positive price')
    return constrained, {'planned_risk':old_risk,'constrained_risk':risk,'planned_reward':old_reward,
                         'constrained_reward':reward,'quantity_recomputed':'subsequent_position_manager.size'}
