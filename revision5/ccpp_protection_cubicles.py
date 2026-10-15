"""
CCPP Dual-Engine Protection Cubicles & Excitation Regulators (ANSI Standard)
=============================================================================
Tier 1: Master Substation Grid Protection (MiCOM P44x equivalent)
        ANSI 81O/81U, ANSI 21, ANSI 67/67N, ANSI 25
Tier 2: Dedicated Turbine-Generator Bay Protection (SEL-300G equivalent)
        ANSI 87G, ANSI 40, ANSI 32R, ANSI 24, ANSI 46, ANSI 60FL
Tier 3: Dedicated Static Exciter / Automatic Voltage Regulator (AVR / DECS)
        Terminal Voltage (Notional Sizing), UEL, OEL Limiters
"""

import numpy as np
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# TIER 1: MASTER SUBSTATION / NIFTY 50 GRID PROTECTION (ANSI 81/21/67/25)
# ---------------------------------------------------------------------------


def _controller_defaults():
    from revision5.controller_parameters import default_controller_parameters
    return default_controller_parameters()

@dataclass
class GridRelayTrip:
    tripped: bool
    ansi_code: str
    reason: str

class MasterGridProtectionMiCOM:
    """
    Substation Intertie Protection for the entire 400kV Busbar (Portfolio Margin).
    Monitors Nifty 50 frequency stability and global fleet risk.
    """
    def __init__(
        self,
        max_daily_fleet_drawdown_pct: Optional[float] = None,  # ANSI 67 fleet hard trip
        nifty_crash_rate_pct: Optional[float] = None,          # ANSI 81U 15-minute drop
        max_regime_vol_z: Optional[float] = None,              # ANSI 21 distance / zone shock
        parameters=None,
    ):
        # Envelopes are registry-owned (micom_* FIXED_SAFETY, revision5/controller_parameters.py);
        # explicit arguments remain for legacy direct construction.
        self.parameters = parameters if parameters is not None else _controller_defaults().grid_protection
        p = self.parameters
        self.max_dd_pct = p.max_daily_fleet_drawdown_pct if max_daily_fleet_drawdown_pct is None else max_daily_fleet_drawdown_pct
        self.nifty_crash_rate = p.nifty_crash_rate_pct if nifty_crash_rate_pct is None else nifty_crash_rate_pct
        self.max_regime_vol_z = p.max_regime_vol_z if max_regime_vol_z is None else max_regime_vol_z
        self.master_breaker_open = False

    def configure(self, parameters) -> None:
        """Install new envelopes; the latched breaker state is preserved.  Before the first
        runtime profile the operating limits follow the new envelopes."""
        self.parameters = parameters
        if not getattr(self, "_runtime_profile_applied", False):
            self.max_dd_pct = parameters.max_daily_fleet_drawdown_pct
            self.nifty_crash_rate = parameters.nifty_crash_rate_pct
            self.max_regime_vol_z = parameters.max_regime_vol_z

    def apply_runtime_profile(self, profile) -> None:
        # These operating trip thresholds can only tighten.
        if not (
            0.0
            < float(
                profile.max_daily_fleet_drawdown_pct
            )
            <= self.parameters.max_daily_fleet_drawdown_pct
        ):
            raise ValueError(
                "MiCOM drawdown limit outside envelope"
            )

        if not (
            0.0
            < float(profile.nifty_crash_rate_pct)
            <= self.parameters.nifty_crash_rate_pct
        ):
            raise ValueError(
                "MiCOM crash-rate limit outside envelope"
            )

        if not (
            0.0
            < float(profile.max_regime_vol_z)
            <= self.parameters.max_regime_vol_z
        ):
            raise ValueError(
                "MiCOM volatility-zone limit outside envelope"
            )

        self.max_dd_pct = float(
            profile.max_daily_fleet_drawdown_pct
        )
        self.nifty_crash_rate = float(
            profile.nifty_crash_rate_pct
        )
        self.max_regime_vol_z = float(
            profile.max_regime_vol_z
        )
        self._runtime_profile_applied = True

    def reset_session(self) -> None:
        """ANSI 67 is a DAILY fleet-drawdown trip: its lockout clears at the session boundary."""
        self.master_breaker_open = False

    def evaluate_grid_intertie(
        self,
        nifty_15m_return: float,
        nifty_vol_z: float,
        fleet_equity_drawdown_pct: float
    ) -> GridRelayTrip:
        # ANSI 67 / 67N: Directional Overcurrent (Cascading Margin Runaway)
        if fleet_equity_drawdown_pct >= self.max_dd_pct:
            self.master_breaker_open = True
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 67",
                reason=f"Directional Overcurrent: Fleet Drawdown {fleet_equity_drawdown_pct*100:.2f}% >= {self.max_dd_pct*100:.2f}%"
            )

        # ANSI 81U / 81O: Under / Over Frequency (Macro Index Crash / Flash Spike)
        if nifty_15m_return <= -self.nifty_crash_rate:
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 81U",
                reason=f"Under-Frequency: NIFTY 15m collapse {nifty_15m_return*100:.2f}% <= -{self.nifty_crash_rate*100:.2f}%"
            )
        if nifty_15m_return >= self.nifty_crash_rate * self.parameters.overfrequency_ratio:
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 81O",
                reason=f"Over-Frequency: NIFTY 15m melt-up {nifty_15m_return*100:.2f}%"
            )

        # ANSI 21: Distance Protection (Zone 1 Systemic Liquidity Shock)
        if abs(nifty_vol_z) >= self.max_regime_vol_z:
            return GridRelayTrip(
                tripped=True,
                ansi_code="ANSI 21",
                reason=f"Distance Zone 1: Volatility shock Z={nifty_vol_z:.2f} outside safe corridor"
            )

        return GridRelayTrip(tripped=False, ansi_code="NORMAL", reason="Grid Synchronized")


