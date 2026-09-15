"""V3.4 Observatory v4: local, read-only correlation dashboard across all
four of today's independent programs - production bot, momentum shadow,
ORB shadow, and the entry-gate dry run - on one screen.

Extends v34_observatory_v3.py (production state/log + momentum shadow)
with two more panels (ORB, entry-gate dry run) rather than replacing it -
v3's snapshot()/newest_session_log()/read_json() behavior is preserved
byte-for-byte for the fields it already reported.

Safety, unchanged from v3: no broker SDK import, no order API, no writes
of any kind - this process only ever reads JSON/log files four other
processes already wrote and serves them over localhost.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from v34_observatory_v3 import newest_session_log, read_json

HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">
<title>V3.4 Observatory v4</title>
<style>
:root{color-scheme:dark;--bg:#07121b;--card:#0d202c;--line:#1d4052;--text:#e9f6ff;--muted:#8eb0c2;--ok:#21d38b;--warn:#ffc857;--bad:#ff6577;--blue:#39a9ff}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px system-ui,Segoe UI,sans-serif}.wrap{max-width:1500px;margin:auto;padding:22px}h1{margin:0;font-size:28px}.sub{color:var(--muted);margin:6px 0 18px}.grid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:15px}.span2{grid-column:span 2}.span4{grid-column:span 4}.label{font-size:11px;color:var(--muted);text-transform:uppercase;letter-spacing:.08em}.value{font-size:20px;font-weight:750;margin-top:8px}.pill{display:inline-block;border-radius:999px;padding:4px 9px;font-size:11px;font-weight:700}.ok{color:var(--ok);background:#0c3b31}.warn{color:var(--warn);background:#453716}.bad{color:var(--bad);background:#421d29}.row{display:flex;justify-content:space-between;gap:12px;padding:9px 0;border-bottom:1px solid #163443}.row:last-child{border:0}.muted{color:var(--muted)}table{width:100%;border-collapse:collapse}th,td{text-align:left;padding:9px;border-bottom:1px solid #163443}th{color:var(--muted);font-size:11px;text-transform:uppercase}.footer{color:var(--muted);margin-top:14px;font-size:12px}@media(max-width:900px){.grid{grid-template-columns:1fr 1fr}.span4{grid-column:span 2}}@media(max-width:560px){.grid{grid-template-columns:1fr}.span2,.span4{grid-column:span 1}}
</style></head><body><div class="wrap">
<h1>V3.4 Observatory v4</h1><div class="sub">Correlated view: production bot &middot; momentum shadow &middot; ORB shadow &middot; entry-gate dry run</div>
<div id="app" class="grid"><div class="card span4">Loading local telemetry…</div></div>
<div class="footer">No Kite import here &middot; No order API &middot; No writes of any kind &middot; Refreshes every 3 seconds</div>
</div><script>
const esc=x=>String(x??'—').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const pill=(text,kind)=>`<span class="pill ${kind}">${esc(text)}</span>`;
function render(d){
 const s=d.state||{},t=d.shadow||{},c=t.candidate||{},caps=t.capabilities||{},e=t.external_variant||{};
 const shadowReady=d.shadow_available===true, blocked=t.decision!=='HYPOTHETICAL_BUY';
 const external=(e.selected||[]).map(x=>`<tr><td>${esc(x.symbol)}</td><td>${Number(x.momentum_12_1).toFixed(4)}</td><td>${esc(x.live_price)}</td><td>${esc(x.paper_quantity)}</td><td>${esc(x.paper_value==null?'WAITING':Number(x.paper_value).toFixed(2))}</td></tr>`).join('');

 const orb=d.orb||{}, orbDecision=orb.decision||'WAITING';
 const orbCandidates=(orb.candidates||[]).map(x=>`<tr><td>${esc(x.symbol)}</td><td>${esc(x.last_price)}</td><td>${esc(x.orb_high)}</td><td>${esc(x.orb_low)}</td><td>${Number(x.breakout_strength_pct).toFixed(3)}%</td></tr>`).join('');
 const orbKind = orbDecision==='HYPOTHETICAL_BUY' ? 'ok' : (orbDecision==='ESTABLISHING_OPENING_RANGE' ? 'warn' : 'warn');

 const gate=d.entry_gate||{};
 const gateRows=(gate.evaluations||[]).map(x=>{
   const g=x.risk_gate||{}, order=x.order_payload||{};
   return `<tr><td>${esc(x.candidate_source)}</td><td>${esc(order.tradingsymbol)}</td><td>${esc(g.quantity)}</td><td>${esc(g.final_stage_reached)}</td><td>${pill(g.allowed?'ALLOWED':'BLOCKED',g.allowed?'ok':'warn')}</td><td class="muted">${esc(g.reason)}</td><td>${pill(x.physical_dispatch_gate?.status||'?','bad')}</td></tr>`;
 }).join('');

 document.getElementById('app').innerHTML=`
 <div class="card"><div class="label">Execution posture (production)</div><div class="value">${pill(d.execution_disabled?'DISABLED':'UNCONFIRMED',d.execution_disabled?'ok':'bad')}</div></div>
 <div class="card"><div class="label">Local engine state</div><div class="value">${esc(s.status)}</div></div>
 <div class="card"><div class="label">Active trade</div><div class="value">${pill(s.active_trade?'YES':'NO',s.active_trade?'warn':'ok')}</div></div>
 <div class="card"><div class="label">Momentum telemetry freshness</div><div class="value">${pill(d.shadow_fresh?'FRESH':(shadowReady?'STALE':'WAITING'),d.shadow_fresh?'ok':'warn')}</div></div>

 <div class="card span2"><div class="label">Window 2 &mdash; momentum shadow decision</div><div class="value">${pill(shadowReady?esc(t.decision):'WAITING FOR TELEMETRY',shadowReady?(blocked?'warn':'ok'):'warn')}</div><div class="row"><span>Selected symbol</span><b>${esc(c.symbol)}</b></div><div class="row"><span>Score</span><b>${esc(c.score)}</b></div></div>
 <div class="card span2"><div class="label">Window 1 &mdash; safety boundary</div><div class="row"><span>Authoritative strategy</span><b>${pill(t.authoritative_strategy?'YES':'NO',t.authoritative_strategy?'bad':'ok')}</b></div><div class="row"><span>Broker write capability</span><b>${pill(caps.broker_write?'YES':'NO',caps.broker_write?'bad':'ok')}</b></div><div class="row"><span>Order API capability</span><b>${pill(caps.order_api?'YES':'NO',caps.order_api?'bad':'ok')}</b></div></div>

 <div class="card span4"><div class="label">Window 2 &mdash; external published-model variant (12-1 momentum, paper only)</div><div class="row"><span>Status</span><b>${pill(esc(e.status||'WAITING'),e.status==='MARKED'?'ok':'warn')}</b></div><div class="row"><span>Formation period</span><b>${esc(e.formation_period)}</b></div><div class="row"><span>Paper capital / equity</span><b>${esc(e.paper_starting_capital)} / ${esc(e.paper_equity==null?'WAITING':Number(e.paper_equity).toFixed(2))}</b></div><div class="row"><span>Paper return</span><b>${esc(e.paper_return==null?'WAITING':(100*Number(e.paper_return)).toFixed(3)+'%')}</b></div><table><thead><tr><th>Symbol</th><th>12-1 score</th><th>Live price</th><th>Paper qty</th><th>Paper value</th></tr></thead><tbody>${external||'<tr><td colspan="5">Waiting for external variant telemetry</td></tr>'}</tbody></table></div>

 <div class="card span4"><div class="label">Window 3 &mdash; ORB shadow (exploratory, not backtested)</div><div class="row"><span>Decision</span><b>${pill(esc(orbDecision),orbKind)}</b></div><div class="row"><span>Opening ranges established</span><b>${esc((orb.symbols_with_established_range||[]).length)} / 19</b></div><table><thead><tr><th>Symbol</th><th>Last price</th><th>ORB high</th><th>ORB low</th><th>Breakout strength</th></tr></thead><tbody>${orbCandidates||'<tr><td colspan="5">No breakout candidates yet</td></tr>'}</tbody></table></div>

 <div class="card span4"><div class="label">Window 4 &mdash; entry gate dry run (real RunnerEntryAuthorizer, zero orders)</div><div class="row"><span>Candidates this cycle</span><b>${esc(gate.candidates_found)}</b></div><table><thead><tr><th>Source</th><th>Symbol</th><th>Qty</th><th>Stage reached</th><th>Verdict</th><th>Reason</th><th>Dispatch</th></tr></thead><tbody>${gateRows||'<tr><td colspan="7">No active candidate</td></tr>'}</tbody></table></div>

 <div class="card span4"><div class="label">Sources</div><div class="row"><span>State</span><b>${esc(d.paths?.state)}</b></div><div class="row"><span>Production log</span><b>${esc(d.paths?.log)}</b></div><div class="row"><span>Momentum shadow</span><b>${esc(d.paths?.shadow)}</b></div><div class="row"><span>ORB shadow</span><b>${esc(d.paths?.orb)}</b></div><div class="row"><span>Entry gate dry run</span><b>${esc(d.paths?.entry_gate)}</b></div><div class="row"><span>Dashboard updated</span><b>${esc(d.server_time)}</b></div></div>`}
async function tick(){try{let r=await fetch('/api/snapshot',{cache:'no-store'});render(await r.json())}catch(e){document.getElementById('app').innerHTML=`<div class="card span4">Dashboard read error: ${esc(e)}</div>`}}tick();setInterval(tick,3000);
</script></body></html>'''


def snapshot(base: Path) -> dict[str, Any]:
    state_path = base / "bot_state_v34.json"
    shadow_path = base / "shadow_strategy_telemetry.json"
    orb_path = base / "orb_shadow_telemetry.json"
    entry_gate_path = base / "entry_gate_dry_run_telemetry.json"
    log_path = newest_session_log(base)
    try:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        log_text = ""
    state = read_json(state_path)
    shadow = read_json(shadow_path)
    orb = read_json(orb_path)
    entry_gate = read_json(entry_gate_path)
    shadow_fresh = False
    observed_at = shadow.get("observed_at_utc")
    if isinstance(observed_at, str):
        try:
            observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            if observed.tzinfo is None:
                observed = observed.replace(tzinfo=timezone.utc)
            shadow_fresh = (datetime.now(timezone.utc) - observed).total_seconds() <= 45
        except ValueError:
            shadow_fresh = False
    return {
        "state": state,
        "shadow": shadow,
        "orb": orb,
        "entry_gate": entry_gate,
        "shadow_available": bool(shadow),
        "shadow_fresh": shadow_fresh,
        "execution_disabled": (
            "LIVE ORDER EXECUTION IS DISABLED" in log_text
            and "LIVE_TRADING_ENABLED = False" in log_text
        ),
        "paths": {
            "state": str(state_path),
            "log": str(log_path),
            "shadow": str(shadow_path),
            "orb": str(orb_path),
            "entry_gate": str(entry_gate_path),
        },
        "server_time": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


class Handler(BaseHTTPRequestHandler):
    base = Path(".").resolve()

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/snapshot":
            payload = json.dumps(snapshot(self.base)).encode("utf-8")
            content_type = "application/json; charset=utf-8"
        elif path == "/":
            payload = HTML.encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, _format: str, *args: Any) -> None:
        return


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default=".")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    Handler.base = Path(args.base).resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print("V3.4 OBSERVATORY v4 — READ ONLY — 4-WINDOW CORRELATION VIEW")
    print(f"Dashboard: http://{args.host}:{args.port}/")
    print(f"Base: {Handler.base}")
    print("Inputs: production state/log + momentum shadow + ORB shadow + entry-gate dry run")
    print("Safety: NO KITE IMPORT / NO ORDER API / NO WRITES OF ANY KIND")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
