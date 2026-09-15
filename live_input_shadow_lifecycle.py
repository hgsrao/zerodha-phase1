"""Local-only lifecycle driven by live collector telemetry; never broker writes."""
from __future__ import annotations
import argparse, json, os, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping
from uuid import uuid4

MARKET_GATES={"quote_integrity","universe_coverage","momentum","stability","affordability","obi"}
def now(): return datetime.now(timezone.utc).isoformat()
def read_json(path):
    try: value=json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError,UnicodeError,json.JSONDecodeError): return {}
    return value if isinstance(value,dict) else {}
def positive_decimal(value,name):
    try: result=Decimal(str(value))
    except (InvalidOperation,ValueError,TypeError) as exc: raise ValueError(f"{name} malformed") from exc
    if not result.is_finite() or result<=0: raise ValueError(f"{name} invalid")
    return result
def fresh(t,max_age=45):
    try:
        stamp=datetime.fromisoformat(str(t["observed_at_utc"]).replace("Z","+00:00"))
        if stamp.tzinfo is None: stamp=stamp.replace(tzinfo=timezone.utc)
        age=(datetime.now(timezone.utc)-stamp).total_seconds()
        return 0<=age<=max_age
    except (KeyError,ValueError,TypeError): return False
def gates_pass(t):
    checks=t.get("checkpoints")
    if not isinstance(checks,list): return False,"CHECKPOINTS_MISSING"
    by={x.get("name"):x for x in checks if isinstance(x,dict)}
    missing=MARKET_GATES-set(by)
    if missing: return False,"GATES_MISSING:"+",".join(sorted(missing))
    failed=sorted(n for n in MARKET_GATES if by[n].get("passed") is not True)
    return (False,"GATES_BLOCKED:"+",".join(failed)) if failed else (True,"ALL_MARKET_GATES_PASS")
def initial_state():
    return {"mode":"LIVE_INPUT_SHADOW_ONLY","status":"WAITING_FOR_MARKET_GATES","active_trade":None,
            "completed_trades":[],"last_reason":"INITIALIZED","updated_at_utc":now(),
            "safety":{"broker_network":False,"broker_write":False,"order_api":False,
                      "production_state_mutation":False,"live_trading_enabled":False,"gate_4":"LOCKED"}}
def step(state,t,stop_pct=Decimal("0.02"),target_pct=Decimal("0.04")):
    if not fresh(t): state.update(last_reason="STALE_OR_MISSING_TELEMETRY",updated_at_utc=now()); return "NO_ACTION"
    candidate=t.get("candidate")
    if not isinstance(candidate,Mapping): state.update(last_reason="CANDIDATE_MISSING",updated_at_utc=now()); return "NO_ACTION"
    if state.get("status")=="WAITING_FOR_MARKET_GATES":
        passed,reason=gates_pass(t); state["last_reason"]=reason
        if not passed: state["updated_at_utc"]=now(); return "NO_ACTION"
        symbol=str(candidate.get("symbol","")).strip().upper(); qty=int(candidate.get("tranche_qty",0) or 0)
        price=positive_decimal(candidate.get("last_price"),"last_price")
        if not symbol or qty<=0: raise ValueError("candidate symbol or quantity invalid")
        trade_id="SHADOW-"+uuid4().hex[:12].upper()
        state["active_trade"]={"trade_id":trade_id,"entry_order_id":trade_id+"-ENTRY",
          "stop_order_id":trade_id+"-STOP","symbol":symbol,"quantity":qty,"entry_price":str(price),
          "last_price":str(price),"stop_price":str((price*(1-stop_pct)).quantize(Decimal("0.01"))),
          "target_price":str((price*(1+target_pct)).quantize(Decimal("0.01"))),
          "entry_score":candidate.get("score"),"entry_obi":candidate.get("obi"),
          "entry_observed_at_utc":t.get("observed_at_utc"),"fill_source":"LIVE_OBSERVED_LTP_SHADOW_FILL"}
        state.update(status="MANAGING_SHADOW_POSITION",last_reason="SHADOW_ENTRY_FILLED_AND_PROTECTED",updated_at_utc=now())
        return "SHADOW_ENTRY"
    if state.get("status")=="MANAGING_SHADOW_POSITION":
        trade=state.get("active_trade")
        if not isinstance(trade,dict): raise RuntimeError("managing state missing trade")
        if str(candidate.get("symbol","")).upper()!=trade["symbol"]:
            state.update(last_reason="SELECTED_SYMBOL_CHANGED_NO_ACTION",updated_at_utc=now()); return "NO_ACTION"
        price=positive_decimal(candidate.get("last_price"),"last_price"); entry=Decimal(trade["entry_price"]); qty=int(trade["quantity"])
        trade["last_price"]=str(price); trade["unrealised_pnl"]=str(((price-entry)*qty).quantize(Decimal("0.01")))
        reason="SHADOW_STOP_TRIGGERED" if price<=Decimal(trade["stop_price"]) else ("SHADOW_TARGET_TRIGGERED" if price>=Decimal(trade["target_price"]) else None)
        if reason:
            done=dict(trade); done.update(exit_order_id=trade["trade_id"]+"-EXIT",exit_price=str(price),exit_reason=reason,
              realised_pnl=str(((price-entry)*qty).quantize(Decimal("0.01"))),exit_observed_at_utc=t.get("observed_at_utc"))
            state.setdefault("completed_trades",[]).append(done); state.update(active_trade=None,status="WAITING_FOR_MARKET_GATES",last_reason=reason,updated_at_utc=now())
            return "SHADOW_EXIT"
        state.update(last_reason="SHADOW_POSITION_MANAGED",updated_at_utc=now()); return "MANAGED"
    raise RuntimeError("unsupported shadow state")
def write_state(path,state):
    path=Path(path)
    if path.name.lower() in {"bot_state_v34.json","bot_state_v34.lock","shadow_strategy_telemetry.json"}: raise ValueError("refusing protected target")
    tmp=path.with_suffix(path.suffix+".tmp"); tmp.write_text(json.dumps(state,indent=2,sort_keys=True),encoding="utf-8"); os.replace(tmp,path)
def run(source,target,interval):
    if interval<1: raise ValueError("interval must be >= 1")
    state=read_json(target) or initial_state()
    print("V3.4 LIVE-INPUT SHADOW LIFECYCLE\nExecution: SHADOW ONLY; broker writes unavailable\nPress Ctrl+C to stop this shadow process only.")
    while True:
        try: event=step(state,read_json(source))
        except Exception as exc: state.update(last_reason=f"FAIL_CLOSED:{type(exc).__name__}:{exc}",updated_at_utc=now()); event="FAIL_CLOSED"
        write_state(target,state); trade=state.get("active_trade") or {}
        print(f"{state['updated_at_utc']} event={event} status={state['status']} symbol={trade.get('symbol','-')} reason={state['last_reason']}")
        time.sleep(interval)
def main():
    p=argparse.ArgumentParser(); p.add_argument("--input",default="shadow_strategy_telemetry.json"); p.add_argument("--state",default="shadow_lifecycle_state.json"); p.add_argument("--interval-seconds",type=float,default=3); a=p.parse_args()
    try: run(Path(a.input),Path(a.state),a.interval_seconds)
    except KeyboardInterrupt: print("Live-input shadow lifecycle stopped.")
    return 0
if __name__=="__main__": raise SystemExit(main())