def nifty_intertie_measurements(
    nifty_closes, vol_z_window: int
) -> Optional[Tuple[float, float]]:
    """MiCOM inputs from completed NIFTY 15-minute closes (oldest first, causal prefix).

    nifty_15m_return  latest completed 15-minute return
    nifty_vol_z       that return in units of the population standard deviation of the
                      ``vol_z_window`` returns before it (the shock is not part of its own scale)

    Returns None when there are too few closes or a close is not a positive finite price.
    A flat reference window gives z = 0 for a flat bar and an infinite shock otherwise.
    """
    window = int(vol_z_window)
    if window < 2:
        raise ValueError("vol_z_window must be at least 2")
    closes = np.asarray(nifty_closes, dtype=float)[-(window + 2):]
    if len(closes) < window + 2 or not np.isfinite(closes).all() or (closes <= 0.0).any():
        return None
    returns = closes[1:] / closes[:-1] - 1.0
    latest, reference = float(returns[-1]), returns[:-1]
    scale = float(reference.std())
    if scale > 0.0:
        return latest, latest / scale
    return latest, (0.0 if latest == 0.0 else float(np.copysign(np.inf, latest)))


# ---------------------------------------------------------------------------
# TIER 2: INDIVIDUAL BAY AUTOMATIC VOLTAGE REGULATOR (AVR / DECS)
# ---------------------------------------------------------------------------

