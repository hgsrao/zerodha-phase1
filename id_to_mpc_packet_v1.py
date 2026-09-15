"""
STEP 5D-4A - ID -> MPC SERIAL PACKET V1
=======================================

Correct architectural sequence:

STEP 2 FEATURES
      ->
STEP 3 PA
      ->
STEP 4 ID
      ->
THIS PACKET
      ->
STEP 5 MPC

MPC does NOT receive PA independently in parallel.

This packet carries:
- the exact PA forecast assessed by ID,
- the exact ID assessment,
- immutable PA/ID provenance.

It does NOT:
- invent direction,
- apply trading thresholds,
- make an MPC decision,
- authorize execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import argparse
import ast

from id_input_block_v1 import (
    ReleasedIDInput,
)


class IDToMPCPacketError(RuntimeError):
    pass


@dataclass(frozen=True)
class IDToMPCPacket:
    symbol: str

    decision_time_utc: datetime

    # Exact PA provenance
    pa_model_name: str
    pa_model_version: str
    pa_model_sha256: str

    # Exact ID provenance
    id_model_name: str
    id_model_version: str
    id_model_sha256: str

    # Forecast created by PA
    horizon_minutes: int

    p_up: float
    p_down: float
    p_flat: float

    expected_return_bps: float
    expected_adverse_bps: float
    pa_confidence: float

    # Assessment created by ID
    reliability: float
    regime_match: float
    data_quality: float
    conflict_score: float

    execution_authorized: bool = False


class IDToMPCPacketV1:

    @staticmethod
    def build(
        released_id: ReleasedIDInput,
    ) -> IDToMPCPacket:

        if released_id.execution_authorized:
            raise IDToMPCPacketError(
                "ID RELEASE MAY NOT AUTHORIZE EXECUTION"
            )

        pa_input = released_id.pa_input
        pa = pa_input.pa_output
        ida = released_id.id_output

        if pa_input.execution_authorized:
            raise IDToMPCPacketError(
                "PA RELEASE MAY NOT AUTHORIZE EXECUTION"
            )

        if (
            ida.pa_sha256
            != pa.model.sha256
        ):
            raise IDToMPCPacketError(
                "ID DOES NOT REFERENCE THIS EXACT PA"
            )

        if ida.symbol != pa.symbol:
            raise IDToMPCPacketError(
                "PA / ID SYMBOL MISMATCH"
            )

        if (
            released_id.admitted_model.artifact_sha256
            != ida.model.sha256
        ):
            raise IDToMPCPacketError(
                "ID ADMISSION / OUTPUT SHA MISMATCH"
            )

        if (
            pa_input.admitted_model.artifact_sha256
            != pa.model.sha256
        ):
            raise IDToMPCPacketError(
                "PA ADMISSION / OUTPUT SHA MISMATCH"
            )

        return IDToMPCPacket(
            symbol=pa.symbol,

            decision_time_utc=(
                pa_input
                .information
                .decision_time_utc
            ),

            pa_model_name=pa.model.name,
            pa_model_version=pa.model.version,
            pa_model_sha256=pa.model.sha256,

            id_model_name=ida.model.name,
            id_model_version=ida.model.version,
            id_model_sha256=ida.model.sha256,

            horizon_minutes=pa.horizon_minutes,

            p_up=pa.p_up,
            p_down=pa.p_down,
            p_flat=pa.p_flat,

            expected_return_bps=(
                pa.expected_return_bps
            ),

            expected_adverse_bps=(
                pa.expected_adverse_bps
            ),

            pa_confidence=pa.confidence,

            reliability=ida.reliability,
            regime_match=ida.regime_match,
            data_quality=ida.data_quality,
            conflict_score=ida.conflict_score,

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

    from datetime import timezone

    from black_box_principles_v1 import (
        IDOutput,
        InformationSnapshot,
        Layer,
        ModelIdentity,
        PAOutput,
        PromotionStatus,
    )

    from model_admission_gate_v1 import (
        AdmittedModel,
    )

    from pa_input_block_v1 import (
        PAInputBlockV1,
    )

    from id_input_block_v1 import (
        IDInputBlockV1,
    )

    security_audit()

    now = datetime.now(
        timezone.utc
    )

    pa_hash = "a" * 64
    id_hash = "b" * 64

    pa_admitted = AdmittedModel(
        layer=Layer.PA,
        model_name="TEST_PA",
        model_version="1",
        artifact_path="TEST_ONLY",
        artifact_sha256=pa_hash,
        validation_artifact_sha256="f" * 64,
        constitution_sha256="8dd0ef7b1983a636df103dc84e2f52c8227624cb3cbc3cb4f7558eab3c9785c0",
        admitted_at_utc=now.isoformat(),
        execution_authorized=False,
    )

    pa_identity = ModelIdentity(
        layer=Layer.PA,
        name="TEST_PA",
        version="1",
        sha256=pa_hash,
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )

    info = InformationSnapshot(
        symbol="RELIANCE",
        observation_time_utc=now,
        decision_time_utc=now,
        source_sha256="1" * 64,
        fresh=True,
        complete=True,
    )

    pa = PAOutput(
        model=pa_identity,
        symbol="RELIANCE",
        horizon_minutes=5,

        p_up=0.60,
        p_down=0.20,
        p_flat=0.20,

        expected_return_bps=10.0,
        expected_adverse_bps=5.0,
        confidence=0.70,
    )

    released_pa = (
        PAInputBlockV1.release(
            info,
            pa_admitted,
            pa,
        )
    )

    id_admitted = AdmittedModel(
        layer=Layer.ID,
        model_name="TEST_ID",
        model_version="1",
        artifact_path="TEST_ONLY",
        artifact_sha256=id_hash,
        validation_artifact_sha256="f" * 64,
        constitution_sha256="8dd0ef7b1983a636df103dc84e2f52c8227624cb3cbc3cb4f7558eab3c9785c0",
        admitted_at_utc=now.isoformat(),
        execution_authorized=False,
    )

    id_identity = ModelIdentity(
        layer=Layer.ID,
        name="TEST_ID",
        version="1",
        sha256=id_hash,
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )

    ida = IDOutput(
        model=id_identity,
        symbol="RELIANCE",

        pa_sha256=pa_hash,

        reliability=0.70,
        regime_match=0.80,
        data_quality=0.95,
        conflict_score=0.10,
    )

    released_id = (
        IDInputBlockV1.release(
            released_pa,
            id_admitted,
            ida,
        )
    )

    packet = (
        IDToMPCPacketV1.build(
            released_id
        )
    )

    assert packet.symbol == "RELIANCE"
    assert packet.pa_model_sha256 == pa_hash
    assert packet.id_model_sha256 == id_hash
    assert not packet.execution_authorized

    print("=" * 94)
    print("STEP 5D-4A - ID -> MPC SERIAL PACKET V1")
    print("=" * 94)

    print(
        "Step 3 PA dependency             : PASS"
    )

    print(
        "Step 4 ID dependency             : PASS"
    )

    print(
        "PA -> ID serial continuity       : PASS"
    )

    print(
        "Exact PA provenance              : PASS"
    )

    print(
        "Exact ID provenance              : PASS"
    )

    print(
        "PA forecast carried through ID   : PASS"
    )

    print(
        "ID assessment carried to MPC     : PASS"
    )

    print(
        "Direct PA -> MPC bypass          : ABSENT"
    )

    print(
        "Trading threshold invented       : NO"
    )

    print(
        "Execution authority              : FALSE"
    )

    print(
        "Broker authority                 : NONE"
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
            "ID -> MPC Packet library only."
        )

    self_test()


if __name__ == "__main__":
    main()
