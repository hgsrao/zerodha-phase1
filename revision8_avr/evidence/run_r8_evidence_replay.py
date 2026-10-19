"""Evidence replay: python run_r8_evidence_replay.py <B|C> <R7|off|shadow|paper> <bars> <outdir>
48 symbols, synthetic data, LIVE_TRADING_ENABLED=False.  Prints one JSON summary line."""
import os, sys, json, time
os.environ["LIVE_TRADING_ENABLED"] = "False"
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)
engine, run, bars, outdir = sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4]
from engine_c.data_contract import load_synthetic
from revision5.topology import SYMBOL_TO_BAY
symbols = sorted(SYMBOL_TO_BAY)
data, nifty, vix, info = load_synthetic(symbols, bars, 60)
if engine == "B":
    import engine_b.runner as runner; native, attr, go = runner.EngineBOrchestrator, "EngineBOrchestrator", runner.run_engine_b
else:
    import engine_c.runner as runner; native, attr, go = runner.EngineCOrchestrator, "EngineCOrchestrator", runner.run_engine_c
cap = []
if run == "R7":
    from revision7_avr.r7_engine import engine_class; import revision7_avr.r7_state as st
    cls = engine_class(native); pre = "r7"
    def factory(*a, **kw):
        o = cls(*a, r7_configs=st.default_configs(symbols), **kw); cap.append(o); return o
else:
    from revision8_avr.r8_engine import engine_class; import revision8_avr.r8_state as st
    cls = engine_class(native); pre = "r8"
    def factory(*a, **kw):
        o = cls(*a, r8_configs=st.default_configs(symbols), r8_adaptation_mode=run, **kw); cap.append(o); return o
setattr(runner, attr, factory)
t = time.time(); rep = go(data, nifty, vix, warmup=60); o = cap[-1]
dec = list(getattr(o, pre + "_decisions"))
row = {"engine": engine, "run": run, "bars": bars, "secs": round(time.time() - t),
       "decisions": getattr(o, pre + "_decision_count"), "digest": getattr(o, pre + "_decision_digest")[:16],
       "trades": len(rep.get("trades") or []), "net_pnl": (rep.get("r5_ledger") or {}).get("net_pnl"),
       "pulses": dict(__import__("collections").Counter(d["pulse"] for d in dec))}
if run != "R7":
    r = o.r8_adaptation_report(); row["adaptation"] = r
    if run == "paper":
        ck = o.r8_snapshot(); o.r8_restore(json.loads(json.dumps(ck, allow_nan=False))); row["checkpoint_roundtrip"] = o.r8_snapshot() == ck
# decision records without MarketAVR telemetry, renamed, for R7-vs-R8 field comparison
strip = [json.loads(json.dumps({k: v for k, v in d.items() if k != "avr"}, sort_keys=True, default=str).replace("r8", "r7").replace("R8", "R7")) for d in dec]
json.dump(strip, open(f"{outdir}/dec_{engine}_{run}_{bars}.json", "w"))
print(json.dumps(row, default=str), flush=True)
