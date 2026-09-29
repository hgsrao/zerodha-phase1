#!/usr/bin/env python3
"""Crash-resumable synchronous two-node R5 Step-5 Stage-A executor.

PLAN-ONLY mode:
- does not load historical market data,
- does not invoke candidate replay,
- does not create calibration state,
- only reconstructs and validates the next deterministic Optuna batch.

EXECUTE mode:
- dispatches whole independent candidates, never symbol shards,
- lower trial number -> desktop,
- higher trial number -> laptop,
- waits for both members of a synchronous batch,
- tells Optuna only in ascending trial-number order,
- persists batch/result state atomically for restart/resume.
"""

from __future__ import annotations

import argparse
import hashlib
import fcntl
import json
import os
import shlex
import time
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import optuna


ROOT = Path(__file__).resolve().parents[1]

REMOTE_WORKER = "/tmp/run_r5_step5_candidate.py"
REMOTE_PROTOCOL = "/tmp/step5_sealed_calibration_protocol.json"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)

    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    blob = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    ).encode()

    return hashlib.sha256(blob).hexdigest()


def command_output(args: list[str]) -> str:
    return subprocess.check_output(
        args,
        text=True,
    ).strip()


def run_checked(
    args: list[str],
    *,
    stdout=None,
    stderr=None,
) -> None:
    subprocess.run(
        args,
        check=True,
        text=True,
        stdout=stdout,
        stderr=stderr,
    )


