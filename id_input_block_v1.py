"""
STEP 5D-2 - ID INPUT BLOCK V1
=============================

Responsibility:
    Accept only an assessment belonging to an admitted ID model
    and tied to the exact released PA model.

ID judges reliability.
ID may NOT invent direction.
ID may NOT authorize execution.
"""

from dataclasses import dataclass
from pathlib import Path
import argparse
import ast

from black_box_principles_v1 import (
    BlackBoxConstitutionV1,
    IDOutput,
    Layer,
    ModelIdentity,
    PromotionStatus,
)

from model_admission_gate_v1 import (
    AdmittedModel,
)

from pa_input_block_v1 import (
    ReleasedPAInput,
)


class IDInputBlockError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleasedIDInput:
    pa_input: ReleasedPAInput
    admitted_model: AdmittedModel
    id_output: IDOutput
    execution_authorized: bool = False


class IDInputBlockV1:

    @staticmethod
    def release(
        pa_input,
        admitted_model,
        id_output,
    ):

        if admitted_model.layer != Layer.ID:
            raise IDInputBlockError(
                "ADMISSION IS NOT ID"
            )

        if id_output.model.layer != Layer.ID:
            raise IDInputBlockError(
                "OUTPUT IS NOT ID"
            )

        if (
            admitted_model.artifact_sha256
            != id_output.model.sha256
        ):
            raise IDInputBlockError(
                "ID MODEL SHA MISMATCH"
            )

        if (
            admitted_model.model_name
            != id_output.model.name
        ):
            raise IDInputBlockError(
                "ID MODEL NAME MISMATCH"
            )

        if (
            admitted_model.model_version
            != id_output.model.version
        ):
            raise IDInputBlockError(
                "ID MODEL VERSION MISMATCH"
            )

        if (
            id_output.pa_sha256
            != pa_input.pa_output.model.sha256
        ):
            raise IDInputBlockError(
                "ID IS NOT ASSESSING THIS EXACT PA"
            )

        if (
            id_output.symbol
            != pa_input.pa_output.symbol
        ):
            raise IDInputBlockError(
                "PA / ID SYMBOL MISMATCH"
            )

        if admitted_model.execution_authorized:
            raise IDInputBlockError(
                "ID ADMISSION MAY NOT EXECUTE"
            )

        BlackBoxConstitutionV1.validate_id(
            id_output,
            pa_input.pa_output,
        )

        return ReleasedIDInput(
            pa_input=pa_input,
            admitted_model=admitted_model,
            id_output=id_output,
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

    from datetime import datetime, timezone

    from black_box_principles_v1 import (
        InformationSnapshot,
        PAOutput,
    )

    from pa_input_block_v1 import (
        PAInputBlockV1,
    )

    security_audit()

    now = datetime.now(timezone.utc)

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

    released_pa = PAInputBlockV1.release(
        info,
        pa_admitted,
        pa,
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

    released_id = IDInputBlockV1.release(
        released_pa,
        id_admitted,
        ida,
    )

    assert not released_id.execution_authorized

    bad_id = IDOutput(
        model=id_identity,
        symbol="RELIANCE",
        pa_sha256="9" * 64,
        reliability=0.70,
        regime_match=0.80,
        data_quality=0.95,
        conflict_score=0.10,
    )

    rejected = False

    try:
        IDInputBlockV1.release(
            released_pa,
            id_admitted,
            bad_id,
        )
    except RuntimeError:
        rejected = True

    assert rejected

    print("=" * 90)
    print("STEP 5D-2 - ID INPUT BLOCK V1")
    print("=" * 90)
    print("Released PA dependency     : PASS")
    print("Admitted ID binding        : PASS")
    print("ID model SHA continuity    : PASS")
    print("Exact PA reference         : PASS")
    print("PA/ID symbol continuity    : PASS")
    print("Wrong-PA rejection         : PASS")
    print("Execution authority        : FALSE")
    print("Broker authority           : NONE")
    print()
    print("REAL ID MODEL              : NONE")
    print("REAL DISCRIMINATION        : NONE")
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
            "ID Input Block library only."
        )

    self_test()


if __name__ == "__main__":
    main()
