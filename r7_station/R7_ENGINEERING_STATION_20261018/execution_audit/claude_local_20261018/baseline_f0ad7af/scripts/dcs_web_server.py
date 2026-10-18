#!/usr/bin/env python3
"""
CCPP Distributed DCS Web HMI Server + Full Trade Telemetry Logger
Handles list-based block structures and displays trade details.
"""

import http.server
import json
import os
import socketserver
import subprocess
from datetime import datetime
from pathlib import Path

PORT = 8085
TRIAL0_FILE = Path("/tmp/r5_stage_a_trial_0_results.json")
TRIAL1_FILE = Path("/tmp/r5_stage_a_trial_1_results.json")
SSH_KEY = Path(os.environ.get("HOME", "/home/srinivas")) / ".ssh/id_ed25519_r5_worker"
LAPTOP_HOST = os.environ.get("R5_WORKER_SSH_TARGET", "")  # same variable as the Step-5 executor

HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>CCPP Master DCS Supervisory HMI</title>
<style>
  :root {
    --bg: #0b0f19;
    --card: #131b2e;
    --card-border: #1e293b;
    --cyan: #06b6d4;
    --green: #10b981;
    --yellow: #f59e0b;
    --red: #ef4444;
    --text: #e2e8f0;
    --muted: #94a3b8;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; }
  body { background: var(--bg); color: var(--text); padding: 16px; font-size: 13px; }
  
  .header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid var(--cyan); padding-bottom: 12px; margin-bottom: 16px; }
  .title { font-size: 18px; font-weight: bold; letter-spacing: 1px; color: var(--cyan); }
  .badge { background: #064e3b; color: #34d399; padding: 4px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; }
  
  .grid-top { display: grid; grid-template-columns: 2fr 1fr; gap: 16px; margin-bottom: 16px; }
  .card { background: var(--card); border: 1px solid var(--card-border); border-radius: 8px; padding: 14px; }
  .card h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--muted); margin-bottom: 12px; border-bottom: 1px solid #1e293b; padding-bottom: 6px; }
  
  .mimic-container { display: flex; flex-direction: column; gap: 12px; }
  .section-label { font-size: 11px; font-weight: bold; color: var(--muted); margin-bottom: 4px; }
  .unit-row { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; }
  .unit-row-stg { display: grid; grid-template-columns: repeat(3, 1fr); gap: 10px; }
  
  .block { background: #182238; border: 1px solid #334155; border-radius: 6px; padding: 10px; position: relative; }
  .block.active { border-color: var(--cyan); box-shadow: 0 0 10px rgba(6, 182, 212, 0.15); }
  .block-title { font-size: 12px; font-weight: bold; display: flex; justify-content: space-between; align-items: center; }
  .block-sub { font-size: 10px; color: var(--muted); margin-top: 2px; }
  .block-val { font-size: 15px; font-weight: bold; margin-top: 8px; color: #38bdf8; }
  .block-meta { display: flex; justify-content: space-between; font-size: 10px; margin-top: 6px; color: var(--muted); }
  
  .hrsg-block {
    background: #1e1b4b; border: 1px dashed #6366f1; border-radius: 6px; padding: 12px; text-align: center;
    display: flex; justify-content: space-around; align-items: center; margin: 6px 0;
  }
  
  .switchyard { background: #0f172a; border: 1px solid #334155; border-radius: 6px; padding: 12px; display: flex; justify-content: space-between; align-items: center; }
  
  table { width: 100%; border-collapse: collapse; margin-top: 6px; }
  th, td { text-align: left; padding: 7px 10px; font-size: 11px; }
  th { color: var(--muted); border-bottom: 1px solid #1e293b; background: #0f172a; position: sticky; top: 0; z-index: 2; }
  td { border-bottom: 1px solid #131c31; white-space: nowrap; }
  
  .val-green { color: var(--green); font-weight: bold; }
  .val-yellow { color: var(--yellow); font-weight: bold; }
  .val-red { color: var(--red); font-weight: bold; }
  
  .table-scroll { max-height: 480px; overflow-y: auto; border: 1px solid var(--card-border); border-radius: 4px; }
</style>
</head>
<body>

<div class="header">
  <div>
    <span class="title">⚡ CCPP DUAL-LOOP DCS SUPERVISORY MONITOR</span>
    <span style="margin-left: 12px; color: var(--muted);" id="clock">--:--:--</span>
  </div>
  <div>
    <span class="badge" id="loop-mode">2-LOOP CLOSED_LOOP_PAPER_APPLY</span>
    <span class="badge" style="background:#1e3a8a; color:#93c5fd; margin-left:6px;" id="grid-status">52G SYNCHRONIZED</span>
  </div>
</div>

<div class="grid-top">
  <!-- Power Plant Mimic Blocks -->
  <div class="card">
    <h3>Combined Cycle Generation Bay Mimic</h3>
    <div class="mimic-container">
      <div class="section-label">GAS TURBINE GENERATOR BAYS (TOPPING CYCLE)</div>
      <div class="unit-row">
        <div class="block active">
          <div class="block-title"><span>GTG 1</span> <span class="val-green">● 52G CLSD</span></div>
          <div class="block-sub">Auto / Energy</div>
          <div class="block-val">₹ 2,40,000</div>
          <div class="block-meta"><span>Droop: 4.0%</span><span>FSR: 0.880</span></div>
        </div>
        <div class="block active">
          <div class="block-title"><span>GTG 2</span> <span class="val-green">● 52G CLSD</span></div>
          <div class="block-sub">Tech / Telecom</div>
          <div class="block-val">₹ 2,10,000</div>
          <div class="block-meta"><span>Droop: 4.0%</span><span>FSR: 0.850</span></div>
        </div>
        <div class="block">
          <div class="block-title"><span>GTG 3</span> <span class="val-yellow">STANDBY</span></div>
          <div class="block-sub">Auxiliary Peaking</div>
          <div class="block-val">₹ 0</div>
          <div class="block-meta"><span>Droop: 4.0%</span><span>FSR: 0.150</span></div>
        </div>
        <div class="block">
          <div class="block-title"><span>GTG 4</span> <span class="val-yellow">STANDBY</span></div>
          <div class="block-sub">Auxiliary Peaking</div>
          <div class="block-val">₹ 0</div>
          <div class="block-meta"><span>Droop: 4.0%</span><span>FSR: 0.150</span></div>
        </div>
      </div>

      <div class="hrsg-block">
        <div><strong>HRSG 01/02 DUAL-PRESSURE STEAM RECOVERY</strong><br><span style="font-size:10px; color:#cbd5e1;">Thermal Enthalpy Conservation: Σ(Bay Power) + Reserve ≡ Total Available MW</span></div>
        <div style="font-size:12px; color:#818cf8;">HP Header: <strong>105.4 bar</strong> | Temp: <strong>540 °C</strong></div>
      </div>

      <div class="section-label">STEAM TURBINE GENERATOR BAYS (BOTTOMING CYCLE)</div>
      <div class="unit-row-stg">
        <div class="block active">
          <div class="block-title"><span>CSTG 1</span> <span class="val-green">● 52G CLSD</span></div>
          <div class="block-sub">Finance / Banks</div>
          <div class="block-val">₹ 1,80,000</div>
          <div class="block-meta"><span>Droop: 5.5%</span><span>FSR: 0.720</span></div>
        </div>
        <div class="block active">
          <div class="block-title"><span>CSTG 2</span> <span class="val-green">● 52G CLSD</span></div>
          <div class="block-sub">Metals / Infra</div>
          <div class="block-val">₹ 1,50,000</div>
          <div class="block-meta"><span>Droop: 6.5%</span><span>FSR: 0.680</span></div>
        </div>
        <div class="block active">
          <div class="block-title"><span>PSTG / BPSTG</span> <span class="val-green">● 52G CLSD</span></div>
          <div class="block-sub">Pharma / FMCG</div>
          <div class="block-val">₹ 1,20,000</div>
          <div class="block-meta"><span>Droop: 7.5%</span><span>FSR: 0.600</span></div>
        </div>
      </div>

      <div class="switchyard">
        <div>
          <span style="font-weight:bold; color:var(--cyan);">400 kV SWITCHYARD & INTERCONNECT</span>
          <span style="margin-left:12px; font-size:11px; color:var(--muted);">Grid Reference: NIFTY 50 / INDIA VIX</span>
        </div>
        <div style="font-size:11px;">
          Freq: <strong class="val-green">50.02 Hz</strong> | Bus Diff: <strong class="val-green">&lt; 0.1%</strong> | ANSI 86 Lockout: <strong class="val-green">RESET</strong>
        </div>
      </div>
    </div>
  </div>

  <!-- Governors & Compute Nodes -->
  <div style="display:flex; flex-direction:column; gap:16px;">
    <div class="card">
      <h3>Speedtronic Mark V Minimum Value Gate</h3>
      <table>
        <tr><th>Limiter Parameter</th><th>Value</th><th>Status</th></tr>
        <tr><td>FSRN (Speed Droop)</td><td>0.9200</td><td class="val-green">PASS</td></tr>
        <tr><td>FSRT (Exhaust Temp / DD)</td><td>0.8800</td><td class="val-red">BINDING</td></tr>
        <tr><td>FSRA (Acceleration Limit)</td><td>0.9500</td><td class="val-green">PASS</td></tr>
        <tr><td>FSRS (Warmup Ramp Schedule)</td><td>1.0000</td><td class="val-green">PASS</td></tr>
        <tr><td>FSRM (Manual Stop Valve)</td><td>1.0000</td><td class="val-green">PASS</td></tr>
        <tr><td>FSRMIN (Flameout Floor)</td><td>0.1500</td><td style="color:var(--muted)">FLOOR</td></tr>
      </table>
    </div>

    <div class="card">
      <h3>Cluster Node Telemetry</h3>
      <table>
        <tr><th>Node</th><th>Status</th><th>Trades</th><th>Net PnL</th></tr>
        <tr>
          <td>B760M (Desktop T0)</td>
          <td id="t0-status" class="val-green">RANKABLE</td>
          <td id="t0-trades">195</td>
          <td id="t0-pnl" class="val-red">₹ -20,462.47</td>
        </tr>
        <tr>
          <td>Blade 15 (Laptop T1)</td>
          <td id="t1-status" class="val-yellow">RUNNING</td>
          <td id="t1-trades">--</td>
          <td id="t1-pnl">--</td>
        </tr>
      </table>
      <div style="margin-top:12px; font-size:11px; color:var(--muted);" id="t0-score">
        Baseline Score: <strong>-29854.1426</strong>
      </div>
    </div>
  </div>
</div>

<!-- Bottom Section: Itemized Trade Execution Ledger -->
<div class="card">
  <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px;">
    <h3 style="margin-bottom:0; border-bottom:none;">Live Stage-A Trade Telemetry Ledger (<span id="ledger-count">0</span> Executed Trades)</h3>
    <div style="font-size:11px; color:var(--muted);">Source: <strong>Trial 0 Canonical Baseline</strong></div>
  </div>
  <div class="table-scroll">
    <table>
      <thead>
        <tr>
          <th>#</th>
          <th>Block</th>
          <th>Symbol</th>
          <th>Side</th>
          <th>Qty</th>
          <th>Entry Time</th>
          <th>Entry Price</th>
          <th>Exit Time</th>
          <th>Exit Price</th>
          <th>Exit Reason</th>
          <th>Gross PnL</th>
          <th>Friction</th>
          <th>Net PnL (₹)</th>
        </tr>
      </thead>
      <tbody id="trade-table-body">
        <tr><td colspan="13" style="text-align:center; color:var(--muted); padding:16px;">Loading telemetry records...</td></tr>
      </tbody>
    </table>
  </div>
</div>

<script>
async function refreshDcs() {
  try {
    const res = await fetch('/api/telemetry');
    const data = await res.json();
    
    document.getElementById('clock').innerText = data.timestamp;
    
    if (data.trial0 && data.trial0.aggregate) {
      const agg = data.trial0.aggregate;
      document.getElementById('t0-status').innerText = agg.status || 'RANKABLE';
      document.getElementById('t0-trades').innerText = agg.completed_trades || 0;
      document.getElementById('t0-pnl').innerText = '₹ ' + Number(agg.net_pnl || 0).toLocaleString('en-IN', {minimumFractionDigits: 2});
      document.getElementById('t0-score').innerHTML = 'Baseline Score: <strong>' + (agg.score ? agg.score.toFixed(4) : '--') + '</strong>';
    }

    if (data.trial1 && data.trial1.aggregate) {
      const agg1 = data.trial1.aggregate;
      document.getElementById('t1-status').innerText = agg1.status || 'DONE';
      document.getElementById('t1-status').className = 'val-green';
      document.getElementById('t1-trades').innerText = agg1.completed_trades || 0;
      document.getElementById('t1-pnl').innerText = '₹ ' + Number(agg1.net_pnl || 0).toLocaleString('en-IN', {minimumFractionDigits: 2});
    } else if (data.laptop_active) {
      document.getElementById('t1-status').innerText = 'ACTIVE (100% CPU)';
      document.getElementById('t1-status').className = 'val-green';
    }

    if (data.trades && data.trades.length > 0) {
      document.getElementById('ledger-count').innerText = data.trades.length;
      let html = '';
      data.trades.forEach((t, i) => {
        const net = Number(t.net_pnl ?? t.pnl ?? 0);
        const gross = Number(t.gross_pnl ?? 0);
        const costs = Number(t.costs ?? t.total_costs ?? 0);
        const pnlClass = net >= 0 ? 'val-green' : 'val-red';
        const sideClass = (t.side || t.action || '').toUpperCase().includes('BUY') ? 'val-green' : 'val-yellow';
        
        const entryT = (t.entry_time || t.entry_timestamp || '--').toString().slice(0, 16);
        const exitT = (t.exit_time || t.exit_timestamp || '--').toString().slice(0, 16);
        const entryP = Number(t.entry_price || t.entry_fill_price || 0).toFixed(2);
        const exitP = Number(t.exit_price || t.exit_fill_price || 0).toFixed(2);
        const exitReason = t.exit_reason || t.reason || 'SQUARE_OFF';

        html += `<tr>
          <td style="color:var(--muted);">${i + 1}</td>
          <td><strong>${t.block}</strong></td>
          <td><strong style="color:#38bdf8;">${t.symbol}</strong></td>
          <td class="${sideClass}">${t.side || 'BUY'}</td>
          <td>${t.quantity || t.qty || 1}</td>
          <td>${entryT}</td>
          <td>₹${entryP}</td>
          <td>${exitT}</td>
          <td>₹${exitP}</td>
          <td><span style="font-size:10px; background:#1e293b; padding:2px 6px; border-radius:3px;">${exitReason}</span></td>
          <td>₹${gross.toFixed(2)}</td>
          <td>₹${costs.toFixed(2)}</td>
          <td class="${pnlClass}">${net >= 0 ? '+' : ''}₹${net.toFixed(2)}</td>
        </tr>`;
      });
      document.getElementById('trade-table-body').innerHTML = html;
    }
  } catch(e) {
    console.error("Telemetry poll failed", e);
  }
}
setInterval(refreshDcs, 2500);
refreshDcs();
</script>
</body>
</html>
"""


class DCSHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(HTML_PAGE.encode("utf-8"))
        elif self.path == "/api/telemetry":
            t0 = {}
            t1 = {}
            trades = []

            if TRIAL0_FILE.exists():
                try:
                    t0 = json.loads(TRIAL0_FILE.read_text())
                    raw_blocks = t0.get("blocks", [])
                    
                    if isinstance(raw_blocks, dict):
                        for b_name, b_val in raw_blocks.items():
                            for tr in b_val.get("trades", []):
                                tr_c = dict(tr)
                                tr_c["block"] = b_name
                                tr_c["symbol"] = tr_c.get("symbol", tr_c.get("tradingsymbol", "UNKNOWN"))
                                trades.append(tr_c)
                    elif isinstance(raw_blocks, list):
                        for idx, b_val in enumerate(raw_blocks):
                            b_name = b_val.get("block_id", b_val.get("name", f"Block_{idx+1}"))
                            for tr in b_val.get("trades", []):
                                tr_c = dict(tr)
                                tr_c["block"] = b_name
                                tr_c["symbol"] = tr_c.get("symbol", tr_c.get("tradingsymbol", "UNKNOWN"))
                                trades.append(tr_c)
                except Exception:
                    pass

            if TRIAL1_FILE.exists():
                try:
                    t1 = json.loads(TRIAL1_FILE.read_text())
                except Exception:
                    pass

            laptop_active = False
            try:
                if not LAPTOP_HOST:
                    raise RuntimeError("R5_WORKER_SSH_TARGET not set")
                cmd = f"ssh -i '{SSH_KEY}' -o BatchMode=yes -o ConnectTimeout=1 {LAPTOP_HOST} 'pgrep -f run_r5_step5_candidate'"
                res = subprocess.check_output(cmd, shell=True, text=True, timeout=2).strip()
                laptop_active = bool(res)
            except Exception:
                laptop_active = False

            payload = {
                "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "trial0": t0,
                "trial1": t1,
                "laptop_active": laptop_active,
                "trades": trades,
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode("utf-8"))
        else:
            self.send_error(404)


socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("127.0.0.1", PORT), DCSHandler) as httpd:
    print(f"⚡ CCPP MASTER DCS WEB SERVER RUNNING ON PORT {PORT}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping CCPP Web Server.")