class BayExcitationAVR:
    """
    Dedicated Static Exciter per Turbine Bay.
    Translates active megawatt demands into reactive terminal voltage (lot size).
    Guards boundaries with Under-Excitation (UEL) & Over-Excitation (OEL) Limiters.
    """
    def __init__(
        self,
        bay_name: str,
        allocated_capital_inr: float,
        min_notional_uel_inr: Optional[float] = None,
        max_notional_oel_inr: Optional[float] = None,
        parameters=None,
    ):
        self.bay_name = bay_name

        if allocated_capital_inr <= 0:
            raise ValueError(
                "allocated capital must be positive"
            )

        self.allocated_capital = float(
            allocated_capital_inr
        )

        # Registry-owned AVR parameters (avr_*, revision5/controller_parameters.py).  The runtime
        # values below are the neutral (stress 0, risk derate 1) profile of those parameters,
        # until the first dynamic AVR profile is applied.
        self.parameters = parameters if parameters is not None else _controller_defaults().avr
        p = self.parameters
        if min_notional_uel_inr is None:
            min_notional_uel_inr = p.uel_min_notional_inr
        if max_notional_oel_inr is None:
            max_notional_oel_inr = self.allocated_capital * p.oel_capital_fraction

        self._base_uel_min = float(
            min_notional_uel_inr
        )

        self._base_oel_fraction = float(
            max_notional_oel_inr
        ) / self.allocated_capital

        # Runtime operating values (neutral profile of the registry parameters).
        self.loading_fraction = p.loading_fraction_base
        self.uel_min_multiplier = 1.0
        self.oel_fraction = self._base_oel_fraction

        self.vol_scalar_min = p.vol_scalar_min
        self.vol_scalar_max = p.vol_scalar_max_base

        self.z_strength_denominator = p.z_strength_denominator_base
        self.z_strength_cap = p.z_strength_cap_base

        # Native AVR inner-loop controller state.
        self.runtime_kp = p.kp_base
        self.runtime_ki = p.ki_base
        self.runtime_kd = p.kd_base
        self.runtime_integral_clamp = p.integral_clamp_base

        self.undervoltage_trip_pu = p.undervoltage_trip_base_pu
        self.overvoltage_trip_pu = p.overvoltage_trip_base_pu
        self.mvar_limit_pu = p.mvar_limit_base_pu

        self.excitation_min = p.excitation_min_base
        self.excitation_max = p.excitation_max_base

        self.avr_integral_error = 0.0
        self.avr_last_error = 0.0

        # 1.0 means no AVR derating of otherwise permitted loading.
        self.excitation_command = 1.0

        self.uel_min = self._base_uel_min
        self.oel_max = float(
            max_notional_oel_inr
        )

    def configure(self, parameters) -> None:
        """Install registry-owned AVR parameters WITHOUT resetting controller state.

        The PID memory (integral, last error) and the excitation command are preserved.  The UEL
        floor and OEL fraction envelope follow the new parameters; until a dynamic profile has been
        applied the runtime gains/limits follow the new neutral values, afterwards the next profile
        (built from the same parameters) replaces them.  State is re-clamped to the new limits.
        """
        self.parameters = parameters
        p = parameters
        self._base_uel_min = float(p.uel_min_notional_inr)
        self._base_oel_fraction = float(p.oel_capital_fraction)
        if not getattr(self, "_runtime_profile_applied", False):
            self.loading_fraction = p.loading_fraction_base
            self.oel_fraction = self._base_oel_fraction
            self.vol_scalar_min, self.vol_scalar_max = p.vol_scalar_min, p.vol_scalar_max_base
            self.z_strength_denominator, self.z_strength_cap = p.z_strength_denominator_base, p.z_strength_cap_base
            self.runtime_kp, self.runtime_ki, self.runtime_kd = p.kp_base, p.ki_base, p.kd_base
            self.runtime_integral_clamp = p.integral_clamp_base
            self.undervoltage_trip_pu, self.overvoltage_trip_pu = p.undervoltage_trip_base_pu, p.overvoltage_trip_base_pu
            self.mvar_limit_pu = p.mvar_limit_base_pu
            self.excitation_min, self.excitation_max = p.excitation_min_base, p.excitation_max_base
        self.excitation_command = max(self.excitation_min, min(self.excitation_max, self.excitation_command))
        self.avr_integral_error = max(-self.runtime_integral_clamp,
                                      min(self.runtime_integral_clamp, self.avr_integral_error))
        self._refresh_runtime_limits()

    def update_allocated_capital(
        self,
        allocated_capital_inr: float,
    ) -> None:
        if allocated_capital_inr <= 0:
            raise ValueError(
                "allocated capital must be positive"
            )

        self.allocated_capital = float(
            allocated_capital_inr
        )

        self._refresh_runtime_limits()

    def _refresh_runtime_limits(self) -> None:
        self.uel_min = (
            self._base_uel_min
            * self.uel_min_multiplier
        )

        self.oel_max = (
            self.allocated_capital
            * self.oel_fraction
        )

    def apply_runtime_profile(self, profile) -> None:
        if getattr(profile, "bay_id", self.bay_name) != self.bay_name:
            raise ValueError(
                "AVR runtime profile bay mismatch"
            )

        values = (
            profile.loading_fraction,
            profile.uel_min_multiplier,
            profile.oel_fraction,
            profile.vol_scalar_min,
            profile.vol_scalar_max,
            profile.z_strength_denominator,
            profile.z_strength_cap,
        )

        if not all(
            isinstance(v, (int, float))
            and v == v
            and abs(float(v)) != float("inf")
            for v in values
        ):
            raise ValueError(
                "AVR runtime profile contains invalid value"
            )

        if not (
            0.0
            < float(profile.loading_fraction)
            <= self.parameters.loading_fraction_base
        ):
            raise ValueError(
                "AVR loading fraction outside envelope"
            )

        if not (
            0.0
            < float(profile.oel_fraction)
            <= self._base_oel_fraction
        ):
            raise ValueError(
                "AVR OEL fraction outside envelope"
            )

        if (
            float(profile.vol_scalar_min) <= 0.0
            or float(profile.vol_scalar_max)
            < float(profile.vol_scalar_min)
        ):
            raise ValueError(
                "invalid AVR volatility scalar range"
            )

        self.loading_fraction = float(
            profile.loading_fraction
        )
        self.uel_min_multiplier = float(
            profile.uel_min_multiplier
        )
        self.oel_fraction = float(
            profile.oel_fraction
        )

        self.vol_scalar_min = float(
            profile.vol_scalar_min
        )
        self.vol_scalar_max = float(
            profile.vol_scalar_max
        )

        self.z_strength_denominator = float(
            profile.z_strength_denominator
        )
        self.z_strength_cap = float(
            profile.z_strength_cap
        )

        self.runtime_kp = float(
            profile.kp
        )
        self.runtime_ki = float(
            profile.ki
        )
        self.runtime_kd = float(
            profile.kd
        )
        self.runtime_integral_clamp = float(
            profile.integral_clamp
        )

        self.undervoltage_trip_pu = float(
            profile.undervoltage_trip_pu
        )
        self.overvoltage_trip_pu = float(
            profile.overvoltage_trip_pu
        )
        self.mvar_limit_pu = float(
            profile.mvar_limit_pu
        )

        self.excitation_min = float(
            profile.excitation_min
        )
        self.excitation_max = float(
            profile.excitation_max
        )

        self._runtime_profile_applied = True

        self.excitation_command = max(
            self.excitation_min,
            min(
                self.excitation_max,
                self.excitation_command,
            ),
        )

        self.avr_integral_error = max(
            -self.runtime_integral_clamp,
            min(
                self.runtime_integral_clamp,
                self.avr_integral_error,
            ),
        )

        self._refresh_runtime_limits()

    def configure_synchronizing_voltage_reference(
        self,
        *,
        initial_reference_pu: float,
        minimum_reference_pu: float,
        maximum_reference_pu: float,
        reference_rate_pu_per_second: float,
    ) -> None:
        """
        Install the generator synchronizing AVR reference channel.

        All operating values are explicit configuration.
        """
        from math import isfinite

        values = (
            initial_reference_pu,
            minimum_reference_pu,
            maximum_reference_pu,
            reference_rate_pu_per_second,
        )

        if not all(isfinite(float(v)) for v in values):
            raise ValueError(
                "synchronizing AVR parameters must be finite"
            )

        if not (
            float(minimum_reference_pu)
            < float(initial_reference_pu)
            < float(maximum_reference_pu)
        ):
            raise ValueError(
                "initial voltage reference must lie inside its limits"
            )

        if float(reference_rate_pu_per_second) <= 0.0:
            raise ValueError(
                "voltage reference rate must be positive"
            )

        self.sync_voltage_reference_pu = float(
            initial_reference_pu
        )

        self.sync_voltage_reference_min_pu = float(
            minimum_reference_pu
        )

        self.sync_voltage_reference_max_pu = float(
            maximum_reference_pu
        )

        self.sync_voltage_reference_rate_pu_per_second = float(
            reference_rate_pu_per_second
        )

    def apply_synchronizing_voltage_pulse(
        self,
        *,
        command,
        pulse_width_seconds: float,
    ) -> float:
        """
        Apply one physical-equivalent 25A VOLTAGE RAISE/LOWER pulse.
        """
        from math import isfinite

        required = (
            "sync_voltage_reference_pu",
            "sync_voltage_reference_min_pu",
            "sync_voltage_reference_max_pu",
            "sync_voltage_reference_rate_pu_per_second",
        )

        if not all(hasattr(self, name) for name in required):
            raise RuntimeError(
                "synchronizing AVR channel not configured"
            )

        width = float(pulse_width_seconds)

        if not isfinite(width) or width < 0.0:
            raise ValueError(
                "pulse_width_seconds must be finite and non-negative"
            )

        normalized = str(
            getattr(command, "value", command)
        ).upper()

        delta = (
            self.sync_voltage_reference_rate_pu_per_second
            * width
        )

        reference = self.sync_voltage_reference_pu

        if normalized in (
            "RAISE",
            "VOLTAGE_RAISE",
        ):
            reference += delta

        elif normalized in (
            "LOWER",
            "VOLTAGE_LOWER",
        ):
            reference -= delta

        elif normalized in (
            "NONE",
            "HOLD",
            "VOLTAGE_NONE",
        ):
            pass

        else:
            raise ValueError(
                f"unsupported synchronizing voltage command: "
                f"{normalized!r}"
            )

        reference = max(
            self.sync_voltage_reference_min_pu,
            min(
                self.sync_voltage_reference_max_pu,
                reference,
            ),
        )

        self.sync_voltage_reference_pu = float(reference)

        return self.sync_voltage_reference_pu

    def evaluate_control(
        self,
        *,
        mode: str = "BUS_VOLTAGE",
        bus_voltage_reference_pu: float = 1.0,
        terminal_voltage_pu: float = 1.0,
        mvar_reference_pu: float = 0.0,
        measured_mvar_pu: float = 0.0,
    ) -> dict:
        """
        Continuous AVR feedback loop.

        BUS_VOLTAGE / VOLTAGE:
            error = synchronized bus reference - terminal voltage

        MVAR:
            error = requested MVAR - measured MVAR

        AVR output is bidirectional. UEL/OEL and voltage protections
        constrain or trip the actuator.
        """
        values = (
            bus_voltage_reference_pu,
            terminal_voltage_pu,
            mvar_reference_pu,
            measured_mvar_pu,
        )

        if not all(
            isinstance(v, (int, float))
            and v == v
            and abs(float(v)) != float("inf")
            for v in values
        ):
            raise ValueError(
                "AVR feedback must be finite"
            )

        terminal_voltage_pu = float(
            terminal_voltage_pu
        )

        if (
            terminal_voltage_pu
            <= self.undervoltage_trip_pu
        ):
            self.excitation_command = 0.0
            return {
                "tripped": True,
                "reason": "ANSI_27_UNDERVOLTAGE",
                "excitation_command": 0.0,
            }

        if (
            terminal_voltage_pu
            >= self.overvoltage_trip_pu
        ):
            self.excitation_command = 0.0
            return {
                "tripped": True,
                "reason": "ANSI_59_OVERVOLTAGE",
                "excitation_command": 0.0,
            }

        normalized_mode = str(mode).upper()

        if normalized_mode in (
            "BUS_VOLTAGE",
            "VOLTAGE",
        ):
            error = (
                float(
                    bus_voltage_reference_pu
                )
                - terminal_voltage_pu
            )

        elif normalized_mode == "MVAR":
            error = (
                float(mvar_reference_pu)
                - float(measured_mvar_pu)
            )

        else:
            raise ValueError(
                f"unsupported AVR mode: {mode!r}"
            )

        self.avr_integral_error = max(
            -self.runtime_integral_clamp,
            min(
                self.runtime_integral_clamp,
                self.avr_integral_error
                + error,
            ),
        )

        derivative = (
            error
            - self.avr_last_error
        )
        self.avr_last_error = error

        delta_u = (
            self.runtime_kp * error
            + self.runtime_ki
            * self.avr_integral_error
            + self.runtime_kd * derivative
        )

        requested_command = (
            self.excitation_command
            + delta_u
        )

        limiter_state = "AVR_PID"

        #
        # MVAR/excitation limiter: do not continue driving farther
        # into an already exceeded reactive-power limit.
        #
        if (
            float(measured_mvar_pu)
            > self.mvar_limit_pu
            and requested_command
            > self.excitation_command
        ):
            requested_command = (
                self.excitation_command
            )
            limiter_state = "OEL_ACTIVE"

        elif (
            float(measured_mvar_pu)
            < -self.mvar_limit_pu
            and requested_command
            < self.excitation_command
        ):
            requested_command = (
                self.excitation_command
            )
            limiter_state = "UEL_ACTIVE"

        command = max(
            self.excitation_min,
            min(
                self.excitation_max,
                requested_command,
            ),
        )

        if command >= self.excitation_max:
            limiter_state = "OEL_ACTIVE"

        elif command <= self.excitation_min:
            limiter_state = "UEL_ACTIVE"

        self.excitation_command = float(
            command
        )

        return {
            "tripped": False,
            "reason": limiter_state,
            "mode": normalized_mode,
            "error": float(error),
            "integral_error": float(
                self.avr_integral_error
            ),
            "derivative": float(
                derivative
            ),
            "delta_u": float(delta_u),
            "excitation_command": float(
                self.excitation_command
            ),
            "terminal_voltage_pu": (
                terminal_voltage_pu
            ),
            "measured_mvar_pu": float(
                measured_mvar_pu
            ),
        }

    def calculate_lot_size(
        self,
        asset_price: float,
        asset_atr: float,
        z_strength: float
    ) -> Tuple[int, str]:
        if asset_price <= 0 or asset_atr <= 0:
            return 0, "AVR: Zero Price / ATR Fault"

        vol_scalar = max(
            self.vol_scalar_min,
            min(
                self.vol_scalar_max,
                1.0
                / (
                    asset_atr
                    / asset_price
                    * 100.0
                ),
            ),
        )

        raw_notional = (
            self.allocated_capital
            * self.loading_fraction
            * self.excitation_command
            * vol_scalar
            * min(
                self.z_strength_cap,
                abs(z_strength)
                / self.z_strength_denominator,
            )
        )

        # OEL: Over-Excitation Limiter (Cap Leverage)
        if raw_notional > self.oel_max:
            clamped_notional = self.oel_max
            limiter_state = "OEL_ACTIVE"
        # UEL: Under-Excitation Limiter (Floor Friction)
        elif raw_notional < self.uel_min:
            clamped_notional = 0
            return 0, "UEL_LOCKOUT: Below Min Commercial Friction"
        else:
            clamped_notional = raw_notional
            limiter_state = "AVR_LINEAR"

        qty = int(clamped_notional / asset_price)
        return qty, limiter_state


