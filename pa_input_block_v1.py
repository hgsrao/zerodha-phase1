"""
STEP 5D-1 - PA INPUT BLOCK V1
=============================

Responsibility:
    Accept only information + output belonging to an admitted PA model.

PA may predict.
PA may NOT decide.
PA may NOT authorize execution.
"""

from dataclasses import dataclass
from pathlib import Path
import argparse
import ast
import hashlib

from black_box_principles_v1 import (
    BlackBoxConstitutionV1,
    InformationSnapshot,
    Layer,
    ModelIdentity,
    PAOutput,
    PromotionStatus,
)

from model_admission_gate_v1 import (
    AdmittedModel,
)


ROOT = Path(__file__).resolve().parent

CONSTITUTION = ROOT / "black_box_principles_v1.py"

EXPECTED_CONSTITUTION_SHA256 = (
    "8dd0ef7b1983a636df103dc84e2f52c8"
    "227624cb3cbc3cb4f7558eab3c9785c0"
)


class PAInputBlockError(RuntimeError):
    pass


@dataclass(frozen=True)
class ReleasedPAInput:
    information: InformationSnapshot
    admitted_model: AdmittedModel
    pa_output: PAOutput
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


class PAInputBlockV1:

    @staticmethod
    def release(
        information,
        admitted_model,
        pa_output,
    ):

        if (
            sha256_file(CONSTITUTION)
            != EXPECTED_CONSTITUTION_SHA256
        ):
            raise PAInputBlockError(
                "CONSTITUTION HASH MISMATCH"
            )

        BlackBoxConstitutionV1.validate_information(
            information
        )

        if admitted_model.layer != Layer.PA:
            raise PAInputBlockError(
                "ADMISSION IS NOT PA"
            )

        if pa_output.model.layer != Layer.PA:
            raise PAInputBlockError(
                "OUTPUT IS NOT PA"
            )

        if (
            admitted_model.artifact_sha256
            != pa_output.model.sha256
        ):
            raise PAInputBlockError(
                "PA MODEL SHA MISMATCH"
            )

        if (
            admitted_model.model_name
            != pa_output.model.name
        ):
            raise PAInputBlockError(
                "PA MODEL NAME MISMATCH"
            )

        if (
            admitted_model.model_version
            != pa_output.model.version
        ):
            raise PAInputBlockError(
                "PA MODEL VERSION MISMATCH"
            )

        if information.symbol != pa_output.symbol:
            raise PAInputBlockError(
                "INFORMATION / PA SYMBOL MISMATCH"
            )

        if admitted_model.execution_authorized:
            raise PAInputBlockError(
                "PA ADMISSION MAY NOT EXECUTE"
            )

        BlackBoxConstitutionV1.validate_pa(
            pa_output
        )

        return ReleasedPAInput(
            information=information,
            admitted_model=admitted_model,
            pa_output=pa_output,
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

    security_audit()

    now = datetime.now(timezone.utc)

    model_hash = "a" * 64

    admitted = AdmittedModel(
        layer=Layer.PA,
        model_name="TEST_PA",
        model_version="1",
        artifact_path="TEST_ONLY",
        artifact_sha256=model_hash,
        validation_artifact_sha256="f" * 64,
        constitution_sha256=EXPECTED_CONSTITUTION_SHA256,
        admitted_at_utc=now.isoformat(),
        execution_authorized=False,
    )

    identity = ModelIdentity(
        layer=Layer.PA,
        name="TEST_PA",
        version="1",
        sha256=model_hash,
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
        model=identity,
        symbol="RELIANCE",
        horizon_minutes=5,
        p_up=0.60,
        p_down=0.20,
        p_flat=0.20,
        expected_return_bps=10.0,
        expected_adverse_bps=5.0,
        confidence=0.70,
    )

    released = PAInputBlockV1.release(
        info,
        admitted,
        pa,
    )

    assert not released.execution_authorized

    # Wrong model identity must fail.
    bad_identity = ModelIdentity(
        layer=Layer.PA,
        name="TEST_PA",
        version="1",
        sha256="9" * 64,
        promotion_status=PromotionStatus.PROMOTED,
        validated_out_of_sample=True,
    )

    bad_pa = PAOutput(
        model=bad_identity,
        symbol="RELIANCE",
        horizon_minutes=5,
        p_up=0.60,
        p_down=0.20,
        p_flat=0.20,
        expected_return_bps=10.0,
        expected_adverse_bps=5.0,
        confidence=0.70,
    )

    rejected = False

    try:
        PAInputBlockV1.release(
            info,
            admitted,
            bad_pa,
        )
    except RuntimeError:
        rejected = True

    assert rejected

    print("=" * 90)
    print("STEP 5D-1 - PA INPUT BLOCK V1")
    print("=" * 90)
    print("Information boundary       : PASS")
    print("Admitted PA binding        : PASS")
    print("PA model SHA continuity    : PASS")
    print("PA prediction contract     : PASS")
    print("Wrong-model rejection      : PASS")
    print("Execution authority        : FALSE")
    print("Broker authority           : NONE")
    print()
    print("REAL PA MODEL              : NONE")
    print("REAL PREDICTION            : NONE")
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
            "PA Input Block library only."
        )

    self_test()


if __name__ == "__main__":
    main()
