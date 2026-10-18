#!/usr/bin/env python3
"""Launch and monitor a long-running calibration process.

The watchdog is deliberately outside the calibration decision path.  It reads
the supervisor checkpoint, writes deterministic health summaries, and can ask
a local Ollama model to explain those summaries.  Ollama never receives an
ability to change parameters, stop a healthy run, or approve a winner.

Example:
    python3 scripts/calibration_watchdog.py \
      --checkpoint output/revision2_calibration_checkpoint.json \
      --interval-seconds 3600 \
      --ollama-model qwen3-coder:30b \
      -- python3 scripts/run_revision2_calibration.py --profile smoke

Production mode remains fail-closed until Revision 2 enforces sealed
train/validation/untouched-test partitions.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_checkpoint(path: Path) -> Dict[str, Any]:
    """Read a checkpoint without treating a partial/absent write as failure."""
    if not path.exists():
        return {"available": False, "reason": "checkpoint not created yet"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"available": False, "reason": f"checkpoint temporarily unreadable: {exc}"}
    if not isinstance(payload, dict) or not isinstance(payload.get("candidates", []), list):
        return {"available": False, "reason": "checkpoint schema is invalid"}
    payload["available"] = True
    return payload


def checkpoint_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    if not payload.get("available"):
        return {
            "checkpoint_available": False,
            "checkpoint_status": payload.get("reason", "unavailable"),
        }

    candidates: List[Dict[str, Any]] = payload.get("candidates", [])
    accepted = [c for c in candidates if c.get("accepted") is True]
    errors = [
        c for c in candidates
        if any(str(r).startswith("candidate raised:") for r in c.get("reject_reasons", []))
    ]
    finite = []
    for candidate in candidates:
        try:
            score = float(candidate.get("score", "-inf"))
        except (TypeError, ValueError):
            continue
        if math.isfinite(score):
            finite.append((score, candidate))

    elapsed_values = []
    for candidate in candidates:
        try:
            elapsed_values.append(float(candidate.get("elapsed_seconds", 0.0)))
        except (TypeError, ValueError):
            pass

    latest = candidates[-1] if candidates else {}
    best = max(finite, key=lambda item: item[0])[1] if finite else None
    return {
        "checkpoint_available": True,
        "candidates_completed": len(candidates),
        "accepted_candidates": len(accepted),
        "candidate_errors": len(errors),
        "latest_phase": latest.get("phase"),
        "latest_accepted": latest.get("accepted"),
        "latest_reject_reasons": latest.get("reject_reasons", []),
        "average_candidate_seconds": (
            round(sum(elapsed_values) / len(elapsed_values), 2) if elapsed_values else None
        ),
        "best_finite_score": best.get("score") if best else None,
        "best_metrics": best.get("metrics") if best else None,
    }


def ollama_summary(model: str, summary: Dict[str, Any], timeout: int) -> str:
    prompt = (
        "You are a read-only calibration operations observer. Summarize this health "
        "snapshot in no more than six bullet points. Flag crashes, repeated candidate "
        "errors, lack of progress, or all candidates being rejected. Do not recommend "
        "changing acceptance thresholds and do not claim profitability.\n\n"
        + json.dumps(summary, indent=2, sort_keys=True, default=str)
    )
    try:
        result = subprocess.run(
            ["ollama", "run", model, prompt],
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return "Ollama unavailable: executable not found"
    except subprocess.TimeoutExpired:
        return f"Ollama unavailable: summary timed out after {timeout}s"
    if result.returncode != 0:
        message = result.stderr.strip() or f"exit code {result.returncode}"
        return f"Ollama unavailable: {message}"
    return result.stdout.strip() or "Ollama returned an empty summary"


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, sort_keys=True, default=str) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--log", type=Path, default=Path("output/calibration_watchdog.jsonl"))
    parser.add_argument("--process-log", type=Path, default=Path("output/calibration_process.log"))
    parser.add_argument("--interval-seconds", type=int, default=3600)
    parser.add_argument("--ollama-model", help="Optional local model, e.g. qwen3-coder:30b")
    parser.add_argument("--ollama-timeout-seconds", type=int, default=180)
    parser.add_argument("command", nargs=argparse.REMAINDER, help="Command after --")
    args = parser.parse_args()
    if args.command and args.command[0] == "--":
        args.command = args.command[1:]
    if not args.command:
        parser.error("a calibration command is required after --")
    if args.interval_seconds < 10:
        parser.error("--interval-seconds must be at least 10")
    return args


def main() -> int:
    args = parse_args()
    args.process_log.parent.mkdir(parents=True, exist_ok=True)
    stop_requested = False
    child: Optional[subprocess.Popen] = None

    def request_stop(signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        if child is not None and child.poll() is None:
            child.send_signal(signum)

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    with args.process_log.open("a", encoding="utf-8", buffering=1) as process_log:
        process_log.write(f"\n[{utc_now()}] START {' '.join(args.command)}\n")
        child = subprocess.Popen(
            args.command,
            stdout=process_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=False,
        )
        next_check = time.monotonic()
        while child.poll() is None and not stop_requested:
            now = time.monotonic()
            if now >= next_check:
                summary = checkpoint_summary(load_checkpoint(args.checkpoint))
                record: Dict[str, Any] = {
                    "timestamp_utc": utc_now(),
                    "process_pid": child.pid,
                    "process_running": True,
                    **summary,
                }
                if args.ollama_model:
                    record["ollama_model"] = args.ollama_model
                    record["ollama_observation"] = ollama_summary(
                        args.ollama_model, summary, args.ollama_timeout_seconds
                    )
                append_jsonl(args.log, record)
                print(json.dumps(record, indent=2, default=str), flush=True)
                next_check = time.monotonic() + args.interval_seconds
            time.sleep(min(2.0, max(0.1, next_check - time.monotonic())))

        return_code = child.wait()
        final_summary = checkpoint_summary(load_checkpoint(args.checkpoint))
        final_record = {
            "timestamp_utc": utc_now(),
            "process_pid": child.pid,
            "process_running": False,
            "process_return_code": return_code,
            "stop_requested": stop_requested,
            **final_summary,
        }
        append_jsonl(args.log, final_record)
        print(json.dumps(final_record, indent=2, default=str), flush=True)
        return return_code


if __name__ == "__main__":
    sys.exit(main())
