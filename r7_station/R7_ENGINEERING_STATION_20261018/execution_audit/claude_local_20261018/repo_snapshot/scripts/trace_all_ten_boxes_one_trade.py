#!/usr/bin/env python3
"""Traces every one of the 10 real boxes' actual outputs, in order, for ONE
real symbol (INFY) and ONE real completed trade -- not a re-derivation,
the actual objects/methods each box call really returned during a real run.

Uses a short real window (through the trade's own day, 2023-07-13) so the
run finishes in seconds, not minutes -- the same real dataset, just not the
full 6 months/3 years, since nothing before/after this trade's day is
needed to see this trade's own lifecycle.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from canonical_parameter_registry import CanonicalParameterRegistry
from market_data_loader import MarketDataLoader
from revision2.dataset_manifest import DatasetManifest
from revision2_external.orchestrator import Revision2ExternalEngineOrchestrator

SYMBOL = "INFY"
TARGET_DAY = "2023-07-13"
WINDOW_START = "2023-07-13 15:10:00"
WINDOW_END = "2023-07-13 15:22:00"


def main():
    manifest = DatasetManifest.load("revision2/DATASET_MANIFEST_48SYMBOL_1MIN.json")
    loader = MarketDataLoader(manifest.data_dir, synthetic_if_missing=False)
    frame = loader._load_symbol_csv(SYMBOL)
    cutoff = pd.Timestamp(TARGET_DAY, tz=frame["timestamp"].dt.tz) + pd.Timedelta(days=1)
    frame = frame[frame["timestamp"] < cutoff].reset_index(drop=True)
    print(f"Using {len(frame)} real bars, {frame['timestamp'].iloc[0]} to {frame['timestamp'].iloc[-1]} (warmup=60 + through {TARGET_DAY})\n")

    registry = CanonicalParameterRegistry()
    orch = Revision2ExternalEngineOrchestrator([SYMBOL], registry, starting_equity=1_000_000.0)

    log = []

    def in_window(ts):
        s = str(ts)
        return WINDOW_START <= s <= WINDOW_END

    def wrap(box, method_name, box_label, fmt):
        orig = getattr(box, method_name)
        def wrapped(*args, **kwargs):
            result = orig(*args, **kwargs)
            log.append((box_label, method_name, args, kwargs, result, fmt))
            return result
        setattr(box, method_name, wrapped)

    # Box 2: DataIngestion.admit(symbol, config) -> (admitted, reason, trace)
    orig_admit = orch.data_ingestion.admit
    def traced_admit(symbol, config):
        r = orig_admit(symbol, config)
        log.append(("BOX2_DataIngestion", "admit", (symbol,), {}, r[:2], None))
        return r
    orch.data_ingestion.admit = traced_admit

    # Box 4: PA.evaluate(snapshot, config) -> (signal, trace)
    orig_pa = orch.pa.evaluate
    def traced_pa(snapshot, config):
        r = orig_pa(snapshot, config)
        if in_window(snapshot.timestamp):
            log.append(("BOX4_PredictiveAnalytics_TALib", "evaluate", (snapshot.timestamp,), {}, r[0], None))
        return r
    orch.pa.evaluate = traced_pa

    # Box 5: id_box.evaluate(signal, config, latest_close=...) -> (decision, trace)
    orig_id = orch.id_box.evaluate
    def traced_id(signal, config, **kw):
        r = orig_id(signal, config, **kw)
        if in_window(signal.timestamp):
            log.append(("BOX5_IntelligentDiscrimination_HMM", "evaluate", (signal.timestamp,), {}, r[0], None))
        return r
    orch.id_box.evaluate = traced_id

    # Box 6: mpc.build_plan(signal, decision, entry_price, atr, config) -> (plan, pid_info, trace)
    orig_mpc = orch.mpc.build_plan
    def traced_mpc(signal, decision, entry_price, atr, config):
        r = orig_mpc(signal, decision, entry_price, atr, config)
        if in_window(signal.timestamp):
            log.append(("BOX6_ModelPredictiveControl_PID", "build_plan", (signal.timestamp,), {"entry_price": entry_price, "atr": atr}, (r[0], r[1]), None))
        return r
    orch.mpc.build_plan = traced_mpc

    # Box 7a: safety_gates_target pre/post sizing
    orig_pre = orch.safety_gates_target.evaluate_pre_sizing
    def traced_pre(*a, **kw):
        r = orig_pre(*a, **kw)
        log.append(("BOX7_SafetyGates_pre_sizing", "evaluate_pre_sizing", (), {}, r[:3], None))
        return r
    orch.safety_gates_target.evaluate_pre_sizing = traced_pre

    orig_post = orch.safety_gates_target.evaluate_post_sizing
    def traced_post(equity_curve, plan, quantity, config):
        r = orig_post(equity_curve, plan, quantity, config)
        log.append(("BOX7_SafetyGates_post_sizing", "evaluate_post_sizing", (), {"quantity": quantity}, r[:2], None))
        return r
    orch.safety_gates_target.evaluate_post_sizing = traced_post

    # Box 8: position_manager.size(...) -> (quantity, trace)
    orig_size = orch.position_manager.size
    def traced_size(*a, **kw):
        r = orig_size(*a, **kw)
        log.append(("BOX8_PositionManager_PyPortfolioOpt", "size", (), {}, r[0], None))
        return r
    orch.position_manager.size = traced_size

    # Box 7b: EntryDecisionEngine.evaluate(...) -> dict
    orig_gate = orch.entry_decision_engine.evaluate
    def traced_gate(*a, **kw):
        r = orig_gate(*a, **kw)
        log.append(("BOX7_EntryDecisionEngine_18gate", "evaluate", (), {"symbol": kw.get("symbol")}, r, None))
        return r
    orch.entry_decision_engine.evaluate = traced_gate

    # Box 9: p01d.create_order(...) -> (order, trace)
    orig_p01d = orch.p01d.create_order
    def traced_p01d(symbol, plan, quantity, config):
        r = orig_p01d(symbol, plan, quantity, config)
        log.append(("BOX9_P01D_SovereignAuthorization", "create_order", (symbol,), {"quantity": quantity}, r[0], None))
        return r
    orch.p01d.create_order = traced_p01d

    # Box 10: broker.place_order(...) -> fill
    orig_broker = orch.broker.place_order
    def traced_broker(**kw):
        r = orig_broker(**kw)
        log.append(("BOX10_UnifiedExecution_PaperBroker", "place_order", (), {"symbol": kw.get("symbol"), "side": kw.get("side"), "quantity": kw.get("quantity")}, r, None))
        return r
    orch.broker.place_order = traced_broker

    report = orch.run({SYMBOL: frame}, warmup=60)

    print("=" * 100)
    print(f"BOX 1 (StartupCapabilityLock/Pydantic): validated at orchestrator construction -- "
          f"config_hash={report['config_hash'][:16]}... (see orchestrator __init__: Pydantic runtime + safety-contract validation, raised ExternalEngineStartupNotCertifiedError if either had failed)")
    print(f"BOX 3 (L2DataCertifier/Pandera): certified once up-front for the whole {SYMBOL} dataset before this loop ran")
    print("=" * 100)

    for i, (box_label, method_name, args, kwargs, result, _fmt) in enumerate(log):
        print(f"\n--- [{i}] {box_label}.{method_name}({', '.join(str(a) for a in args)}"
              f"{', ' if kwargs else ''}{', '.join(f'{k}={v}' for k, v in kwargs.items())}) ---")
        print(result)

    print("\n" + "=" * 100)
    print("REAL COMPLETED TRADE(S) for this window (from the orchestrator's own ledger):")
    for t in report["trades"]:
        if t["symbol"] == SYMBOL and WINDOW_START.split()[1][:5] <= t["entry_timestamp"].split()[1][:5] <= WINDOW_END.split()[1][:5]:
            print(f"  ENTRY  {t['entry_timestamp']}  {t['side']} {t['quantity']} @ {t['entry_price']}")
            print(f"  EXIT   {t['exit_timestamp']}  @ {t['exit_price']}  reason={t['reason']}")
            print(f"  gross_pnl={t['pnl']:.2f}  costs={t['costs']:.2f}  net_pnl={t['net_pnl']:.2f}")


if __name__ == "__main__":
    main()
