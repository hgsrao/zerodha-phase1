#!/usr/bin/env python3
"""V3.4 Observatory v2 - read-only live dashboard + deterministic observability validation.

Safety:
- Never imports the trading engine or Kite.
- Never places/modifies/cancels orders.
- Live mode reads only local staging JSON/log files.
- Validation mode uses synthetic, deterministic fixtures only; it cannot touch the broker.
"""
from __future__ import annotations
import argparse, json, re
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

STAGES=["STARTUP","RECONCILING","FLAT","QUANT_SCAN","CANDIDATE_VALIDATION","ENTRY_PENDING","PARTIAL_POSITION","PROTECTION_PENDING","MANAGING","EXIT_CANCEL_SL","EXIT_SUBMIT","EXIT_PENDING","RECONCILIATION_HALT"]
SCENARIOS=[
    ("FLAT", "Clean startup / no trade"),
    ("ENTRY_PENDING", "Entry order accepted, awaiting fill"),
    ("PARTIAL_POSITION", "Partial entry fill"),
    ("PROTECTION_PENDING", "Position exists, SL being established"),
    ("MANAGING", "Fully protected active position"),
    ("EXIT_PENDING", "Exit order submitted / awaiting convergence"),
    ("FLAT_AFTER_EXIT", "Exit fully converged back to flat"),
    ("RECONCILIATION_HALT", "Broker/local mismatch requires hard halt"),
    ("INVARIANT_FAILURE", "Synthetic invariant violation — must show HALT"),
]


def read_json(p:Path):
    try: return json.loads(p.read_text(encoding='utf-8'))
    except Exception: return {}

def parse_log(p:Path, limit=60):
    if not p.exists(): return []
    try: lines=p.read_text(encoding='utf-8',errors='replace').splitlines()[-500:]
    except Exception: return []
    rx=re.compile(r'^(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+)\s+\[(\w+)\]\s+(.*)$')
    out=[]
    for line in lines:
        m=rx.match(line)
        if m: out.append({'time':m.group(1),'level':m.group(2),'message':m.group(3)})
    return out[-limit:]

def active_trade(qty=100, status='MANAGING'):
    return {
        'symbol':'DEMO-SYMBOL','entry_order_id':'DEMO-ENTRY-001','stop_order_id':'DEMO-SL-001',
        'exit_order_id':'','target_qty':qty,'filled_qty':qty,'broker_qty':qty,
        'entry_hwm':'125.20','stop_hwm':'124.10','exit_hwm':'—','state':status
    }

