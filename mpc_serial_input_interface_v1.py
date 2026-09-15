"""
STEP 5D-4B - MPC SERIAL INPUT INTERFACE V1
==========================================

Correct Step-5 direct inputs:

INPUT 1:
    Step-4 ID output packet.
    This already contains the exact PA forecast that ID assessed.

INPUT 2:
    Full deterministic MPC constraint state.

Therefore:

STEP 2 -> PA -> ID -> MPC
                    ^
                    |
               CONSTRAINTS

PA is NOT independently connected to MPC.

This module only establishes the corrected interface.
It contains NO MPC trading policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import ast
import hashlib

from id_to_mpc_packet_v1 import (
    IDToMPCPacket,
)

from mpc_constraint_state_snapshot_v1 import (
    ReleasedMPCConstraintState,
)


ROOT = Path(__file__).resolve().parent

PA_BLOCK = (
    ROOT / "pa_input_block_v1.py"
)

ID_BLOCK = (
    ROOT / "id_input_block_v1.py"
)

CONSTRAINT_HEALTH_BLOCK = (
    ROOT / "mpc_constraint_input_block_v1.py"
)

CONSTRAINT_STATE_BLOCK = (
    ROOT / "mpc_constraint_state_snapshot_v1.py"
)


EXPECTED_PA_SHA256 = (
    "7df3cebc6296c1a2d7147b0499f3bb75"
    "d8d5486f6cd0cbdb50795486150ef528"
)

EXPECTED_ID_SHA256 = (
    "fa938b731a250972216b4a2e0181269f"
    "51f2fbba528fa2ab87799cecf491fa49"
)

EXPECTED_CONSTRAINT_HEALTH_SHA256 = (
    "a812ccc856c5ae275d92c2d324875e2e"
    "1526382bfd97a3026d217111818a0f91"
)

EXPECTED_CONSTRAINT_STATE_SHA256 = (
    "ece2008f4b936a0be11a65048ff8ee11"
    "005b33acc9c224784c9f0d8a9bdc80b3"
)


class MPCSerialInterfaceError(RuntimeError):
    pass


@dataclass(frozen=True)
class MPCSerialInputEnvelope:

    # Direct intelligence input to MPC:
    # Step-4 ID packet containing the PA forecast.
    intelligence: IDToMPCPacket

    # Separate control-state input.
    constraints: ReleasedMPCConstraintState

    mpc_input_ready: bool

    execution_authorized: bool = False


def sha256_file(path):

    h = hashlib.sha256()

    with path.open("rb") as f:
        for block in iter(
            lambda: f.read(1024 * 1024),
            b"",
        ):
            h.update(block)

    return h.hexdigest()


def verify_frozen_inputs():

    expected = {
        PA_BLOCK:
            EXPECTED_PA_SHA256,

        ID_BLOCK:
            EXPECTED_ID_SHA256,

        CONSTRAINT_HEALTH_BLOCK:
            EXPECTED_CONSTRAINT_HEALTH_SHA256,

        CONSTRAINT_STATE_BLOCK:
            EXPECTED_CONSTRAINT_STATE_SHA256,
    }

    for path, expected_sha in expected.items():

        if not path.exists():
            raise MPCSerialInterfaceError(
                f"FROZEN INPUT MISSING: {path.name}"
            )

        actual = sha256_file(
            path
        )

        if actual != expected_sha:
            raise MPCSerialInterfaceError(
                f"FROZEN INPUT HASH MISMATCH: {path.name}"
            )


class MPCSerialInputInterfaceV1:

    @classmethod
    def connect(
        cls,
        intelligence: IDToMPCPacket,
        constraints: ReleasedMPCConstraintState,
    ) -> MPCSerialInputEnvelope:

        verify_frozen_inputs()

        # ----------------------------------------------------
        # PA cannot bypass ID because MPC accepts only the
        # Step-4 serial packet, not PAOutput.
        # ----------------------------------------------------

        if intelligence.execution_authorized:
            raise MPCSerialInterfaceError(
                "INTELLIGENCE INPUT MAY NOT AUTHORIZE EXECUTION"
            )

        if constraints.execution_authorized:
            raise MPCSerialInterfaceError(
                "CONSTRAINT INPUT MAY NOT AUTHORIZE EXECUTION"
            )

        if not constraints.eligible_for_mpc:
            raise MPCSerialInterfaceError(
                "CONSTRAINT INPUT FAIL-CLOSED"
            )

        if (
            intelligence.symbol
            != constraints.snapshot.symbol
        ):
            raise MPCSerialInterfaceError(
                "INTELLIGENCE / CONSTRAINT SYMBOL MISMATCH"
            )

        if not intelligence.pa_model_sha256:
            raise MPCSerialInterfaceError(
                "PA PROVENANCE MISSING"
            )

        if not intelligence.id_model_sha256:
            raise MPCSerialInterfaceError(
                "ID PROVENANCE MISSING"
            )

        return MPCSerialInputEnvelope(
            intelligence=intelligence,
            constraints=constraints,
            mpc_input_ready=True,
            execution_authorized=False,
        )


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


def self_test():

    from datetime import (
        datetime,
        timezone,
    )

    from id_to_mpc_packet_v1 import (
        IDToMPCPacket,
    )

    from mpc_constraint_state_snapshot_v1 import (
        MPCConstraintSnapshot,
        PositionState,
        ReleasedMPCConstraintState,
    )

    security_audit()
    verify_frozen_inputs()

    now = datetime.now(
        timezone.utc
    )

    intelligence = IDToMPCPacket(
        symbol="RELIANCE",

        decision_time_utc=now,

        pa_model_name="TEST_PA",
        pa_model_version="1",
        pa_model_sha256="a" * 64,

        id_model_name="TEST_ID",
        id_model_version="1",
        id_model_sha256="b" * 64,

        horizon_minutes=5,

        p_up=0.60,
        p_down=0.20,
        p_flat=0.20,

        expected_return_bps=10.0,
        expected_adverse_bps=5.0,
        pa_confidence=0.70,

        reliability=0.70,
        regime_match=0.80,
        data_quality=0.95,
        conflict_score=0.10,

        execution_authorized=False,
    )

    snapshot = MPCConstraintSnapshot(
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

        execution_authorized=False,
    )

    constraints = ReleasedMPCConstraintState(
        snapshot=snapshot,
        eligible_for_mpc=True,
        reason_codes=(),
        execution_authorized=False,
    )

    envelope = (
        MPCSerialInputInterfaceV1.connect(
            intelligence,
            constraints,
        )
    )

    assert envelope.mpc_input_ready
    assert not envelope.execution_authorized

    # --------------------------------------------------------
    # Symbol mismatch must fail.
    # --------------------------------------------------------

    bad_snapshot = MPCConstraintSnapshot(
        symbol="SBIN",
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

        execution_authorized=False,
    )

    bad_constraints = (
        ReleasedMPCConstraintState(
            snapshot=bad_snapshot,
            eligible_for_mpc=True,
            reason_codes=(),
            execution_authorized=False,
        )
    )

    rejected = False

    try:
        MPCSerialInputInterfaceV1.connect(
            intelligence,
            bad_constraints,
        )

    except RuntimeError:
        rejected = True

    assert rejected

    # --------------------------------------------------------
    # Closed constraint state must fail.
    # --------------------------------------------------------

    closed_constraints = (
        ReleasedMPCConstraintState(
            snapshot=snapshot,
            eligible_for_mpc=False,
            reason_codes=(
                "HARD_HALT_ACTIVE",
            ),
            execution_authorized=False,
        )
    )

    rejected = False

    try:
        MPCSerialInputInterfaceV1.connect(
            intelligence,
            closed_constraints,
        )

    except RuntimeError:
        rejected = True

    assert rejected

    print("=" * 98)
    print("STEP 5D-4B - MPC SERIAL INPUT INTERFACE V1")
    print("=" * 98)

    print(
        "Frozen PA block integrity          : PASS"
    )

    print(
        "Frozen ID block integrity          : PASS"
    )

    print(
        "Frozen constraint-health integrity : PASS"
    )

    print(
        "Frozen constraint-state integrity  : PASS"
    )

    print()
    print(
        "Step 2 -> PA                       : UPSTREAM CONTRACT"
    )

    print(
        "PA -> ID                           : SERIAL / ENFORCED"
    )

    print(
        "ID -> MPC                          : CONNECTED"
    )

    print(
        "Direct PA -> MPC                   : ABSENT"
    )

    print(
        "Constraint State -> MPC            : CONNECTED"
    )

    print()
    print(
        "MPC DIRECT INPUT COUNT             : 2"
    )

    print(
        "  Input 1                          : ID-TO-MPC PACKET"
    )

    print(
        "  Input 2                          : CONSTRAINT STATE"
    )

    print()
    print(
        "Symbol continuity                  : PASS"
    )

    print(
        "Closed constraints fail-closed     : PASS"
    )

    print(
        "MPC trading policy                 : NOT IMPLEMENTED"
    )

    print(
        "Real PA model                      : NONE"
    )

    print(
        "Real ID model                      : NONE"
    )

    print(
        "Execution authority                : FALSE"
    )

    print(
        "Broker authority                   : NONE"
    )

    print(
        "P01D sovereignty                   : PRESERVED"
    )

    print("=" * 98)


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--self-test",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.self_test:
        raise SystemExit(
            "MPC Serial Input Interface library only."
        )

    self_test()


if __name__ == "__main__":
    main()
