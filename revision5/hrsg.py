"""
Revision 5 Heat Recovery Steam Generator (HRSG)
================================================

Portfolio-control analogue of CCPP heat recovery.

IMPORTANT:
This module does NOT claim physical thermal efficiency.

Its recovery_fraction, correlation threshold and superheat limits are
frozen Revision-5 design constants pending replay/calibration evidence.

Functions:
1. Recover a controlled fraction of capital from unavailable GTG bays.
2. Route recovered capacity only to healthy steam-turbine bays.
3. Attenuate highly correlated bay allocations.
4. Hold curtailed/damped capital in reserve rather than forcibly
   redeploying it.
5. Recommend bounded steam-bay AVR headroom.

No broker I/O occurs here.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import isfinite, sqrt
from statistics import fmean
from typing import Deque, Dict, Mapping, Optional, Tuple

from revision5.topology import (
    BAY_IDS,
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)


GAS_TURBINE_BAYS = (
    GTG1_HEAVY_INDUSTRY,
    GTG2_TECH_TELECOM,
)

STEAM_TURBINE_BAYS = (
    CSTG1_BFSI,
    CSTG2_CONSUMER_AUTO,
    BPSTG_HEALTHCARE,
)


@dataclass(frozen=True)
class HRSGBalanceResult:
    allocations: Dict[str, float]
    reserve_cash: float
    curtailed_gtg_capital: float
    recovered_capital: float
    correlation_penalties: Dict[str, float]


class HeatRecoverySteamGenerator:
    """
    Execution-neutral HRSG capital-coupling controller.

    recovery_fraction:
        Fraction of curtailed GTG capital made available to healthy STGs.
        This is NOT thermodynamic plant efficiency.

    rho_crit:
        Pairwise return-correlation threshold at which economizer
        attenuation begins.

    min_penalty_factor:
        Maximum allowed correlation derating. 0.70 means a bay may be
        reduced to 70% of its pre-damper allocation.

    max_superheat_boost:
        Maximum recommended STG AVR headroom factor above baseline.
        The plant integration layer must still obey its hard AVR/OEL limit.
    """

    def __init__(
        self,
        *,
        base_plant_capital: float = 1_000_000.0,
        recovery_fraction: float = 0.60,
        rho_crit: float = 0.65,
        min_penalty_factor: float = 0.70,
        max_superheat_boost: float = 0.15,
        correlation_window: int = 30,
        min_correlation_samples: int = 6,
    ):
        if base_plant_capital <= 0:
            raise ValueError(
                "base_plant_capital must be positive"
            )

        if not 0.0 <= recovery_fraction <= 1.0:
            raise ValueError(
                "recovery_fraction must be in [0, 1]"
            )

        if not -1.0 < rho_crit < 1.0:
            raise ValueError(
                "rho_crit must be strictly between -1 and 1"
            )

        if not 0.0 < min_penalty_factor <= 1.0:
            raise ValueError(
                "min_penalty_factor must be in (0, 1]"
            )

        if not 0.0 <= max_superheat_boost <= 1.0:
            raise ValueError(
                "max_superheat_boost must be in [0, 1]"
            )

        if correlation_window < min_correlation_samples:
            raise ValueError(
                "correlation_window must be >= min_correlation_samples"
            )

        if min_correlation_samples < 2:
            raise ValueError(
                "min_correlation_samples must be >= 2"
            )

        self.base_plant_capital = float(
            base_plant_capital
        )

        self.recovery_fraction = float(
            recovery_fraction
        )

        self.rho_crit = float(rho_crit)

        self.min_penalty_factor = float(
            min_penalty_factor
        )

        self.max_superheat_boost = float(
            max_superheat_boost
        )

        self.correlation_window = int(
            correlation_window
        )

        self.min_correlation_samples = int(
            min_correlation_samples
        )

        # Synchronous per-bar/per-observation snapshots.
        # This avoids falsely correlating returns from different timestamps.
        self._return_snapshots: Deque[
            Dict[str, float]
        ] = deque(
            maxlen=self.correlation_window
        )

    @staticmethod
    def _validate_bay(
        bay_id: str,
    ) -> None:
        if bay_id not in BAY_IDS:
            raise ValueError(
                f"Unknown Revision-5 bay: {bay_id!r}"
            )

    def record_return_snapshot(
        self,
        bay_returns: Mapping[str, float],
    ) -> None:
        """
        Record one synchronous return observation.

        Returns are decimal fractions:
            +0.01 = +1%
            -0.01 = -1%
        """
        if not bay_returns:
            raise ValueError(
                "bay_returns cannot be empty"
            )

        snapshot: Dict[str, float] = {}

        for bay_id, value in bay_returns.items():
            self._validate_bay(bay_id)

            value = float(value)

            if not isfinite(value):
                raise ValueError(
                    "bay return must be finite"
                )

            snapshot[bay_id] = value

        self._return_snapshots.append(
            snapshot
        )

    @staticmethod
    def _pearson(
        xs: list[float],
        ys: list[float],
    ) -> float:
        if len(xs) != len(ys):
            raise ValueError(
                "correlation inputs must align"
            )

        if len(xs) < 2:
            return 0.0

        mean_x = fmean(xs)
        mean_y = fmean(ys)

        dx = [
            value - mean_x
            for value in xs
        ]

        dy = [
            value - mean_y
            for value in ys
        ]

        var_x = fmean(
            value * value
            for value in dx
        )

        var_y = fmean(
            value * value
            for value in dy
        )

        if var_x <= 1e-15 or var_y <= 1e-15:
            return 0.0

        covariance = fmean(
            x * y
            for x, y in zip(dx, dy)
        )

        corr = covariance / sqrt(
            var_x * var_y
        )

        return max(
            -1.0,
            min(1.0, corr),
        )

    def calculate_cross_bay_correlations(
        self,
    ) -> Dict[Tuple[str, str], float]:

        result: Dict[
            Tuple[str, str],
            float,
        ] = {}

        for index, bay_a in enumerate(
            BAY_IDS
        ):
            for bay_b in BAY_IDS[
                index + 1:
            ]:
                xs: list[float] = []
                ys: list[float] = []

                for snapshot in (
                    self._return_snapshots
                ):
                    if (
                        bay_a in snapshot
                        and bay_b in snapshot
                    ):
                        xs.append(
                            snapshot[bay_a]
                        )
                        ys.append(
                            snapshot[bay_b]
                        )

                if (
                    len(xs)
                    < self.min_correlation_samples
                ):
                    result[
                        (bay_a, bay_b)
                    ] = 0.0

                    continue

                result[
                    (bay_a, bay_b)
                ] = self._pearson(
                    xs,
                    ys,
                )

        return result

    def compute_economizer_penalties(
        self,
        bay_beta: Optional[
            Mapping[str, float]
        ] = None,
    ) -> Dict[str, float]:
        """
        Return multiplicative allocation factors.

        Without beta information:
            both highly correlated bays are damped.

        With beta information:
            only the higher-beta member of a correlated pair is damped.
            Equal beta values damp both.
        """
        if bay_beta is not None:
            for bay_id, beta in bay_beta.items():
                self._validate_bay(
                    bay_id
                )

                if not isfinite(
                    float(beta)
                ):
                    raise ValueError(
                        "bay beta must be finite"
                    )

        penalties = {
            bay_id: 1.0
            for bay_id in BAY_IDS
        }

        correlations = (
            self.calculate_cross_bay_correlations()
        )

        for (
            bay_a,
            bay_b,
        ), corr in correlations.items():

            if corr <= self.rho_crit:
                continue

            severity = (
                (corr - self.rho_crit)
                / (1.0 - self.rho_crit)
            )

            damper = (
                1.0
                - severity
                * (
                    1.0
                    - self.min_penalty_factor
                )
            )

            damper = max(
                self.min_penalty_factor,
                min(1.0, damper),
            )

            if (
                bay_beta is not None
                and bay_a in bay_beta
                and bay_b in bay_beta
            ):
                beta_a = float(
                    bay_beta[bay_a]
                )

                beta_b = float(
                    bay_beta[bay_b]
                )

                if beta_a > beta_b:
                    penalties[bay_a] = min(
                        penalties[bay_a],
                        damper,
                    )

                elif beta_b > beta_a:
                    penalties[bay_b] = min(
                        penalties[bay_b],
                        damper,
                    )

                else:
                    penalties[bay_a] = min(
                        penalties[bay_a],
                        damper,
                    )

                    penalties[bay_b] = min(
                        penalties[bay_b],
                        damper,
                    )

            else:
                penalties[bay_a] = min(
                    penalties[bay_a],
                    damper,
                )

                penalties[bay_b] = min(
                    penalties[bay_b],
                    damper,
                )

        return penalties

    @staticmethod
    def _cooldown_remaining(
        state: Mapping[str, object],
    ) -> int:
        value = state.get(
            "cooldown_bars_remaining",
            0,
        )

        try:
            remaining = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "cooldown_bars_remaining "
                "must be an integer"
            ) from exc

        if remaining < 0:
            raise ValueError(
                "cooldown_bars_remaining "
                "cannot be negative"
            )

        return remaining

    def _is_unavailable(
        self,
        state: Mapping[str, object],
    ) -> bool:

        return bool(
            state.get(
                "tripped_offline",
                False,
            )
        ) or (
            self._cooldown_remaining(
                state
            )
            > 0
        )

    def balance_capital(
        self,
        *,
        bay_states: Mapping[
            str,
            Mapping[str, object],
        ],
        base_allocations: Mapping[
            str,
            float,
        ],
        bay_beta: Optional[
            Mapping[str, float]
        ] = None,
    ) -> HRSGBalanceResult:
        """
        Apply GTG recovery + covariance damping.

        Conservation rule:

            active allocations + reserve cash
            == base plant capital

        The controller never scales allocations upward merely to force
        100% deployment.
        """
        if set(bay_states) != set(BAY_IDS):
            raise ValueError(
                "bay_states must contain exactly "
                "the five Revision-5 bays"
            )

        if set(base_allocations) != set(
            BAY_IDS
        ):
            raise ValueError(
                "base_allocations must contain "
                "exactly the five Revision-5 bays"
            )

        allocations: Dict[
            str,
            float,
        ] = {}

        for bay_id in BAY_IDS:
            amount = float(
                base_allocations[bay_id]
            )

            if (
                not isfinite(amount)
                or amount < 0.0
            ):
                raise ValueError(
                    "allocations must be "
                    "finite and non-negative"
                )

            allocations[bay_id] = amount

        starting_allocated = sum(
            allocations.values()
        )

        if (
            starting_allocated
            > self.base_plant_capital
            + 1e-6
        ):
            raise ValueError(
                "base allocations exceed "
                "plant capital"
            )

        reserve_cash = (
            self.base_plant_capital
            - starting_allocated
        )

        curtailed_gtg_capital = 0.0
        recovered_capital = 0.0

        # Remove unavailable GTG allocations.
        for gtg_id in GAS_TURBINE_BAYS:
            if not self._is_unavailable(
                bay_states[gtg_id]
            ):
                continue

            source_capital = (
                allocations[gtg_id]
            )

            allocations[gtg_id] = 0.0

            curtailed_gtg_capital += (
                source_capital
            )

            recovered = (
                source_capital
                * self.recovery_fraction
            )

            recovered_capital += recovered

            # Unrecovered portion remains reserve.
            reserve_cash += (
                source_capital
                - recovered
            )

        # An unavailable STG cannot deploy its base allocation.
        # Unlike GTG exhaust recovery, its capital is not harvested
        # into another turbine. It remains safely in plant reserve.
        for stg_id in STEAM_TURBINE_BAYS:
            if not self._is_unavailable(
                bay_states[stg_id]
            ):
                continue

            reserve_cash += allocations[stg_id]
            allocations[stg_id] = 0.0

        # Route recovered GTG capital only to healthy STGs.
        active_stgs = [
            bay_id
            for bay_id in STEAM_TURBINE_BAYS
            if not self._is_unavailable(
                bay_states[bay_id]
            )
        ]

        if recovered_capital > 0.0:
            if not active_stgs:
                reserve_cash += (
                    recovered_capital
                )

            else:
                baseload_total = sum(
                    base_allocations[
                        bay_id
                    ]
                    for bay_id
                    in active_stgs
                )

                if baseload_total > 0.0:
                    for bay_id in (
                        active_stgs
                    ):
                        share = (
                            recovered_capital
                            * base_allocations[
                                bay_id
                            ]
                            / baseload_total
                        )

                        allocations[
                            bay_id
                        ] += share

                else:
                    equal_share = (
                        recovered_capital
                        / len(active_stgs)
                    )

                    for bay_id in (
                        active_stgs
                    ):
                        allocations[
                            bay_id
                        ] += equal_share

        penalties = (
            self.compute_economizer_penalties(
                bay_beta=bay_beta
            )
        )

        # Correlation derating goes to reserve.
        for bay_id in BAY_IDS:
            before = allocations[
                bay_id
            ]

            after = (
                before
                * penalties[bay_id]
            )

            allocations[bay_id] = after
            reserve_cash += (
                before - after
            )

        conserved = (
            sum(allocations.values())
            + reserve_cash
        )

        if abs(
            conserved
            - self.base_plant_capital
        ) > 1e-6:
            raise RuntimeError(
                "HRSG capital conservation "
                "invariant violated"
            )

        return HRSGBalanceResult(
            allocations=allocations,
            reserve_cash=reserve_cash,
            curtailed_gtg_capital=(
                curtailed_gtg_capital
            ),
            recovered_capital=(
                recovered_capital
            ),
            correlation_penalties=(
                penalties
            ),
        )

    def superheat_headroom_factor(
        self,
        *,
        bay_id: str,
        recent_expectancy_r: float,
    ) -> float:
        """
        Return a bounded STG headroom recommendation.

        +0.60 means +0.60R average expectancy,
        NOT a 60% win rate.

        This factor must still be clamped by the hard AVR/OEL
        protection layer when integration occurs.
        """
        self._validate_bay(
            bay_id
        )

        expectancy = float(
            recent_expectancy_r
        )

        if not isfinite(
            expectancy
        ):
            raise ValueError(
                "recent_expectancy_r "
                "must be finite"
            )

        if bay_id not in (
            STEAM_TURBINE_BAYS
        ):
            return 1.0

        if expectancy >= 0.60:
            return (
                1.0
                + self.max_superheat_boost
            )

        if expectancy >= 0.45:
            return (
                1.0
                + 0.50
                * self.max_superheat_boost
            )

        return 1.0
