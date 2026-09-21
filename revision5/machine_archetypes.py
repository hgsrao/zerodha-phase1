"""
Frozen Revision-5 machine archetype registry.

IMPORTANT
---------
These are ENGINEERING SIMULATION CANDIDATES for the R5 trading/CCPP model.

They are NOT:
- OEM turbine-generator constants
- site commissioning settings
- relay commissioning settings

They establish relative dynamic behaviour only:

    GTG1 / GTG2   FAST
    CSTG1 / CSTG2 MEDIUM
    BPSTG         SLOW

Existing R5 droop philosophy is preserved:
    GTG   4.0 %
    CSTG  5.5 %
    BPSTG 7.5 %
"""

from __future__ import annotations

from dataclasses import dataclass

from revision5.machine_dynamics import (
    MachineDynamicSpec,
    MechanicalProtectionSpec,
)


PROVENANCE = (
    "R5_ENGINEERING_CANDIDATE_"
    "NOT_OEM_NOT_FOR_COMMISSIONING"
)


@dataclass(frozen=True)
class BayOperatingEnvelope:
    """
    Trading equivalent of a unit-commitment operating envelope.

    Fractions are relative to that bay's available allocation.

    This structure is presently used by the independent feasibility
    auditor. It is not another strategy authority.
    """

    minimum_operating_fraction: float
    maximum_operating_fraction: float

    ramp_up_fraction_per_bar: float
    ramp_down_fraction_per_bar: float

    startup_ramp_fraction: float
    shutdown_ramp_fraction: float

    minimum_online_bars: int
    minimum_offline_bars: int

    reserve_headroom_fraction: float

    def validate(self) -> None:
        fractions = (
            self.minimum_operating_fraction,
            self.maximum_operating_fraction,
            self.ramp_up_fraction_per_bar,
            self.ramp_down_fraction_per_bar,
            self.startup_ramp_fraction,
            self.shutdown_ramp_fraction,
            self.reserve_headroom_fraction,
        )

        if not all(
            0.0 <= float(v) <= 1.0
            for v in fractions
        ):
            raise ValueError(
                "operating-envelope fractions "
                "must be within [0,1]"
            )

        if (
            self.minimum_operating_fraction
            >= self.maximum_operating_fraction
        ):
            raise ValueError(
                "minimum operating fraction "
                "must be below maximum"
            )

        if (
            self.maximum_operating_fraction
            + self.reserve_headroom_fraction
            > 1.0 + 1e-12
        ):
            raise ValueError(
                "maximum operating load plus reserve "
                "cannot exceed bay capacity"
            )

        if (
            self.minimum_online_bars < 0
            or self.minimum_offline_bars < 0
        ):
            raise ValueError(
                "minimum online/offline bars "
                "cannot be negative"
            )


@dataclass(frozen=True)
class MachineArchetype:
    bay_id: str
    description: str
    provenance: str

    dynamics: MachineDynamicSpec
    protection: MechanicalProtectionSpec
    operating: BayOperatingEnvelope


def _gtg_protection() -> MechanicalProtectionSpec:
    return MechanicalProtectionSpec(
        machine_kind="GAS_TURBINE",

        overspeed_alarm_hz=52.0,
        overspeed_trip_hz=55.0,

        acceleration_trip_hz_per_s=3.0,

        vibration_alarm_mm_s=7.0,
        vibration_trip_mm_s=10.0,

        low_lube_oil_trip_bar=1.2,
        low_hydraulic_pressure_trip_bar=1.5,

        bearing_temperature_alarm_c=95.0,
        bearing_temperature_trip_c=115.0,

        exhaust_temperature_alarm_c=600.0,
        exhaust_temperature_trip_c=650.0,

        exhaust_spread_hold_c=40.0,
        exhaust_spread_trip_c=70.0,

        flame_loss_trip_delay_s=0.5,
        fuel_loss_trip_delay_s=0.5,

        lockout_trip_codes=(
            "MECH_OVERSPEED",
            "MECH_VIBRATION",
            "MECH_LOW_LUBE_OIL",
            "MECH_LOW_HYDRAULIC",
            "MECH_BEARING_TEMP",
            "MECH_EXHAUST_TEMP",
            "MECH_EXHAUST_SPREAD",
            "MECH_FLAME_LOSS",
            "MECH_FUEL_LOSS",
        ),
    )


