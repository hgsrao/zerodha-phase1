"""
MPC CONTROLLER V1
=================

Purpose
-------
Pure decision/control layer sitting between future PA/ID models and P01D.

PA answers:
    What is likely to happen?

ID answers:
    How much should we trust that prediction?

MPC answers:
    Given prediction + trust + constraints, what action is worth proposing?

P01D answers:
    Is that proposal actually allowed to execute?

IMPORTANT
---------
- NO broker imports.
- NO Kite API.
- NO order placement.
- NO production authority.
- NO capital authority.
- Outputs are recommendations only.
- P01D remains sovereign.

Current policy constants are ENGINEERING DEFAULTS ONLY.
They are NOT research validated and NOT production frozen.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
import argparse
import ast
import math


# ============================================================
# PUBLIC CONTRACT
# ============================================================

class MPCAction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"
    HOLD = "HOLD"
    NO_TRADE = "NO_TRADE"


@dataclass(frozen=True)
class PAPrediction:
    """
    Output contract expected from future PA model.

    All information must be known at decision time.
    """

    horizon_minutes: int

    p_up: float
    p_down: float
    p_flat: float

    # Signed expected price move:
    # positive = bullish, negative = bearish.
    expected_return_bps: float

    # Expected adverse excursion over the same horizon.
    expected_adverse_bps: float

    # PA's own confidence.
    confidence: float


@dataclass(frozen=True)
class IDAssessment:
    """
    Output contract expected from future ID model.

    ID does NOT predict direction.
    It evaluates whether PA's prediction deserves trust.
    """

    reliability: float
    regime_match: float
    data_quality: float
    conflict_score: float


@dataclass(frozen=True)
class MPCConstraints:
    """
    Non-broker constraints visible to MPC.

    P01D remains the final safety authority even when this says allowed.
    """

    intelligence_enabled: bool = True
    market_data_fresh: bool = True
    controller_enabled: bool = True


@dataclass(frozen=True)
class MPCPolicy:
    """
    ENGINEERING DEFAULTS ONLY.

    These values exist so the controller can be structurally tested.
    They are NOT statistically optimized and are NOT production rules.
    """

    minimum_trust: float = 0.25
    minimum_directional_gap: float = 0.05
    minimum_utility_bps: float = 1.0
    risk_aversion: float = 0.50
    maximum_conflict: float = 0.60


@dataclass(frozen=True)
class MPCDecision:
    action: MPCAction

    horizon_minutes: int

    trust_score: float
    directional_gap: float

    long_utility_bps: float
    short_utility_bps: float
    selected_utility_bps: float

    conviction: float

    reason_codes: tuple[str, ...]

    production_authorized: bool = False
    execution_authorized: bool = False


# ============================================================
# VALIDATION
# ============================================================

def _bounded(name: str, value: float) -> None:
    if not math.isfinite(value):
        raise ValueError(
            f"{name} must be finite"
        )

    if not 0.0 <= value <= 1.0:
        raise ValueError(
            f"{name} must be between 0 and 1"
        )


def validate_pa(pa: PAPrediction) -> None:

    if pa.horizon_minutes <= 0:
        raise ValueError(
            "horizon_minutes must be positive"
        )

    for name, value in [
        ("p_up", pa.p_up),
        ("p_down", pa.p_down),
        ("p_flat", pa.p_flat),
        ("confidence", pa.confidence),
    ]:
        _bounded(
            name,
            value,
        )

    probability_sum = (
        pa.p_up
        + pa.p_down
        + pa.p_flat
    )

    if not math.isclose(
        probability_sum,
        1.0,
        abs_tol=1e-6,
    ):
        raise ValueError(
            "PA probabilities must sum to 1"
        )

    if not math.isfinite(
        pa.expected_return_bps
    ):
        raise ValueError(
            "expected_return_bps must be finite"
        )

    if (
        not math.isfinite(
            pa.expected_adverse_bps
        )
        or
        pa.expected_adverse_bps < 0
    ):
        raise ValueError(
            "expected_adverse_bps must be finite and non-negative"
        )


def validate_id(
    ida: IDAssessment,
) -> None:

    for name, value in [
        ("reliability", ida.reliability),
        ("regime_match", ida.regime_match),
        ("data_quality", ida.data_quality),
        ("conflict_score", ida.conflict_score),
    ]:
        _bounded(
            name,
            value,
        )


# ============================================================
# MPC CORE
# ============================================================

class MPCControllerV1:

    def __init__(
        self,
        policy: MPCPolicy | None = None,
    ):
        self.policy = (
            policy
            if policy is not None
            else MPCPolicy()
        )

    def decide(
        self,
        pa: PAPrediction,
        ida: IDAssessment,
        constraints: MPCConstraints,
    ) -> MPCDecision:

        validate_pa(pa)
        validate_id(ida)

        # ----------------------------------------------------
        # Gate 1: external/controller availability
        # ----------------------------------------------------

        if not constraints.controller_enabled:
            return self._no_trade(
                pa,
                "CONTROLLER_DISABLED",
            )

        if not constraints.intelligence_enabled:
            return self._no_trade(
                pa,
                "INTELLIGENCE_DISABLED",
            )

        if not constraints.market_data_fresh:
            return self._no_trade(
                pa,
                "STALE_MARKET_DATA",
            )

        # ----------------------------------------------------
        # Step 1: ID-derived trust
        #
        # Trust falls if:
        # - PA lacks confidence
        # - ID reliability is weak
        # - regime does not match
        # - data quality is weak
        # - signals conflict
        # ----------------------------------------------------

        trust = (
            pa.confidence
            *
            ida.reliability
            *
            ida.regime_match
            *
            ida.data_quality
            *
            (1.0 - ida.conflict_score)
        )

        directional_gap = abs(
            pa.p_up - pa.p_down
        )

        # ----------------------------------------------------
        # Gate 2: prediction quality
        # ----------------------------------------------------

        reasons = []

        if (
            ida.conflict_score
            > self.policy.maximum_conflict
        ):
            reasons.append(
                "EXCESSIVE_SIGNAL_CONFLICT"
            )

        if (
            trust
            < self.policy.minimum_trust
        ):
            reasons.append(
                "INSUFFICIENT_TRUST"
            )

        if (
            directional_gap
            <
            self.policy.minimum_directional_gap
        ):
            reasons.append(
                "INSUFFICIENT_DIRECTIONAL_SEPARATION"
            )

        if reasons:
            return MPCDecision(
                action=MPCAction.NO_TRADE,
                horizon_minutes=pa.horizon_minutes,
                trust_score=float(trust),
                directional_gap=float(
                    directional_gap
                ),
                long_utility_bps=0.0,
                short_utility_bps=0.0,
                selected_utility_bps=0.0,
                conviction=0.0,
                reason_codes=tuple(
                    reasons
                ),
            )

        # ----------------------------------------------------
        # Step 2: MPC objective
        #
        # PA supplies signed expected return.
        # ID determines how strongly we trust it.
        # Expected adverse excursion is penalized.
        #
        # This is intentionally simple in V1.
        # ----------------------------------------------------

        trusted_edge = (
            trust
            *
            pa.expected_return_bps
        )

        risk_penalty = (
            self.policy.risk_aversion
            *
            pa.expected_adverse_bps
        )

        long_utility = (
            trusted_edge
            -
            risk_penalty
        )

        short_utility = (
            -trusted_edge
            -
            risk_penalty
        )

        # ----------------------------------------------------
        # Step 3: control action selection
        # ----------------------------------------------------

        best_utility = max(
            0.0,
            long_utility,
            short_utility,
        )

        if (
            best_utility
            <
            self.policy.minimum_utility_bps
        ):
            return MPCDecision(
                action=MPCAction.HOLD,
                horizon_minutes=pa.horizon_minutes,
                trust_score=float(trust),
                directional_gap=float(
                    directional_gap
                ),
                long_utility_bps=float(
                    long_utility
                ),
                short_utility_bps=float(
                    short_utility
                ),
                selected_utility_bps=float(
                    best_utility
                ),
                conviction=0.0,
                reason_codes=(
                    "NO_ACTION_HAS_SUFFICIENT_UTILITY",
                ),
            )

        if (
            long_utility
            >
            short_utility
        ):
            action = MPCAction.LONG
            selected = long_utility
            direction_probability = pa.p_up

        else:
            action = MPCAction.SHORT
            selected = short_utility
            direction_probability = pa.p_down

        # ----------------------------------------------------
        # Step 4: conviction
        #
        # Conviction is informational only.
        # It is NOT position sizing authority.
        # ----------------------------------------------------

        conviction = min(
            1.0,
            max(
                0.0,
                trust
                *
                direction_probability
                *
                (
                    directional_gap
                    + 0.5
                ),
            ),
        )

        return MPCDecision(
            action=action,
            horizon_minutes=pa.horizon_minutes,
            trust_score=float(trust),
            directional_gap=float(
                directional_gap
            ),
            long_utility_bps=float(
                long_utility
            ),
            short_utility_bps=float(
                short_utility
            ),
            selected_utility_bps=float(
                selected
            ),
            conviction=float(
                conviction
            ),
            reason_codes=(
                "MPC_ACTION_RECOMMENDATION",
                "P01D_AUTHORIZATION_REQUIRED",
            ),
            production_authorized=False,
            execution_authorized=False,
        )

    @staticmethod
    def _no_trade(
        pa: PAPrediction,
        reason: str,
    ) -> MPCDecision:

        return MPCDecision(
            action=MPCAction.NO_TRADE,
            horizon_minutes=pa.horizon_minutes,
            trust_score=0.0,
            directional_gap=0.0,
            long_utility_bps=0.0,
            short_utility_bps=0.0,
            selected_utility_bps=0.0,
            conviction=0.0,
            reason_codes=(
                reason,
            ),
            production_authorized=False,
            execution_authorized=False,
        )


# ============================================================
# SECURITY SELF-AUDIT
# ============================================================

FORBIDDEN_IMPORTS = {
    "kiteconnect",
}

FORBIDDEN_CALL_NAMES = {
    "place_order",
    "modify_order",
    "cancel_order",
}


def source_security_audit() -> None:

    path = Path(__file__).resolve()

    tree = ast.parse(
        path.read_text(
            encoding="utf-8",
        )
    )

    for node in ast.walk(tree):

        if isinstance(
            node,
            ast.Import,
        ):
            for alias in node.names:
                if (
                    alias.name
                    in FORBIDDEN_IMPORTS
                ):
                    raise RuntimeError(
                        "Forbidden broker import"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):
            if (
                node.module
                in FORBIDDEN_IMPORTS
            ):
                raise RuntimeError(
                    "Forbidden broker import"
                )

        if isinstance(
            node,
            ast.Call,
        ):
            fn = node.func

            if isinstance(
                fn,
                ast.Attribute,
            ):
                name = fn.attr

            elif isinstance(
                fn,
                ast.Name,
            ):
                name = fn.id

            else:
                name = None

            if (
                name
                in FORBIDDEN_CALL_NAMES
            ):
                raise RuntimeError(
                    "Forbidden broker-write primitive"
                )


# ============================================================
# OFFLINE SELF TEST
# ============================================================

def self_test() -> None:

    source_security_audit()

    controller = MPCControllerV1(
        policy=MPCPolicy(
            minimum_trust=0.20,
            minimum_directional_gap=0.05,
            minimum_utility_bps=1.0,
            risk_aversion=0.25,
            maximum_conflict=0.60,
        )
    )

    # --------------------------------------------------------
    # Strong bullish case
    # --------------------------------------------------------

    long_case = controller.decide(
        PAPrediction(
            horizon_minutes=5,
            p_up=0.70,
            p_down=0.15,
            p_flat=0.15,
            expected_return_bps=12.0,
            expected_adverse_bps=4.0,
            confidence=0.90,
        ),
        IDAssessment(
            reliability=0.90,
            regime_match=0.90,
            data_quality=0.95,
            conflict_score=0.05,
        ),
        MPCConstraints(),
    )

    assert (
        long_case.action
        == MPCAction.LONG
    )

    assert (
        not long_case.execution_authorized
    )

    # --------------------------------------------------------
    # Strong bearish case
    # --------------------------------------------------------

    short_case = controller.decide(
        PAPrediction(
            horizon_minutes=5,
            p_up=0.10,
            p_down=0.75,
            p_flat=0.15,
            expected_return_bps=-14.0,
            expected_adverse_bps=4.0,
            confidence=0.95,
        ),
        IDAssessment(
            reliability=0.90,
            regime_match=0.90,
            data_quality=0.95,
            conflict_score=0.05,
        ),
        MPCConstraints(),
    )

    assert (
        short_case.action
        == MPCAction.SHORT
    )

    # --------------------------------------------------------
    # Weak/conflicted information must fail closed
    # --------------------------------------------------------

    weak_case = controller.decide(
        PAPrediction(
            horizon_minutes=5,
            p_up=0.36,
            p_down=0.34,
            p_flat=0.30,
            expected_return_bps=2.0,
            expected_adverse_bps=8.0,
            confidence=0.40,
        ),
        IDAssessment(
            reliability=0.40,
            regime_match=0.40,
            data_quality=0.90,
            conflict_score=0.50,
        ),
        MPCConstraints(),
    )

    assert weak_case.action in {
        MPCAction.NO_TRADE,
        MPCAction.HOLD,
    }

    # --------------------------------------------------------
    # Stale market data must fail closed
    # --------------------------------------------------------

    stale_case = controller.decide(
        PAPrediction(
            horizon_minutes=5,
            p_up=0.80,
            p_down=0.10,
            p_flat=0.10,
            expected_return_bps=20.0,
            expected_adverse_bps=3.0,
            confidence=0.99,
        ),
        IDAssessment(
            reliability=0.99,
            regime_match=0.99,
            data_quality=0.99,
            conflict_score=0.0,
        ),
        MPCConstraints(
            market_data_fresh=False,
        ),
    )

    assert (
        stale_case.action
        == MPCAction.NO_TRADE
    )

    # --------------------------------------------------------
    # Invalid probabilities must be rejected
    # --------------------------------------------------------

    rejected = False

    try:
        controller.decide(
            PAPrediction(
                horizon_minutes=5,
                p_up=0.8,
                p_down=0.8,
                p_flat=0.2,
                expected_return_bps=10.0,
                expected_adverse_bps=5.0,
                confidence=0.9,
            ),
            IDAssessment(
                reliability=0.9,
                regime_match=0.9,
                data_quality=0.9,
                conflict_score=0.1,
            ),
            MPCConstraints(),
        )

    except ValueError:
        rejected = True

    assert rejected

    print("=" * 90)
    print("MPC CONTROLLER V1 - SELF TEST")
    print("=" * 90)
    print("Source security audit       : PASS")
    print("Broker imports              : NONE")
    print("Broker-write authority      : NONE")
    print("PA input contract           : PASS")
    print("ID input contract           : PASS")
    print("LONG decision path          : PASS")
    print("SHORT decision path         : PASS")
    print("Weak-signal fail-closed     : PASS")
    print("Stale-data fail-closed      : PASS")
    print("Malformed input rejection   : PASS")
    print("Execution authority         : FALSE")
    print("Production authority        : FALSE")
    print("P01D sovereignty            : PRESERVED")
    print()
    print("POLICY STATUS:")
    print("ENGINEERING DEFAULTS - NOT RESEARCH VALIDATED")
    print("=" * 90)


def main() -> None:

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if args.self_test:
        self_test()
        return

    raise SystemExit(
        "MPC Controller V1 is a library module. "
        "Use --self-test for offline verification."
    )


if __name__ == "__main__":
    main()
