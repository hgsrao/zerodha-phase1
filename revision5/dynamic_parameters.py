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

from dataclasses import dataclass
from typing import Dict
import math

from revision5.governor import BAY_GOVERNOR_SPECS


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

    @property
    def control_stress(self) -> float:
        # loading_fraction is already dynamically derated by R5.
        return _clamp(
            1.0
            - (
                float(self.loading_fraction)
                / 0.15
            ),
            0.0,
            1.0,
        )

    @property
    def kp(self) -> float:
        stress = self.control_stress
        return _clamp(
            0.80 + 0.60 * stress,
            0.80,
            1.40,
        )

    @property
    def ki(self) -> float:
        stress = self.control_stress
        return _clamp(
            0.15 / (1.0 + stress),
            0.075,
            0.15,
        )

    @property
    def kd(self) -> float:
        stress = self.control_stress
        return _clamp(
            0.10 + 0.10 * stress,
            0.10,
            0.20,
        )

    @property
    def integral_clamp(self) -> float:
        stress = self.control_stress
        return _clamp(
            0.50 - 0.20 * stress,
            0.30,
            0.50,
        )

    @property
    def undervoltage_trip_pu(self) -> float:
        # Stress narrows the safe voltage corridor.
        return _clamp(
            0.90 + 0.05 * self.control_stress,
            0.90,
            0.95,
        )

    @property
    def overvoltage_trip_pu(self) -> float:
        return _clamp(
            1.10 - 0.05 * self.control_stress,
            1.05,
            1.10,
        )

    @property
    def mvar_limit_pu(self) -> float:
        return _clamp(
            0.90 - 0.30 * self.control_stress,
            0.60,
            0.90,
        )

    @property
    def excitation_min(self) -> float:
        return _clamp(
            0.10 + 0.10 * self.control_stress,
            0.10,
            0.20,
        )

    @property
    def excitation_max(self) -> float:
        return _clamp(
            1.00 - 0.25 * self.control_stress,
            0.75,
            1.00,
        )


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

    def __init__(self) -> None:
        self._version = 0

    @staticmethod
    def _normalized_state(
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

        vol = _clamp(vol, 0.50, 2.50)
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

        excess_vol = max(0.0, vol - 1.0)

        stress = _clamp(
            0.55 * turbulent
            + 0.25 * chop
            + 0.20 * excess_vol,
            0.0,
            1.0,
        )

        risk_derate = _clamp(
            1.0
            - 0.45 * stress
            - 0.25 * drawdown,
            0.40,
            1.0,
        )

        trend_bias = _clamp(
            trend - turbulent,
            -1.0,
            1.0,
        )

        #
        # Strategy operating profile
        #

        entry_confidence = _clamp(
            0.15
            + 0.10 * stress
            + 0.025 * chop,
            0.02,
            0.35,
        )

        min_rr = _clamp(
            1.50 + 0.60 * stress,
            1.00,
            3.00,
        )

        min_hold = int(
            round(
                _clamp(
                    2.0
                    + 2.0 * chop
                    + 3.0 * turbulent,
                    1.0,
                    8.0,
                )
            )
        )

        max_hold = int(
            round(
                _clamp(
                    60.0
                    - 20.0 * stress
                    + 10.0 * trend,
                    20.0,
                    120.0,
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
                    15.0 * (
                        0.80
                        + 0.90 * stress
                    ),
                    8.0,
                    30.0,
                )
            )
        )

        target_cooldown = int(
            round(
                _clamp(
                    3.0 * (
                        0.80
                        + 0.70 * stress
                    ),
                    2.0,
                    8.0,
                )
            )
        )

        dispatcher_min_floor = _clamp(
            0.08 * (
                1.0
                - 0.20 * stress
            ),
            0.05,
            0.10,
        )

        dispatcher_max_ceiling = _clamp(
            0.35 * risk_derate,
            0.15,
            0.35,
        )

        if (
            dispatcher_min_floor
            >= dispatcher_max_ceiling
        ):
            dispatcher_min_floor = min(
                dispatcher_min_floor,
                dispatcher_max_ceiling * 0.50,
            )

        plant = PlantRuntimeProfile(
            loss_cooldown_bars=loss_cooldown,
            target_cooldown_bars=target_cooldown,
            dispatcher_min_floor=dispatcher_min_floor,
            dispatcher_max_ceiling=dispatcher_max_ceiling,
        )

        #
        # Execution operating profile
        #

        slippage = _clamp(
            0.001
            * (
                1.0
                + 0.75 * (vol - 1.0)
            ),
            0.0005,
            0.0020,
        )

        max_market_age = int(
            round(
                _clamp(
                    30.0 / (
                        1.0
                        + 0.75 * stress
                    ),
                    10.0,
                    30.0,
                )
            )
        )

        order_timeout = int(
            round(
                _clamp(
                    30.0 * (
                        1.0
                        + 0.50 * stress
                    ),
                    15.0,
                    60.0,
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
                0.25 * (
                    1.0
                    - 0.30 * stress
                ),
                0.10,
                0.25,
            ),
            max_gross_exposure_fraction=_clamp(
                0.50 * risk_derate,
                0.20,
                0.50,
            ),
            max_symbol_exposure_fraction=_clamp(
                0.15 * risk_derate,
                0.05,
                0.15,
            ),
            max_concurrent_positions=int(
                round(
                    _clamp(
                        5.0 * risk_derate,
                        1.0,
                        5.0,
                    )
                )
            ),
            daily_loss_limit_multiplier=_clamp(
                risk_derate,
                0.40,
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

        for bay_id, base in BAY_GOVERNOR_SPECS.items():

            target_r = _clamp(
                base.target_r
                * (
                    1.0
                    + 0.15 * trend_bias
                    - 0.15 * stress
                ),
                base.target_r * 0.70,
                base.target_r * 1.25,
            )

            outcome_window = int(
                round(
                    _clamp(
                        base.outcome_window
                        * (
                            1.0
                            + 0.80 * stress
                        ),
                        3.0,
                        12.0,
                    )
                )
            )

            integral_clamp = _clamp(
                base.integral_clamp
                / (
                    1.0
                    + 0.80 * stress
                    + 0.50 * abs_error
                ),
                base.integral_clamp * 0.50,
                base.integral_clamp * 1.25,
            )

            droop_gain = _clamp(
                base.grid_droop_gain
                * vol
                * (
                    1.0
                    + 0.50 * stress
                ),
                base.grid_droop_gain * 0.50,
                base.grid_droop_gain * 2.00,
            )

            droop_max = _clamp(
                base.grid_droop_max
                * (
                    1.0
                    + 0.60 * stress
                ),
                base.grid_droop_max * 0.80,
                base.grid_droop_max * 1.60,
            )

            #
            # R5-native gain scheduling.
            #

            kp = base.kp * _clamp(
                1.0
                + 1.00 * abs_error
                + 0.25 * stress,
                0.60,
                2.20,
            )

            ki = base.ki / _clamp(
                1.0
                + 1.50 * abs_error
                + 1.00 * stress,
                1.00,
                3.50,
            )

            kd = base.kd * _clamp(
                1.0
                + 0.70 * abs_error
                + 0.50 * stress,
                0.60,
                2.00,
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
                            + 0.10 * trend_bias
                            - 0.25 * stress,
                            base.base_z - 0.50,
                            base.base_z + 0.10,
                        )
                    ),
                    droop_r=float(
                        _clamp(
                            base.droop_r
                            * (
                                1.0
                                - 0.25 * stress
                            ),
                            base.droop_r * 0.60,
                            base.droop_r,
                        )
                    ),
                    dynamic_offset_min=float(
                        _clamp(
                            base.dynamic_offset_min
                            * (
                                1.0
                                + 0.25 * stress
                            ),
                            base.dynamic_offset_min * 1.50,
                            base.dynamic_offset_min * 0.75,
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

        for bay_id in BAY_GOVERNOR_SPECS:
            avr_by_bay[bay_id] = AVRRuntimeProfile(
                bay_id=bay_id,
                loading_fraction=float(
                    _clamp(
                        0.15 * risk_derate,
                        0.05,
                        0.15,
                    )
                ),
                uel_min_multiplier=float(
                    _clamp(
                        1.0 + 0.75 * stress,
                        1.0,
                        1.75,
                    )
                ),
                oel_fraction=float(
                    _clamp(
                        0.40 * risk_derate,
                        0.15,
                        0.40,
                    )
                ),
                vol_scalar_min=0.20,
                vol_scalar_max=float(
                    _clamp(
                        2.0 * risk_derate,
                        0.75,
                        2.0,
                    )
                ),
                z_strength_denominator=float(
                    _clamp(
                        1.80
                        * (
                            1.0
                            + 0.50 * stress
                        ),
                        1.80,
                        2.70,
                    )
                ),
                z_strength_cap=float(
                    _clamp(
                        1.50 * risk_derate,
                        0.75,
                        1.50,
                    )
                ),
            )

            unit_protection_by_bay[
                bay_id
            ] = UnitProtectionRuntimeProfile(
                bay_id=bay_id,

                # Protection is dynamically tightened only.
                stale_tick_limit_seconds=float(
                    _clamp(
                        120.0
                        * (
                            1.0
                            - 0.50 * stress
                        ),
                        45.0,
                        120.0,
                    )
                ),
                max_spread_fraction=float(
                    _clamp(
                        0.005
                        * (
                            1.0
                            - 0.40 * stress
                        ),
                        0.0025,
                        0.005,
                    )
                ),
                vol_surge_mult=float(
                    _clamp(
                        3.50
                        * (
                            1.0
                            - 0.30 * stress
                        ),
                        2.0,
                        3.50,
                    )
                ),
                max_slippage_pct=float(
                    _clamp(
                        0.0035
                        * (
                            1.0
                            - 0.30 * stress
                        ),
                        0.0020,
                        0.0035,
                    )
                ),
                max_adverse_bars=int(
                    round(
                        _clamp(
                            4.0
                            * (
                                1.0
                                - 0.50 * stress
                            ),
                            2.0,
                            4.0,
                        )
                    )
                ),
            )

        grid_protection = GridProtectionRuntimeProfile(
            # Again: tighten only. Never widen protection.
            max_daily_fleet_drawdown_pct=float(
                _clamp(
                    0.020
                    * (
                        1.0
                        - 0.35 * stress
                    ),
                    0.010,
                    0.020,
                )
            ),
            nifty_crash_rate_pct=float(
                _clamp(
                    0.015
                    * (
                        1.0
                        - 0.25 * stress
                    ),
                    0.008,
                    0.015,
                )
            ),
            max_regime_vol_z=float(
                _clamp(
                    3.0
                    * (
                        1.0
                        - 0.30 * stress
                    ),
                    1.80,
                    3.0,
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
