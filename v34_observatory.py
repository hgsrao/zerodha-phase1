#!/usr/bin/env python3
"""
V3.4 Observatory - read-only local dashboard.

Safety design:
- Does NOT import the trading engine.
- Does NOT connect to Zerodha/Kite.
- Does NOT place, modify, or cancel orders.
- Reads only local JSON state and log files.
- Defaults to observation/staging artifacts.
"""

from __future__ import annotations
import argparse
import json
import re
import threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

HTML = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>V3.4 Observatory</title>
<style>
:root{
  --bg:#071018; --panel:#0d1822; --panel2:#101f2b; --line:#203341;
  --text:#e8f0f5; --muted:#8fa4b3; --green:#38d996; --amber:#f4c95d;
  --red:#ff6673; --blue:#63b3ff; --white:#fff;
}
*{box-sizing:border-box}
body{margin:0;background:linear-gradient(145deg,#061018,#0a141d 55%,#08111a);
font-family:Inter,Segoe UI,Arial,sans-serif;color:var(--text)}
.wrap{max-width:1500px;margin:auto;padding:20px}
.header{display:flex;justify-content:space-between;align-items:flex-start;gap:20px;margin-bottom:18px}
h1{margin:0;font-size:28px;letter-spacing:.4px}
.sub{color:var(--muted);margin-top:5px}
.badges{display:flex;gap:8px;flex-wrap:wrap;justify-content:flex-end}
.badge{border:1px solid var(--line);background:#0c1822;border-radius:999px;padding:7px 11px;font-size:12px}
.badge.green{border-color:#1f7758;color:var(--green)}
.badge.amber{border-color:#7d6321;color:var(--amber)}
.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}
.grid2{display:grid;grid-template-columns:1.25fr .75fr;gap:12px;margin-top:12px}
.panel{background:rgba(13,24,34,.94);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:0 10px 35px rgba(0,0,0,.18)}
.cardtitle{display:flex;justify-content:space-between;align-items:center;margin-bottom:10px}
.cardtitle strong{font-size:14px}
.status{font-size:11px;font-weight:700;padding:5px 8px;border-radius:999px}
.pass{background:#0d3227;color:var(--green)}
.warn{background:#3a2d0b;color:var(--amber)}
.fail{background:#3a1117;color:var(--red)}
.info{background:#0c2940;color:var(--blue)}
.metric{font-size:25px;font-weight:750;margin:7px 0}
.muted{color:var(--muted);font-size:12px}
.row{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #182833}
.row:last-child{border-bottom:0}
.value{font-weight:650;text-align:right}
.pipeline{display:flex;align-items:center;gap:7px;flex-wrap:wrap;margin-top:8px}
.stage{padding:10px 12px;border:1px solid var(--line);border-radius:10px;background:#0b151e;font-size:12px}
.stage.current{border-color:var(--blue);box-shadow:0 0 0 1px rgba(99,179,255,.25);color:var(--blue)}
.arrow{color:#516979}
table{width:100%;border-collapse:collapse;font-size:12px}
th,td{text-align:left;padding:8px;border-bottom:1px solid #1a2a35}
th{color:var(--muted);font-weight:600}
pre{white-space:pre-wrap;word-break:break-word;margin:0;color:#bfd0da;font-size:11px;line-height:1.45}
.event{padding:7px 0;border-bottom:1px solid #182833}
.event b{color:var(--muted);font-weight:500}
.footer{margin-top:12px;color:#6f8796;font-size:11px}
@media(max-width:1000px){.grid4,.grid2{grid-template-columns:1fr 1fr}}
@media(max-width:650px){.grid4,.grid2{grid-template-columns:1fr}.header{display:block}.badges{justify-content:flex-start;margin-top:12px}}
</style>
</head>
<body>
<div class="wrap">
  <div class="header">
    <div>
      <h1>V3.4 Observatory</h1>
      <div class="sub">Read-only operational cockpit • staging-safe • auto refresh</div>
    </div>
    <div class="badges">
      <span id="mode" class="badge amber">MODE: —</span>
      <span id="kite" class="badge">KITE: —</span>
      <span id="updated" class="badge">UPDATED: —</span>
    </div>
  </div>

  <div class="grid4">
    <div class="panel">
      <div class="cardtitle"><strong>PILLAR 1 · RECONCILIATION</strong><span id="p1s" class="status info">—</span></div>
      <div id="p1m" class="metric">—</div>
      <div class="muted">Broker ↔ local state</div>
    </div>
    <div class="panel">
      <div class="cardtitle"><strong>PILLAR 2 · EXECUTION</strong><span id="p2s" class="status info">—</span></div>
      <div id="p2m" class="metric">—</div>
      <div class="muted">Order lifecycle / protection</div>
    </div>
    <div class="panel">
      <div class="cardtitle"><strong>PILLAR 3 · POSITION</strong><span id="p3s" class="status info">—</span></div>
      <div id="p3m" class="metric">—</div>
      <div class="muted">Position / trade context</div>
    </div>
    <div class="panel">
      <div class="cardtitle"><strong>PILLAR 4 · RISK</strong><span id="p4s" class="status info">—</span></div>
      <div id="p4m" class="metric">—</div>
      <div class="muted">Realised + unrealised MTM</div>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="cardtitle"><strong>ENGINE PIPELINE</strong><span id="stateBadge" class="status info">—</span></div>
      <div class="pipeline" id="pipeline"></div>
      <div style="margin-top:14px">
        <div class="row"><span class="muted">Current state</span><span id="state" class="value">—</span></div>
        <div class="row"><span class="muted">Trading day</span><span id="day" class="value">—</span></div>
        <div class="row"><span class="muted">Active trade</span><span id="active" class="value">—</span></div>
        <div class="row"><span class="muted">Halt source</span><span id="halt" class="value">—</span></div>
        <div class="row"><span class="muted">Operator acknowledgement</span><span id="ack" class="value">—</span></div>
      </div>
    </div>

    <div class="panel">
      <div class="cardtitle"><strong>RISK SNAPSHOT</strong><span id="riskBadge" class="status info">—</span></div>
      <div class="row"><span class="muted">Realised net P&L</span><span id="rpnl" class="value">—</span></div>
      <div class="row"><span class="muted">Unrealised MTM</span><span id="umpnl" class="value">—</span></div>
      <div class="row"><span class="muted">Total P&L</span><span id="tpnl" class="value">—</span></div>
      <div class="row"><span class="muted">Daily loss limit</span><span id="limit" class="value">—</span></div>
      <div class="row"><span class="muted">Clearance required</span><span id="clearance" class="value">—</span></div>
    </div>
  </div>

  <div class="grid2">
    <div class="panel">
      <div class="cardtitle"><strong>TRADE / ORDER CONTEXT</strong><span class="status info">READ ONLY</span></div>
      <div id="trade"></div>
    </div>
    <div class="panel">
      <div class="cardtitle"><strong>RECENT ENGINE EVENTS</strong><span id="eventCount" class="status info">0</span></div>
      <div id="events"></div>
    </div>
  </div>

  <div class="panel" style="margin-top:12px">
    <div class="cardtitle"><strong>RAW OBSERVATION PAYLOAD</strong><span class="status info">NO COMMAND CHANNEL</span></div>
    <pre id="raw">Loading…</pre>
  </div>

  <div class="footer">
    This dashboard only reads local files. It cannot place, modify, cancel, or transmit a Zerodha order.
  </div>
</div>
<script>
const stages=["STARTUP","RECONCILING","FLAT","QUANT_SCAN","CANDIDATE_VALIDATION","ENTRY_PENDING","MANAGING","EXIT","RECONCILIATION_HALT"];
function esc(x){return String(x??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]));}
function badge(id,text,kind="info"){const e=document.getElementById(id);e.textContent=text;e.className="status "+kind;}
function fmt(v){if(v===null||v===undefined||v==="")return "—";return String(v);}
function set(id,v){document.getElementById(id).textContent=fmt(v);}
function money(v){const n=Number(v);return Number.isFinite(n)?"₹"+n.toLocaleString("en-IN",{minimumFractionDigits:2,maximumFractionDigits:2}):fmt(v);}
function kind(status){return status==="PASS"||status==="SAFE"?"pass":status==="WARN"||status==="NOT INSTRUMENTED"?"warn":status==="HALT"||status==="FAIL"?"fail":"info";}
function render(d){
  const s=d.state||{};
  const obs=d.observation||{};
  const mode=obs.live_trading_enabled===false?"OBSERVATION ONLY":(obs.live_trading_enabled===true?"LIVE TRADING":"UNKNOWN");
  document.getElementById("mode").textContent="MODE: "+mode;
  document.getElementById("mode").className="badge "+(mode==="OBSERVATION ONLY"?"amber":"fail");
  document.getElementById("kite").textContent="KITE: "+(obs.kite_connected?"CONNECTED":"NOT VERIFIED");
  document.getElementById("kite").className="badge "+(obs.kite_connected?"green":"amber");
  document.getElementById("updated").textContent="UPDATED: "+new Date().toLocaleTimeString();

  const rec=obs.reconciliation||{};
  badge("p1s",rec.status||"NOT INSTRUMENTED",kind(rec.status));
  document.getElementById("p1m").textContent=rec.summary||"—";

  const ex=obs.execution||{};
  badge("p2s",ex.status||"NOT INSTRUMENTED",kind(ex.status));
  document.getElementById("p2m").textContent=ex.summary||"—";

  const pos=obs.position||{};
  badge("p3s",pos.status||"NOT INSTRUMENTED",kind(pos.status));
  document.getElementById("p3m").textContent=pos.summary||"—";

  const risk=obs.risk||{};
  badge("p4s",risk.status||"—",kind(risk.status));
  document.getElementById("p4m").textContent=risk.summary||"—";

  const current=String(s.status||"UNKNOWN").toUpperCase();
  badge("stateBadge",current,current==="RECONCILIATION_HALT"?"fail":"info");
  document.getElementById("pipeline").innerHTML=stages.map((x,i)=>{
    const active=x===current;
    return (i?'<span class="arrow">→</span>':'')+'<span class="stage '+(active?'current':'')+'">'+esc(x)+'</span>';
  }).join("");
  set("state",current);set("day",s.trading_day);set("active",s.active_trade?"YES":"NO");
  set("halt",s.halt_source||s.halt_reason||"—");set("ack",s.operator_acknowledgement||"—");

  set("rpnl",money(s.realised_net_pnl));set("umpnl",money(s.unrealised_mtm));
  const total=(Number(s.realised_net_pnl||0)+Number(s.unrealised_mtm||0));
  set("tpnl",money(total));set("limit",money(obs.daily_loss_limit));
  set("clearance",s.clearance_required===true?"YES":"NO");
  badge("riskBadge",risk.status||"—",kind(risk.status));

  const t=s.active_trade;
  document.getElementById("trade").innerHTML=t?Object.entries(t).map(([k,v])=>`<div class="row"><span class="muted">${esc(k)}</span><span class="value">${esc(v)}</span></div>`).join(""):"<div class='muted'>No active trade context.</div>";

  const events=d.events||[];
  document.getElementById("eventCount").textContent=events.length;
  document.getElementById("events").innerHTML=events.length?events.map(e=>`<div class="event"><b>${esc(e.time)}</b> ${esc(e.level)} — ${esc(e.message)}</div>`).join(""):"<div class='muted'>No parsed events.</div>";
  document.getElementById("raw").textContent=JSON.stringify(d,null,2);
}
async function refresh(){try{const r=await fetch("/api/snapshot?ts="+Date.now(),{cache:"no-store"});render(await r.json());}catch(e){document.getElementById("raw").textContent="Dashboard read error: "+e;}}
refresh();setInterval(refresh,1000);
</script>
</body>
</html>
"""

def read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as exc:
        return {"_read_error": str(exc)}

def parse_boolish(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true","1","yes","on")
    return False

def parse_log(path: Path, limit=40):
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]
    except Exception:
        return []
    out=[]
    rx=re.compile(r"^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+)\s+\[(\w+)\]\s+(.*)$")
    for line in lines:
        m=rx.match(line)
        if m:
            out.append({"time":m.group(1),"level":m.group(2),"message":m.group(3)})
    return out[-limit:]

def build_snapshot(base: Path):
    state_path=base/"bot_state_v34_staging.json"
    log_path=base/"bot_production.log"
    state=read_json(state_path)
    events=parse_log(log_path)

    # These are intentionally conservative: the dashboard only claims what the
    # state/log files can substantiate. It does not invent internal engine telemetry.
    text="\n".join(e["message"] for e in events[-80:])
    recon_status="PASS" if "STARTUP_RECONCILIATION_PASSED" in text else "NOT INSTRUMENTED"
    recon_summary="Broker/local reconciliation passed" if recon_status=="PASS" else "No confirmed reconciliation event"
    execution_status="SAFE" if "LIVE ORDER EXECUTION IS DISABLED" in text or "OBSERVATION-ONLY MODE ACTIVE" in text else "NOT INSTRUMENTED"
    execution_summary="Physical live execution disabled" if execution_status=="SAFE" else "Order lifecycle telemetry not available"
    active=bool(state.get("active_trade"))
    position_status="FLAT" if not active and str(state.get("status","")).upper()=="FLAT" else ("MANAGING" if active else "NOT INSTRUMENTED")
    position_summary="No local active trade" if position_status=="FLAT" else ("Active local trade present" if active else "Position telemetry not available")
    try:
        total=float(state.get("realised_net_pnl","0"))+float(state.get("unrealised_mtm","0"))
        limit=float(state.get("daily_loss_limit", "0") or 0)
    except Exception:
        total=0.0; limit=0.0
    risk_status="SAFE"
    if state.get("clearance_required") is True or str(state.get("status","")).upper()=="RECONCILIATION_HALT":
        risk_status="HALT"
    elif limit and total <= -abs(limit):
        risk_status="HALT"
    risk_summary="Risk gate not tripped" if risk_status=="SAFE" else "Risk/clearance halt indicated"

    # The runner's current production code uses an explicit constant for this.
    # We infer observation-only only from the log, not from an executable import.
    live_enabled = False if ("LIVE_TRADING_ENABLED = False" in text or "LIVE ORDER EXECUTION IS DISABLED" in text) else None
    kite_connected = "Connected to Kite successfully." in text

    return {
        "observation":{
            "live_trading_enabled": live_enabled,
            "kite_connected": kite_connected,
            "daily_loss_limit": state.get("daily_loss_limit"),
            "reconciliation":{"status":recon_status,"summary":recon_summary},
            "execution":{"status":execution_status,"summary":execution_summary},
            "position":{"status":position_status,"summary":position_summary},
            "risk":{"status":risk_status,"summary":risk_summary},
        },
        "state":state,
        "events":events,
        "paths":{"state":str(state_path),"log":str(log_path)},
        "server_time":datetime.now().isoformat(timespec="seconds"),
    }

class Handler(BaseHTTPRequestHandler):
    base=Path(".").resolve()
    def log_message(self, fmt, *args):
        pass
    def do_GET(self):
        path=urlparse(self.path).path
        if path=="/":
            body=HTML.encode("utf-8")
            self.send_response(200); self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        if path=="/api/snapshot":
            body=json.dumps(build_snapshot(self.base),default=str).encode("utf-8")
            self.send_response(200); self.send_header("Content-Type","application/json; charset=utf-8")
            self.send_header("Cache-Control","no-store"); self.send_header("Content-Length",str(len(body))); self.end_headers(); self.wfile.write(body)
            return
        self.send_error(404)

def main():
    ap=argparse.ArgumentParser(description="V3.4 read-only observability dashboard")
    ap.add_argument("--base",default=".",help="Bot directory containing staging state/log")
    ap.add_argument("--host",default="127.0.0.1")
    ap.add_argument("--port",type=int,default=8765)
    args=ap.parse_args()
    Handler.base=Path(args.base).resolve()
    server=ThreadingHTTPServer((args.host,args.port),Handler)
    print("====================================================")
    print(" V3.4 OBSERVATORY — READ ONLY")
    print("====================================================")
    print("Bot directory :",Handler.base)
    print("Dashboard     :",f"http://{args.host}:{args.port}/")
    print("State source  :",Handler.base/"bot_state_v34_staging.json")
    print("Log source    :",Handler.base/"bot_production.log")
    print("Safety        : NO KITE IMPORT / NO ORDER API / READ ONLY")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[INFO] Observatory stopped.")
    finally:
        server.server_close()

if __name__=="__main__":
    main()