# ---------------------------------------------------------------------------
# TIER 3: INDIVIDUAL BAY GENERATOR UNIT PROTECTION (ANSI MATRIX SEL-300G)
# ---------------------------------------------------------------------------

@dataclass
class UnitRelayTrip:
    tripped: bool
    ansi_code: str
    symbol: str
    reason: str

class BayUnitProtectionSEL300G:
    """
    Dedicated Multifunction Generator Protection Relay per Turbine Bay.
    Isolates single symbols without tripping the rest of the bay or fleet.
    """
    def __init__(
        self,
        bay_name: str,
        max_slippage_pct: Optional[float] = None,   # ANSI 87G differential trip
        max_adverse_bars: Optional[int] = None,     # ANSI 32R negative drift timeout
        vol_surge_mult: Optional[float] = None,     # ANSI 24 volts/hertz overfluxing
        parameters=None,
    ):
        # Envelopes are registry-owned (sel_* FIXED_SAFETY, revision5/controller_parameters.py).
        self.parameters = parameters if parameters is not None else _controller_defaults().unit_protection
        p = self.parameters
        self.bay_name = bay_name
        self.max_slippage_pct = p.max_slippage_fraction if max_slippage_pct is None else max_slippage_pct
        self.max_adverse_bars = p.max_adverse_bars if max_adverse_bars is None else max_adverse_bars
        self.vol_surge_mult = p.vol_surge_mult if vol_surge_mult is None else vol_surge_mult

        self.stale_tick_limit_seconds = p.stale_tick_limit_seconds
        self.max_spread_fraction = p.max_spread_fraction

        self.adverse_drift_counters: Dict[str, int] = {}

    def configure(self, parameters) -> None:
        """Install new protection envelopes; trip counters are preserved.  Before the first
        runtime profile the operating limits follow the new envelopes."""
        self.parameters = parameters
        if not getattr(self, "_runtime_profile_applied", False):
            self.max_slippage_pct = parameters.max_slippage_fraction
            self.max_adverse_bars = parameters.max_adverse_bars
            self.vol_surge_mult = parameters.vol_surge_mult
            self.stale_tick_limit_seconds = parameters.stale_tick_limit_seconds
            self.max_spread_fraction = parameters.max_spread_fraction

    def apply_runtime_profile(self, profile) -> None:
        if getattr(profile, "bay_id", self.bay_name) != self.bay_name:
            raise ValueError(
                "SEL300G runtime profile bay mismatch"
            )

        # Dynamic protection is allowed to tighten the original
        # protection envelope, never widen it.
        if not (
            0.0
            < float(profile.stale_tick_limit_seconds)
            <= self.parameters.stale_tick_limit_seconds
        ):
            raise ValueError(
                "SEL300G stale-data limit outside envelope"
            )

        if not (
            0.0
            < float(profile.max_spread_fraction)
            <= self.parameters.max_spread_fraction
        ):
            raise ValueError(
                "SEL300G spread limit outside envelope"
            )

        if not (
            0.0
            < float(profile.vol_surge_mult)
            <= self.parameters.vol_surge_mult
        ):
            raise ValueError(
                "SEL300G volatility limit outside envelope"
            )

        self.stale_tick_limit_seconds = float(
            profile.stale_tick_limit_seconds
        )
        self.max_spread_fraction = float(
            profile.max_spread_fraction
        )
        self.vol_surge_mult = float(
            profile.vol_surge_mult
        )
        self.max_slippage_pct = float(
            profile.max_slippage_pct
        )
        self.max_adverse_bars = int(
            profile.max_adverse_bars
        )
        self._runtime_profile_applied = True

    def check_pre_synchronization(
        self,
        symbol: str,
        bid: float,
        ask: float,
        last_tick_age_sec: float,
        hist_atr: float,
        curr_bar_range: float
    ) -> UnitRelayTrip:
        # ANSI 60FL: Potential Transformer (PT) Fuse Failure / Stale Data Loss
        if last_tick_age_sec > self.stale_tick_limit_seconds:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 60FL",
                symbol=symbol,
                reason=f"PT Fuse Failure: Tick age {last_tick_age_sec:.1f}s exceeds {self.stale_tick_limit_seconds:.1f}s limit"
            )

        # ANSI 40: Loss of Field / Severe Liquidity Evaporation
        spread_pct = (ask - bid) / (bid + 1e-9)
        if spread_pct > self.max_spread_fraction:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 40",
                symbol=symbol,
                reason=f"Loss of Excitation: Illiquid spread {spread_pct*100:.2f}% > {self.max_spread_fraction*100:.2f}%"
            )

        # ANSI 24: Volts / Hertz Overfluxing (Extreme Intra-Bar Volatility Spike)
        if hist_atr > 0 and (curr_bar_range / hist_atr) > self.vol_surge_mult:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 24",
                symbol=symbol,
                reason=f"V/Hz Overfluxing: Bar range {curr_bar_range / hist_atr:.1f}x ATR > {self.vol_surge_mult}x"
            )

        return UnitRelayTrip(tripped=False, ansi_code="NORMAL", symbol=symbol, reason="Ready")

    def check_in_flight_protection(
        self,
        symbol: str,
        entry_price: float,
        current_price: float,
        exec_fill_price: float,
        intended_price: float
    ) -> UnitRelayTrip:
        # ANSI 87G: Differential Protection (Instantaneous Execution Anomaly)
        slippage = abs(exec_fill_price - intended_price) / intended_price
        if slippage > self.max_slippage_pct:
            return UnitRelayTrip(
                tripped=True,
                ansi_code="ANSI 87G",
                symbol=symbol,
                reason=f"Stator Differential Trip: Slippage {slippage*100:.2f}% > {self.max_slippage_pct*100:.2f}%"
            )

        # ANSI 32R: Reverse Power Relay (Turbine Motorization / Adverse Drift)
        if current_price < entry_price:
            self.adverse_drift_counters[symbol] = self.adverse_drift_counters.get(symbol, 0) + 1
            if self.adverse_drift_counters[symbol] >= self.max_adverse_bars:
                return UnitRelayTrip(
                    tripped=True,
                    ansi_code="ANSI 32R",
                    symbol=symbol,
                    reason=f"Reverse Power Trip: Continuous motorization for {self.max_adverse_bars} intervals"
                )
        else:
            self.adverse_drift_counters[symbol] = 0

        return UnitRelayTrip(tripped=False, ansi_code="NORMAL", symbol=symbol, reason="Operating")
