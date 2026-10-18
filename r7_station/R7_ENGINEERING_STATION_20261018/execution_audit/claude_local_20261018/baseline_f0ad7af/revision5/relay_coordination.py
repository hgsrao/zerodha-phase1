"""
Native Revision-5 protection-relay coordination.

Implements:
- IEC IDMT low-set + high-set overcurrent stages
- definite-time two-stage elements
- generator differential protection
- distance protection zones
- breaker/86 lockout state

No Revision-2/3/4 dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite


class IECInverseCurve(str, Enum):
    STANDARD_INVERSE = "STANDARD_INVERSE"
    VERY_INVERSE = "VERY_INVERSE"
    EXTREMELY_INVERSE = "EXTREMELY_INVERSE"
    LONG_TIME_INVERSE = "LONG_TIME_INVERSE"


IEC_CURVE_CONSTANTS = {
    IECInverseCurve.STANDARD_INVERSE: (
        0.14,
        0.02,
    ),
    IECInverseCurve.VERY_INVERSE: (
        13.5,
        1.0,
    ),
    IECInverseCurve.EXTREMELY_INVERSE: (
        80.0,
        2.0,
    ),
    IECInverseCurve.LONG_TIME_INVERSE: (
        120.0,
        1.0,
    ),
}


def iec_operate_time_seconds(
    *,
    multiple: float,
    tms: float,
    curve: IECInverseCurve,
) -> float:
    if not isfinite(multiple):
        raise ValueError(
            "multiple must be finite"
        )

    if not isfinite(tms) or tms <= 0.0:
        raise ValueError(
            "TMS must be finite and positive"
        )

    if multiple <= 1.0:
        return float("inf")

    beta, alpha = (
        IEC_CURVE_CONSTANTS[curve]
    )

    denominator = (
        multiple ** alpha
        - 1.0
    )

    if denominator <= 0.0:
        return float("inf")

    return (
        float(tms)
        * beta
        / denominator
    )


@dataclass(frozen=True)
class RelayTrip:
    tripped: bool
    element: str
    stage: str
    reason: str

    operate_time_seconds: float | None = None
    elapsed_seconds: float = 0.0


@dataclass(frozen=True)
class IDMTLowStage:
    pickup_multiple: float
    tms: float
    curve: IECInverseCurve


@dataclass(frozen=True)
class HighSetStage:
    pickup_multiple: float
    delay_seconds: float


class DualStageIDMTRelay:
    """
    LOW SET  : IEC IDMT
    HIGH SET : definite-time / fast stage
    """

    def __init__(
        self,
        *,
        element: str,
        low: IDMTLowStage,
        high: HighSetStage,
    ):
        if low.pickup_multiple <= 0.0:
            raise ValueError(
                "low pickup must be positive"
            )

        if (
            high.pickup_multiple
            <= low.pickup_multiple
        ):
            raise ValueError(
                "high pickup must exceed "
                "low pickup"
            )

        if high.delay_seconds < 0.0:
            raise ValueError(
                "high-set delay cannot be negative"
            )

        self.element = element

        self.low = low
        self.high = high

        self.low_elapsed_seconds = 0.0
        self.high_elapsed_seconds = 0.0

    def reset(self) -> None:
        self.low_elapsed_seconds = 0.0
        self.high_elapsed_seconds = 0.0

    def evaluate(
        self,
        *,
        measured_multiple: float,
        dt_seconds: float,
    ) -> RelayTrip:

        measured_multiple = float(
            measured_multiple
        )

        dt_seconds = float(dt_seconds)

        if (
            not isfinite(measured_multiple)
            or not isfinite(dt_seconds)
            or dt_seconds < 0.0
        ):
            raise ValueError(
                "invalid relay measurement/time"
            )

        #
        # HIGH SET
        #
        if (
            measured_multiple
            >= self.high.pickup_multiple
        ):
            self.high_elapsed_seconds += (
                dt_seconds
            )

            if (
                self.high_elapsed_seconds
                >= self.high.delay_seconds
            ):
                return RelayTrip(
                    tripped=True,
                    element=self.element,
                    stage="HIGH_SET",
                    reason=(
                        f"{self.element} high-set "
                        f"{measured_multiple:.3f}x >= "
                        f"{self.high.pickup_multiple:.3f}x"
                    ),
                    operate_time_seconds=(
                        self.high.delay_seconds
                    ),
                    elapsed_seconds=(
                        self.high_elapsed_seconds
                    ),
                )
        else:
            self.high_elapsed_seconds = 0.0

        #
        # LOW SET IDMT
        #
        low_multiple = (
            measured_multiple
            / self.low.pickup_multiple
        )

        if low_multiple > 1.0:
            operate_time = (
                iec_operate_time_seconds(
                    multiple=low_multiple,
                    tms=self.low.tms,
                    curve=self.low.curve,
                )
            )

            self.low_elapsed_seconds += (
                dt_seconds
            )

            if (
                self.low_elapsed_seconds
                >= operate_time
            ):
                return RelayTrip(
                    tripped=True,
                    element=self.element,
                    stage="LOW_SET_IDMT",
                    reason=(
                        f"{self.element} IDMT "
                        f"{measured_multiple:.3f}x"
                    ),
                    operate_time_seconds=(
                        operate_time
                    ),
                    elapsed_seconds=(
                        self.low_elapsed_seconds
                    ),
                )
        else:
            self.low_elapsed_seconds = 0.0

        return RelayTrip(
            tripped=False,
            element=self.element,
            stage="NORMAL",
            reason="No trip",
        )


@dataclass(frozen=True)
class DefiniteStage:
    pickup: float
    delay_seconds: float


class DualStageDefiniteRelay:
    """
    Generic two-level protection for functions where
    IEC overcurrent IDMT is not the correct characteristic.
    """

    def __init__(
        self,
        *,
        element: str,
        low: DefiniteStage,
        high: DefiniteStage,
        direction: str = "ABOVE",
    ):
        self.element = element
        self.low = low
        self.high = high

        direction = direction.upper()

        if direction not in {
            "ABOVE",
            "BELOW",
        }:
            raise ValueError(
                "direction must be ABOVE or BELOW"
            )

        self.direction = direction

        self.low_elapsed_seconds = 0.0
        self.high_elapsed_seconds = 0.0

    def _picked_up(
        self,
        value: float,
        pickup: float,
    ) -> bool:

        if self.direction == "ABOVE":
            return value >= pickup

        return value <= pickup

    def evaluate(
        self,
        *,
        value: float,
        dt_seconds: float,
    ) -> RelayTrip:

        value = float(value)
        dt_seconds = float(dt_seconds)

        if self._picked_up(
            value,
            self.high.pickup,
        ):
            self.high_elapsed_seconds += (
                dt_seconds
            )

            if (
                self.high_elapsed_seconds
                >= self.high.delay_seconds
            ):
                return RelayTrip(
                    True,
                    self.element,
                    "HIGH_SET",
                    f"{self.element} high stage",
                    self.high.delay_seconds,
                    self.high_elapsed_seconds,
                )
        else:
            self.high_elapsed_seconds = 0.0

        if self._picked_up(
            value,
            self.low.pickup,
        ):
            self.low_elapsed_seconds += (
                dt_seconds
            )

            if (
                self.low_elapsed_seconds
                >= self.low.delay_seconds
            ):
                return RelayTrip(
                    True,
                    self.element,
                    "LOW_SET",
                    f"{self.element} low stage",
                    self.low.delay_seconds,
                    self.low_elapsed_seconds,
                )
        else:
            self.low_elapsed_seconds = 0.0

        return RelayTrip(
            False,
            self.element,
            "NORMAL",
            "No trip",
        )


@dataclass(frozen=True)
class DifferentialSetting:
    minimum_pickup_pu: float
    slope: float
    high_set_pu: float


class GeneratorDifferentialRelay:
    """
    87G percentage-biased differential protection.

    No IDMT low/high staging is imposed on differential protection.
    """

    def __init__(
        self,
        setting: DifferentialSetting,
    ):
        self.setting = setting

    def evaluate(
        self,
        *,
        differential_current_pu: float,
        restraint_current_pu: float,
    ) -> RelayTrip:

        diff = abs(
            float(differential_current_pu)
        )

        restraint = abs(
            float(restraint_current_pu)
        )

        if diff >= self.setting.high_set_pu:
            return RelayTrip(
                True,
                "ANSI_87G",
                "UNRESTRAINED_HIGH_SET",
                "Generator differential high-set",
            )

        threshold = max(
            self.setting.minimum_pickup_pu,
            self.setting.slope
            * restraint,
        )

        if diff >= threshold:
            return RelayTrip(
                True,
                "ANSI_87G",
                "PERCENTAGE_DIFFERENTIAL",
                "Generator differential operate",
            )

        return RelayTrip(
            False,
            "ANSI_87G",
            "NORMAL",
            "No differential trip",
        )


@dataclass(frozen=True)
class DistanceZone:
    name: str
    reach_ratio: float
    delay_seconds: float


class DistanceProtectionRelay:
    """
    ANSI 21 distance protection.

    apparent_impedance_ratio:
        1.0 = protected-line reference reach.
    """

    def __init__(
        self,
        zones: tuple[
            DistanceZone,
            ...,
        ],
    ):
        self.zones = zones

        self.elapsed = {
            zone.name: 0.0
            for zone in zones
        }

    def reset(self) -> None:
        for name in self.elapsed:
            self.elapsed[name] = 0.0

    def evaluate(
        self,
        *,
        apparent_impedance_ratio: float,
        dt_seconds: float,
    ) -> RelayTrip:

        z = abs(
            float(apparent_impedance_ratio)
        )

        dt_seconds = float(dt_seconds)

        for zone in self.zones:
            if z <= zone.reach_ratio:
                self.elapsed[
                    zone.name
                ] += dt_seconds

                if (
                    self.elapsed[zone.name]
                    >= zone.delay_seconds
                ):
                    return RelayTrip(
                        True,
                        "ANSI_21",
                        zone.name,
                        (
                            f"Distance {zone.name}: "
                            f"Zratio={z:.3f}"
                        ),
                        zone.delay_seconds,
                        self.elapsed[
                            zone.name
                        ],
                    )
            else:
                self.elapsed[
                    zone.name
                ] = 0.0

        return RelayTrip(
            False,
            "ANSI_21",
            "NORMAL",
            "No distance-zone trip",
        )


class Breaker86Lockout:
    """
    ANSI 86 lockout.

    Once a protection trip opens the breaker, it remains open
    until an explicit reset.
    """

    def __init__(self):
        self.breaker_open = False
        self.trip_reason = None

    def trip(
        self,
        reason: str,
    ) -> None:
        self.breaker_open = True
        self.trip_reason = str(reason)

    def reset(self) -> None:
        self.breaker_open = False
        self.trip_reason = None