def validation_snapshot(scenario):
    base={'live_trading_enabled':False,'kite_connected':False,'daily_loss_limit':'1000','reconciliation':{'status':'PASS','summary':'Synthetic validation input'},'execution':{'status':'SAFE','summary':'No order API / simulation only'},'position':{'status':'FLAT','summary':'No position'},'risk':{'status':'SAFE','summary':'Risk gate not tripped'}}
    state={'status':'FLAT','trading_day':'2026-08-11','active_trade':None,'realised_net_pnl':'0','unrealised_mtm':'0','daily_loss_limit':'1000','clearance_required':False,'halt_source':'','operator_acknowledgement':''}
    inv=[]
    if scenario=='FLAT':
        pass
    elif scenario=='ENTRY_PENDING':
        state['status']='ENTRY_PENDING'; state['active_trade']={'symbol':'DEMO-SYMBOL','entry_order_id':'DEMO-ENTRY-001','target_qty':100,'filled_qty':0,'entry_hwm':'0','stop_order_id':''}; base['execution']={'status':'SAFE','summary':'Entry pending; no protective position yet'}
    elif scenario=='PARTIAL_POSITION':
        state['status']='PARTIAL_POSITION'; state['active_trade']=active_trade(100,'PARTIAL_POSITION'); state['active_trade']['filled_qty']=40; state['active_trade']['broker_qty']=40; state['active_trade']['stop_order_id']=''; base['position']={'status':'PARTIAL','summary':'40 / 100 filled'}; base['execution']={'status':'WARN','summary':'Partial entry; protection not yet active'}
    elif scenario=='PROTECTION_PENDING':
        state['status']='PROTECTION_PENDING'; state['active_trade']=active_trade(); state['active_trade']['stop_order_id']=''; base['position']={'status':'ACTIVE','summary':'100 / 100 filled'}; base['execution']={'status':'WARN','summary':'Position active; protective SL not yet proven'}
    elif scenario=='MANAGING':
        state['status']='MANAGING'; state['active_trade']=active_trade(); state['unrealised_mtm']='320'; base['position']={'status':'PASS','summary':'Protected active position'}; base['execution']={'status':'SAFE','summary':'Entry + protective SL converged'}; base['risk']={'status':'SAFE','summary':'Protected position within risk limits'}
    elif scenario=='EXIT_PENDING':
        state['status']='EXIT_PENDING'; state['active_trade']=active_trade(); state['active_trade']['exit_order_id']='DEMO-EXIT-001'; state['active_trade']['filled_qty']=60; state['active_trade']['broker_qty']=40; state['unrealised_mtm']='120'; base['position']={'status':'WARN','summary':'40 remaining; exit convergence pending'}; base['execution']={'status':'WARN','summary':'Exit pending; broker/local quantities must converge'}
    elif scenario=='FLAT_AFTER_EXIT':
        state['status']='FLAT'; state['active_trade']=None; state['realised_net_pnl']='240'; base['risk']={'status':'SAFE','summary':'Exit converged; no active exposure'}
    elif scenario=='RECONCILIATION_HALT':
        state['status']='RECONCILIATION_HALT'; state['halt_source']='BROKER_LOCAL_MISMATCH'; state['clearance_required']=True; base['reconciliation']={'status':'HALT','summary':'Synthetic broker/local mismatch'}; base['risk']={'status':'HALT','summary':'Clearance required before recovery'}
    elif scenario=='INVARIANT_FAILURE':
        state['status']='MANAGING'; state['active_trade']=active_trade(); state['active_trade']['broker_qty']=100; state['active_trade']['target_qty']=100; state['active_trade']['sl_qty']=50; base['execution']={'status':'HALT','summary':'Protective SL quantity mismatch'}; base['risk']={'status':'HALT','summary':'Invariant failure must halt'}
    # Derive invariants from fixture
    t=state.get('active_trade') or {}
    if t:
        broker=int(t.get('broker_qty',0) or 0); target=int(t.get('target_qty',0) or 0)
        sl=t.get('sl_qty', target if state['status']=='MANAGING' else None)
        inv.append({'name':'Broker quantity = local target','actual':f'{broker} = {target}','pass':broker==target})
        if state['status']=='MANAGING': inv.append({'name':'Protective SL exists','actual':'YES' if t.get('stop_order_id') else 'NO','pass':bool(t.get('stop_order_id'))})
        if state['status']=='MANAGING': inv.append({'name':'SL quantity = broker quantity','actual':f'{sl} = {broker}','pass':sl==broker})
    else:
        inv.append({'name':'No active position while FLAT','actual':'PASS','pass':True})
    if state['status']=='RECONCILIATION_HALT': inv.append({'name':'Halt state requires clearance','actual':'YES','pass':state['clearance_required']})
    overall=all(x['pass'] for x in inv)
    if not overall:
        state['status']='RECONCILIATION_HALT' if scenario=='INVARIANT_FAILURE' else state['status']
    return {'observation':base,'state':state,'events':[], 'paths':{'state':'SYNTHETIC VALIDATION FIXTURE','log':'SYNTHETIC VALIDATION FIXTURE'}, 'server_time':datetime.now().isoformat(timespec='seconds'),'validation':{'active':True,'scenario':scenario,'scenario_label':dict(SCENARIOS).get(scenario,scenario),'overall_pass':overall,'invariants':inv}}

