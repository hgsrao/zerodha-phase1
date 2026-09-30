#!/usr/bin/env python3
"""Deterministic two-node coordinator for R5 Step-5 calibration.

Validation mode does not load market data and does not execute candidates.
Stage-A execution is deliberately not enabled by this initial sealed version.
"""

from __future__ import annotations

import argparse
import os
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import optuna


ROOT = Path(__file__).resolve().parents[1]


def _executor():
    # The Stage-A executor owns the remote layout and remote engine-identity rules.
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "r5_step5_stage_a_executor", ROOT / "scripts" / "run_r5_step5_stage_a_executor.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()

    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)

    return h.hexdigest()


def run(
    command: list[str],
    *,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=check,
    )


def suggest_params(
    trial: optuna.Trial,
    protocol: dict,
) -> dict:
    params = {}

    for name in protocol["search_surface"]:
        spec = protocol["search_space"][name]

        if spec["type"] == "int":
            value = trial.suggest_int(
                name,
                int(spec["minimum"]),
                int(spec["maximum"]),
            )

        elif spec["type"] == "float":
            value = trial.suggest_float(
                name,
                float(spec["minimum"]),
                float(spec["maximum"]),
            )

        else:
            raise ValueError(
                f"unsupported search type for {name}: "
                f"{spec['type']}"
            )

        params[name] = value

    return params


def create_first_batch(
    protocol: dict,
) -> list[dict]:
    optimizer = protocol["optimizer"]

    sampler = optuna.samplers.TPESampler(
        seed=int(optimizer["seed"]),
        n_startup_trials=int(
            optimizer["n_startup_trials"]
        ),
    )

    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
    )

    defaults = {
        name: protocol["search_space"][name]["default"]
        for name in protocol["search_surface"]
    }

    study.enqueue_trial(defaults)

    batch = []

    for _ in range(
        int(optimizer["candidate_parallelism"])
    ):
        trial = study.ask()

        params = suggest_params(
            trial,
            protocol,
        )

        batch.append({
            "trial_number": trial.number,
            "params": params,
        })

    return batch


def validate_registry_contract(
    protocol: dict,
) -> None:
    from canonical_parameter_registry import (
        CanonicalParameterRegistry,
    )

    registry = CanonicalParameterRegistry()
    registry.verify_frozen_identity()

    expected_registry = protocol[
        "registry_identity_sha256"
    ]

    if (
        registry.FROZEN_IDENTITY_SHA256
        != expected_registry
    ):
        raise SystemExit(
            "REGISTRY_IDENTITY: FAIL"
        )

    if set(protocol["search_surface"]) != set(
        protocol["search_space"]
    ):
        raise SystemExit(
            "SEARCH_SPACE_KEYS: FAIL"
        )

    for name in protocol["search_surface"]:
        spec = registry.get(name)
        sealed = protocol["search_space"][name]

        if not registry.is_calibratable(
            name,
            registry.ENGINE_EXTERNAL,
        ):
            raise SystemExit(
                f"SEARCH_PARAMETER_NOT_CALIBRATABLE: {name}"
            )

        observed = {
            "type": spec.param_type,
            "default": spec.default,
            "minimum": spec.minimum,
            "maximum": spec.maximum,
        }

        if observed != sealed:
            raise SystemExit(
                f"SEARCH_SPACE_DRIFT: {name}: "
                f"{observed} != {sealed}"
            )


def validate_protocol_contract(
    protocol: dict,
) -> None:
    opt = protocol["optimizer"]

    required = {
        "jobs": 2,
        "candidate_parallelism": 2,
        "n_startup_trials": 2,
        "seed": 20260924,
        "trials": 8,
        "scheduler":
            "synchronous_two_trial_batches",
    }

    for key, expected in required.items():
        if opt.get(key) != expected:
            raise SystemExit(
                f"OPTIMIZER_CONTRACT_FAIL: "
                f"{key}={opt.get(key)!r}, "
                f"expected={expected!r}"
            )

    if len(
        protocol["sampling_plan"]["stage_a"]
    ) != 3:
        raise SystemExit(
            "STAGE_A_BLOCK_COUNT: FAIL"
        )

    if len(
        protocol["sampling_plan"]["stage_b"]
    ) != 6:
        raise SystemExit(
            "STAGE_B_BLOCK_COUNT: FAIL"
        )

    if protocol["contamination_rules"][
        "2026_selection_data"
    ]:
        raise SystemExit(
            "2026_SELECTION_GUARD: FAIL"
        )

    if protocol["engine"]["live_trading"]:
        raise SystemExit(
            "LIVE_TRADING_GUARD: FAIL"
        )

    if protocol["engine"][
        "broker_network_access"
    ]:
        raise SystemExit(
            "BROKER_NETWORK_GUARD: FAIL"
        )


def validate_first_batch(
    protocol: dict,
) -> list[dict]:
    first = create_first_batch(protocol)
    second = create_first_batch(protocol)

    if first != second:
        raise SystemExit(
            "FIRST_BATCH_DETERMINISM: FAIL"
        )

    if [x["trial_number"] for x in first] != [0, 1]:
        raise SystemExit(
            "FIRST_BATCH_TRIAL_NUMBERS: FAIL"
        )

    defaults = {
        name: protocol["search_space"][name]["default"]
        for name in protocol["search_surface"]
    }

    if first[0]["params"] != defaults:
        raise SystemExit(
            "TRIAL_ZERO_DEFAULTS: FAIL"
        )

    return first