def _steam_protection() -> MechanicalProtectionSpec:
    return MechanicalProtectionSpec(
        machine_kind="STEAM_TURBINE",

        overspeed_alarm_hz=52.0,
        overspeed_trip_hz=55.0,

        acceleration_trip_hz_per_s=2.0,

        vibration_alarm_mm_s=7.0,
        vibration_trip_mm_s=10.0,

        low_lube_oil_trip_bar=1.2,
        low_hydraulic_pressure_trip_bar=1.5,

        bearing_temperature_alarm_c=95.0,
        bearing_temperature_trip_c=115.0,

        exhaust_temperature_alarm_c=None,
        exhaust_temperature_trip_c=None,

        exhaust_spread_hold_c=None,
        exhaust_spread_trip_c=None,

        flame_loss_trip_delay_s=None,
        fuel_loss_trip_delay_s=None,

        lockout_trip_codes=(
            "MECH_OVERSPEED",
            "MECH_VIBRATION",
            "MECH_LOW_LUBE_OIL",
            "MECH_LOW_HYDRAULIC",
            "MECH_BEARING_TEMP",
        ),
    )


GTG_OPERATING = BayOperatingEnvelope(
    minimum_operating_fraction=0.05,
    maximum_operating_fraction=0.90,

    ramp_up_fraction_per_bar=0.25,
    ramp_down_fraction_per_bar=0.35,

    startup_ramp_fraction=0.20,
    shutdown_ramp_fraction=0.30,

    minimum_online_bars=1,
    minimum_offline_bars=1,

    reserve_headroom_fraction=0.10,
)


CSTG_OPERATING = BayOperatingEnvelope(
    minimum_operating_fraction=0.08,
    maximum_operating_fraction=0.88,

    ramp_up_fraction_per_bar=0.15,
    ramp_down_fraction_per_bar=0.20,

    startup_ramp_fraction=0.12,
    shutdown_ramp_fraction=0.18,

    minimum_online_bars=2,
    minimum_offline_bars=2,

    reserve_headroom_fraction=0.12,
)


BPSTG_OPERATING = BayOperatingEnvelope(
    minimum_operating_fraction=0.10,
    maximum_operating_fraction=0.85,

    ramp_up_fraction_per_bar=0.08,
    ramp_down_fraction_per_bar=0.10,

    startup_ramp_fraction=0.08,
    shutdown_ramp_fraction=0.10,

    minimum_online_bars=3,
    minimum_offline_bars=3,

    reserve_headroom_fraction=0.15,
)


