"""
STEP 5D-3B - MPC CONSTRAINT STATE SNAPSHOT V1
=============================================

This is the second half of MPC Input #3.

Existing 5D-3:
    verifies that required control/risk state is available.

5D-3B:
    carries the actual deterministic constraint state to MPC.

It does NOT:
- predict direction,
- generate alpha,
- choose trades,
- define risk thresholds,
- override P01D,
- authorize execution.

P01D remains sovereign.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
import argparse
import ast
import math
import re

from mpc_constraint_input_block_v1 import (
    ReleasedConstraintInput,
)


class PositionState(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


class MPCConstraintStateError(RuntimeError):
    pass


@dataclass(frozen=True)
class MPCConstraintSnapshot:
    symbol: str
    asof_utc: datetime
    source_sha256: str

    # Current position / exposure
    position_state: PositionState
    position_quantity: int
    gross_exposure_fraction: float

    # Remaining capacity supplied by control/risk state.
    # No thresholds are defined here.
    risk_budget_available_fraction: float
    turnover_capacity_remaining_fraction: float

    # Cost information supplied at runtime.
    estimated_round_trip_cost_bps: float

    # Deterministic control state.
    session_open: bool
    entry_gate_open: bool
    cooldown_clear: bool
    same_symbol_clear: bool
    position_slot_available: bool
    hard_halt_active: bool

    execution_authorized: bool = False


@dataclass(frozen=True)
class ReleasedMPCConstraintState:
    snapshot: MPCConstraintSnapshot

    eligible_for_mpc: bool
    reason_codes: tuple[str, ...]

    execution_authorized: bool = False


def _finite_nonnegative(
    name: str,
    value: float,
) -> None:

    if not math.isfinite(value):
        raise MPCConstraintStateError(
            f"{name}: NON-FINITE"
        )

    if value < 0.0:
        raise MPCConstraintStateError(
            f"{name}: NEGATIVE"
        )


class MPCConstraintStateBlockV1:

    @classmethod
    def release(
        cls,
        health_gate: ReleasedConstraintInput,
        snapshot: MPCConstraintSnapshot,
    ) -> ReleasedMPCConstraintState:

        # ----------------------------------------------------
        # Existing 5D-3 health gate must pass first.
        # ----------------------------------------------------

        if not health_gate.eligible_for_mpc:
            raise MPCConstraintStateError(
                "CONSTRAINT HEALTH GATE CLOSED"
            )

        if health_gate.execution_authorized:
            raise MPCConstraintStateError(
                "HEALTH GATE MAY NOT AUTHORIZE EXECUTION"
            )

        # ----------------------------------------------------
        # Identity / chronology / provenance.
        # ----------------------------------------------------

        if (
            health_gate.symbol
            != snapshot.symbol
        ):
            raise MPCConstraintStateError(
                "CONSTRAINT SYMBOL MISMATCH"
            )

        if not snapshot.symbol:
            raise MPCConstraintStateError(
                "SYMBOL REQUIRED"
            )

        if snapshot.asof_utc.tzinfo is None:
            raise MPCConstraintStateError(
                "TIMESTAMP MUST BE TIMEZONE-AWARE"
            )

        if not re.fullmatch(
            r"[0-9a-fA-F]{64}",
            snapshot.source_sha256,
        ):
            raise MPCConstraintStateError(
                "CONSTRAINT PROVENANCE INVALID"
            )

        if snapshot.execution_authorized:
            raise MPCConstraintStateError(
                "CONSTRAINT STATE MAY NOT AUTHORIZE EXECUTION"
            )

        # ----------------------------------------------------
        # Position-state consistency.
        # ----------------------------------------------------

        if snapshot.position_quantity < 0:
            raise MPCConstraintStateError(
                "POSITION QUANTITY NEGATIVE"
            )

        if (
            snapshot.position_state
            == PositionState.FLAT
            and snapshot.position_quantity != 0
        ):
            raise MPCConstraintStateError(
                "FLAT POSITION MUST HAVE ZERO QUANTITY"
            )

        if (
            snapshot.position_state
            in {
                PositionState.LONG,
                PositionState.SHORT,
            }
            and snapshot.position_quantity <= 0
        ):
            raise MPCConstraintStateError(
                "NON-FLAT POSITION REQUIRES POSITIVE QUANTITY"
            )

        # ----------------------------------------------------
        # Numerical state validation.
        #
        # No policy thresholds are imposed here.
        # ----------------------------------------------------

        _finite_nonnegative(
            "gross_exposure_fraction",
            snapshot.gross_exposure_fraction,
        )

        _finite_nonnegative(
            "risk_budget_available_fraction",
            snapshot.risk_budget_available_fraction,
        )

        _finite_nonnegative(
            "turnover_capacity_remaining_fraction",
            snapshot.turnover_capacity_remaining_fraction,
        )

        _finite_nonnegative(
            "estimated_round_trip_cost_bps",
            snapshot.estimated_round_trip_cost_bps,
        )

        # ----------------------------------------------------
        # Deterministic control-state evaluation.
        # ----------------------------------------------------

        reasons = []

        if not snapshot.session_open:
            reasons.append(
                "SESSION_CLOSED"
            )

        if not snapshot.entry_gate_open:
            reasons.append(
                "ENTRY_GATE_CLOSED"
            )

        if not snapshot.cooldown_clear:
            reasons.append(
                "COOLDOWN_ACTIVE"
            )

        if not snapshot.same_symbol_clear:
            reasons.append(
                "SAME_SYMBOL_LOCK_ACTIVE"
            )

        if not snapshot.position_slot_available:
            reasons.append(
                "POSITION_SLOT_UNAVAILABLE"
            )

        if snapshot.hard_halt_active:
            reasons.append(
                "HARD_HALT_ACTIVE"
            )

        if (
            snapshot.risk_budget_available_fraction
            <= 0.0
        ):
            reasons.append(
                "NO_RISK_BUDGET_AVAILABLE"
            )

        if (
            snapshot.turnover_capacity_remaining_fraction
            <= 0.0
        ):
            reasons.append(
                "NO_TURNOVER_CAPACITY"
            )

        return ReleasedMPCConstraintState(
            snapshot=snapshot,

            eligible_for_mpc=(
                len(reasons) == 0
            ),

            reason_codes=tuple(
                reasons
            ),

            execution_authorized=False,
        )


# ============================================================
# SECURITY AUDIT
# ============================================================

FORBIDDEN_IMPORTS = {
    "kiteconnect",
}

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

        if isinstance(
            node,
            ast.Import,
        ):
            for item in node.names:
                if item.name in FORBIDDEN_IMPORTS:
                    raise RuntimeError(
                        "BROKER IMPORT PROHIBITED"
                    )

        if isinstance(
            node,
            ast.ImportFrom,
        ):
            if node.module in FORBIDDEN_IMPORTS:
                raise RuntimeError(
                    "BROKER IMPORT PROHIBITED"
                )

        if isinstance(
            node,
            ast.Call,
        ):

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


# ============================================================
# OFFLINE SELF TEST
# ============================================================

def self_test():

    from datetime import timezone

    security_audit()

    now = datetime.now(
        timezone.utc
    )

    health = ReleasedConstraintInput(
        symbol="RELIANCE",
        asof_utc=now,
        eligible_for_mpc=True,
        reason_codes=(),
        execution_authorized=False,
    )

    # --------------------------------------------------------
    # Valid full constraint state.
    # Numerical values here are TEST VALUES ONLY.
    # --------------------------------------------------------

    good = MPCConstraintSnapshot(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        position_state=PositionState.FLAT,
        position_quantity=0,
        gross_exposure_fraction=0.0,

        risk_budget_available_fraction=0.005,
        turnover_capacity_remaining_fraction=1.0,

        estimated_round_trip_cost_bps=5.0,

        session_open=True,
        entry_gate_open=True,
        cooldown_clear=True,
        same_symbol_clear=True,
        position_slot_available=True,
        hard_halt_active=False,
    )

    released = (
        MPCConstraintStateBlockV1.release(
            health,
            good,
        )
    )

    assert released.eligible_for_mpc
    assert not released.execution_authorized

    # --------------------------------------------------------
    # Hard halt must make MPC ineligible.
    # --------------------------------------------------------

    halted = MPCConstraintSnapshot(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        position_state=PositionState.FLAT,
        position_quantity=0,
        gross_exposure_fraction=0.0,

        risk_budget_available_fraction=0.005,
        turnover_capacity_remaining_fraction=1.0,

        estimated_round_trip_cost_bps=5.0,

        session_open=True,
        entry_gate_open=True,
        cooldown_clear=True,
        same_symbol_clear=True,
        position_slot_available=True,
        hard_halt_active=True,
    )

    halted_result = (
        MPCConstraintStateBlockV1.release(
            health,
            halted,
        )
    )

    assert not halted_result.eligible_for_mpc
    assert (
        "HARD_HALT_ACTIVE"
        in halted_result.reason_codes
    )

    # --------------------------------------------------------
    # No risk capacity must fail closed.
    # --------------------------------------------------------

    no_risk = MPCConstraintSnapshot(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        position_state=PositionState.FLAT,
        position_quantity=0,
        gross_exposure_fraction=0.0,

        risk_budget_available_fraction=0.0,
        turnover_capacity_remaining_fraction=1.0,

        estimated_round_trip_cost_bps=5.0,

        session_open=True,
        entry_gate_open=True,
        cooldown_clear=True,
        same_symbol_clear=True,
        position_slot_available=True,
        hard_halt_active=False,
    )

    no_risk_result = (
        MPCConstraintStateBlockV1.release(
            health,
            no_risk,
        )
    )

    assert not no_risk_result.eligible_for_mpc

    assert (
        "NO_RISK_BUDGET_AVAILABLE"
        in no_risk_result.reason_codes
    )

    # --------------------------------------------------------
    # Bad position-state consistency rejected.
    # --------------------------------------------------------

    bad_position = MPCConstraintSnapshot(
        symbol="RELIANCE",
        asof_utc=now,
        source_sha256="c" * 64,

        position_state=PositionState.FLAT,
        position_quantity=10,
        gross_exposure_fraction=0.1,

        risk_budget_available_fraction=0.005,
        turnover_capacity_remaining_fraction=1.0,

        estimated_round_trip_cost_bps=5.0,

        session_open=True,
        entry_gate_open=True,
        cooldown_clear=True,
        same_symbol_clear=True,
        position_slot_available=True,
        hard_halt_active=False,
    )

    rejected = False

    try:
        MPCConstraintStateBlockV1.release(
            health,
            bad_position,
        )

    except RuntimeError:
        rejected = True

    assert rejected

    print("=" * 94)
    print("STEP 5D-3B - MPC CONSTRAINT STATE SNAPSHOT V1")
    print("=" * 94)

    print(
        "5D-3 health-gate dependency       : PASS"
    )

    print(
        "Position-state input              : PASS"
    )

    print(
        "Exposure-state input              : PASS"
    )

    print(
        "Available-risk input              : PASS"
    )

    print(
        "Turnover-capacity input           : PASS"
    )

    print(
        "Transaction-cost input            : PASS"
    )

    print(
        "Session/control-state input       : PASS"
    )

    print(
        "Hard-halt fail-closed             : PASS"
    )

    print(
        "No-risk-capacity fail-closed      : PASS"
    )

    print(
        "Position consistency enforcement  : PASS"
    )

    print(
        "Prediction authority              : NONE"
    )

    print(
        "Direction authority               : NONE"
    )

    print(
        "Execution authority               : FALSE"
    )

    print(
        "Broker authority                  : NONE"
    )

    print()
    print(
        "POLICY THRESHOLDS INVENTED        : NO"
    )

    print(
        "P01D SOVEREIGNTY                  : PRESERVED"
    )

    print("=" * 94)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.self_test:
        raise SystemExit(
            "MPC Constraint State Block library only."
        )

    self_test()


if __name__ == "__main__":
    main()
