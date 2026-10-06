"""
Independent Revision-5 bay feasibility audit.

This module does NOT generate ENTRY/HOLD/EXIT decisions.

It answers only:

    "Is the proposed operating point physically/operationally feasible
     under the frozen bay envelope?"

This intentionally follows the separation used by constrained
unit-commitment systems: strategy proposes; deterministic constraints
audit feasibility.
"""

from __future__ import annotations

from dataclasses import dataclass

from revision5.machine_archetypes import (
    BayOperatingEnvelope,
)


@dataclass(frozen=True)
class BayFeasibilityInput:
    previous_load_fraction: float
    requested_load_fraction: float

    elapsed_online_bars: int
    elapsed_offline_bars: int

    generator_breaker_closed: bool
    machine_available: bool


@dataclass(frozen=True)
class BayFeasibilityResult:
    feasible: bool
    violations: tuple[str, ...]
    effective_maximum_fraction: float


class BayFeasibilityChecker:

    def evaluate(
        self,
        *,
        envelope: BayOperatingEnvelope,
        state: BayFeasibilityInput,
    ) -> BayFeasibilityResult:

        envelope.validate()

        previous = float(
            state.previous_load_fraction
        )

        requested = float(
            state.requested_load_fraction
        )

        if not (
            0.0 <= previous <= 1.0
            and 0.0 <= requested <= 1.0
        ):
            raise ValueError(
                "load fractions must be within [0,1]"
            )

        if (
            state.elapsed_online_bars < 0
            or state.elapsed_offline_bars < 0
        ):
            raise ValueError(
                "elapsed bar counters cannot be negative"
            )

        violations = []

        effective_max = min(
            envelope.maximum_operating_fraction,
            1.0
            - envelope.reserve_headroom_fraction,
        )

        if requested > effective_max + 1e-12:
            violations.append(
                "CAPACITY_OR_RESERVE_LIMIT"
            )

        if (
            requested > 0.0
            and requested
            < envelope.minimum_operating_fraction
        ):
            violations.append(
                "BELOW_MINIMUM_OPERATING_LOAD"
            )

        starting = (
            previous == 0.0
            and requested > 0.0
        )

        stopping = (
            previous > 0.0
            and requested == 0.0
        )

        if starting:

            if (
                state.elapsed_offline_bars
                < envelope.minimum_offline_bars
            ):
                violations.append(
                    "MINIMUM_OFFLINE_TIME"
                )

            if (
                requested
                > envelope.startup_ramp_fraction
                + 1e-12
            ):
                violations.append(
                    "STARTUP_RAMP_LIMIT"
                )

        elif requested > previous:

            if (
                requested - previous
                > envelope.ramp_up_fraction_per_bar
                + 1e-12
            ):
                violations.append(
                    "RAMP_UP_LIMIT"
                )

        if stopping:

            if (
                state.elapsed_online_bars
                < envelope.minimum_online_bars
            ):
                violations.append(
                    "MINIMUM_ONLINE_TIME"
                )

            if (
                previous
                > envelope.shutdown_ramp_fraction
                + 1e-12
            ):
                violations.append(
                    "SHUTDOWN_RAMP_LIMIT"
                )

        elif requested < previous:

            if (
                previous - requested
                > envelope.ramp_down_fraction_per_bar
                + 1e-12
            ):
                violations.append(
                    "RAMP_DOWN_LIMIT"
                )

        if (
            requested > 0.0
            and not state.generator_breaker_closed
        ):
            violations.append(
                "GENERATOR_BREAKER_OPEN"
            )

        if (
            requested > 0.0
            and not state.machine_available
        ):
            violations.append(
                "MACHINE_UNAVAILABLE"
            )

        return BayFeasibilityResult(
            feasible=not violations,
            violations=tuple(violations),
            effective_maximum_fraction=(
                effective_max
            ),
        )