def atomic_json(path: Path, value: Any) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fd, temp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )

    try:
        with os.fdopen(fd, "w") as handle:
            json.dump(
                value,
                handle,
                indent=2,
                sort_keys=True,
                default=str,
            )

            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        os.replace(
            temp_name,
            path,
        )

    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def verify_baseline_ancestry(protocol: dict) -> None:
    # The sealed engine commit named by the protocol itself must be an ancestor of HEAD.
    # (V1 anchored on the tagged tooling commit 096f8e7; V2 anchors on its own frozen
    # engine parent, so one executor serves every protocol version.)
    baseline = protocol["frozen_parent"]["commit"]

    result = subprocess.run(
        [
            "git",
            "-C",
            str(ROOT),
            "merge-base",
            "--is-ancestor",
            baseline,
            "HEAD",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    if result.returncode != 0:
        raise SystemExit(
            "BASELINE_ANCESTRY: FAIL"
        )


def remote_text(
    ssh_key: Path,
    laptop: str,
    command: str,
) -> str:
    return command_output([
        "ssh",
        "-i",
        str(ssh_key),
        "-o",
        "BatchMode=yes",
        "-o", "ConnectTimeout=10",
        "-o", "ServerAliveInterval=15",
        "-o", "ServerAliveCountMax=2",
        laptop,
        command,
    ])


def detect_remote_python(
    ssh_key: Path,
    laptop: str,
) -> str:
    command = r'''
if [ -x "$HOME/.venvs/zerodha-phase1-r5-worker/bin/python" ]; then
    printf "%s\n" "$HOME/.venvs/zerodha-phase1-r5-worker/bin/python"
elif [ -x "$HOME/.venvs/zerodha-phase1-r5/bin/python" ]; then
    printf "%s\n" "$HOME/.venvs/zerodha-phase1-r5/bin/python"
else
    exit 20
fi
'''

    value = remote_text(
        ssh_key,
        laptop,
        command,
    )

    if not value.startswith("/"):
        raise SystemExit(
            "REMOTE_PYTHON: FAIL"
        )

    return value


def verify_remote_control_plane(
    ssh_key: Path,
    laptop: str,
    protocol_sha: str,
    worker_sha: str,
    expected_remote_host: str,
    expected_engine_commit: str,
) -> dict:
    remote_host = remote_text(
        ssh_key,
        laptop,
        "hostname",
    )

    if remote_host != expected_remote_host:
        raise SystemExit(
            f"REMOTE_HOST: FAIL "
            f"{remote_host!r}"
        )

    remote_commit = remote_text(
        ssh_key,
        laptop,
        'cd "$HOME/projects/zerodha-phase1" '
        '&& git rev-parse HEAD',
    )

    if not remote_commit.startswith(expected_engine_commit):
        raise SystemExit(
            f"REMOTE_ENGINE_PARENT: FAIL "
            f"{remote_commit}"
        )

    remote_worker_sha = remote_text(
        ssh_key,
        laptop,
        f"sha256sum {REMOTE_WORKER} "
        "| awk '{print $1}'",
    )

    if remote_worker_sha != worker_sha:
        raise SystemExit(
            "REMOTE_WORKER_PARITY: FAIL"
        )

    remote_protocol_sha = remote_text(
        ssh_key,
        laptop,
        f"sha256sum {REMOTE_PROTOCOL} "
        "| awk '{print $1}'",
    )

    if remote_protocol_sha != protocol_sha:
        raise SystemExit(
            "REMOTE_PROTOCOL_PARITY: FAIL"
        )

    remote_python = detect_remote_python(
        ssh_key,
        laptop,
    )

    return {
        "remote_host": remote_host,
        "remote_engine_parent":
            remote_commit,
        "remote_python":
            remote_python,
        "remote_worker_sha256":
            remote_worker_sha,
        "remote_protocol_sha256":
            remote_protocol_sha,
    }


def suggest_params(
    trial: optuna.Trial,
    protocol: dict,
) -> dict:
    params = {}

    for name in protocol["search_surface"]:
        spec = protocol["search_space"][name]

        if spec["type"] == "int":
            params[name] = trial.suggest_int(
                name,
                int(spec["minimum"]),
                int(spec["maximum"]),
            )

        elif spec["type"] == "float":
            params[name] = trial.suggest_float(
                name,
                float(spec["minimum"]),
                float(spec["maximum"]),
            )

        else:
            raise SystemExit(
                f"UNSUPPORTED_PARAMETER_TYPE: "
                f"{name}"
            )

    return params


def create_study(
    protocol: dict,
) -> optuna.Study:
    opt = protocol["optimizer"]

    sampler = optuna.samplers.TPESampler(
        seed=int(opt["seed"]),
        n_startup_trials=int(
            opt["n_startup_trials"]
        ),
    )

    study = optuna.create_study(
        direction="maximize",
        sampler=sampler,
    )

    defaults = {
        name:
            protocol["search_space"][name][
                "default"
            ]
        for name in protocol["search_surface"]
    }

    study.enqueue_trial(
        defaults
    )

    return study


def validate_state_identity(
    state: dict,
    protocol_sha: str,
    worker_sha: str,
    protocol: dict,
) -> None:
    expected = {
        "version": 1,
        "stage": "A",
        "protocol_sha256":
            protocol_sha,
        "worker_sha256":
            worker_sha,
        "optimizer_seed":
            int(
                protocol["optimizer"]["seed"]
            ),
        "trial_count":
            int(
                protocol["optimizer"]["trials"]
            ),
        "batch_size":
            int(
                protocol["optimizer"][
                    "candidate_parallelism"
                ]
            ),
    }

    for key, value in expected.items():
        if state.get(key) != value:
            raise SystemExit(
                f"STATE_IDENTITY_FAIL: "
                f"{key}: "
                f"{state.get(key)!r} "
                f"!= {value!r}"
            )


def fresh_state(
    protocol_sha: str,
    worker_sha: str,
    protocol: dict,
) -> dict:
    return {
        "version": 1,
        "stage": "A",
        "protocol_sha256":
            protocol_sha,
        "worker_sha256":
            worker_sha,
        "optimizer_seed":
            int(
                protocol["optimizer"]["seed"]
            ),
        "trial_count":
            int(
                protocol["optimizer"]["trials"]
            ),
        "batch_size":
            int(
                protocol["optimizer"][
                    "candidate_parallelism"
                ]
            ),
        "batches": [],
    }


def reconstruct_study(
    protocol: dict,
    state: dict,
) -> tuple[
    optuna.Study,
    dict | None,
]:
    study = create_study(
        protocol
    )

    incomplete = None

    batches = sorted(
        state["batches"],
        key=lambda row: row["batch_index"],
    )

    for batch in batches:
        specs = sorted(
            batch["trials"],
            key=lambda row:
                row["trial_number"],
        )

        generated = []

        for spec in specs:
            trial = study.ask()

            params = suggest_params(
                trial,
                protocol,
            )

            if trial.number != spec[
                "trial_number"
            ]:
                raise SystemExit(
                    "STATE_TRIAL_NUMBER_DRIFT"
                )

            if params != spec["params"]:
                raise SystemExit(
                    "STATE_PARAMETER_DRIFT"
                )

            generated.append(
                (
                    trial,
                    spec,
                )
            )

        if batch["status"] == "COMPLETE":
            for trial, spec in generated:
                if spec.get("score") is None:
                    raise SystemExit(
                        "COMPLETE_TRIAL_WITHOUT_SCORE"
                    )

                study.tell(
                    trial,
                    float(spec["score"]),
                )

        elif batch["status"] == "PLANNED":
            if incomplete is not None:
                raise SystemExit(
                    "MULTIPLE_INCOMPLETE_BATCHES"
                )

            incomplete = batch

        else:
            raise SystemExit(
                f"INVALID_BATCH_STATUS: "
                f"{batch['status']}"
            )

    return study, incomplete


def propose_next_batch(
    study: optuna.Study,
    protocol: dict,
    state: dict,
) -> dict | None:
    trial_count = int(
        protocol["optimizer"]["trials"]
    )

    existing = sum(
        len(batch["trials"])
        for batch in state["batches"]
    )

    if existing >= trial_count:
        return None

    batch_size = int(
        protocol["optimizer"][
            "candidate_parallelism"
        ]
    )

    batch_index = (
        len(state["batches"])
    )

    trials = []

    for offset in range(batch_size):
        if existing + offset >= trial_count:
            break

        trial = study.ask()

        params = suggest_params(
            trial,
            protocol,
        )

        node = (
            "desktop"
            if offset == 0
            else "laptop"
        )

        trials.append({
            "trial_number":
                trial.number,
            "assigned_node":
                node,
            "params":
                params,
            "params_sha256":
                canonical_hash(params),
            "status":
                "PLANNED",
            "score":
                None,
            "candidate_sha256":
                None,
        })

    return {
        "batch_index":
            batch_index,
        "status":
            "PLANNED",
        "trials":
            trials,
    }


def next_batch_plan(
    protocol: dict,
    state: dict,
) -> dict | None:
    study, incomplete = reconstruct_study(
        protocol,
        state,
    )

    if incomplete is not None:
        return incomplete

    return propose_next_batch(
        study,
        protocol,
        state,
    )


def validate_candidate_result(
    result: dict,
    spec: dict,
    protocol_sha: str,
    worker_sha: str,
) -> None:
    if result.get("mode") != "CANDIDATE_EVALUATION":
        raise SystemExit(
            "RESULT_MODE: FAIL"
        )

    if result.get("stage") != "A":
        raise SystemExit(
            "RESULT_STAGE: FAIL"
        )

    if (
        result.get("protocol_sha256")
        != protocol_sha
    ):
        raise SystemExit(
            "RESULT_PROTOCOL_SHA: FAIL"
        )

    if (
        result.get("worker_sha256")
        != worker_sha
    ):
        raise SystemExit(
            "RESULT_WORKER_SHA: FAIL"
        )

    if result.get("params") != spec["params"]:
        raise SystemExit(
            "RESULT_PARAMS: FAIL"
        )

    aggregate = result["aggregate"]

    if int(
        aggregate["safety_violations"]
    ) != 0:
        raise SystemExit(
            f"SAFETY_VIOLATION_TRIAL_"
            f"{spec['trial_number']}"
        )

    if not aggregate["rankable"]:
        raise SystemExit(
            "NON_RANKABLE_WITH_ZERO_SAFETY: FAIL"
        )

    if aggregate["score"] is None:
        raise SystemExit(
            "RESULT_SCORE_MISSING"
        )


def load_valid_existing_result(
    result_path: Path,
    spec: dict,
    protocol_sha: str,
    worker_sha: str,
) -> dict | None:
    if not result_path.is_file():
        return None

    result = json.loads(result_path.read_text())
    validate_candidate_result(result, spec, protocol_sha, worker_sha)

    return result


def prepare_params_files(
    state_dir: Path,
    batch: dict,
) -> dict[int, Path]:
    params_dir = (
        state_dir
        / "params"
    )

    params_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    output = {}

    for spec in batch["trials"]:
        number = int(
            spec["trial_number"]
        )

        path = (
            params_dir
            / f"trial_{number:03d}.json"
        )

        atomic_json(
            path,
            spec["params"],
        )

        if (
            sha256_file(path)
            != hashlib.sha256(
                (
                    json.dumps(
                        spec["params"],
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n"
                ).encode()
            ).hexdigest()
        ):
            raise SystemExit(
                "PARAM_FILE_WRITE: FAIL"
            )

        output[number] = path

    return output


def result_path(
    state_dir: Path,
    trial_number: int,
) -> Path:
    return (
        state_dir
        / "results"
        / f"trial_{trial_number:03d}.json"
    )


def log_path(
    state_dir: Path,
    trial_number: int,
    node: str,
) -> Path:
    return (
        state_dir
        / "logs"
        / f"trial_{trial_number:03d}.{node}.log"
    )


def launch_local(
    spec: dict,
    params_path: Path,
    result: Path,
    log: Path,
    protocol_path: Path,
    protocol_sha: str,
    worker_path: Path,
):
    result.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    log.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    result.unlink(
        missing_ok=True,
    )

    handle = log.open("w")

    process = subprocess.Popen(
        [
            sys.executable,
            str(worker_path),
            "--root",
            str(ROOT),
            "--protocol",
            str(protocol_path),
            "--expected-protocol-sha",
            protocol_sha,
            "--stage",
            "A",
            "--params",
            str(params_path),
            "--output",
            str(result),
        ],
        stdout=handle,
        stderr=subprocess.STDOUT,
        text=True,
    )

    return process, handle


# Runs on the laptop using only the standard library. A durable claim precedes
# spawning: a lost SSH acknowledgement cannot cause a second candidate launch.
REMOTE_CONTROL = r"""
import json, os, pathlib, subprocess, sys
job = json.loads(sys.argv[1])
action = sys.argv[2]
base = pathlib.Path(job["job_path"])
result = pathlib.Path(job["result_path"])

def emit(status, **extra):
    print(json.dumps(dict(status=status, **extra)), flush=True)

if base.exists():
    saved = json.loads((base / "identity.json").read_text())
    if saved != job:
        raise SystemExit("REMOTE_JOB_IDENTITY_MISMATCH")
    if result.is_file():
        emit("COMPLETE")
    elif (base / "exit.json").exists():
        emit("FAILED", **json.loads((base / "exit.json").read_text()))
    elif (base / "pid.json").exists():
        pid = json.loads((base / "pid.json").read_text())["pid"]
        try:
            cmd = pathlib.Path("/proc", str(pid), "cmdline").read_bytes()
            alive = str(base).encode() in cmd
        except FileNotFoundError:
            alive = False
        emit("RUNNING" if alive else "AMBIGUOUS", pid=pid)
    else:
        emit("AMBIGUOUS")
elif result.is_file():
    # Recover results produced by the original foreground executor as well.
    emit("COMPLETE")
elif action == "probe":
    emit("ABSENT")
else:
    base.mkdir()  # Exclusive claim; never silently retry a partial launch.
    with (base / "identity.json").open("x") as f:
        json.dump(job, f)
        f.flush()
        os.fsync(f.fileno())
    runner = r'''
import json, os, pathlib, subprocess, sys
job = json.loads(sys.argv[1])
base = pathlib.Path(job["job_path"])
def save(name, value):
    tmp = base / (name + ".tmp")
    with tmp.open("w") as f:
        json.dump(value, f)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, base / name)
save("pid.json", {"pid": os.getpid()})
try:
    rc = subprocess.call(job["argv"], stdin=subprocess.DEVNULL)
    if rc == 0:
        os.replace(job["pending_path"], job["result_path"])
    save("exit.json", {"returncode": rc})
except BaseException as exc:
    save("exit.json", {"returncode": -1, "error": str(exc)})
'''
    with open(job["log_path"], "ab", buffering=0) as log:
        proc = subprocess.Popen([sys.executable, "-c", runner, json.dumps(job)],
                                stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                start_new_session=True, close_fds=True)
    emit("RUNNING", pid=proc.pid)
"""


def remote_job_status(job, ssh_key, laptop, remote_python, action="probe"):
    command = shlex.join([
        remote_python, "-c", REMOTE_CONTROL, json.dumps(job), action,
    ])
    return json.loads(remote_text(ssh_key, laptop, command))


def launch_remote(
    spec, params_path, result, log, ssh_key, laptop, remote_python, protocol_sha,
):
    number = int(spec["trial_number"])
    prefix = f"/tmp/r5_step5_trial_{number:03d}"
    # Preserve legacy result paths for recovery, but bind each job to its inputs.
    job = {
        "job_path": prefix + ".job",
        "result_path": prefix + "_result.json",
        "pending_path": prefix + "_result.pending.json",
        "log_path": prefix + ".log",
        "params_sha256": canonical_hash(spec["params"]),
        "protocol_sha256": protocol_sha,
        "laptop": laptop,
        "argv": [remote_python, REMOTE_WORKER,
                 "--root", str(Path(remote_python).parents[3] / "projects/zerodha-phase1"),
                 "--protocol", REMOTE_PROTOCOL,
                 "--expected-protocol-sha", protocol_sha, "--stage", "A",
                 "--params", prefix + "_params.json",
                 "--output", prefix + "_result.pending.json"],
    }
    record = result.parent.parent / "jobs" / f"trial_{number:03d}.json"
    if record.exists():
        saved = json.loads(record.read_text())
        if saved["job"] != job:
            raise SystemExit("LOCAL_REMOTE_JOB_IDENTITY_MISMATCH")
    else:
        atomic_json(record, {"job": job, "status": "INTENT"})
    status = remote_job_status(job, ssh_key, laptop, remote_python)
    if status["status"] == "ABSENT":
        run_checked(["scp", "-i", str(ssh_key), "-o", "BatchMode=yes",
                     str(params_path), f"{laptop}:{prefix}_params.json"])
        status = remote_job_status(job, ssh_key, laptop, remote_python, "launch")
    saved = json.loads(record.read_text())
    atomic_json(record, {**saved, **status})
    return {"job": job, "record": record, "status": status,
            "node": "laptop", "remote_python": remote_python}


def copy_remote_result(ssh_key, laptop, remote_result, local_result,
                       spec, protocol_sha, worker_sha):
    local_result.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=local_result.parent, suffix=".download")
    os.close(fd)
    temporary = Path(name)
    try:
        run_checked(["scp", "-i", str(ssh_key), "-o", "BatchMode=yes",
                     f"{laptop}:{remote_result}", str(temporary)])
        validate_candidate_result(json.loads(temporary.read_text()), spec,
                                  protocol_sha, worker_sha)
        os.replace(temporary, local_result)
    finally:
        temporary.unlink(missing_ok=True)


def wait_remote(info, ssh_key, laptop):
    status = info["status"]
    while status["status"] == "RUNNING":
        time.sleep(10)
        # Transport failures leave durable state intact for a later resume.
        status = remote_job_status(info["job"], ssh_key, laptop,
                                   info["remote_python"])
        saved = json.loads(info["record"].read_text())
        atomic_json(info["record"], {**saved, **status})
    if status["status"] != "COMPLETE":
        raise SystemExit(f"REMOTE_JOB_REQUIRES_INSPECTION: {status}")


def execute_batch(
    *,
    state_dir: Path,
    batch: dict,
    protocol: dict,
    protocol_path: Path,
    protocol_sha: str,
    worker_path: Path,
    worker_sha: str,
    ssh_key: Path,
    laptop: str,
    remote_python: str,
) -> dict:
    params_paths = prepare_params_files(
        state_dir,
        batch,
    )

    existing_results = {}

    for spec in batch["trials"]:
        number = int(
            spec["trial_number"]
        )

        path = result_path(
            state_dir,
            number,
        )

        existing = load_valid_existing_result(
            path,
            spec,
            protocol_sha,
            worker_sha,
        )

        if existing is not None:
            existing_results[number] = existing

    processes = {}

    for spec in batch["trials"]:
        number = int(
            spec["trial_number"]
        )

        if number in existing_results:
            continue

        node = spec["assigned_node"]

        local_result = result_path(
            state_dir,
            number,
        )

        logfile = log_path(
            state_dir,
            number,
            node,
        )

        if node == "desktop":
            proc, handle = launch_local(
                spec,
                params_paths[number],
                local_result,
                logfile,
                protocol_path,
                protocol_sha,
                worker_path,
            )

            processes[number] = {
                "process": proc,
                "handle": handle,
                "node": node,
                "remote_result": None,
            }

        elif node == "laptop":
            processes[number] = launch_remote(
                spec, params_paths[number], local_result, logfile,
                ssh_key, laptop, remote_python, protocol_sha,
            )

        else:
            raise SystemExit(
                f"UNKNOWN_NODE: {node}"
            )

    failures = []

    for number in sorted(processes):
        info = processes[number]

        if info["node"] == "laptop":
            wait_remote(info, ssh_key, laptop)
            continue

        rc = info["process"].wait()

        info["handle"].close()

        if rc != 0:
            failures.append(
                (
                    number,
                    info["node"],
                    rc,
                )
            )

    if failures:
        raise SystemExit(
            "BATCH_PROCESS_FAILURE: "
            + repr(failures)
        )

    for number in sorted(processes):
        info = processes[number]

        if info["node"] == "laptop":
            copy_remote_result(
                ssh_key,
                laptop,
                info["job"]["result_path"],
                result_path(
                    state_dir,
                    number,
                ),
                next(s for s in batch["trials"] if s["trial_number"] == number),
                protocol_sha, worker_sha,
            )

    results = {}

    for spec in sorted(
        batch["trials"],
        key=lambda row:
            row["trial_number"],
    ):
        number = int(
            spec["trial_number"]
        )

        path = result_path(
            state_dir,
            number,
        )

        if not path.is_file():
            raise SystemExit(
                f"RESULT_MISSING: "
                f"trial {number}"
            )

        result = json.loads(
            path.read_text()
        )

        validate_candidate_result(
            result,
            spec,
            protocol_sha,
            worker_sha,
        )

        results[number] = result

    return results


def freeze_completed_batch(
    state: dict,
    batch: dict,
    results: dict[int, dict],
) -> None:
    specs = sorted(
        batch["trials"],
        key=lambda row:
            row["trial_number"],
    )

    for spec in specs:
        number = int(
            spec["trial_number"]
        )

        result = results[number]

        spec["score"] = float(
            result["aggregate"]["score"]
        )

        spec["candidate_sha256"] = (
            result["candidate_sha256"]
        )

        spec["status"] = "COMPLETE"

    batch["status"] = "COMPLETE"


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
        "--worker",
        default=str(
            ROOT
            / "scripts"
            / "run_r5_step5_candidate.py"
        ),
    )

    parser.add_argument(
        "--expected-protocol-sha",
        required=True,
    )

    parser.add_argument(
        "--expected-worker-sha",
        required=True,
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
        "--state-dir",
        required=True,
    )

    parser.add_argument(
        "--plan-only",
        action="store_true",
    )

    parser.add_argument(
        "--execute",
        action="store_true",
    )

    parser.add_argument(
        "--max-batches",
        type=int,
        default=1,
    )

    args = parser.parse_args()

    if args.plan_only == args.execute:
        raise SystemExit(
            "Choose exactly one of "
            "--plan-only or --execute"
        )

    if args.max_batches < 1:
        raise SystemExit(
            "--max-batches must be >= 1"
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

    state_dir = Path(
        args.state_dir
    ).expanduser().resolve()

    protocol_sha = sha256_file(
        protocol_path
    )

    worker_sha = sha256_file(
        worker_path
    )

    if (
        protocol_sha
        != args.expected_protocol_sha
    ):
        raise SystemExit(
            "LOCAL_PROTOCOL_SHA: FAIL"
        )

    if worker_sha != args.expected_worker_sha:
        raise SystemExit(
            "LOCAL_WORKER_SHA: FAIL"
        )

    protocol = json.loads(
        protocol_path.read_text()
    )

    verify_baseline_ancestry(protocol)

    host = command_output(
        ["hostname"]
    )

    if (
        host
        != protocol["distributed_execution"][
            "coordinator"
        ]
    ):
        raise SystemExit(
            "COORDINATOR_HOST: FAIL"
        )

    remote = verify_remote_control_plane(
        ssh_key,
        args.laptop,
        protocol_sha,
        worker_sha,
        protocol["distributed_execution"][
            "secondary_worker"
        ],
        protocol["frozen_parent"]["commit"],
    )

    state_path = (
        state_dir
        / "stage_a_state.json"
    )

    execution_lock = None
    if args.execute:
        state_dir.mkdir(parents=True, exist_ok=True)
        execution_lock = (state_dir / "executor.lock").open("a")
        try:
            fcntl.flock(execution_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise SystemExit("EXECUTOR_ALREADY_RUNNING")

    if state_path.is_file():
        state = json.loads(
            state_path.read_text()
        )

        validate_state_identity(
            state,
            protocol_sha,
            worker_sha,
            protocol,
        )

    else:
        state = fresh_state(
            protocol_sha,
            worker_sha,
            protocol,
        )

    plan = next_batch_plan(
        protocol,
        state,
    )

    if args.plan_only:
        output = {
            "mode": "PLAN_ONLY",
            "market_replay_executed": False,
            "state_file_created": False,
            "protocol_sha256":
                protocol_sha,
            "worker_sha256":
                worker_sha,
            "baseline_commit":
                protocol["frozen_parent"]["commit"],
            "remote":
                remote,
            "completed_batches":
                sum(
                    1
                    for b in state["batches"]
                    if b["status"] == "COMPLETE"
                ),
            "next_batch":
                plan,
        }

        print(
            json.dumps(
                output,
                indent=2,
                sort_keys=True,
            )
        )

        return

    batches_executed = 0

    while (
        batches_executed
        < args.max_batches
    ):
        plan = next_batch_plan(
            protocol,
            state,
        )

        if plan is None:
            print(
                "STAGE_A_ALL_TRIALS_COMPLETE"
            )
            break

        if plan not in state["batches"]:
            state["batches"].append(
                plan
            )

            atomic_json(
                state_path,
                state,
            )

        results = execute_batch(
            state_dir=state_dir,
            batch=plan,
            protocol=protocol,
            protocol_path=protocol_path,
            protocol_sha=protocol_sha,
            worker_path=worker_path,
            worker_sha=worker_sha,
            ssh_key=ssh_key,
            laptop=args.laptop,
            remote_python=remote[
                "remote_python"
            ],
        )

        freeze_completed_batch(
            state,
            plan,
            results,
        )

        atomic_json(
            state_path,
            state,
        )

        batches_executed += 1

        summary = {
            "batch_index":
                plan["batch_index"],
            "trials": [
                {
                    "trial_number":
                        spec[
                            "trial_number"
                        ],
                    "assigned_node":
                        spec[
                            "assigned_node"
                        ],
                    "score":
                        spec["score"],
                    "candidate_sha256":
                        spec[
                            "candidate_sha256"
                        ],
                }
                for spec in sorted(
                    plan["trials"],
                    key=lambda row:
                        row["trial_number"],
                )
            ],
        }

        print(
            json.dumps(
                summary,
                indent=2,
                sort_keys=True,
            )
        )

    print(
        f"STATE_PATH={state_path}"
    )


if __name__ == "__main__":
    main()
