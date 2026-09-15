"""One-time trial: prove the dual-engine setup (original + expanded
universe, two separate terminals) actually works BEFORE relying on it
live tomorrow.

NOT a real Zerodha connection - same fake-broker discipline as every
other sandbox demo this session (v34_bridge_ea1_sandbox_demo.py,
v34_bridge_full_pipeline_sandbox_demo.py). This launches TWO REAL,
SEPARATE OS PROCESSES (subprocess.Popen, running at the same literal
moment - not sequential, not simulated) - exactly the "two PowerShell
terminals" scenario tomorrow, just with a fake Kite connection instead
of a real one, so it's safe to run right now without any credentials.

What this proves, for real, not by inspection:
1. Both processes' build_production_engine() succeeds independently.
2. Each resolves the CORRECT universe (original=19 symbols,
   expanded=50) via its own UNIVERSE_MODE env var - proven by printing
   external_momentum_shadow.EXTERNAL_UNIVERSE's real length inside each
   process, not asserted from outside.
3. Both hold their own OS-level runner lock SIMULTANEOUSLY, in separate
   data directories, with zero collision - the actual failure mode this
   whole design exists to avoid, genuinely exercised, not assumed safe.
4. Both are shadow_mode=True, LIVE_TRADING_ENABLED=False throughout -
   zero real orders possible in either process.

Each worker process is a small inline script (passed via `python -c`),
using the exact same fake-kiteconnect-module-injection trick
test_v34_bridge_runner_main.py already uses to avoid any real network
call.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

BASE_DIR = Path(tempfile.gettempdir()) / "v34_bridge_dual_engine_trial"

WORKER_SCRIPT = textwrap.dedent("""
    import os
    import sys
    import types

    # Fake kiteconnect module - no real network call possible, same
    # pattern test_v34_bridge_runner_main.py already uses.
    sys.path.insert(0, {project_dir!r})
    from test_v34_bridge_kite_broker_client import FakeKiteConnectWithOrders

    class FakeKiteConnectSDK(FakeKiteConnectWithOrders):
        def __init__(self, api_key=None):
            super().__init__()
            self.api_key = api_key
        def set_access_token(self, token):
            pass

    fake_module = types.ModuleType("kiteconnect")
    fake_module.KiteConnect = FakeKiteConnectSDK
    sys.modules["kiteconnect"] = fake_module

    os.environ["KITE_API_KEY"] = "fake-trial-key"
    os.environ["KITE_ACCESS_TOKEN"] = "fake-trial-token"
    os.environ["DP_CHARGE_PER_SYMBOL"] = "15.34"
    os.environ["SHADOW_MODE"] = "true"

    from external_momentum_shadow import EXTERNAL_UNIVERSE, UNIVERSE_MODE
    print(f"[{{os.environ['LABEL']}}] UNIVERSE_MODE={{UNIVERSE_MODE}} resolved_universe_size={{len(EXTERNAL_UNIVERSE)}}")

    import v34_bridge_runner_main as runner_main
    engine = runner_main._build_engine(on_event=lambda msg: None)

    print(f"[{{os.environ['LABEL']}}] engine built OK: status={{engine.state.status.value}} "
          f"halted={{engine.terminator.halted}} broker={{engine.broker.raw_broker.__class__.__name__}} "
          f"live_trading_enabled={{runner_main.LIVE_TRADING_ENABLED}}")

    # Hold the lock briefly while the OTHER process is also running -
    # this is the actual moment that would collide if the data
    # directories weren't genuinely separate.
    import time
    time.sleep(2.0)

    engine.lock_provider.release()
    print(f"[{{os.environ['LABEL']}}] lock released cleanly. DONE.")
""").format(project_dir=str(Path(__file__).parent))


def main() -> int:
    if BASE_DIR.exists():
        shutil.rmtree(BASE_DIR)
    original_dir = BASE_DIR / "original"
    expanded_dir = BASE_DIR / "expanded"
    original_dir.mkdir(parents=True)
    expanded_dir.mkdir(parents=True)

    print("=== Dual-engine trial: launching BOTH processes at the same moment ===\n")

    env_a = os.environ.copy()
    env_a.update({"LABEL": "TERMINAL-A (original)", "UNIVERSE_MODE": "original", "RUNNER_DATA_DIR": str(original_dir)})
    env_b = os.environ.copy()
    env_b.update({"LABEL": "TERMINAL-B (expanded)", "UNIVERSE_MODE": "expanded", "RUNNER_DATA_DIR": str(expanded_dir)})

    proc_original = subprocess.Popen(
        [sys.executable, "-c", WORKER_SCRIPT],
        cwd=str(Path(__file__).parent), env=env_a,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    proc_expanded = subprocess.Popen(
        [sys.executable, "-c", WORKER_SCRIPT],
        cwd=str(Path(__file__).parent), env=env_b,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )

    out_a, _ = proc_original.communicate(timeout=60)
    out_b, _ = proc_expanded.communicate(timeout=60)

    print("--- TERMINAL A output (original universe) ---")
    print(out_a)
    print("--- TERMINAL B output (expanded universe) ---")
    print(out_b)

    ok_a = proc_original.returncode == 0 and "DONE." in out_a
    ok_b = proc_expanded.returncode == 0 and "DONE." in out_b
    print(f"=== Result: Terminal A {'OK' if ok_a else 'FAILED'}, Terminal B {'OK' if ok_b else 'FAILED'} ===")
    return 0 if (ok_a and ok_b) else 1


if __name__ == "__main__":
    sys.exit(main())