def live_paths(base:Path):
    state_path=base/'bot_state_v34.json'
    session_logs=sorted(
        (base/'session_logs').glob('*/bot_production.log'),
        key=lambda path:path.parent.name,
        reverse=True,
    )
    log_path=session_logs[0] if session_logs else base/'bot_production.log'
    return state_path,log_path

def live_snapshot(base:Path):
    state_path,log_path=live_paths(base)
    state=read_json(state_path); events=parse_log(log_path)
    text='\n'.join(e['message'] for e in events[-100:]); status=str(state.get('status','UNKNOWN')).upper()
    rec='PASS' if 'STARTUP_RECONCILIATION_PASSED' in text else 'NOT INSTRUMENTED'
    ex='SAFE' if 'LIVE ORDER EXECUTION IS DISABLED' in text or 'OBSERVATION-ONLY MODE ACTIVE' in text else 'NOT INSTRUMENTED'
    active=bool(state.get('active_trade'))
    pos='FLAT' if status=='FLAT' and not active else ('ACTIVE' if active else 'NOT INSTRUMENTED')
    risk='HALT' if state.get('clearance_required') is True or status=='RECONCILIATION_HALT' else 'SAFE'
    inv=[]
    t=state.get('active_trade') or {}
    if active:
        broker=t.get('broker_qty',t.get('filled_qty',0)); target=t.get('target_qty',0)
        inv.append({'name':'Broker quantity = local target','actual':f'{broker} = {target}','pass':str(broker)==str(target)})
        if status in ('MANAGING','PROTECTION_PENDING'):
            inv.append({'name':'Protective SL exists','actual':'YES' if t.get('stop_order_id') else 'NO','pass':bool(t.get('stop_order_id'))})
    else: inv.append({'name':'No active position while FLAT','actual':'PASS','pass':True})
    return {'observation':{'live_trading_enabled':False if 'LIVE_TRADING_ENABLED = False' in text or 'LIVE ORDER EXECUTION IS DISABLED' in text else None,'kite_connected':'Connected to Kite successfully.' in text,'daily_loss_limit':state.get('daily_loss_limit'),'reconciliation':{'status':rec,'summary':'Broker/local reconciliation passed' if rec=='PASS' else 'No confirmed reconciliation event'},'execution':{'status':ex,'summary':'Physical live execution disabled' if ex=='SAFE' else 'Execution telemetry not confirmed'},'position':{'status':pos,'summary':'No local active trade' if pos=='FLAT' else ('Active local trade present' if active else 'Position telemetry unavailable')},'risk':{'status':risk,'summary':'Risk gate not tripped' if risk=='SAFE' else 'Risk/clearance halt indicated'}},'state':state,'events':events,'paths':{'state':str(state_path),'log':str(log_path),'session_id':log_path.parent.name if log_path.parent.parent.name=='session_logs' else None},'server_time':datetime.now().isoformat(timespec='seconds'),'validation':{'active':False,'scenario':'LIVE','scenario_label':'Live read-only artifacts','overall_pass':all(x['pass'] for x in inv),'invariants':inv}}

