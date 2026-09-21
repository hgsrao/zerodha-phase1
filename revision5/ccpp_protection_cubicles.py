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
        max_daily_fleet_drawdown_pct: float = 0.020,  # ANSI 67: 2.0% fleet hard trip
        nifty_crash_rate_pct: float = 0.015,          # ANSI 81U: 1.5% drop in 15 mins
        max_regime_vol_z: float = 3.0                 # ANSI 21: Distance / Zone shock
    ):
        self.max_dd_pct = max_daily_fleet_drawdown_pct
        self.nifty_crash_rate = nifty_crash_rate_pct
        self.max_regime_vol_z = max_regime_vol_z
        self.master_breaker_open = False

    def apply_runtime_profile(self, profile) -> None:
        # These operating trip thresholds can only tighten.
        if not (
            0.0
            < float(
                profile.max_daily_fleet_drawdown_pct
            )
            <= 0.020
        ):
            raise ValueError(
                "MiCOM drawdown limit outside envelope"
            )

        if not (
            0.0
            < float(profile.nifty_crash_rate_pct)
            <= 0.015
        ):
            raise ValueError(
                "MiCOM crash-rate limit outside envelope"
            )

        if not (
            0.0
            < float(profile.max_regime_vol_z)
            <= 3.0
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
        if nifty_15m_return >= self.nifty_crash_rate * 1.5:
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
        min_notional_uel_inr: float = 25000.0,
        max_notional_oel_inr: float = 800000.0
    ):
        self.bay_name = bay_name

        if allocated_capital_inr <= 0:
            raise ValueError(
                "allocated capital must be positive"
            )

        self.allocated_capital = float(
            allocated_capital_inr
        )

        self._base_uel_min = float(
            min_notional_uel_inr
        )

        self._base_oel_fraction = float(
            max_notional_oel_inr
        ) / self.allocated_capital

        # Runtime operating values.
        self.loading_fraction = 0.15
        self.uel_min_multiplier = 1.0
        self.oel_fraction = self._base_oel_fraction

        self.vol_scalar_min = 0.20
        self.vol_scalar_max = 2.0

        self.z_strength_denominator = 1.80
        self.z_strength_cap = 1.50

        # Native AVR inner-loop controller state.
        self.runtime_kp = 0.80
        self.runtime_ki = 0.15
        self.runtime_kd = 0.10
        self.runtime_integral_clamp = 0.50

        self.undervoltage_trip_pu = 0.90
        self.overvoltage_trip_pu = 1.10
        self.mvar_limit_pu = 0.90

        self.excitation_min = 0.10
        self.excitation_max = 1.00

        self.avr_integral_error = 0.0
        self.avr_last_error = 0.0

        # 1.0 means no AVR derating of otherwise permitted loading.
        self.excitation_command = 1.0

        self.uel_min = self._base_uel_min
        self.oel_max = float(
            max_notional_oel_inr
        )

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
            <= 0.15
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
        max_slippage_pct: float = 0.0035,   # ANSI 87G: 0.35% Diff Trip
        max_adverse_bars: int = 4,          # ANSI 32R: Negative drift timeout
        vol_surge_mult: float = 3.5         # ANSI 24: Volts/Hz Overfluxing
    ):
        self.bay_name = bay_name
        self.max_slippage_pct = max_slippage_pct
        self.max_adverse_bars = max_adverse_bars
        self.vol_surge_mult = vol_surge_mult

        self.stale_tick_limit_seconds = 120.0
        self.max_spread_fraction = 0.005

        self.adverse_drift_counters: Dict[str, int] = {}

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
            <= 120.0
        ):
            raise ValueError(
                "SEL300G stale-data limit outside envelope"
            )

        if not (
            0.0
            < float(profile.max_spread_fraction)
            <= 0.005
        ):
            raise ValueError(
                "SEL300G spread limit outside envelope"
            )

        if not (
            0.0
            < float(profile.vol_surge_mult)
            <= 3.5
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
