#!/usr/bin/env python3
"""R5-D01 evidence probe: REAL Revision2ExternalEngineOrchestrator.run() on the EXISTING Block 1 replay input.

Usage: python d01_replay_probe.py {baseline|runtime} <output.json> [SYM1,SYM2,...]

* baseline: orchestrator constructed normally with NO combined-cycle runtime.
* runtime : orchestrator constructed normally with a real CombinedCycleRuntime, real CombinedCycleStore (SQLite file in a
            temp dir), real HandoffManager and real EngineBController (opt-in policies), real CostedPaperBrokerAdapter.

No shim, no mock, no monkey-patch of production behaviour.  The only wrapper is a pass-through counter around
runtime.reconcile that records the call and re-raises anything it raises.  Input data: the repository's own causal Block 1
loader (scripts/run_r5_step5_candidate.prepare_block); parameters: Trial 007 file; protocol v2; full governor authority.
"""
import sys, json, hashlib, tempfile, traceback, importlib.util, os
from pathlib import Path
ROOT = Path("/home/srinivas/projects/zerodha-r5-governor-refactor"); sys.path.insert(0, str(ROOT))
mode, out_path = sys.argv[1], Path(sys.argv[2]); symbols = (sys.argv[3] if len(sys.argv) > 3 else "TITAN").split(",")
DATA_ROOT = Path(os.environ["R5_D01_DATA_ROOT"])   # directory of symlinks: revision2 -> repo revision2/, local_workspace -> zerodha-phase1 local_workspace/
PARAMS = Path("/home/srinivas/projects/zerodha-phase1/outputs/r5_step5_stage_a_v2_state/params/trial_007.json")
spec = importlib.util.spec_from_file_location("step5", ROOT / "scripts/run_r5_step5_candidate.py"); step5 = importlib.util.module_from_spec(spec); spec.loader.exec_module(step5)
proto = json.loads((ROOT / "revision5/step5_sealed_calibration_protocol_v2.json").read_text()); params = json.loads(PARAMS.read_text())
frames, feeds, audit = step5.prepare_block(DATA_ROOT, proto, proto["sampling_plan"]["stage_a"][0])
from canonical_parameter_registry import CanonicalParameterRegistry
from revision2_external.grid_context import SealedGridContextProvider
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator
from revision5.ccpp_unified_plant import CentralPlantMasterDCS
registry = CanonicalParameterRegistry(); registry.verify_frozen_identity()
equity = float(proto["block_execution_contract"]["starting_equity_per_block"])
plant = CentralPlantMasterDCS(total_capital=equity, db_path=":memory:")
runtime = None
if mode == "runtime":
    from revision5.combined_cycle_store import CombinedCycleStore
    from revision5.handoff_manager import HandoffManager, HandoffConfig
    from revision5.engine_b_management import EngineBController, EngineBPolicy
    from revision5.combined_cycle_runtime import CombinedCycleRuntime
    store = CombinedCycleStore(Path(tempfile.mkdtemp(prefix="d01_store_")) / "cycle.db")
    runtime = CombinedCycleRuntime(store, HandoffManager(store, HandoffConfig(enabled=True)), EngineBController(EngineBPolicy(enabled=True)))
orch = Revision2ExternalEngineOrchestrator(symbols, registry, calibration_overrides=params, starting_equity=equity,
    grid_context_provider=SealedGridContextProvider(feeds["NIFTY_50_15MIN"], feeds["INDIA_VIX_15MIN"]), real_plant_dcs=plant,
    plant_control_mode="PAPER_APPLY", closed_loop_mode="active_paper", telemetry_mode="compact", governor_authority="full",
    combined_cycle_runtime=runtime)
broker_cls = type(orch.broker)
print(f"BROKER CLASS: {broker_cls.__module__}.{broker_cls.__qualname__}  environment={orch.broker.environment!r}")
print(f"RUNTIME CLASS: {None if runtime is None else type(runtime).__module__ + '.' + type(runtime).__qualname__}")
print(f"ORCHESTRATOR CLASS: {type(orch).__module__}.{type(orch).__qualname__} (constructed normally; run() will be called)")
if orch.broker.environment != "paper" or broker_cls.__qualname__ != "CostedPaperBrokerAdapter":
    print("REFUSING TO RUN: broker is not the intended paper implementation"); sys.exit(3)
counter = {"reconcile_calls": 0, "reconcile_errors": []}
if runtime is not None:
    _orig = runtime.reconcile
    def counted(engine, snapshot):                    # pass-through observation: records, never alters or suppresses
        counter["reconcile_calls"] += 1
        try: return _orig(engine, snapshot)
        except BaseException as e:
            counter["reconcile_errors"].append(f"{type(e).__name__}: {e}"); raise
    runtime.reconcile = counted
result = {"mode": mode, "symbols": symbols, "broker_class": f"{broker_cls.__module__}.{broker_cls.__qualname__}",
          "runtime_class": None if runtime is None else type(runtime).__qualname__, "input": {"loader": "scripts/run_r5_step5_candidate.prepare_block", "block": 1,
          "slice_sha256": audit.get("slice_sha256"), "params_file": str(PARAMS), "params_sha256": hashlib.sha256(PARAMS.read_bytes()).hexdigest(),
          "protocol_sha256": hashlib.sha256((ROOT / "revision5/step5_sealed_calibration_protocol_v2.json").read_bytes()).hexdigest()}, "exception": None}
try:
    rep = orch.run({s: frames[s] for s in symbols}, warmup=int(proto["block_execution_contract"]["stock_warmup_bars_per_symbol"]))
    result["completed"] = True
    trades = rep.get("trades", [])
    result["trades"] = len(trades); result["trade_ledger_sha256"] = hashlib.sha256(json.dumps(trades, sort_keys=True, default=str).encode()).hexdigest()
    result["trade_sides"] = sorted({t["side"] for t in trades}); result["exit_reasons"] = sorted({t["reason"] for t in trades})
except BaseException as e:
    result["completed"] = False; result["exception"] = "".join(traceback.format_exception(type(e), e, e.__traceback__))[-1500:]
    result["exception_line"] = f"{type(e).__name__}: {e}"
result.update(counter)
result["broker_positions_after"] = {s: dict(p) for s, p in orch.broker.positions.items()}
result["open_trades_after"] = list(orch.open_trades); result["completed_trades"] = len(orch.completed_trades)
if runtime is not None:
    result["store_open_after"] = [(s.record.position_id, s.record.lifecycle_state) for s in runtime.store.list_open()]
    result["handoff_requests"] = len(runtime.store.requests())
out_path.write_text(json.dumps(result, indent=1, default=str))
print(f"completed={result['completed']} exception={result.get('exception_line')} reconcile_calls={counter['reconcile_calls']} trades={result.get('trades')}")
sys.exit(0 if result["completed"] else 1)