MACHINE_ARCHETYPES = {

    "GTG1_HEAVY_INDUSTRY": MachineArchetype(
        bay_id="GTG1_HEAVY_INDUSTRY",
        description="FAST GTG heavy-industry analogue",
        provenance=PROVENANCE,

        dynamics=MachineDynamicSpec(
            name="GTG1_HEAVY_INDUSTRY",
            machine_kind="GAS_TURBINE",
            response_class="FAST",

            nominal_frequency_hz=50.0,
            droop_fraction=0.040,

            inertia_h_seconds=3.5,
            damping_pu=1.00,

            governor_time_constant_s=0.15,
            actuator_time_constant_s=0.10,
            turbine_time_constant_s=0.30,

            mechanical_power_min_pu=0.0,
            mechanical_power_max_pu=1.0,

            ramp_up_pu_per_s=0.40,
            ramp_down_pu_per_s=0.55,

            speed_deadband_hz=0.020,

            acceleration_limit_hz_per_s=2.0,
            deceleration_limit_hz_per_s=2.5,
        ),

        protection=_gtg_protection(),
        operating=GTG_OPERATING,
    ),

    "GTG2_TECH_TELECOM": MachineArchetype(
        bay_id="GTG2_TECH_TELECOM",
        description="FAST GTG technology/telecom analogue",
        provenance=PROVENANCE,

        dynamics=MachineDynamicSpec(
            name="GTG2_TECH_TELECOM",
            machine_kind="GAS_TURBINE",
            response_class="FAST",

            nominal_frequency_hz=50.0,
            droop_fraction=0.040,

            inertia_h_seconds=3.2,
            damping_pu=0.90,

            governor_time_constant_s=0.12,
            actuator_time_constant_s=0.09,
            turbine_time_constant_s=0.26,

            mechanical_power_min_pu=0.0,
            mechanical_power_max_pu=1.0,

            ramp_up_pu_per_s=0.45,
            ramp_down_pu_per_s=0.60,

            speed_deadband_hz=0.020,

            acceleration_limit_hz_per_s=2.2,
            deceleration_limit_hz_per_s=2.7,
        ),

        protection=_gtg_protection(),
        operating=GTG_OPERATING,
    ),

    "CSTG1_BFSI": MachineArchetype(
        bay_id="CSTG1_BFSI",
        description="MEDIUM CSTG BFSI analogue",
        provenance=PROVENANCE,

        dynamics=MachineDynamicSpec(
            name="CSTG1_BFSI",
            machine_kind="STEAM_TURBINE",
            response_class="MEDIUM",

            nominal_frequency_hz=50.0,
            droop_fraction=0.055,

            inertia_h_seconds=5.5,
            damping_pu=1.20,

            governor_time_constant_s=0.35,
            actuator_time_constant_s=0.30,
            turbine_time_constant_s=0.80,

            mechanical_power_min_pu=0.0,
            mechanical_power_max_pu=1.0,

            ramp_up_pu_per_s=0.20,
            ramp_down_pu_per_s=0.25,

            speed_deadband_hz=0.025,

            acceleration_limit_hz_per_s=1.20,
            deceleration_limit_hz_per_s=1.40,
        ),

        protection=_steam_protection(),
        operating=CSTG_OPERATING,
    ),

    "CSTG2_CONSUMER_AUTO": MachineArchetype(
        bay_id="CSTG2_CONSUMER_AUTO",
        description="MEDIUM CSTG consumer/auto analogue",
        provenance=PROVENANCE,

        dynamics=MachineDynamicSpec(
            name="CSTG2_CONSUMER_AUTO",
            machine_kind="STEAM_TURBINE",
            response_class="MEDIUM",

            nominal_frequency_hz=50.0,
            droop_fraction=0.055,

            inertia_h_seconds=5.8,
            damping_pu=1.25,

            governor_time_constant_s=0.40,
            actuator_time_constant_s=0.32,
            turbine_time_constant_s=0.90,

            mechanical_power_min_pu=0.0,
            mechanical_power_max_pu=1.0,

            ramp_up_pu_per_s=0.18,
            ramp_down_pu_per_s=0.22,

            speed_deadband_hz=0.025,

            acceleration_limit_hz_per_s=1.10,
            deceleration_limit_hz_per_s=1.30,
        ),

        protection=_steam_protection(),
        operating=CSTG_OPERATING,
    ),

    "BPSTG_HEALTHCARE": MachineArchetype(
        bay_id="BPSTG_HEALTHCARE",
        description="SLOW bottoming/healthcare analogue",
        provenance=PROVENANCE,

        dynamics=MachineDynamicSpec(
            name="BPSTG_HEALTHCARE",
            machine_kind="STEAM_TURBINE",
            response_class="SLOW",

            nominal_frequency_hz=50.0,
            droop_fraction=0.075,

            inertia_h_seconds=8.0,
            damping_pu=1.50,

            governor_time_constant_s=0.60,
            actuator_time_constant_s=0.50,
            turbine_time_constant_s=1.40,

            mechanical_power_min_pu=0.0,
            mechanical_power_max_pu=1.0,

            ramp_up_pu_per_s=0.10,
            ramp_down_pu_per_s=0.12,

            speed_deadband_hz=0.030,

            acceleration_limit_hz_per_s=0.70,
            deceleration_limit_hz_per_s=0.80,
        ),

        protection=_steam_protection(),
        operating=BPSTG_OPERATING,
    ),
}


def validate_machine_archetypes(
    bay_ids,
) -> None:

    expected = set(bay_ids)
    actual = set(MACHINE_ARCHETYPES)

    if actual != expected:
        raise RuntimeError(
            "machine/topology mismatch: "
            f"missing={sorted(expected-actual)}, "
            f"extra={sorted(actual-expected)}"
        )

    for bay_id, profile in (
        MACHINE_ARCHETYPES.items()
    ):
        if profile.bay_id != bay_id:
            raise RuntimeError(
                f"identity mismatch: {bay_id}"
            )

        if profile.provenance != PROVENANCE:
            raise RuntimeError(
                f"{bay_id}: provenance missing"
            )

        profile.dynamics.validate()
        profile.protection.validate()
        profile.operating.validate()
