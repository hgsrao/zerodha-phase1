"""
STEP 5D-3 - MPC CONSTRAINT INPUT BLOCK V1
=========================================

Responsibility:
    Supply deterministic runtime eligibility / state constraints
    to the MPC layer.

This block:
- does NOT predict,
- does NOT create market direction,
- does NOT authorize trades,
- does NOT replace P01D risk authority.

Unknown state fails closed.
"""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import argparse
import ast
import re


class ConstraintInputError(RuntimeError):
    pass


@dataclass(frozen=True)
class MPCConstraintState:
    symbol: str
    asof_utc: datetime

    source_sha256: str

    controller_enabled: bool
    intelligence_enabled: bool
    market_data_fresh: bool

    p01d_state_available: bool
    position_state_known: bool
    risk_state_known: bool
    session_state_known: bool
    transaction_cost_state_known: bool

    execution_authorized: bool = False


@dataclass(frozen=True)
class ReleasedConstraintInput:
    symbol: str
    asof_utc: datetime

    eligible_for_mpc: bool
    reason_codes: tuple[str, ...]

    execution_authorized: bool = False


class MPCConstraintInputBlockV1:

    @staticmethod
    def release(
        state: MPCConstraintState,
    ) -> ReleasedConstraintInput:

        if not state.symbol:
            raise ConstraintInputError(
                "SYMBOL REQUIRED"
            )

        if state.asof_utc.tzinfo is None:
            raise ConstraintInputError(
                "CONSTRAINT TIMESTAMP MUST BE TIMEZONE-AWARE"
            )

        if not re.fullmatch(
            r"[0-9a-fA-F]{64}",
            state.source_sha256,
        ):
            raise ConstraintInputError(
                "CONSTRAINT SOURCE PROVENANCE INVALID"
            )

        if state.execution_authorized:
            raise ConstraintInputError(
                "CONSTRAINT BLOCK MAY NOT AUTHORIZE EXECUTION"
            )

        reasons = []

        if not state.controller_enabled:
            reasons.append(
                "CONTROLLER_DISABLED"
            )

        if not state.intelligence_enabled:
            reasons.append(
                "INTELLIGENCE_DISABLED"
            )

        if not state.market_data_fresh:
            reasons.append(
                "STALE_MARKET_DATA"
            )

        if not state.p01d_state_available:
            reasons.append(
                "P01D_STATE_UNAVAILABLE"
            )

        if not state.position_state_known:
            reasons.append(
                "POSITION_STATE_UNKNOWN"
            )

        if not state.risk_state_known:
            reasons.append(
                "RISK_STATE_UNKNOWN"
            )

        if not state.session_state_known:
            reasons.append(
                "SESSION_STATE_UNKNOWN"
            )

        if not state.transaction_cost_state_known:
            reasons.append(
                "TRANSACTION_COST_STATE_UNKNOWN"
            )

        return ReleasedConstraintInput(
            symbol=state.symbol,
            asof_utc=state.asof_utc,
            eligible_for_mpc=(
                len(reasons) == 0
            ),
            reason_codes=tuple(reasons),
            execution_authorized=False,
        )


FORBIDDEN_IMPORTS = {"kiteconnect"}
FORBIDDEN_CALLS = {
    "place_order",
    "modify_order",
    "cancel_order",
}


def security_audit():

    tree = ast.parse(
        Path(__file__).read_text(
            encoding="utf-8"
        )
    )

    for node in ast.walk(tree):

        if isinstance(node, ast.Import):
            for item in node.names:
                if item.name in FORBIDDEN_IMPORTS:
                    raise RuntimeError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(node, ast.ImportFrom):
            if node.module in FORBIDDEN_IMPORTS:
                raise RuntimeError(
                    "BROKER IMPORT PROHIBITED"
                )

        if isinstance(node, ast.Call):

            if isinstance(
                node.func,
                ast.Attribute,
            ):
                name = node.func.attr
            elif isinstance(
                node.func,
                ast.Name,
            ):
                name = node.func.id
            else:
                name = ""

            if name in FORBIDDEN_CALLS:
                raise RuntimeError(
                    "BROKER WRITE PROHIBITED"
                )


def self_test():

    from datetime import timezone

    security_audit()

    now = datetime.now(timezone.utc)

    good = MPCConstraintState(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        controller_enabled=True,
        intelligence_enabled=True,
        market_data_fresh=True,

        p01d_state_available=True,
        position_state_known=True,
        risk_state_known=True,
        session_state_known=True,
        transaction_cost_state_known=True,
    )

    released = (
        MPCConstraintInputBlockV1.release(
            good
        )
    )

    assert released.eligible_for_mpc
    assert not released.execution_authorized

    stale = MPCConstraintState(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        controller_enabled=True,
        intelligence_enabled=True,
        market_data_fresh=False,

        p01d_state_available=True,
        position_state_known=True,
        risk_state_known=True,
        session_state_known=True,
        transaction_cost_state_known=True,
    )

    stale_result = (
        MPCConstraintInputBlockV1.release(
            stale
        )
    )

    assert not stale_result.eligible_for_mpc
    assert (
        "STALE_MARKET_DATA"
        in stale_result.reason_codes
    )

    unknown_risk = MPCConstraintState(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        controller_enabled=True,
        intelligence_enabled=True,
        market_data_fresh=True,

        p01d_state_available=True,
        position_state_known=True,
        risk_state_known=False,
        session_state_known=True,
        transaction_cost_state_known=True,
    )

    unknown_result = (
        MPCConstraintInputBlockV1.release(
            unknown_risk
        )
    )

    assert not unknown_result.eligible_for_mpc
    assert (
        "RISK_STATE_UNKNOWN"
        in unknown_result.reason_codes
    )

    print("=" * 90)
    print("STEP 5D-3 - MPC CONSTRAINT INPUT BLOCK V1")
    print("=" * 90)
    print("Deterministic state input  : PASS")
    print("Healthy-state release      : PASS")
    print("Stale-data fail-closed     : PASS")
    print("Unknown-risk fail-closed   : PASS")
    print("P01D state dependency      : ENFORCED")
    print("Prediction authority       : NONE")
    print("Direction authority        : NONE")
    print("Execution authority        : FALSE")
    print("Broker authority           : NONE")
    print("=" * 90)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--self-test",
        action="store_true",
    )
    args = parser.parse_args()

    if not args.self_test:
        raise SystemExit(
            "Constraint Input Block library only."
        )

    self_test()


if __name__ == "__main__":
    main()