def remote_text(
    ssh_key: Path,
    laptop: str,
    command: str,
) -> str:
    result = run([
        "ssh",
        "-i",
        str(ssh_key),
        "-o",
        "BatchMode=yes",
        laptop,
        command,
    ])

    return result.stdout.strip()


def main() -> None:
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--protocol",
        default=str(
            ROOT
            / "revision5"
            / "step5_sealed_calibration_protocol.json"
        ),
    )

    parser.add_argument(
        "--expected-protocol-sha",
        required=True,
    )

    parser.add_argument(
        "--worker",
        default=str(
            ROOT
            / "scripts"
            / "run_r5_step5_candidate.py"
        ),
    )

    parser.add_argument(
        "--ssh-key",
        required=True,
    )

    parser.add_argument(
        "--laptop",
        default=os.environ.get("R5_WORKER_SSH_TARGET"),
    )

    parser.add_argument(
        "--remote-worker",
        default="/tmp/run_r5_step5_candidate.py",
        help="legacy layout only; a remote worktree uses its own scripts/ copy",
    )

    parser.add_argument(
        "--remote-protocol",
        default="/tmp/step5_sealed_calibration_protocol.json",
        help="legacy layout only; a remote worktree uses its own revision5/ copy",
    )

    parser.add_argument(
        "--remote-root",
        default=None,
        help=("remote worktree the laptop worker runs from; default: the protocol's "
              "distributed_execution.remote_worker_root, else the legacy V1/V2 layout"),
    )

    parser.add_argument(
        "--validate-only",
        action="store_true",
    )

    args = parser.parse_args()

    if not args.validate_only:
        raise SystemExit(
            "STAGE-A EXECUTION IS NOT ENABLED "
            "IN THIS COORDINATOR YET"
        )

    protocol_path = Path(
        args.protocol
    ).resolve()

    worker_path = Path(
        args.worker
    ).resolve()

    ssh_key = Path(
        args.ssh_key
    ).resolve()

    protocol_sha = sha256_file(
        protocol_path
    )

    if (
        protocol_sha
        != args.expected_protocol_sha
    ):
        raise SystemExit(
            "PROTOCOL_SHA: FAIL "
            f"{protocol_sha}"
        )

    protocol = json.loads(
        protocol_path.read_text()
    )

    hostname = subprocess.check_output(
        ["hostname"],
        text=True,
    ).strip()

    expected_host = protocol[
        "distributed_execution"
    ]["coordinator"]

    if hostname != expected_host:
        raise SystemExit(
            f"COORDINATOR_HOST: FAIL "
            f"{hostname} != {expected_host}"
        )

    validate_protocol_contract(
        protocol
    )

    validate_registry_contract(
        protocol
    )

    first_batch = validate_first_batch(
        protocol
    )

    local_worker_sha = sha256_file(
        worker_path
    )

    remote_host = remote_text(
        ssh_key,
        args.laptop,
        "hostname",
    )

    if (
        remote_host
        != protocol["distributed_execution"][
            "secondary_worker"
        ]
    ):
        raise SystemExit(
            "REMOTE_HOST_IDENTITY: FAIL"
        )

    executor = _executor()
    layout = executor.resolve_remote_layout(
        ssh_key, args.laptop, protocol, protocol_path, protocol_sha, args.remote_root)
    if layout["mode"] == "worktree":
        args.remote_worker = layout["worker"]
        args.remote_protocol = layout["protocol"]

    remote_worker_sha = remote_text(
        ssh_key,
        args.laptop,
        f"sha256sum {args.remote_worker} | awk '{{print $1}}'",
    )

    remote_protocol_sha = remote_text(
        ssh_key,
        args.laptop,
        f"sha256sum {args.remote_protocol} | awk '{{print $1}}'",
    )


    if remote_worker_sha != local_worker_sha:
        raise SystemExit(
            "REMOTE_WORKER_PARITY: FAIL"
        )

    if remote_protocol_sha != protocol_sha:
        raise SystemExit(
            "REMOTE_PROTOCOL_PARITY: FAIL"
        )

    # Checked in the directory the remote worker will run from.
    remote_commit = executor.verify_remote_engine(
        ssh_key, args.laptop, layout, protocol["frozen_parent"]["commit"])

    print(
        json.dumps(
            {
                "mode": "VALIDATE_ONLY",
                "protocol_sha256":
                    protocol_sha,
                "worker_sha256":
                    local_worker_sha,
                "coordinator_host":
                    hostname,
                "remote_host":
                    remote_host,
                "remote_engine_parent":
                    remote_commit,
                "first_batch":
                    first_batch,
            },
            indent=2,
            sort_keys=True,
        )
    )

    print()
    print("PROTOCOL_CONTRACT: PASS")
    print("REGISTRY_CONTRACT: PASS")
    print("TRIAL_ZERO_DEFAULTS: PASS")
    print("FIRST_BATCH_DETERMINISM: PASS")
    print("REMOTE_WORKER_PARITY: PASS")
    print("REMOTE_PROTOCOL_PARITY: PASS")
    print("REMOTE_ENGINE_PARENT: PASS")
    print("NO_MARKET_REPLAY_EXECUTED: PASS")


if __name__ == "__main__":
    main()
