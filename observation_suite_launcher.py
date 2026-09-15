"""Controlled launcher for runner + read-only collector + Observatory v3.

One request-token exchange is shared through child-process environment memory.
Credentials are never printed or written to disk.  The launcher refuses to run
unless the production source retains LIVE_TRADING_ENABLED = False.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

from read_only_broker_inspection import normalize_request_token, summarize


RUNNER = "run_production_p01d_candidate.py"
COLLECTOR = "read_only_shadow_collector.py"
DASHBOARD = "v34_observatory_v3.py"
LIVE_FALSE_GUARD = "LIVE_TRADING_ENABLED = False"


def validate_sources(base: Path) -> None:
    required = [RUNNER, COLLECTOR, DASHBOARD, "shadow_strategy_evaluator.py"]
    missing = [name for name in required if not (base / name).is_file()]
    if missing:
        raise RuntimeError(f"FAIL_CLOSED: missing required files: {missing}")
    runner_source = (base / RUNNER).read_text(encoding="utf-8-sig")
    guard_lines = [line.strip() for line in runner_source.splitlines()]
    if LIVE_FALSE_GUARD not in guard_lines:
        raise RuntimeError("FAIL_CLOSED: exact LIVE_TRADING_ENABLED = False guard is absent")
    collector_source = (base / COLLECTOR).read_text(encoding="utf-8-sig")
    forbidden = (".place_order(", ".modify_order(", ".cancel_order(", "request_entry(")
    if any(token in collector_source for token in forbidden):
        raise RuntimeError("FAIL_CLOSED: collector contains a forbidden trading operation")


def child_environment(access_token: str) -> dict[str, str]:
    if not access_token:
        raise ValueError("access_token is empty")
    environment = os.environ.copy()
    environment["KITE_ACCESS_TOKEN"] = access_token
    return environment


def stop_process(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def stop_runner_gracefully(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT"):
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)
    try:
        process.wait(timeout=15)
    except subprocess.TimeoutExpired:
        stop_process(process)


def broker_clean_preflight(kite) -> dict:
    result = summarize(kite.positions(), kite.orders())
    if not result["broker_clean"]:
        raise RuntimeError(
            "FAIL_CLOSED: broker is not clean; "
            f"nonzero_positions={len(result['nonzero_net_positions'])} "
            f"active_orders={len(result['active_orders'])}\n"
            f"BROKER_STATE={json.dumps(result, indent=2)}"
        )
    return result


def pin_new_session(base: Path, before: set[Path], runner_pid: int) -> Path | None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        current = set((base / "session_logs").glob("*/bot_production.log"))
        created = current - before
        if created:
            selected = max(created, key=lambda path: path.stat().st_mtime)
            manifest = {
                "runner_pid": runner_pid,
                "session_log": str(selected.relative_to(base)),
                "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "live_trading_enabled": False,
                "gate_4": "LOCKED",
            }
            temporary = base / "observation_suite_runtime.json.tmp"
            temporary.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            os.replace(temporary, base / "observation_suite_runtime.json")
            return selected
        time.sleep(0.1)
    return None


def main() -> int:
    base = Path(__file__).resolve().parent
    validate_sources(base)
    api_key = os.getenv("KITE_API_KEY")
    api_secret = os.getenv("KITE_API_SECRET")
    if not api_key or not api_secret:
        print("[BLOCK] KITE_API_KEY or KITE_API_SECRET is unavailable.")
        return 2

    from kiteconnect import KiteConnect

    supplied_token = os.environ.pop("KITE_REQUEST_TOKEN", "")
    if supplied_token:
        request_token = normalize_request_token(supplied_token)
    else:
        request_token = normalize_request_token(
            input("Paste fresh Zerodha request token or redirect URL: ")
        )
    kite = KiteConnect(api_key=api_key)
    session = kite.generate_session(request_token, api_secret=api_secret)
    access_token = session.get("access_token") if isinstance(session, dict) else None
    if not isinstance(access_token, str) or not access_token:
        print("[BLOCK] Access-token exchange failed.")
        return 2

    kite.set_access_token(access_token)
    profile = kite.profile()
    if not isinstance(profile, dict) or not profile.get("user_id"):
        print("[BLOCK] Broker profile verification failed.")
        return 2
    try:
        broker_clean_preflight(kite)
    except RuntimeError as exc:
        print(f"[BLOCK] {exc}")
        print("[BLOCK] Use read_only_broker_inspection.py to identify broker state.")
        return 2

    environment = child_environment(access_token)
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    collector_log = (base / "shadow_collector.log").open("a", encoding="utf-8")
    dashboard_log = (base / "observatory_v3.log").open("a", encoding="utf-8")
    collector = dashboard = runner = None
    try:
        dashboard = subprocess.Popen(
            [sys.executable, "-u", DASHBOARD, "--base", str(base),
             "--host", "127.0.0.1", "--port", "8765"],
            cwd=base, env=environment, stdout=dashboard_log,
            stderr=subprocess.STDOUT, creationflags=creation_flags,
        )
        collector = subprocess.Popen(
            [sys.executable, "-u", COLLECTOR, "--output",
             "shadow_strategy_telemetry.json", "--interval-seconds", "15"],
            cwd=base, env=environment, stdout=collector_log,
            stderr=subprocess.STDOUT, creationflags=creation_flags,
        )
        time.sleep(1.0)
        if dashboard.poll() is not None or collector.poll() is not None:
            raise RuntimeError("FAIL_CLOSED: dashboard or collector exited during startup")
        print("====================================================")
        print("V3.4 CONTROLLED OBSERVATION SUITE")
        print("LIVE_TRADING_ENABLED = False verified")
        print("Dashboard: http://127.0.0.1:8765/")
        print("Collector: quote-only; execution authorization blocked")
        print("Starting observation-only production runner...")
        print("====================================================")
        sessions_before = set((base / "session_logs").glob("*/bot_production.log"))
        runner_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        runner = subprocess.Popen(
            [sys.executable, "-u", RUNNER], cwd=base, env=environment,
            creationflags=runner_flags,
        )
        pinned = pin_new_session(base, sessions_before, runner.pid)
        if pinned is None:
            raise RuntimeError("FAIL_CLOSED: runner session log was not created")
        print(f"Pinned session log: {pinned.relative_to(base)}")
        return runner.wait()
    except KeyboardInterrupt:
        print("Observation suite shutdown requested.")
        stop_runner_gracefully(runner)
        return 130
    except Exception as exc:
        print(f"[BLOCK] {exc}")
        stop_runner_gracefully(runner)
        return 2
    finally:
        stop_process(collector)
        stop_process(dashboard)
        collector_log.close()
        dashboard_log.close()
        environment.pop("KITE_ACCESS_TOKEN", None)


if __name__ == "__main__":
    raise SystemExit(main())