HTML=r'''<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>V3.4 Observatory v2</title><style>
:root{--bg:#061018;--p:#0d1923;--p2:#101f2b;--l:#203341;--t:#e8f0f5;--m:#8fa4b3;--g:#38d996;--a:#f4c95d;--r:#ff6673;--b:#63b3ff}*{box-sizing:border-box}body{margin:0;background:linear-gradient(145deg,#061018,#0a141d);font-family:Segoe UI,Arial;color:var(--t)}.wrap{max-width:1550px;margin:auto;padding:18px}.head{display:flex;justify-content:space-between;gap:15px;align-items:start}.h1{font-size:28px;font-weight:800}.sub{color:var(--m);margin-top:4px}.badges{display:flex;gap:7px;flex-wrap:wrap}.badge,.status{border:1px solid var(--l);border-radius:999px;padding:6px 9px;font-size:11px;font-weight:700}.green,.pass{color:var(--g);background:#0d3227}.amber,.warn{color:var(--a);background:#3a2d0b}.fail{color:var(--r);background:#3a1117}.info{color:var(--b);background:#0c2940}.toolbar{margin-top:14px;display:flex;gap:9px;align-items:center;flex-wrap:wrap}.toolbar button,.toolbar select{background:#0d1923;color:var(--t);border:1px solid var(--l);padding:9px 11px;border-radius:9px}.toolbar button.active{border-color:var(--b);color:var(--b)}.grid4{display:grid;grid-template-columns:repeat(4,1fr);gap:11px;margin-top:12px}.grid2{display:grid;grid-template-columns:1.2fr .8fr;gap:11px;margin-top:11px}.panel{background:rgba(13,25,35,.95);border:1px solid var(--l);border-radius:13px;padding:14px}.ct{display:flex;justify-content:space-between;align-items:center;margin-bottom:9px}.metric{font-size:23px;font-weight:800;margin:7px 0}.muted{color:var(--m);font-size:12px}.row{display:flex;justify-content:space-between;gap:12px;padding:7px 0;border-bottom:1px solid #182833}.row:last-child{border-bottom:0}.value{font-weight:700;text-align:right}.pipeline{display:flex;gap:5px;align-items:center;flex-wrap:wrap}.stage{padding:8px 9px;border:1px solid var(--l);border-radius:8px;background:#0b151e;font-size:11px}.current{border-color:var(--b);color:var(--b);box-shadow:0 0 0 1px #1b4c6a}.invariants{display:grid;grid-template-columns:1fr;gap:7px}.inv{display:grid;grid-template-columns:1.4fr .8fr 70px;gap:8px;align-items:center;padding:9px;border:1px solid var(--l);border-radius:8px;background:#0a151d;font-size:12px}.events{max-height:250px;overflow:auto}.event{padding:7px 0;border-bottom:1px solid #182833;font-size:11px}.event b{color:var(--m);font-weight:500}.tradegrid{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.kv{border:1px solid var(--l);border-radius:8px;padding:9px}.kv .k{font-size:10px;color:var(--m)}.kv .v{font-weight:750;margin-top:3px}.footer{margin-top:11px;color:#6f8796;font-size:11px}@media(max-width:1000px){.grid4,.grid2{grid-template-columns:1fr 1fr}}@media(max-width:650px){.grid4,.grid2{grid-template-columns:1fr}.tradegrid{grid-template-columns:1fr 1fr}.head{display:block}}
</style></head><body><div class="wrap"><div class="head"><div><div class="h1">V3.4 Observatory <span style="color:var(--b)">v2</span></div><div class="sub">Read-only command center + deterministic observability validation</div></div><div class="badges"><span id="mode" class="badge amber">MODE: —</span><span id="kite" class="badge">KITE: —</span><span id="upd" class="badge">UPDATED: —</span></div></div><div class="toolbar"><button id="liveBtn" onclick="setMode('live')">LIVE STAGING</button><button id="valBtn" onclick="setMode('validation')">OBSERVABILITY VALIDATION</button><select id="scenario" onchange="refresh()"></select><span id="valResult" class="status info">—</span></div><div class="grid4"><div class="panel"><div class="ct"><b>PILLAR 1 · RECONCILIATION</b><span id="p1s" class="status info">—</span></div><div id="p1m" class="metric">—</div><div class="muted">Broker ↔ local state</div></div><div class="panel"><div class="ct"><b>PILLAR 2 · EXECUTION</b><span id="p2s" class="status info">—</span></div><div id="p2m" class="metric">—</div><div class="muted">Order lifecycle / protection</div></div><div class="panel"><div class="ct"><b>PILLAR 3 · POSITION</b><span id="p3s" class="status info">—</span></div><div id="p3m" class="metric">—</div><div class="muted">Position / trade context</div></div><div class="panel"><div class="ct"><b>PILLAR 4 · RISK</b><span id="p4s" class="status info">—</span></div><div id="p4m" class="metric">—</div><div class="muted">Exposure / halt conditions</div></div></div><div class="grid2"><div class="panel"><div class="ct"><b>ENGINE LIFECYCLE</b><span id="stateBadge" class="status info">—</span></div><div class="pipeline" id="pipeline"></div><div style="margin-top:12px"><div class="row"><span class="muted">Current state</span><span id="state" class="value">—</span></div><div class="row"><span class="muted">Trading day</span><span id="day" class="value">—</span></div><div class="row"><span class="muted">Active trade</span><span id="active" class="value">—</span></div><div class="row"><span class="muted">Halt source</span><span id="halt" class="value">—</span></div><div class="row"><span class="muted">Clearance required</span><span id="clearance" class="value">—</span></div></div></div><div class="panel"><div class="ct"><b>INVARIANT MONITOR</b><span id="invBadge" class="status info">—</span></div><div id="invariants" class="invariants"></div></div></div><div class="grid2"><div class="panel"><div class="ct"><b>ENTRY → PROTECTION → MANAGEMENT → EXIT</b><span class="status info">READ ONLY</span></div><div id="trade" class="tradegrid"></div></div><div class="panel"><div class="ct"><b>RECENT ENGINE EVENTS</b><span id="ec" class="status info">0</span></div><div id="events" class="events"></div></div></div><div class="panel" style="margin-top:11px"><div class="ct"><b>OBSERVABILITY VALIDATION RESULT</b><span id="valTitle" class="status info">—</span></div><div id="valText" class="muted">Switch to OBSERVABILITY VALIDATION to test every lifecycle state without touching the broker.</div></div><div class="footer">Safety: this process has no Kite import and no order API. Validation scenarios are synthetic fixtures only.</div></div><script>
let mode='live'; const stages='''+json.dumps(STAGES)+r'''; const scenarios='''+json.dumps(SCENARIOS)+r''';
const $=id=>document.getElementById(id); function esc(x){return String(x??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]))} function kind(s){return ['PASS','SAFE','FLAT'].includes(s)?'pass':(['WARN','PARTIAL','NOT INSTRUMENTED'].includes(s)?'warn':(['HALT','FAIL'].includes(s)?'fail':'info'))} function badge(id,t,k){$(id).textContent=t||'—';$(id).className='status '+kind(k||t)} function setMode(m){mode=m;$('liveBtn').classList.toggle('active',m==='live');$('valBtn').classList.toggle('active',m==='validation');$('scenario').style.display=m==='validation'?'inline-block':'none';refresh()} function render(d){const o=d.observation||{},s=d.state||{},v=d.validation||{};let m=o.live_trading_enabled===false?'OBSERVATION ONLY':'VALIDATION SIMULATION';$('mode').textContent='MODE: '+m;$('mode').className='badge '+(mode==='validation'?'amber':'amber');$('kite').textContent='KITE: '+(o.kite_connected?'CONNECTED':mode==='validation'?'NOT USED':'NOT VERIFIED');$('kite').className='badge '+(o.kite_connected?'green':'amber');$('upd').textContent='UPDATED: '+new Date().toLocaleTimeString();[['p1',o.reconciliation],['p2',o.execution],['p3',o.position],['p4',o.risk]].forEach(([p,x])=>{badge(p+'s',x?.status||'—',x?.status);$(p+'m').textContent=x?.summary||'—'});let cur=String(s.status||'UNKNOWN').toUpperCase();badge('stateBadge',cur,cur==='RECONCILIATION_HALT'?'HALT':'INFO');$('pipeline').innerHTML=stages.map((x,i)=>(i?'<span style="color:#516979">→</span>':'')+`<span class="stage ${x===cur?'current':''}">${esc(x)}</span>`).join('');$('state').textContent=cur;$('day').textContent=s.trading_day||'—';$('active').textContent=s.active_trade?'YES':'NO';$('halt').textContent=s.halt_source||'—';$('clearance').textContent=s.clearance_required?'YES':'NO';badge('invBadge',v.overall_pass?'ALL PASS':'HALT / FAIL',v.overall_pass?'PASS':'HALT');$('invariants').innerHTML=(v.invariants||[]).map(x=>`<div class="inv"><span>${esc(x.name)}</span><span>${esc(x.actual)}</span><span class="status ${x.pass?'pass':'fail'}">${x.pass?'PASS':'FAIL'}</span></div>`).join('')||'<div class="muted">No invariants available.</div>';let t=s.active_trade||{};let fields=['symbol','entry_order_id','target_qty','filled_qty','broker_qty','stop_order_id','exit_order_id','entry_hwm','stop_hwm','exit_hwm'];$('trade').innerHTML=fields.map(k=>`<div class="kv"><div class="k">${k}</div><div class="v">${esc(t[k]??'—')}</div></div>`).join('')||'<div class="muted">No active trade context.</div>';$('ec').textContent=(d.events||[]).length;$('events').innerHTML=(d.events||[]).map(e=>`<div class="event"><b>${esc(e.time)}</b> ${esc(e.level)} — ${esc(e.message)}</div>`).join('')||'<div class="muted">No events in fixture.</div>';if(mode==='validation'){$('valTitle').textContent=v.overall_pass?'SCENARIO PASS':'SCENARIO FAIL';$('valTitle').className='status '+(v.overall_pass?'pass':'fail');$('valText').textContent=v.scenario_label+' — the UI is being fed a deterministic synthetic state. No broker calls are made.'}else{$('valTitle').textContent='LIVE ARTIFACT VIEW';$('valTitle').className='status info';$('valText').textContent='Live mode reads the staging JSON/log only; it does not infer unexposed engine internals.'} $('scenario').value=v.scenario||'FLAT'} async function refresh(){try{let q=mode==='validation'?'?mode=validation&scenario='+encodeURIComponent($('scenario').value):'?mode=live';let r=await fetch('/api/snapshot'+q+'&ts='+Date.now(),{cache:'no-store'});render(await r.json())}catch(e){$('valText').textContent='Read error: '+e}} scenarios.forEach(x=>{let o=document.createElement('option');o.value=x[0];o.textContent=x[0]+' — '+x[1];$('scenario').appendChild(o)});setMode('live');setInterval(refresh,1000);
</script></body></html>'''

