"""
Revision-5 native dynamic parameter engine.

This module has NO dependency on Revision-2/3/4 dynamic-control code.

Design rules
------------
1. Existing R5 specifications remain immutable certified bases/envelopes.
2. Operating values are generated as immutable per-bar snapshots.
3. Dynamic values are bounded and fail closed on invalid numeric input.
4. No broker I/O.
5. No mutation of BAY_GOVERNOR_SPECS.
6. No mutation of CentralPlantMasterDCS.
7. Consumers will be wired separately after this module is certified.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional
import math

from revision5.controller_parameters import (
    AVRParameters,
    R5ControllerParameters,
    default_controller_parameters,
)


def _finite(name: str, value: float) -> float:
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite")
    return value


def _clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, float(value)))


@dataclass(frozen=True)
class R5EnvironmentState:
    """
    Current Revision-5 supervisory environment.

    Probabilities do not need to arrive perfectly normalized; normalization
    occurs inside the controller. Invalid/negative inputs fail closed.
    """

    normalized_vol: float = 1.0

    trend_probability: float = 1.0 / 3.0
    chop_probability: float = 1.0 / 3.0
    turbulent_probability: float = 1.0 / 3.0

    fleet_drawdown_fraction: float = 0.0

    # Feedback error used by R5 gain scheduling.
    control_error_delta: float = 0.0


@dataclass(frozen=True)
class GovernorRuntimeProfile:
    bay_id: str

    target_r: float
    outcome_window: int
    integral_clamp: float

    grid_droop_gain: float
    grid_droop_max: float

    kp: float
    ki: float
    kd: float

    base_z: float
    droop_r: float
    dynamic_offset_min: float
    dynamic_offset_max: float


@dataclass(frozen=True)
class AVRRuntimeProfile:
    bay_id: str

    loading_fraction: float
    uel_min_multiplier: float
    oel_fraction: float

    vol_scalar_min: float
    vol_scalar_max: float

    z_strength_denominator: float
    z_strength_cap: float

    # Registry-owned AVR coefficients (avr_* in revision5/controller_parameters.py).
    # None means the registry defaults (direct test construction).
    params: Optional[AVRParameters] = field(default=None, compare=False, repr=False)

    @property
    def _p(self) -> AVRParameters:
        return self.params if self.params is not None else default_controller_parameters().avr

    @property
    def control_stress(self) -> float:
        # loading_fraction is already dynamically derated by R5.
        return _clamp(
            1.0
            - (
                float(self.loading_fraction)
                / self._p.loading_fraction_base
            ),
            0.0,
            1.0,
        )

    @property
    def kp(self) -> float:
        p, stress = self._p, self.control_stress
        return _clamp(p.kp_base + p.kp_stress_gain * stress, p.kp_base, p.kp_max)

    @property
    def ki(self) -> float:
        p, stress = self._p, self.control_stress
        return _clamp(p.ki_base / (1.0 + p.ki_stress_gain * stress), p.ki_min, p.ki_base)

    @property
    def kd(self) -> float:
        p, stress = self._p, self.control_stress
        return _clamp(p.kd_base + p.kd_stress_gain * stress, p.kd_base, p.kd_max)

    @property
    def integral_clamp(self) -> float:
        p, stress = self._p, self.control_stress
        return _clamp(p.integral_clamp_base - p.integral_clamp_stress_gain * stress,
                      p.integral_clamp_min, p.integral_clamp_base)

    @property
    def undervoltage_trip_pu(self) -> float:
        # Stress narrows the safe voltage corridor.
        p = self._p
        return _clamp(p.undervoltage_trip_base_pu + p.undervoltage_trip_stress_gain * self.control_stress,
                      p.undervoltage_trip_base_pu, p.undervoltage_trip_max_pu)

    @property
    def overvoltage_trip_pu(self) -> float:
        p = self._p
        return _clamp(p.overvoltage_trip_base_pu - p.overvoltage_trip_stress_gain * self.control_stress,
                      p.overvoltage_trip_min_pu, p.overvoltage_trip_base_pu)

    @property
    def mvar_limit_pu(self) -> float:
        p = self._p
        return _clamp(p.mvar_limit_base_pu - p.mvar_limit_stress_gain * self.control_stress,
                      p.mvar_limit_min_pu, p.mvar_limit_base_pu)

    @property
    def excitation_min(self) -> float:
        p = self._p
        return _clamp(p.excitation_min_base + p.excitation_min_stress_gain * self.control_stress,
                      p.excitation_min_base, p.excitation_min_max)

    @property
    def excitation_max(self) -> float:
        p = self._p
        return _clamp(p.excitation_max_base - p.excitation_max_stress_gain * self.control_stress,
                      p.excitation_max_min, p.excitation_max_base)


@dataclass(frozen=True)
class UnitProtectionRuntimeProfile:
    bay_id: str

    stale_tick_limit_seconds: float
    max_spread_fraction: float
    vol_surge_mult: float

    max_slippage_pct: float
    max_adverse_bars: int


@dataclass(frozen=True)
class GridProtectionRuntimeProfile:
    max_daily_fleet_drawdown_pct: float
    nifty_crash_rate_pct: float
    max_regime_vol_z: float


@dataclass(frozen=True)
class PlantRuntimeProfile:
    loss_cooldown_bars: int
    target_cooldown_bars: int

    dispatcher_min_floor: float
    dispatcher_max_ceiling: float


@dataclass(frozen=True)
class StrategyRuntimeProfile:
    entry_confidence_threshold: float
    min_risk_reward_ratio: float

    min_hold_bars: int
    max_hold_bars: int


@dataclass(frozen=True)
class ExecutionRuntimeProfile:
    slippage_tolerance_fraction: float
    max_market_data_age_seconds: int
    order_timeout_seconds: int


@dataclass(frozen=True)
class SafetyRuntimeProfile:
    """
    Runtime operating limits.

    These may tighten dynamically.

    Absolute certification envelopes remain outside this object and may
    never be widened by market/regime feedback.
    """

    drawdown_halt_threshold: float

    max_gross_exposure_fraction: float
    max_symbol_exposure_fraction: float

    max_concurrent_positions: int

    # Applied to the externally approved monetary daily-loss ceiling.
    daily_loss_limit_multiplier: float


@dataclass(frozen=True)
class DynamicParameterSnapshot:
    """
    One immutable parameter image for one R5 control interval/bar.
    """

    version: int

    normalized_vol: float
    trend_probability: float
    chop_probability: float
    turbulent_probability: float

    stress_index: float
    risk_derate_factor: float

    governor_by_bay: Dict[str, GovernorRuntimeProfile]
    avr_by_bay: Dict[str, AVRRuntimeProfile]
    unit_protection_by_bay: Dict[
        str,
        UnitProtectionRuntimeProfile,
    ]
    grid_protection: GridProtectionRuntimeProfile

    plant: PlantRuntimeProfile
    strategy: StrategyRuntimeProfile
    execution: ExecutionRuntimeProfile
    safety: SafetyRuntimeProfile


class Revision5DynamicParameterController:
    """
    Native Revision-5 dynamic parameter generator.

    Current R5 constants serve only as certified neutral/base references.
    Consumers should eventually use DynamicParameterSnapshot operating
    values instead of directly consuming those bases.
    """

    def __init__(self, parameters: Optional[R5ControllerParameters] = None) -> None:
        self._version = 0
        self.configure(parameters if parameters is not None else default_controller_parameters())

    def configure(self, parameters: R5ControllerParameters) -> None:
        """Install registry-owned coefficients; the version counter (state) is preserved."""
        if not isinstance(parameters, R5ControllerParameters):
            raise TypeError("parameters must be R5ControllerParameters")
        self.parameters = parameters

    def _normalized_state(
        self,
        env: R5EnvironmentState,
    ) -> tuple[float, float, float, float, float, float]:

        vol = _finite(
            "normalized_vol",
            env.normalized_vol,
        )

        trend = _finite(
            "trend_probability",
            env.trend_probability,
        )

        chop = _finite(
            "chop_probability",
            env.chop_probability,
        )

        turbulent = _finite(
            "turbulent_probability",
            env.turbulent_probability,
        )

        drawdown = _finite(
            "fleet_drawdown_fraction",
            env.fleet_drawdown_fraction,
        )

        error_delta = _finite(
            "control_error_delta",
            env.control_error_delta,
        )

        if vol <= 0.0:
            raise ValueError(
                "normalized_vol must be positive"
            )

        if min(trend, chop, turbulent) < 0.0:
            raise ValueError(
                "regime probabilities cannot be negative"
            )

        total = trend + chop + turbulent

        if total <= 0.0:
            raise ValueError(
                "regime probability total must be positive"
            )

        trend /= total
        chop /= total
        turbulent /= total

        vol = _clamp(vol, self.parameters.stress.vol_min, self.parameters.stress.vol_max)
        drawdown = _clamp(drawdown, 0.0, 1.0)

        return (
            vol,
            trend,
            chop,
            turbulent,
            drawdown,
            error_delta,
        )

    def evaluate(
        self,
        env: R5EnvironmentState,
    ) -> DynamicParameterSnapshot:

        (
            vol,
            trend,
            chop,
            turbulent,
            drawdown,
            error_delta,
        ) = self._normalized_state(env)

        #
        # Common supervisory state
        #

        P = self.parameters
        st, cd, gs, avr = P.stress, P.cooldown, P.governor_schedule, P.avr
        sel, micom = P.unit_protection, P.grid_protection

        excess_vol = max(0.0, vol - 1.0)

        stress = _clamp(
            st.stress_turbulent_weight * turbulent
            + st.stress_chop_weight * chop
            + st.stress_excess_vol_weight * excess_vol,
            0.0,
            1.0,
        )

        risk_derate = _clamp(
            1.0
            - st.risk_derate_stress_gain * stress
            - st.risk_derate_drawdown_gain * drawdown,
            st.risk_derate_lower_bound,
            1.0,
        )

        trend_bias = _clamp(
            trend - turbulent,
            -1.0,
            1.0,
        )

        #
        # Strategy operating profile -- each value is a schedule of the run's own base
        # (registry parameter); consumed only with dynamic_profile_mode="consume".
        #
        ps, base = P.profile, P.profile_bases

        entry_confidence = _clamp(
            base["entry_confidence_threshold"]
            + ps.entry_conf_stress_gain * stress
            + ps.entry_conf_chop_gain * chop,
            ps.entry_conf_min,
            ps.entry_conf_max,
        )

        min_rr = _clamp(
            base["min_risk_reward_ratio"] + ps.min_rr_stress_gain * stress,
            ps.min_rr_min,
            ps.min_rr_max,
        )

        min_hold = int(
            round(
                _clamp(
                    base["min_hold_bars"]
                    + ps.min_hold_chop_gain * chop
                    + ps.min_hold_turbulent_gain * turbulent,
                    ps.min_hold_min,
                    ps.min_hold_max,
                )
            )
        )

        max_hold = int(
            round(
                _clamp(
                    base["max_hold_bars"]
                    - ps.max_hold_stress_gain * stress
                    + ps.max_hold_trend_gain * trend,
                    ps.max_hold_min,
                    ps.max_hold_max,
                )
            )
        )

        strategy = StrategyRuntimeProfile(
            entry_confidence_threshold=entry_confidence,
            min_risk_reward_ratio=min_rr,
            min_hold_bars=min_hold,
            max_hold_bars=max_hold,
        )

        #
        # Plant operating profile
        #

        loss_cooldown = int(
            round(
                _clamp(
                    float(cd.loss_cooldown_base_bars) * (
                        cd.loss_cooldown_stress_offset
                        + cd.loss_cooldown_stress_gain * stress
                    ),
                    float(cd.loss_cooldown_min_bars),
                    float(cd.loss_cooldown_max_bars),
                )
            )
        )

        target_cooldown = int(
            round(
                _clamp(
                    float(cd.target_cooldown_base_bars) * (
                        cd.target_cooldown_stress_offset
                        + cd.target_cooldown_stress_gain * stress
                    ),
                    float(cd.target_cooldown_min_bars),
                    float(cd.target_cooldown_max_bars),
                )
            )
        )

        dispatcher_min_floor = _clamp(
            P.dispatcher.allocation_floor * (
                1.0
                - ps.dispatch_floor_stress_gain * stress
            ),
            ps.dispatch_floor_min,
            ps.dispatch_floor_max,
        )

        dispatcher_max_ceiling = _clamp(
            P.dispatcher.allocation_ceiling * risk_derate,
            ps.dispatch_ceiling_min,
            P.dispatcher.allocation_ceiling,
        )

        if (
            dispatcher_min_floor
            >= dispatcher_max_ceiling
        ):
            dispatcher_min_floor = min(
                dispatcher_min_floor,
                dispatcher_max_ceiling * ps.dispatch_floor_ceiling_ratio,
            )

        plant = PlantRuntimeProfile(
            loss_cooldown_bars=loss_cooldown,
            target_cooldown_bars=target_cooldown,
            dispatcher_min_floor=dispatcher_min_floor,
            dispatcher_max_ceiling=dispatcher_max_ceiling,
        )

        #
        # Execution operating profile (applied tighten-only against the safety contract)
        #

        slippage = _clamp(
            base["max_slippage_fraction"]
            * (
                1.0
                + ps.slippage_vol_gain * (vol - 1.0)
            ),
            ps.slippage_min_fraction,
            ps.slippage_max_fraction,
        )

        max_market_age = int(
            round(
                _clamp(
                    base["max_market_data_age_seconds"] / (
                        1.0
                        + ps.data_age_stress_gain * stress
                    ),
                    ps.data_age_min_seconds,
                    base["max_market_data_age_seconds"],
                )
            )
        )

        order_timeout = int(
            round(
                _clamp(
                    base["order_timeout_seconds_execution"] * (
                        1.0
                        + ps.order_timeout_stress_gain * stress
                    ),
                    ps.order_timeout_min_seconds,
                    ps.order_timeout_max_seconds,
                )
            )
        )

        execution = ExecutionRuntimeProfile(
            slippage_tolerance_fraction=slippage,
            max_market_data_age_seconds=max_market_age,
            order_timeout_seconds=order_timeout,
        )

        #
        # Safety operating profile.
        #
        # Dynamic controller is allowed to tighten these limits,
        # never widen beyond the certified neutral envelopes.
        #

        safety = SafetyRuntimeProfile(
            drawdown_halt_threshold=_clamp(
                base["safety_drawdown_halt_threshold"] * (
                    1.0
                    - ps.drawdown_halt_stress_gain * stress
                ),
                ps.drawdown_halt_min,
                base["safety_drawdown_halt_threshold"],
            ),
            max_gross_exposure_fraction=_clamp(
                base["max_gross_exposure_fraction"] * risk_derate,
                ps.gross_exposure_min,
                base["max_gross_exposure_fraction"],
            ),
            max_symbol_exposure_fraction=_clamp(
                base["max_exposure_per_symbol_fraction"] * risk_derate,
                ps.symbol_exposure_min,
                base["max_exposure_per_symbol_fraction"],
            ),
            max_concurrent_positions=int(
                round(
                    _clamp(
                        base["max_concurrent_positions"] * risk_derate,
                        ps.concurrent_positions_min,
                        base["max_concurrent_positions"],
                    )
                )
            ),
            daily_loss_limit_multiplier=_clamp(
                risk_derate,
                ps.daily_loss_multiplier_min,
                1.0,
            ),
        )

        #
        # Five independent R5 bay governor profiles.
        #
        # Base BayGovernorSpec objects are NEVER mutated.
        #

        governor_by_bay: Dict[
            str,
            GovernorRuntimeProfile,
        ] = {}

        abs_error = abs(error_delta)

        for bay_id, base in P.governor_specs.items():

            target_r = _clamp(
                base.target_r
                * (
                    1.0
                    + gs.target_trend_gain * trend_bias
                    - gs.target_stress_gain * stress
                ),
                base.target_r * gs.target_min_scale,
                base.target_r * gs.target_max_scale,
            )

            outcome_window = int(
                round(
                    _clamp(
                        base.outcome_window
                        * (
                            1.0
                            + gs.window_stress_gain * stress
                        ),
                        float(gs.window_min),
                        float(gs.window_max),
                    )
                )
            )

            integral_clamp = _clamp(
                base.integral_clamp
                / (
                    1.0
                    + gs.integral_stress_gain * stress
                    + gs.integral_error_gain * abs_error
                ),
                base.integral_clamp * gs.integral_min_scale,
                base.integral_clamp * gs.integral_max_scale,
            )

            droop_gain = _clamp(
                base.grid_droop_gain
                * vol
                * (
                    1.0
                    + gs.droop_gain_stress_gain * stress
                ),
                base.grid_droop_gain * gs.droop_gain_min_scale,
                base.grid_droop_gain * gs.droop_gain_max_scale,
            )

            droop_max = _clamp(
                base.grid_droop_max
                * (
                    1.0
                    + gs.droop_max_stress_gain * stress
                ),
                base.grid_droop_max * gs.droop_max_min_scale,
                base.grid_droop_max * gs.droop_max_max_scale,
            )

            #
            # R5-native gain scheduling.
            #

            kp = base.kp * _clamp(
                1.0
                + gs.kp_error_gain * abs_error
                + gs.kp_stress_gain * stress,
                gs.kp_scale_min,
                gs.kp_scale_max,
            )

            ki = base.ki / _clamp(
                1.0
                + gs.ki_error_gain * abs_error
                + gs.ki_stress_gain * stress,
                gs.ki_divisor_min,
                gs.ki_divisor_max,
            )

            kd = base.kd * _clamp(
                1.0
                + gs.kd_error_gain * abs_error
                + gs.kd_stress_gain * stress,
                gs.kd_scale_min,
                gs.kd_scale_max,
            )

            governor_by_bay[bay_id] = (
                GovernorRuntimeProfile(
                    bay_id=bay_id,
                    target_r=float(target_r),
                    outcome_window=outcome_window,
                    integral_clamp=float(
                        integral_clamp
                    ),
                    grid_droop_gain=float(
                        droop_gain
                    ),
                    grid_droop_max=float(
                        droop_max
                    ),
                    kp=float(kp),
                    ki=float(ki),
                    kd=float(kd),

                    base_z=float(
                        _clamp(
                            base.base_z
                            + gs.base_z_trend_gain * trend_bias
                            - gs.base_z_stress_gain * stress,
                            base.base_z - gs.base_z_lower_offset,
                            base.base_z + gs.base_z_upper_offset,
                        )
                    ),
                    droop_r=float(
                        _clamp(
                            base.droop_r
                            * (
                                1.0
                                - gs.droop_r_stress_gain * stress
                            ),
                            base.droop_r * gs.droop_r_min_scale,
                            base.droop_r,
                        )
                    ),
                    dynamic_offset_min=float(
                        _clamp(
                            base.dynamic_offset_min
                            * (
                                1.0
                                + gs.offset_min_stress_gain * stress
                            ),
                            base.dynamic_offset_min * gs.offset_min_lower_scale,
                            base.dynamic_offset_min * gs.offset_min_upper_scale,
                        )
                    ),
                    dynamic_offset_max=float(
                        _clamp(
                            base.dynamic_offset_max
                            * risk_derate,
                            0.0,
                            base.dynamic_offset_max,
                        )
                    ),
                )
            )

        #
        # Native R5 AVR operating profiles.
        #
        # Stress may derate loading/OEL or raise the UEL friction floor.
        # It can never increase loading beyond the certified neutral base.
        #

        avr_by_bay: Dict[str, AVRRuntimeProfile] = {}

        unit_protection_by_bay: Dict[
            str,
            UnitProtectionRuntimeProfile,
        ] = {}

        for bay_id in P.governor_specs:
            avr_by_bay[bay_id] = AVRRuntimeProfile(
                bay_id=bay_id,
                loading_fraction=float(
                    _clamp(
                        avr.loading_fraction_base * risk_derate,
                        avr.loading_fraction_min,
                        avr.loading_fraction_base,
                    )
                ),
                uel_min_multiplier=float(
                    _clamp(
                        1.0 + avr.uel_multiplier_stress_gain * stress,
                        1.0,
                        avr.uel_multiplier_max,
                    )
                ),
                oel_fraction=float(
                    _clamp(
                        avr.oel_capital_fraction * risk_derate,
                        avr.oel_fraction_min,
                        avr.oel_capital_fraction,
                    )
                ),
                vol_scalar_min=avr.vol_scalar_min,
                vol_scalar_max=float(
                    _clamp(
                        avr.vol_scalar_max_base * risk_derate,
                        avr.vol_scalar_max_min,
                        avr.vol_scalar_max_base,
                    )
                ),
                z_strength_denominator=float(
                    _clamp(
                        avr.z_strength_denominator_base
                        * (
                            1.0
                            + avr.z_strength_denominator_stress_gain * stress
                        ),
                        avr.z_strength_denominator_base,
                        avr.z_strength_denominator_max,
                    )
                ),
                z_strength_cap=float(
                    _clamp(
                        avr.z_strength_cap_base * risk_derate,
                        avr.z_strength_cap_min,
                        avr.z_strength_cap_base,
                    )
                ),
                params=avr,
            )

            unit_protection_by_bay[
                bay_id
            ] = UnitProtectionRuntimeProfile(
                bay_id=bay_id,

                # Protection is dynamically tightened only.
                stale_tick_limit_seconds=float(
                    _clamp(
                        sel.stale_tick_limit_seconds
                        * (
                            1.0
                            - sel.stale_tick_stress_gain * stress
                        ),
                        sel.stale_tick_min_seconds,
                        sel.stale_tick_limit_seconds,
                    )
                ),
                max_spread_fraction=float(
                    _clamp(
                        sel.max_spread_fraction
                        * (
                            1.0
                            - sel.spread_stress_gain * stress
                        ),
                        sel.spread_min_fraction,
                        sel.max_spread_fraction,
                    )
                ),
                vol_surge_mult=float(
                    _clamp(
                        sel.vol_surge_mult
                        * (
                            1.0
                            - sel.vol_surge_stress_gain * stress
                        ),
                        sel.vol_surge_min_mult,
                        sel.vol_surge_mult,
                    )
                ),
                max_slippage_pct=float(
                    _clamp(
                        sel.max_slippage_fraction
                        * (
                            1.0
                            - sel.slippage_stress_gain * stress
                        ),
                        sel.slippage_min_fraction,
                        sel.max_slippage_fraction,
                    )
                ),
                max_adverse_bars=int(
                    round(
                        _clamp(
                            float(sel.max_adverse_bars)
                            * (
                                1.0
                                - sel.adverse_stress_gain * stress
                            ),
                            float(sel.adverse_min_bars),
                            float(sel.max_adverse_bars),
                        )
                    )
                ),
            )

        grid_protection = GridProtectionRuntimeProfile(
            # Again: tighten only. Never widen protection.
            max_daily_fleet_drawdown_pct=float(
                _clamp(
                    micom.max_daily_fleet_drawdown_pct
                    * (
                        1.0
                        - micom.drawdown_stress_gain * stress
                    ),
                    micom.drawdown_min_pct,
                    micom.max_daily_fleet_drawdown_pct,
                )
            ),
            nifty_crash_rate_pct=float(
                _clamp(
                    micom.nifty_crash_rate_pct
                    * (
                        1.0
                        - micom.crash_stress_gain * stress
                    ),
                    micom.crash_min_pct,
                    micom.nifty_crash_rate_pct,
                )
            ),
            max_regime_vol_z=float(
                _clamp(
                    micom.max_regime_vol_z
                    * (
                        1.0
                        - micom.vol_z_stress_gain * stress
                    ),
                    micom.vol_z_min,
                    micom.max_regime_vol_z,
                )
            ),
        )

        self._version += 1

        return DynamicParameterSnapshot(
            version=self._version,
            normalized_vol=vol,
            trend_probability=trend,
            chop_probability=chop,
            turbulent_probability=turbulent,
            stress_index=stress,
            risk_derate_factor=risk_derate,
            governor_by_bay=governor_by_bay,
            avr_by_bay=avr_by_bay,
            unit_protection_by_bay=(
                unit_protection_by_bay
            ),
            grid_protection=grid_protection,
            plant=plant,
            strategy=strategy,
            execution=execution,
            safety=safety,
        )
