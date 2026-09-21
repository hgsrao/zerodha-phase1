"""
Revision 5 CCPP Unified Plant Core
===================================

Execution-neutral integration of:

* canonical 48-symbol / five-bay topology
* Engine-A / Engine-B operating interlocks
* persistent Engine-A one-fill-per-bay/day latch
* frozen per-bay R-multiple PID/droop governors
* MiCOM master-grid protection
* SEL-300G bay protection
* AVR lot sizing
* dynamic merit-order capital allocation
* machine-bay cooldown/trip state
* Engine-A 15:15 square-off intent generation

This module DOES NOT place broker orders.

Historical replay, paper execution and future live execution must consume
the same admission and square-off intents through separate adapters.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from math import isfinite
from pathlib import Path
from statistics import fmean, pstdev
from typing import Any, Dict, Iterable, Mapping, Optional

from revision5.topology import (
    BAY_IDS,
    FLEET_TOPOLOGY,
    SYMBOL_TO_BAY,
    bay_for_symbol,
)

from revision5.engine_state import (
    ENGINE_A,
    ENGINE_B,
    EngineStateStore,
    engine_a_squareoff_due,
    trading_date,
    validate_engine,
)

from revision5.governor import (
    BAY_GOVERNOR_SPECS,
    BayTurbineClosedLoopGovernor,
)

from revision5.hrsg import (
    HRSGBalanceResult,
    HeatRecoverySteamGenerator,
)

from revision5.ccpp_protection_cubicles import (
    MasterGridProtectionMiCOM,
    BayExcitationAVR,
    BayUnitProtectionSEL300G,
)


logger = logging.getLogger("CCPP_Plant_R5")


LOSS_COOLDOWN_BARS = 15
TARGET_COOLDOWN_BARS = 3


@dataclass(frozen=True)
class SquareOffIntent:
    engine_mode: str
    symbol: str
    bay_id: str
    requested_at: datetime
    reason: str
    position_id: Optional[str] = None


class DynamicBayLoadDispatcher:
    """
    Dynamic merit-order capital allocation.

    Feedback input is R-multiple only.
    This is controller state, not PID coefficient tuning.
    """

    def __init__(
        self,
        total_capital: float = 1_000_000.0,
        min_floor: float = 0.08,
        max_ceiling: float = 0.35,
    ):
        if total_capital <= 0:
            raise ValueError("total_capital must be positive")

        if not 0 < min_floor < max_ceiling <= 1:
            raise ValueError("invalid dispatcher bounds")

        self.total_capital = float(total_capital)
        self.min_floor = float(min_floor)
        self.max_ceiling = float(max_ceiling)

        self.trade_history_r: Dict[str, list[float]] = {
            bay_id: []
            for bay_id in BAY_IDS
        }

        self.weights = {
            bay_id: BAY_GOVERNOR_SPECS[bay_id].capital_weight
            for bay_id in BAY_IDS
        }

    def register_trade(
        self,
        bay_id: str,
        realized_r: float,
    ) -> None:
        if bay_id not in BAY_IDS:
            raise ValueError(f"Unknown bay: {bay_id!r}")

        if not isfinite(realized_r):
            raise ValueError("realized_r must be finite")

        history = self.trade_history_r[bay_id]
        history.append(float(realized_r))

        if len(history) > 20:
            history.pop(0)

        scores: Dict[str, float] = {}

        for candidate_bay in BAY_IDS:
            values = self.trade_history_r[candidate_bay]

            if len(values) < 3:
                scores[candidate_bay] = 1.0
                continue

            downside = [
                value
                for value in values
                if value < 0.0
            ]

            if len(downside) > 1:
                downside_dev = pstdev(downside)
            else:
                downside_dev = 0.5

            score = 1.0 + (
                fmean(values)
                / max(0.2, downside_dev)
            )

            scores[candidate_bay] = max(
                0.1,
                score,
            )

        total_score = sum(scores.values())

        raw_targets = {
            bay_id: scores[bay_id] / total_score
            for bay_id in BAY_IDS
        }

        clamped = {
            bay_id: min(
                self.max_ceiling,
                max(
                    self.min_floor,
                    raw_targets[bay_id],
                ),
            )
            for bay_id in BAY_IDS
        }

        clamped_total = sum(clamped.values())

        for candidate_bay in BAY_IDS:
            target_weight = (
                clamped[candidate_bay]
                / clamped_total
            )

            self.weights[candidate_bay] = (
                0.85 * self.weights[candidate_bay]
                + 0.15 * target_weight
            )

        # Normalize after smoothing so the portfolio remains exactly 100%.
        weight_total = sum(self.weights.values())

        self.weights = {
            bay_id: weight / weight_total
            for bay_id, weight in self.weights.items()
        }

    def get_allocation(self, bay_id: str) -> float:
        if bay_id not in BAY_IDS:
            raise ValueError(f"Unknown bay: {bay_id!r}")

        return (
            self.total_capital
            * self.weights[bay_id]
        )


class TurbineBayPanel:
    """
    One decoupled turbine bay.

    Contains:
      * frozen Mark-V governor
      * AVR sizing controller
      * SEL-300G protection
      * session trip/cooldown state

    Cooldown is BAR-INDEX based.

    It is never decremented because another symbol in the same bay
    happened to be evaluated.
    """

    def __init__(
        self,
        bay_id: str,
        total_plant_capital: float,
    ):
        if bay_id not in BAY_IDS:
            raise ValueError(f"Unknown bay: {bay_id!r}")

        self.bay_id = bay_id
        self.spec = BAY_GOVERNOR_SPECS[bay_id]
        self.symbols = FLEET_TOPOLOGY[bay_id]

        self.governor = BayTurbineClosedLoopGovernor(
            self.spec
        )

        initial_capital = (
            total_plant_capital
            * self.spec.capital_weight
        )

        self.avr = BayExcitationAVR(
            bay_name=bay_id,
            allocated_capital_inr=initial_capital,
            min_notional_uel_inr=15_000.0,
            max_notional_oel_inr=initial_capital * 0.40,
        )

        self.relay = BayUnitProtectionSEL300G(
            bay_name=bay_id
        )

        self.bay_capital = initial_capital

        # Plant-level capital actually available after HRSG coupling.
        #
        # This is intentionally separate from bay_capital because
        # update_capital() rejects zero. A tripped/cooling bay may
        # therefore have effective_allocation == 0 while its last
        # positive AVR configuration remains stored. Admission is
        # blocked by protection before AVR sizing in that condition.
        self.effective_allocation = initial_capital

        self.consecutive_stops = 0
        self.tripped_offline = False

        # Entry allowed when bar_index >= this value.
        self.cooldown_until_bar_exclusive = 0

        # Current runtime cooldown settings.
        self.loss_cooldown_bars = (
            LOSS_COOLDOWN_BARS
        )
        self.target_cooldown_bars = (
            TARGET_COOLDOWN_BARS
        )

    def reset_session(self) -> None:
        """
        Reset session-scoped protection state.

        Governor R-history intentionally survives the day boundary.
        """
        self.consecutive_stops = 0
        self.tripped_offline = False
        self.cooldown_until_bar_exclusive = 0

    def update_capital(
        self,
        new_capital: float,
    ) -> None:
        if new_capital <= 0:
            raise ValueError(
                "bay capital must remain positive"
            )

        self.bay_capital = float(new_capital)

        self.avr.update_allocated_capital(
            self.bay_capital
        )

    def cooldown_remaining(
        self,
        bar_index: int,
    ) -> int:
        if bar_index < 0:
            raise ValueError(
                "bar_index must be non-negative"
            )

        return max(
            0,
            self.cooldown_until_bar_exclusive
            - bar_index,
        )

    def apply_dynamic_snapshot(
        self,
        snapshot,
    ) -> None:
        """
        Apply one R5 runtime snapshot directly to this physical bay:
        governor + AVR + SEL300G + cooldown controller.
        """
        self.governor.apply_runtime_profile(
            snapshot.governor_by_bay[
                self.bay_id
            ]
        )

        self.avr.apply_runtime_profile(
            snapshot.avr_by_bay[
                self.bay_id
            ]
        )

        self.relay.apply_runtime_profile(
            snapshot.unit_protection_by_bay[
                self.bay_id
            ]
        )

        self.loss_cooldown_bars = int(
            snapshot.plant.loss_cooldown_bars
        )

        self.target_cooldown_bars = int(
            snapshot.plant.target_cooldown_bars
        )

    def install_synchronizing_equipment(
        self,
        *,
        auto_synchronizer,
        synchrocheck_relay,
        initial_speed_reference_hz: float,
        minimum_speed_reference_hz: float,
        maximum_speed_reference_hz: float,
        speed_reference_rate_hz_per_second: float,
        initial_voltage_reference_pu: float,
        minimum_voltage_reference_pu: float,
        maximum_voltage_reference_pu: float,
        voltage_reference_rate_pu_per_second: float,
    ) -> None:
        """
        Install native R5 generator synchronizing equipment.

        25A = active control.
        25  = independent close supervision.
        """
        from revision5.startup_synchronization import (
            MachineKind,
            TurbineStartupSequencer,
        )

        self.auto_synchronizer_25a = (
            auto_synchronizer
        )

        self.synchrocheck_relay_25 = (
            synchrocheck_relay
        )

        machine_kind = (
            MachineKind.GAS_TURBINE
            if self.bay_id.startswith("GTG")
            else MachineKind.STEAM_TURBINE
        )

        self.startup_sequencer = (
            TurbineStartupSequencer(
                machine_kind
            )
        )

        self.governor.configure_synchronizing_speed_reference(
            initial_reference_hz=(
                initial_speed_reference_hz
            ),
            minimum_reference_hz=(
                minimum_speed_reference_hz
            ),
            maximum_reference_hz=(
                maximum_speed_reference_hz
            ),
            reference_rate_hz_per_second=(
                speed_reference_rate_hz_per_second
            ),
        )

        self.avr.configure_synchronizing_voltage_reference(
            initial_reference_pu=(
                initial_voltage_reference_pu
            ),
            minimum_reference_pu=(
                minimum_voltage_reference_pu
            ),
            maximum_reference_pu=(
                maximum_voltage_reference_pu
            ),
            reference_rate_pu_per_second=(
                voltage_reference_rate_pu_per_second
            ),
        )

    def _startup_inputs(
        self,
        **overrides,
    ):
        from revision5.startup_synchronization import (
            StartupInputs,
        )

        values = {
            "start_command": False,
            "starting_means_ready": False,
            "crank_complete": False,
            "flame_proven": False,
            "exhaust_spread_ok": True,
            "exhaust_spread_trip": False,
            "fsnl_reached": False,
            "synchronizer_ready": False,
            "synchronizer_permissive": False,
            "generator_breaker_closed": False,
            "minimum_load_reached": False,
            "auxiliary_services_stable": False,
            "mechanical_trip": False,
        }

        values.update(overrides)

        return StartupInputs(**values)

    def prepare_startup(self) -> None:
        if not hasattr(
            self,
            "startup_sequencer",
        ):
            raise RuntimeError(
                "synchronizing equipment not installed"
            )

        self.startup_sequencer.reset()

        reset = getattr(
            self.auto_synchronizer_25a,
            "reset",
            None,
        )

        if reset is not None:
            reset()

    def advance_startup(
        self,
        **startup_inputs,
    ):
        if not hasattr(
            self,
            "startup_sequencer",
        ):
            raise RuntimeError(
                "synchronizing equipment not installed"
            )

        return self.startup_sequencer.step(
            self._startup_inputs(
                **startup_inputs
            )
        )

    def run_synchronizing_control(
        self,
        *,
        dt_seconds: float,
        generator_frequency_hz: float,
        bus_frequency_hz: float,
        generator_voltage_pu: float,
        bus_voltage_pu: float,
        generator_phase_deg: float,
        bus_phase_deg: float,
        dead_bus_close_authorized: bool = False,
    ) -> dict:
        """
        Execute one synchronizing-control scan.

        LIVE BUS:
            25A -> governor/AVR pulses
            independent 25 -> closing permissive
            both required for 52G request.

        DEAD BUS:
            25A is deliberately not asked to synchronize against a
            zero-frequency/de-energized bus. Explicit dead-bus authority
            plus Relay 25 dead-bus supervision controls closing.
        """
        from revision5.startup_synchronization import (
            StartupState,
        )

        if not hasattr(
            self,
            "startup_sequencer",
        ):
            raise RuntimeError(
                "synchronizing equipment not installed"
            )

        if (
            self.startup_sequencer.state
            != StartupState.SYNC_READY
        ):
            return {
                "breaker_close_requested": False,
                "reason": (
                    "UNIT_NOT_SYNC_READY:"
                    + self.startup_sequencer.state.value
                ),
            }

        if self.tripped_offline:
            return {
                "breaker_close_requested": False,
                "reason": "UNIT_TRIPPED_OFFLINE",
            }

        dead_bus_threshold = float(
            self.synchrocheck_relay_25
            .spec
            .dead_bus_voltage_pu
        )

        dead_bus = (
            float(bus_voltage_pu)
            <= dead_bus_threshold
        )

        auto_output = None

        if not dead_bus:
            auto_output = (
                self.auto_synchronizer_25a.step(
                    dt_seconds=dt_seconds,
                    generator_frequency_hz=(
                        generator_frequency_hz
                    ),
                    bus_frequency_hz=(
                        bus_frequency_hz
                    ),
                    generator_voltage_pu=(
                        generator_voltage_pu
                    ),
                    bus_voltage_pu=(
                        bus_voltage_pu
                    ),
                    generator_phase_deg=(
                        generator_phase_deg
                    ),
                    bus_phase_deg=(
                        bus_phase_deg
                    ),
                    enabled=True,
                )
            )

            self.governor.apply_synchronizing_speed_pulse(
                command=auto_output.speed_command,
                pulse_width_seconds=(
                    auto_output.speed_pulse_width_seconds
                ),
            )

            self.avr.apply_synchronizing_voltage_pulse(
                command=auto_output.voltage_command,
                pulse_width_seconds=(
                    auto_output.voltage_pulse_width_seconds
                ),
            )

        #
        # The actual AVR remains a closed-loop controller during
        # synchronizing. The 25A pulses move its reference.
        #
        avr_control = self.avr.evaluate_control(
            mode="BUS_VOLTAGE",
            bus_voltage_reference_pu=(
                self.avr.sync_voltage_reference_pu
            ),
            terminal_voltage_pu=(
                generator_voltage_pu
            ),
            mvar_reference_pu=0.0,
            measured_mvar_pu=0.0,
        )

        relay_25 = (
            self.synchrocheck_relay_25.evaluate(
                generator_frequency_hz=(
                    generator_frequency_hz
                ),
                bus_frequency_hz=(
                    bus_frequency_hz
                ),
                generator_voltage_pu=(
                    generator_voltage_pu
                ),
                bus_voltage_pu=(
                    bus_voltage_pu
                ),
                generator_phase_deg=(
                    generator_phase_deg
                ),
                bus_phase_deg=(
                    bus_phase_deg
                ),
                dead_bus_close_authorized=(
                    dead_bus_close_authorized
                ),
            )
        )

        if dead_bus:
            #
            # First island-forming generator:
            # there is no energized waveform for 25A to chase.
            #
            combined_permissive = bool(
                dead_bus_close_authorized
                and relay_25.permitted
            )

            control_reason = (
                "DEAD_BUS_CLOSE_PATH"
            )

        else:
            combined_permissive = bool(
                auto_output.close_opportunity
                and relay_25.permitted
            )

            control_reason = (
                "LIVE_BUS_25A_AND_25"
            )

        if avr_control.get("tripped", False):
            combined_permissive = False
            control_reason = (
                "AVR_PROTECTION_BLOCK"
            )

        transition = (
            self.startup_sequencer.step(
                self._startup_inputs(
                    synchronizer_permissive=(
                        combined_permissive
                    )
                )
            )
        )

        return {
            "breaker_close_requested": bool(
                transition.breaker_close_requested
            ),
            "combined_permissive": (
                combined_permissive
            ),
            "dead_bus": dead_bus,
            "automatic_25a": auto_output,
            "synchrocheck_25": relay_25,
            "avr_control": avr_control,
            "speed_reference_hz": (
                self.governor
                .sync_speed_reference_hz
            ),
            "speed_error_hz": (
                self.governor
                .synchronizing_speed_error(
                    generator_frequency_hz
                )
            ),
            "voltage_reference_pu": (
                self.avr
                .sync_voltage_reference_pu
            ),
            "reason": control_reason,
        }

    def install_machine_dynamics(
        self,
        *,
        dynamic_spec,
        mechanical_protection_spec,
    ) -> None:
        """
        Install the actual R5 turbine-generator dynamic model and
        independent mechanical protection cubicle.
        """
        from revision5.machine_dynamics import (
            TurbineMachineModel,
            TurbineMechanicalProtection,
        )

        if (
            dynamic_spec.machine_kind
            != mechanical_protection_spec.machine_kind
        ):
            raise ValueError(
                "machine dynamics/protection kind mismatch"
            )

        expected_kind = (
            "GAS_TURBINE"
            if self.bay_id.startswith("GTG")
            else "STEAM_TURBINE"
        )

        if dynamic_spec.machine_kind != expected_kind:
            raise ValueError(
                f"{self.bay_id} requires {expected_kind}"
            )

        self.machine_model = TurbineMachineModel(
            dynamic_spec
        )

        self.mechanical_protection = (
            TurbineMechanicalProtection(
                mechanical_protection_spec
            )
        )

    def run_machine_scan(
        self,
        *,
        dt_seconds: float,
        load_reference_pu: float,
        electrical_power_pu: float,
        vibration_mm_s: float,
        lube_oil_pressure_bar: float,
        hydraulic_pressure_bar: float,
        bearing_temperature_c: float,
        speed_reference_hz=None,
        flame_proven=None,
        fuel_available=None,
        exhaust_temperature_c=None,
        exhaust_spread_c=None,
    ) -> dict:
        """
        One actual machine-control/protection scan.

        During synchronization, the reference comes directly from
        the real governor channel modified by 25A SPEED RAISE/LOWER.

        During later loaded operation a caller may explicitly provide
        another governor speed reference.
        """
        from revision5.machine_dynamics import (
            MechanicalMeasurements,
        )

        if not hasattr(self, "machine_model"):
            raise RuntimeError(
                "machine dynamics not installed"
            )

        if speed_reference_hz is None:
            if hasattr(
                self.governor,
                "sync_speed_reference_hz",
            ):
                speed_reference_hz = (
                    self.governor
                    .sync_speed_reference_hz
                )
            else:
                speed_reference_hz = (
                    self.machine_model
                    .spec
                    .nominal_frequency_hz
                )

        machine_state = (
            self.machine_model.step(
                speed_reference_hz=(
                    speed_reference_hz
                ),
                load_reference_pu=(
                    load_reference_pu
                ),
                electrical_power_pu=(
                    electrical_power_pu
                ),
                dt_seconds=dt_seconds,
            )
        )

        protection = (
            self.mechanical_protection.evaluate(
                MechanicalMeasurements(
                    frequency_hz=(
                        machine_state.frequency_hz
                    ),
                    acceleration_hz_per_s=(
                        machine_state
                        .acceleration_hz_per_s
                    ),
                    vibration_mm_s=(
                        vibration_mm_s
                    ),
                    lube_oil_pressure_bar=(
                        lube_oil_pressure_bar
                    ),
                    hydraulic_pressure_bar=(
                        hydraulic_pressure_bar
                    ),
                    bearing_temperature_c=(
                        bearing_temperature_c
                    ),
                    dt_seconds=dt_seconds,
                    flame_proven=flame_proven,
                    fuel_available=fuel_available,
                    exhaust_temperature_c=(
                        exhaust_temperature_c
                    ),
                    exhaust_spread_c=(
                        exhaust_spread_c
                    ),
                )
            )
        )

        if (
            protection.hold_startup
            and hasattr(
                self,
                "startup_sequencer",
            )
        ):
            #
            # Hold is deliberately not a trip.
            # Startup state simply does not advance.
            #
            pass

        return {
            "machine_state": machine_state,
            "mechanical_protection": protection,
            "mechanical_trip": protection.tripped,
            "startup_hold": protection.hold_startup,
        }

    def evaluate_admission(
        self,
        *,
        symbol: str,
        bar_index: int,
        z_score: float,
        price: float,
        atr: float,
        bid: float,
        ask: float,
        tick_age_s: float,
        bar_range: float,
        grid_return_fraction: float = 0.0,
        bus_voltage_reference_pu: float = 1.0,
        terminal_voltage_pu: float = 1.0,
        mvar_reference_pu: float = 0.0,
        measured_mvar_pu: float = 0.0,
        avr_mode: str = "BUS_VOLTAGE",
    ) -> dict:
        if hasattr(self, "startup_sequencer"):
            from revision5.startup_synchronization import (
                StartupState,
            )

            if (
                self.startup_sequencer.state
                != StartupState.DISPATCH_READY
            ):
                return {
                    "admitted": False,
                    "reason": (
                        "UNIT_NOT_DISPATCH_READY:"
                        + self.startup_sequencer.state.value
                    ),
                }

        if symbol not in self.symbols:
            return {
                "admitted": False,
                "reason": (
                    f"SYMBOL_NOT_IN_BAY:{symbol}"
                ),
            }

        if self.tripped_offline:
            return {
                "admitted": False,
                "reason": (
                    f"ANSI_86_LOCKOUT:{self.bay_id}"
                ),
            }

        remaining = self.cooldown_remaining(
            bar_index
        )

        if remaining > 0:
            return {
                "admitted": False,
                "reason": "BAY_COOLDOWN",
                "cooldown_bars_remaining": remaining,
            }

        trip = self.relay.check_pre_synchronization(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last_tick_age_sec=tick_age_s,
            hist_atr=atr,
            curr_bar_range=bar_range,
        )

        if trip.tripped:
            return {
                "admitted": False,
                "reason": (
                    f"SEL300G_TRIP:"
                    f"{trip.ansi_code}:"
                    f"{trip.reason}"
                ),
            }

        governor_entry = (
            self.governor.evaluate_entry_request(
                z_score=z_score,
                grid_return_fraction=(
                    grid_return_fraction
                ),
            )
        )

        dynamic_z = governor_entry[
            "dynamic_z"
        ]

        if governor_entry["action"] != "ENTRY":
            return {
                "admitted": False,
                "reason": (
                    governor_entry["reason"]
                ),
                "z_score": float(z_score),
                "dynamic_z": float(dynamic_z),
            }

        avr_control = self.avr.evaluate_control(
            mode=avr_mode,
            bus_voltage_reference_pu=(
                bus_voltage_reference_pu
            ),
            terminal_voltage_pu=(
                terminal_voltage_pu
            ),
            mvar_reference_pu=(
                mvar_reference_pu
            ),
            measured_mvar_pu=(
                measured_mvar_pu
            ),
        )

        if avr_control["tripped"]:
            return {
                "admitted": False,
                "reason": (
                    "AVR_TRIP:"
                    + avr_control["reason"]
                ),
                "dynamic_z": float(
                    dynamic_z
                ),
                "avr_control": avr_control,
            }

        qty, avr_state = (
            self.avr.calculate_lot_size(
                asset_price=price,
                asset_atr=atr,
                z_strength=z_score,
            )
        )

        if qty <= 0:
            return {
                "admitted": False,
                "reason": f"AVR_CLAMP:{avr_state}",
            }

        stop_distance = max(
            self.spec.atr_barrier * atr,
            price * 0.0065,
        )

        target_distance = max(
            self.spec.target_m * atr,
            stop_distance * 1.5,
        )

        return {
            "admitted": True,
            "quantity": int(qty),
            "stop_loss": round(
                price - stop_distance,
                2,
            ),
            "take_profit": round(
                price + target_distance,
                2,
            ),
            "dynamic_z": float(dynamic_z),
            "avr_state": avr_state,
            "avr_control": avr_control,
            "governor_entry": governor_entry,
            "bay_id": self.bay_id,
            "symbol": symbol,
        }

    def evaluate_position_control(
        self,
        *,
        symbol: str,
        current_r: float,
        reference_r: float,
        max_favorable_r: float,
        elapsed_bars: int,
        min_hold_bars: int,
        max_hold_bars: int,
        hard_stop_r: float,
        trade_target_r: float,
        bid: float,
        ask: float,
        tick_age_s: float,
        atr: float,
        bar_range: float,
        bus_voltage_reference_pu: float = 1.0,
        terminal_voltage_pu: float = 1.0,
        mvar_reference_pu: float = 0.0,
        measured_mvar_pu: float = 0.0,
        avr_mode: str = "BUS_VOLTAGE",
    ) -> dict:
        """
        Continuous physical-unit control:
        Governor = HOLD/EXIT authority.
        AVR = loading/excitation authority.
        SEL300G = unit protection override.
        """
        if symbol not in self.symbols:
            return {
                "action": "EXIT",
                "reason": (
                    f"SYMBOL_NOT_IN_BAY:{symbol}"
                ),
            }

        trip = self.relay.check_pre_synchronization(
            symbol=symbol,
            bid=bid,
            ask=ask,
            last_tick_age_sec=tick_age_s,
            hist_atr=atr,
            curr_bar_range=bar_range,
        )

        if trip.tripped:
            return {
                "action": "EXIT",
                "reason": (
                    f"SEL300G_TRIP:"
                    f"{trip.ansi_code}:"
                    f"{trip.reason}"
                ),
            }

        avr_control = self.avr.evaluate_control(
            mode=avr_mode,
            bus_voltage_reference_pu=(
                bus_voltage_reference_pu
            ),
            terminal_voltage_pu=(
                terminal_voltage_pu
            ),
            mvar_reference_pu=(
                mvar_reference_pu
            ),
            measured_mvar_pu=(
                measured_mvar_pu
            ),
        )

        if avr_control["tripped"]:
            return {
                "action": "EXIT",
                "reason": (
                    "AVR_TRIP:"
                    + avr_control["reason"]
                ),
                "avr_control": avr_control,
            }

        governor_control = (
            self.governor.evaluate_position_control(
                measured_r=current_r,
                reference_r=reference_r,
                max_favorable_r=max_favorable_r,
                elapsed_bars=elapsed_bars,
                min_hold_bars=min_hold_bars,
                max_hold_bars=max_hold_bars,
                hard_stop_r=hard_stop_r,
                trade_target_r=trade_target_r,
            )
        )

        governor_control[
            "avr_control"
        ] = avr_control

        governor_control[
            "avr_load_factor"
        ] = float(
            self.avr.excitation_command
        )

        return governor_control

    def register_outcome(
        self,
        *,
        realized_r: float,
        reason: str,
        bar_index: int,
    ) -> float:
        if bar_index < 0:
            raise ValueError(
                "bar_index must be non-negative"
            )

        control_u = self.governor.register_trade(
            realized_r
        )

        normalized_reason = reason.upper()

        if (
            "STOP" in normalized_reason
            or realized_r < 0.0
        ):
            self.consecutive_stops += 1

            self.cooldown_until_bar_exclusive = (
                bar_index
                + self.loss_cooldown_bars
                + 1
            )

            if self.consecutive_stops >= 2:
                self.tripped_offline = True

        elif (
            "TARGET" in normalized_reason
            or realized_r > 0.0
        ):
            self.consecutive_stops = 0

            self.cooldown_until_bar_exclusive = (
                bar_index
                + self.target_cooldown_bars
                + 1
            )

        self.governor.confirm_position_closed()

        return control_u


class CentralPlantMasterDCS:
    """
    Unified five-bay Revision-5 supervisor.

    No broker I/O is performed here.
    """

    def __init__(
        self,
        total_capital: float = 1_000_000.0,
        db_path: str | Path = (
            "revision5/r5_plant_state.sqlite3"
        ),
    ):
        if total_capital <= 0:
            raise ValueError(
                "total_capital must be positive"
            )

        self.total_capital = float(
            total_capital
        )

        self.grid_relay = (
            MasterGridProtectionMiCOM()
        )

        # Minimal Revision-5 electrical network:
        # five generator breakers + one grid-intertie breaker.
        from revision5.electrical_network import (
            PlantElectricalNetwork,
        )

        self.electrical_network = (
            PlantElectricalNetwork(
                BAY_IDS
            )
        )

        self.dispatcher = (
            DynamicBayLoadDispatcher(
                total_capital=total_capital
            )
        )

        # HRSG sits between merit-order dispatch and physical bay
        # capital/AVR configuration.
        self.hrsg = HeatRecoverySteamGenerator(
            base_plant_capital=total_capital
        )

        self.hrsg_last_balance: Optional[
            HRSGBalanceResult
        ] = None

        self.hrsg_reserve_cash = 0.0

        self.state_store = EngineStateStore(
            db_path
        )

        self.bays: Dict[
            str,
            TurbineBayPanel,
        ] = {
            bay_id: TurbineBayPanel(
                bay_id,
                total_capital,
            )
            for bay_id in BAY_IDS
        }

        # Revision-5 native dynamic control system.
        #
        # No Revision-2/3/4 controller is imported or called.
        from revision5.dynamic_parameters import (
            Revision5DynamicParameterController,
        )

        self.dynamic_controller = (
            Revision5DynamicParameterController()
        )

        self.dynamic_snapshot = None
        self._dynamic_bar_index = None

        self.current_trading_date: Optional[
            date
        ] = None

        self.current_bar_index: Optional[
            int
        ] = None

    def apply_dynamic_environment(
        self,
        environment,
    ):
        """
        Calculate and install one native R5 dynamic operating snapshot.

        This directly changes the operating values consumed by:
        - all five bay governors,
        - all five AVRs,
        - all five SEL300G unit relays,
        - the plant MiCOM grid relay,
        - bay cooldown controllers.

        Base specifications are not mutated.
        """
        snapshot = self.dynamic_controller.evaluate(
            environment
        )

        self.grid_relay.apply_runtime_profile(
            snapshot.grid_protection
        )

        for bay in self.bays.values():
            bay.apply_dynamic_snapshot(
                snapshot
            )

        self.dynamic_snapshot = snapshot

        return snapshot

    def configure_unit_synchronizer(
        self,
        *,
        bay_id: str,
        auto_synchronizer,
        synchrocheck_relay,
        initial_speed_reference_hz: float,
        minimum_speed_reference_hz: float,
        maximum_speed_reference_hz: float,
        speed_reference_rate_hz_per_second: float,
        initial_voltage_reference_pu: float,
        minimum_voltage_reference_pu: float,
        maximum_voltage_reference_pu: float,
        voltage_reference_rate_pu_per_second: float,
    ) -> None:
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        self.bays[
            bay_id
        ].install_synchronizing_equipment(
            auto_synchronizer=auto_synchronizer,
            synchrocheck_relay=synchrocheck_relay,
            initial_speed_reference_hz=(
                initial_speed_reference_hz
            ),
            minimum_speed_reference_hz=(
                minimum_speed_reference_hz
            ),
            maximum_speed_reference_hz=(
                maximum_speed_reference_hz
            ),
            speed_reference_rate_hz_per_second=(
                speed_reference_rate_hz_per_second
            ),
            initial_voltage_reference_pu=(
                initial_voltage_reference_pu
            ),
            minimum_voltage_reference_pu=(
                minimum_voltage_reference_pu
            ),
            maximum_voltage_reference_pu=(
                maximum_voltage_reference_pu
            ),
            voltage_reference_rate_pu_per_second=(
                voltage_reference_rate_pu_per_second
            ),
        )

    def prepare_unit_startup(
        self,
        *,
        bay_id: str,
    ) -> None:
        """
        Put the selected generator breaker in the startup-open state.

        This does NOT clear unit-trip or ANSI-86 protection state.
        """
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        breaker = (
            self.electrical_network
            .unit_breaker(
                bay_id
            )
        )

        breaker.open(
            reason="UNIT_STARTUP",
            source="R5_STARTUP_SEQUENCE",
        )

        self.bays[
            bay_id
        ].prepare_startup()

    def advance_unit_startup(
        self,
        *,
        bay_id: str,
        **startup_inputs,
    ):
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        return self.bays[
            bay_id
        ].advance_startup(
            **startup_inputs
        )

    def run_unit_synchronization(
        self,
        *,
        bay_id: str,
        dt_seconds: float,
        generator_frequency_hz: float,
        bus_frequency_hz: float,
        generator_voltage_pu: float,
        bus_voltage_pu: float,
        generator_phase_deg: float,
        bus_phase_deg: float,
        dead_bus_close_authorized: bool = False,
    ) -> dict:
        """
        Run 25A + independent 25 and operate the real 52G only if
        the turbine startup sequencer requests closure.
        """
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        bay = self.bays[bay_id]

        result = bay.run_synchronizing_control(
            dt_seconds=dt_seconds,
            generator_frequency_hz=(
                generator_frequency_hz
            ),
            bus_frequency_hz=(
                bus_frequency_hz
            ),
            generator_voltage_pu=(
                generator_voltage_pu
            ),
            bus_voltage_pu=(
                bus_voltage_pu
            ),
            generator_phase_deg=(
                generator_phase_deg
            ),
            bus_phase_deg=(
                bus_phase_deg
            ),
            dead_bus_close_authorized=(
                dead_bus_close_authorized
            ),
        )

        result["breaker_closed"] = False

        if not result.get(
            "breaker_close_requested",
            False,
        ):
            return result

        breaker = (
            self.electrical_network
            .unit_breaker(
                bay_id
            )
        )

        if breaker.lockout_86:
            result[
                "breaker_close_requested"
            ] = False

            result["reason"] = (
                "52G_BLOCKED_ANSI86"
            )

            return result

        if bay.tripped_offline:
            result[
                "breaker_close_requested"
            ] = False

            result["reason"] = (
                "52G_BLOCKED_UNIT_TRIP"
            )

            return result

        self.electrical_network.close_unit_breaker(
            bay_id
        )

        bay.advance_startup(
            generator_breaker_closed=True
        )

        result["breaker_closed"] = True
        result["reason"] = (
            "52G_CLOSED_SYNCHRONIZED"
        )

        return result

    def configure_unit_machine(
        self,
        *,
        bay_id: str,
        dynamic_spec,
        mechanical_protection_spec,
    ) -> None:
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        self.bays[
            bay_id
        ].install_machine_dynamics(
            dynamic_spec=dynamic_spec,
            mechanical_protection_spec=(
                mechanical_protection_spec
            ),
        )

    def run_unit_machine_scan(
        self,
        *,
        bay_id: str,
        dt_seconds: float,
        load_reference_pu: float,
        electrical_power_pu: float,
        vibration_mm_s: float,
        lube_oil_pressure_bar: float,
        hydraulic_pressure_bar: float,
        bearing_temperature_c: float,
        speed_reference_hz=None,
        flame_proven=None,
        fuel_available=None,
        exhaust_temperature_c=None,
        exhaust_spread_c=None,
    ) -> dict:
        """
        Run machine dynamics and independent mechanical protection.

        Mechanical trip:
            -> selected unit 52G opens
            -> optional ANSI-86 lockout according to protection spec
            -> no other generator breaker opens
            -> grid intertie remains untouched.
        """
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        bay = self.bays[bay_id]

        result = bay.run_machine_scan(
            dt_seconds=dt_seconds,
            load_reference_pu=(
                load_reference_pu
            ),
            electrical_power_pu=(
                electrical_power_pu
            ),
            vibration_mm_s=(
                vibration_mm_s
            ),
            lube_oil_pressure_bar=(
                lube_oil_pressure_bar
            ),
            hydraulic_pressure_bar=(
                hydraulic_pressure_bar
            ),
            bearing_temperature_c=(
                bearing_temperature_c
            ),
            speed_reference_hz=(
                speed_reference_hz
            ),
            flame_proven=flame_proven,
            fuel_available=fuel_available,
            exhaust_temperature_c=(
                exhaust_temperature_c
            ),
            exhaust_spread_c=(
                exhaust_spread_c
            ),
        )

        protection = (
            result["mechanical_protection"]
        )

        if protection.tripped:

            self.trip_unit_breaker(
                bay_id=bay_id,
                reason=(
                    f"{protection.trip_code}:"
                    f"{protection.reason}"
                ),
                source=(
                    "TURBINE_MECHANICAL_PROTECTION"
                ),
                lockout=(
                    protection.lockout_86
                ),
            )

            if hasattr(
                bay,
                "startup_sequencer",
            ):
                bay.startup_sequencer.trip(
                    protection.reason
                )

        result["electrical_status"] = (
            self.electrical_network.snapshot()
        )

        return result

    def begin_bar(
        self,
        current_dt: datetime,
        bar_index: int,
        environment=None,
    ) -> None:
        if bar_index < 0:
            raise ValueError(
                "bar_index must be non-negative"
            )

        session_date = trading_date(
            current_dt
        )

        if (
            self.current_trading_date
            != session_date
        ):
            self.current_trading_date = (
                session_date
            )

            self.current_bar_index = None
            self._dynamic_bar_index = None

            for bay in self.bays.values():
                bay.reset_session()

        if (
            self.current_bar_index is not None
            and bar_index
            < self.current_bar_index
        ):
            raise ValueError(
                "bar_index moved backwards "
                "within the same trading session"
            )

        if (
            environment is not None
            and self._dynamic_bar_index
            != bar_index
        ):
            self.apply_dynamic_environment(
                environment
            )

            self._dynamic_bar_index = (
                bar_index
            )

        self.current_bar_index = bar_index

    def _hrsg_bay_states(
        self,
        bar_index: int,
    ) -> Dict[str, dict]:
        """
        Translate physical bay protection state into the HRSG contract.
        """
        if bar_index < 0:
            raise ValueError(
                "bar_index must be non-negative"
            )

        return {
            bay_id: {
                "tripped_offline": (
                    bay.tripped_offline
                ),
                "cooldown_bars_remaining": (
                    bay.cooldown_remaining(
                        bar_index
                    )
                ),
            }
            for bay_id, bay
            in self.bays.items()
        }

    def apply_hrsg_balance(
        self,
        *,
        bar_index: int,
        bay_beta: Optional[
            Mapping[str, float]
        ] = None,
    ) -> HRSGBalanceResult:
        """
        Convert dispatcher merit-order allocations into HRSG-constrained
        effective bay allocations.

        Capital conservation invariant:

            sum(effective allocations) + reserve
            == total plant capital

        Correlation derating is retained as reserve and is never
        normalized back into forced deployment.
        """
        if bar_index < 0:
            raise ValueError(
                "bar_index must be non-negative"
            )

        base_allocations = {
            bay_id: (
                self.dispatcher.get_allocation(
                    bay_id
                )
            )
            for bay_id in BAY_IDS
        }

        balance = self.hrsg.balance_capital(
            bay_states=self._hrsg_bay_states(
                bar_index
            ),
            base_allocations=(
                base_allocations
            ),
            bay_beta=bay_beta,
        )

        for bay_id, allocation in (
            balance.allocations.items()
        ):
            bay = self.bays[bay_id]

            allocation = float(
                allocation
            )

            bay.effective_allocation = (
                allocation
            )

            # update_capital() deliberately requires positive capital.
            #
            # Zero means HRSG considers this bay unavailable. Existing
            # ANSI-86/cooldown protection blocks admission before AVR
            # sizing, so the previous positive AVR configuration is
            # left untouched until the bay becomes available again.
            if allocation > 0.0:
                bay.update_capital(
                    allocation
                )

        self.hrsg_last_balance = balance
        self.hrsg_reserve_cash = float(
            balance.reserve_cash
        )

        conserved = (
            sum(
                bay.effective_allocation
                for bay in self.bays.values()
            )
            + self.hrsg_reserve_cash
        )

        if abs(
            conserved - self.total_capital
        ) > 1e-6:
            raise RuntimeError(
                "DCS/HRSG capital conservation "
                "invariant violated"
            )

        return balance

    def record_hrsg_return_snapshot(
        self,
        bay_returns: Mapping[
            str,
            float,
        ],
    ) -> None:
        """
        Feed one synchronous return snapshot into HRSG covariance logic.

        Trade-close R outcomes are intentionally not routed here because
        asynchronously closed trades are not synchronous observations
        and therefore must not be used as pairwise correlation samples.
        """
        self.hrsg.record_return_snapshot(
            bay_returns
        )

    def hrsg_status(self) -> dict:
        """
        Read-only supervisory state for telemetry/replay reports.
        """
        return {
            "reserve_cash": (
                self.hrsg_reserve_cash
            ),
            "effective_allocations": {
                bay_id: (
                    bay.effective_allocation
                )
                for bay_id, bay
                in self.bays.items()
            },
            "has_balance": (
                self.hrsg_last_balance
                is not None
            ),
        }

    def trip_unit_breaker(
        self,
        *,
        bay_id: str,
        reason: str,
        source: str,
        lockout: bool = False,
    ) -> None:
        """
        Selective unit isolation.

        No other turbine breaker and no grid breaker is operated.
        """
        if bay_id not in self.bays:
            raise KeyError(
                f"unknown bay: {bay_id}"
            )

        self.electrical_network.trip_unit(
            bay_id,
            reason=reason,
            source=source,
            lockout=lockout,
        )

        # Keep the existing bay-operability state consistent with
        # the electrical breaker state.
        self.bays[
            bay_id
        ].tripped_offline = True

    def reset_unit_breaker(
        self,
        *,
        bay_id: str,
    ) -> None:
        """
        Explicit operator/reset path for an isolated unit.
        """
        self.electrical_network.reset_unit_lockout(
            bay_id
        )

        self.electrical_network.close_unit_breaker(
            bay_id
        )

        self.bays[
            bay_id
        ].tripped_offline = False

    def open_grid_intertie(
        self,
        *,
        reason: str,
        source: str,
    ) -> None:
        """
        Open only the grid breaker.

        Healthy generating units remain electrically connected to
        PLANT_BUS and therefore remain available for island/house load.
        """
        self.electrical_network.open_grid_intertie(
            reason=reason,
            source=source,
        )

    def close_grid_intertie(
        self,
    ) -> None:
        self.electrical_network.close_grid_intertie()

    def electrical_status(self) -> dict:
        return self.electrical_network.snapshot()

    def evaluate_entry(
        self,
        *,
        engine_mode: str,
        symbol: str,
        bar_dt: datetime,
        bar_index: int,
        z_score: float,
        price: float,
        atr: float,
        bid: float,
        ask: float,
        tick_age_s: float,
        bar_range: float,
        nifty_15m_ret: float = 0.0,
        nifty_vol_z: float = 0.0,
        fleet_equity_dd_pct: float = 0.0,
        bus_voltage_reference_pu: float = 1.0,
        terminal_voltage_pu: float = 1.0,
        mvar_reference_pu: float = 0.0,
        measured_mvar_pu: float = 0.0,
        avr_mode: str = "BUS_VOLTAGE",
        dynamic_environment=None,
    ) -> dict:
        validate_engine(engine_mode)

        self.begin_bar(
            bar_dt,
            bar_index,
            environment=dynamic_environment,
        )

        try:
            bay_id = bay_for_symbol(
                symbol
            )
        except KeyError:
            return {
                "admitted": False,
                "reason": (
                    f"UNMAPPED_SYMBOL:{symbol}"
                ),
            }

        if not (
            self.electrical_network
            .unit_available(
                bay_id
            )
        ):
            return {
                "admitted": False,
                "reason": (
                    f"UNIT_BREAKER_OPEN:"
                    f"{bay_id}"
                ),
                "electrical_mode": (
                    self.electrical_network
                    .mode.value
                ),
            }

        allowed, reason = (
            self.state_store.entry_allowed(
                engine_mode,
                bay_id,
                bar_dt,
            )
        )

        if not allowed:
            return {
                "admitted": False,
                "reason": reason,
            }

        grid_check = (
            self.grid_relay.evaluate_grid_intertie(
                nifty_15m_return=nifty_15m_ret,
                nifty_vol_z=nifty_vol_z,
                fleet_equity_drawdown_pct=(
                    fleet_equity_dd_pct
                ),
            )
        )

        if grid_check.tripped:
            self.open_grid_intertie(
                reason=(
                    f"{grid_check.ansi_code}:"
                    f"{grid_check.reason}"
                ),
                source="MiCOM_GRID_PROTECTION",
            )

            return {
                "admitted": False,
                "reason": (
                    f"SUBSTATION_TRIP:"
                    f"{grid_check.ansi_code}:"
                    f"{grid_check.reason}"
                ),
            }

        # Plant merit-order request is passed through HRSG before
        # bay-level AVR sizing and admission.
        self.apply_hrsg_balance(
            bar_index=bar_index
        )

        bay = self.bays[bay_id]

        result = bay.evaluate_admission(
            symbol=symbol,
            bar_index=bar_index,
            z_score=z_score,
            price=price,
            atr=atr,
            bid=bid,
            ask=ask,
            tick_age_s=tick_age_s,
            bar_range=bar_range,
            grid_return_fraction=(
                nifty_15m_ret
            ),
            bus_voltage_reference_pu=(
                bus_voltage_reference_pu
            ),
            terminal_voltage_pu=(
                terminal_voltage_pu
            ),
            mvar_reference_pu=(
                mvar_reference_pu
            ),
            measured_mvar_pu=(
                measured_mvar_pu
            ),
            avr_mode=avr_mode,
        )

        if (
            not result.get(
                "admitted",
                False,
            )
            and str(
                result.get(
                    "reason",
                    "",
                )
            ).startswith(
                "SEL300G_TRIP:"
            )
        ):
            self.trip_unit_breaker(
                bay_id=bay_id,
                reason=str(
                    result["reason"]
                ),
                source=(
                    "SEL300G_UNIT_PROTECTION"
                ),
                # 86 lockout will be requested explicitly by severe
                # elements such as 87G when relay coordination is wired.
                lockout=False,
            )

        # HRSG state is exposed on all bay-evaluated outcomes so replay
        # and paper reports can audit capital routing even when admission
        # is denied downstream.
        result.update(
            {
                "hrsg_effective_allocation": (
                    bay.effective_allocation
                ),
                "hrsg_reserve_cash": (
                    self.hrsg_reserve_cash
                ),
            }
        )

        if result.get("admitted"):
            result.update(
                {
                    "engine": engine_mode,
                    "bay_id": bay_id,
                    "symbol": symbol,
                }
            )

        return result

    def evaluate_position_control(
        self,
        *,
        engine_mode: str,
        symbol: str,
        bar_dt: datetime,
        bar_index: int,
        current_r: float,
        reference_r: float,
        max_favorable_r: float,
        elapsed_bars: int,
        min_hold_bars: int,
        max_hold_bars: int,
        hard_stop_r: float,
        trade_target_r: float,
        bid: float,
        ask: float,
        tick_age_s: float,
        atr: float,
        bar_range: float,
        nifty_15m_ret: float = 0.0,
        nifty_vol_z: float = 0.0,
        fleet_equity_dd_pct: float = 0.0,
        bus_voltage_reference_pu: float = 1.0,
        terminal_voltage_pu: float = 1.0,
        mvar_reference_pu: float = 0.0,
        measured_mvar_pu: float = 0.0,
        avr_mode: str = "BUS_VOLTAGE",
        dynamic_environment=None,
    ) -> dict:
        """
        Continuous closed-loop control for an already-open position.

        Governor owns HOLD/EXIT.
        AVR controls permitted excitation/loading.
        SEL/MiCOM may force EXIT.
        """
        validate_engine(engine_mode)

        self.begin_bar(
            bar_dt,
            bar_index,
            environment=dynamic_environment,
        )

        try:
            bay_id = bay_for_symbol(symbol)
        except KeyError:
            return {
                "action": "EXIT",
                "reason": (
                    f"UNMAPPED_SYMBOL:{symbol}"
                ),
            }

        grid_check = (
            self.grid_relay.evaluate_grid_intertie(
                nifty_15m_return=nifty_15m_ret,
                nifty_vol_z=nifty_vol_z,
                fleet_equity_drawdown_pct=(
                    fleet_equity_dd_pct
                ),
            )
        )

        if grid_check.tripped:
            return {
                "action": "EXIT",
                "reason": (
                    f"SUBSTATION_TRIP:"
                    f"{grid_check.ansi_code}:"
                    f"{grid_check.reason}"
                ),
            }

        bay = self.bays[bay_id]

        result = bay.evaluate_position_control(
            symbol=symbol,
            current_r=current_r,
            reference_r=reference_r,
            max_favorable_r=max_favorable_r,
            elapsed_bars=elapsed_bars,
            min_hold_bars=min_hold_bars,
            max_hold_bars=max_hold_bars,
            hard_stop_r=hard_stop_r,
            trade_target_r=trade_target_r,
            bid=bid,
            ask=ask,
            tick_age_s=tick_age_s,
            atr=atr,
            bar_range=bar_range,
            bus_voltage_reference_pu=(
                bus_voltage_reference_pu
            ),
            terminal_voltage_pu=(
                terminal_voltage_pu
            ),
            mvar_reference_pu=(
                mvar_reference_pu
            ),
            measured_mvar_pu=(
                measured_mvar_pu
            ),
            avr_mode=avr_mode,
        )

        result.update(
            {
                "engine": engine_mode,
                "bay_id": bay_id,
                "symbol": symbol,
            }
        )

        return result

    def on_trade_filled(
        self,
        *,
        engine_mode: str,
        symbol: str,
        filled_at: datetime,
    ) -> bool:
        """
        Confirm a broker/replay fill.

        Only a confirmed ENGINE_A fill consumes
        the persistent bay allowance.
        """
        validate_engine(engine_mode)

        bay_id = bay_for_symbol(
            symbol
        )

        if engine_mode == ENGINE_A:
            return (
                self.state_store
                .consume_engine_a_fill(
                    bay_id,
                    filled_at,
                )
            )

        return True

    def on_trade_closed(
        self,
        *,
        symbol: str,
        pnl_r: float,
        reason: str,
        closed_at: datetime,
        bar_index: int,
    ) -> None:
        self.begin_bar(
            closed_at,
            bar_index,
        )

        bay_id = bay_for_symbol(
            symbol
        )

        bay = self.bays[bay_id]

        bay.register_outcome(
            realized_r=pnl_r,
            reason=reason,
            bar_index=bar_index,
        )

        self.dispatcher.register_trade(
            bay_id,
            pnl_r,
        )

    def build_engine_a_squareoff_intents(
        self,
        *,
        current_dt: datetime,
        open_positions: Iterable[
            Mapping[str, Any]
        ],
    ) -> list[SquareOffIntent]:
        """
        Build mandatory Engine-A liquidation intents.

        This deliberately does NOT send orders.
        Replay/paper/live adapters execute these
        identical intents through their own I/O layer.
        """
        if not engine_a_squareoff_due(
            current_dt
        ):
            return []

        intents: list[
            SquareOffIntent
        ] = []

        for position in open_positions:
            engine_mode = position.get(
                "engine_mode"
            )

            if engine_mode != ENGINE_A:
                continue

            symbol = str(
                position["symbol"]
            )

            bay_id = bay_for_symbol(
                symbol
            )

            position_id = position.get(
                "position_id"
            )

            intents.append(
                SquareOffIntent(
                    engine_mode=ENGINE_A,
                    symbol=symbol,
                    bay_id=bay_id,
                    requested_at=current_dt,
                    reason=(
                        "ENGINE_A_MANDATORY_"
                        "SQUAREOFF_15_15_IST"
                    ),
                    position_id=(
                        None
                        if position_id is None
                        else str(position_id)
                    ),
                )
            )

        return intents