class Handler(BaseHTTPRequestHandler):
    base=Path('.')
    def log_message(self,*args): pass
    def do_GET(self):
        p=urlparse(self.path); path=p.path; q=parse_qs(p.query)
        if path=='/':
            b=HTML.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        if path=='/api/snapshot':
            mode=q.get('mode',['live'])[0]; scenario=q.get('scenario',['FLAT'])[0]
            d=validation_snapshot(scenario) if mode=='validation' else live_snapshot(self.base)
            b=json.dumps(d,default=str).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.send_header('Content-Length',str(len(b))); self.end_headers(); self.wfile.write(b); return
        self.send_error(404)

def main():
    ap=argparse.ArgumentParser(); ap.add_argument('--base',default='.'); ap.add_argument('--host',default='127.0.0.1'); ap.add_argument('--port',type=int,default=8765); args=ap.parse_args(); Handler.base=Path(args.base).resolve(); state_path,log_path=live_paths(Handler.base); s=ThreadingHTTPServer((args.host,args.port),Handler); print('V3.4 OBSERVATORY v2 — READ ONLY'); print('Dashboard:',f'http://{args.host}:{args.port}/'); print('Live state:',state_path); print('Live log:',log_path); print('Safety: NO KITE IMPORT / NO ORDER API / READ ONLY'); print('Press Ctrl+C to stop.');
    try:s.serve_forever()
    except KeyboardInterrupt:print('\n[INFO] Observatory stopped.')
    finally:s.server_close()
if __name__=='__main__':main()
