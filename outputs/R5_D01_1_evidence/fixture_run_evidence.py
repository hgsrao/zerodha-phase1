#!/usr/bin/env python3
"""D01.1 evidence: runs the SAME replay fixture function the permanent test uses and prints the facts the report needs.
Usage: python fixture_run_evidence.py [repo_root]   (repo_root defaults to the working tree; pass a temp tree for the pre-D01 counterfactual)"""
import sys, json, hashlib, tempfile, importlib.util, traceback
from pathlib import Path
root = Path(sys.argv[1] if len(sys.argv) > 1 else "/home/srinivas/projects/zerodha-r5-governor-refactor").resolve(); sys.path.insert(0, str(root))
import revision2_external.paper_execution as pe
spec = importlib.util.spec_from_file_location("d01_1_test", root / "tests/test_r5_d01_orchestrator_run_integration.py"); m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
print("paper_execution module:", pe.__file__, "sha256", hashlib.sha256(Path(pe.__file__).read_bytes()).hexdigest())
prov = json.loads((m.FIXTURES / "PROVENANCE.json").read_text())
print("input classification:", prov["classification"], "| symbol:", m.SYMBOL, "| fixture rows:", {k: v["rows"] for k, v in prov["files"].items()})
orch, rt, store, obs, titan, warmup = m.build_orchestrator(Path(tempfile.mkdtemp(prefix="d01_1_")))
print("broker class (before run):", f"{type(orch.broker).__module__}.{type(orch.broker).__qualname__}", "| environment:", orch.broker.environment)
try:
    rep = orch.run({m.SYMBOL: titan}, warmup=warmup)
except BaseException as e:
    print("RUN RAISED:", type(e).__name__, ":", e); print("reconcile calls observed before the exception:", obs); traceback.print_exc(limit=-4); sys.exit(1)
r = {"bars": len(titan)}
print("broker class:", f"{type(orch.broker).__module__}.{type(orch.broker).__qualname__}", "| environment:", orch.broker.environment)
print("runtime class:", f"{type(rt).__module__}.{type(rt).__qualname__}", "| store:", type(store).__qualname__, "| handoff:", type(rt.handoff).__qualname__, "| engine_b:", type(rt.engine_b).__qualname__)
trades = rep["trades"]; t = trades[0]
print("bars in fixture frame:", r["bars"], "| scored bars processed:", r["bars"] - 60 - 1)
print("fills through paper broker:", len(orch.broker.fills), "| first fill:", {k: orch.broker.fills[0][k] for k in orch.broker.fills[0] if k in ("side", "quantity", "filled_price", "symbol")})
print("trades:", len(trades), "| side:", t["side"], "| qty:", t["quantity"], "| entry:", t["entry_timestamp"], "@", t["entry_price"], "| exit:", t["exit_timestamp"], "@", t["exit_price"], "| reason:", t["reason"], "| bars_held:", t["bars_held"])
print("reconcile calls observed (TEST_OBSERVATION_SPY):", len(obs), "| after the fill (fills_so_far>=1):", sum(c["fills_so_far"] >= 1 for c in obs))
for i, c in enumerate(obs, 1): print("  reconcile", i, c)
print("trade ledger sha256 (json.dumps(trades, sort_keys, default=str)):", hashlib.sha256(json.dumps(trades, sort_keys=True, default=str).encode()).hexdigest())
d = store.load(t["trade_id"]); print("durable record:", d.record.lifecycle_state, d.record.owner_engine, d.record.product, "| open in store:", len(store.list_open()), "| open_trades:", list(orch.open_trades), "| handoff requests:", len(store.requests()))
print("broker position after run:", dict(orch.broker.get_position(m.SYMBOL)))
