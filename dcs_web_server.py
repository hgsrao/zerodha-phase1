import http.server
import socketserver
import json
import sqlite3
import re
from pathlib import Path

PORT = 8080

def get_live_prices():
    prices = {}
    
    # 1. Parse latest real-time ticks from runner_live.log
    log_path = Path("runner_live.log")
    if log_path.exists():
        try:
            with open(log_path, "r", errors="ignore") as f:
                lines = f.readlines()[-150:]
            for line in lines:
                m = re.search(r'\[TICK PULSE\]\s+([A-Z0-9_\-]+)\s+\|\s+LTP:\s+₹?([\d,\.]+)', line)
                if m:
                    sym = m.group(1).strip()
                    val = float(m.group(2).replace(',', ''))
                    prices[sym] = val
        except Exception:
            pass

    # 2. Augment / fallback from PAPER_SIGNALS_LOG.json
    sig_path = Path("PAPER_SIGNALS_LOG.json")
    if sig_path.exists():
        try:
            sigs = json.loads(sig_path.read_text())
            if isinstance(sigs, list):
                for s in sigs:
                    sym = s.get("symbol")
                    cp = s.get("current_price")
                    if sym and cp and sym not in prices:
                        prices[sym] = float(cp)
        except Exception:
            pass

    return prices

def build_plant_state():
    db_path = Path("paper_trading_dual_engine.db")
    positions = []
    if db_path.exists():
        try:
            conn = sqlite3.connect(str(db_path))
            c = conn.cursor()
            rows = c.execute("SELECT id, engine, turbine, symbol, entry_price, qty, stop_price, target_price, entry_time FROM open_positions").fetchall()
            for r in rows:
                positions.append({
                    "id": r[0], "engine": r[1], "sector": r[2], "symbol": r[3],
                    "entry_price": float(r[4]), "shares": int(r[5]), "stop_loss": float(r[6]),
                    "target": float(r[7]), "entry_time": str(r[8])
                })
            conn.close()
        except Exception as e:
            print("DB read error:", e)

    live_prices = get_live_prices()
    total_mtm = 0.0

    for pos in positions:
        entry = pos['entry_price']
        qty = pos['shares']
        sym = pos['symbol']
        sl = pos['stop_loss']
        target = pos['target']
        
        ltp = live_prices.get(sym, entry)
        pnl = (ltp - entry) * qty
        pnl_pct = ((ltp - entry) / entry) * 100.0 if entry > 0 else 0.0
        
        # Calculate progress towards target (0% at SL, entry % in middle, 100% at Target)
        span = target - sl if target > sl else 1.0
        progress = max(0.0, min(100.0, ((ltp - sl) / span) * 100.0))
        
        pos['current_price'] = round(ltp, 2)
        pos['unrealized_pnl'] = round(pnl, 2)
        pos['pnl_pct'] = round(pnl_pct, 2)
        pos['progress_pct'] = round(progress, 1)
        total_mtm += pnl

    return {
        "last_update": "LIVE",
        "plant_permissive": True,
        "engine_a_count": len([p for p in positions if 'ENGINE_A' in p['engine']]),
        "engine_b_count": len([p for p in positions if 'ENGINE_B' in p['engine']]),
        "total_mtm": round(total_mtm, 2),
        "positions": positions
    }

HTML_PAGE = """<!DOCTYPE html>
<html>
<head>
    <title>CCPP DUAL-ENGINE DCS SUPERVISORY</title>
    <meta charset="utf-8">
    <style>
        body { background-color: #0b0f19; color: #e2e8f0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, monospace; margin: 0; padding: 20px; }
        .header { display: flex; justify-content: space-between; border-bottom: 2px solid #1e293b; padding-bottom: 15px; align-items: center; }
        .title { font-size: 20px; font-weight: bold; letter-spacing: 1px; }
        .badge { background: #064e3b; color: #34d399; padding: 4px 10px; border-radius: 4px; font-weight: bold; }
        .audio-btn { background: #1e293b; border: 1px solid #334155; color: #38bdf8; padding: 5px 12px; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px; margin-left: 10px; }
        .audio-btn:hover { background: #334155; }
        .kpi-grid { display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin: 20px 0; }
        .card { background: #111827; border: 1px solid #1f2937; border-radius: 6px; padding: 15px; }
        .card-title { font-size: 11px; color: #9ca3af; letter-spacing: 1px; font-weight: 600; }
        .card-val { font-size: 24px; font-weight: bold; margin: 10px 0; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; font-size: 13px; }
        th { background: #1f2937; color: #9ca3af; text-align: left; padding: 10px; }
        td { padding: 10px; border-bottom: 1px solid #1e293b; }
        .pos { color: #10b981; font-weight: bold; }
        .neg { color: #ef4444; font-weight: bold; }
        .bar-container { background-color: #1e293b; border-radius: 4px; height: 12px; width: 120px; overflow: hidden; position: relative; border: 1px solid #334155; }
        .bar-fill { height: 100%; background: linear-gradient(90deg, #ef4444 0%, #f59e0b 50%, #10b981 100%); transition: width 0.4s ease-in-out; }
        .bar-text { position: absolute; width: 100%; text-align: center; top: 0; left: 0; font-size: 9px; line-height: 12px; font-weight: bold; color: #ffffff; text-shadow: 0 0 2px #000; }
    </style>
</head>
<body>
    <div class="header">
        <div>
            <div class="title">CCPP GENERATION UNIT 600 • SUPERVISORY DCS</div>
            <div style="color: #64748b; font-size: 11px;">ANSI 25 SYNCHROCHECK • DUAL-ENGINE BUSBAR • DYNAMIC MTM</div>
        </div>
        <div style="display: flex; align-items: center;">
            <button class="audio-btn" id="audioToggle" onclick="toggleAudio()">🔊 AUDIO ALERT: OFF</button>
            <span class="badge" style="margin-left: 10px;">PLANT PERMISSIVE</span>
        </div>
    </div>
    <div class="kpi-grid">
        <div class="card"><div class="card-title">FAST GAS TURBINES (ENGINE A)</div><div class="card-val" id="eng-a">-</div><div style="font-size:11px; color:#64748b;">CCPP Swing Slots</div></div>
        <div class="card"><div class="card-title">BASELOAD STEAM (ENGINE B)</div><div class="card-val" id="eng-b">-</div><div style="font-size:11px; color:#64748b;">Deep Carry Slots</div></div>
        <div class="card"><div class="card-title">TOTAL UNREALIZED MTM</div><div class="card-val" id="total-mtm">₹0.00</div><div style="font-size:11px; color:#64748b;">Dynamic Busbar Yield</div></div>
        <div class="card"><div class="card-title">SYNCHROCHECK RELAYS</div><div class="card-val" style="color:#34d399; font-size: 18px;">25 SYNC: LOCKED</div><div style="font-size:11px; color:#64748b;">Freq: 50.0 Hz</div></div>
    </div>
    <table>
        <thead>
            <tr><th>ID</th><th>Engine</th><th>Sector</th><th>Symbol</th><th>Qty</th><th>Entry</th><th>Live LTP</th><th>SL</th><th>Target</th><th>Progress (SL → Target)</th><th>Unrealized P&L</th><th>Time</th></tr>
        </thead>
        <tbody id="pos-tbody"></tbody>
    </table>
    <script>
        let audioCtx = null;
        let audioEnabled = false;
        let lastKnownPositions = {};

        function toggleAudio() {
            if (!audioCtx) {
                audioCtx = new (window.AudioContext || window.webkitAudioContext)();
            }
            if (audioCtx.state === 'suspended') {
                audioCtx.resume();
            }
            audioEnabled = !audioEnabled;
            const btn = document.getElementById('audioToggle');
            btn.innerText = audioEnabled ? '🔊 AUDIO ALERT: ON' : '🔈 AUDIO ALERT: OFF';
            btn.style.color = audioEnabled ? '#10b981' : '#38bdf8';
            if (audioEnabled) playTone(880, 0.1);
        }

        function playTone(freq, duration) {
            if (!audioEnabled || !audioCtx) return;
            try {
                const osc = audioCtx.createOscillator();
                const gain = audioCtx.createGain();
                osc.type = 'sine';
                osc.frequency.setValueAtTime(freq, audioCtx.currentTime);
                gain.gain.setValueAtTime(0.2, audioCtx.currentTime);
                gain.gain.exponentialRampToValueAtTime(0.001, audioCtx.currentTime + duration);
                osc.connect(gain);
                gain.connect(audioCtx.destination);
                osc.start();
                osc.stop(audioCtx.currentTime + duration);
            } catch(e) {}
        }

        function playExecutionChime() {
            playTone(523.25, 0.12);
            setTimeout(() => playTone(659.25, 0.12), 120);
            setTimeout(() => playTone(783.99, 0.25), 240);
        }

        async function update() {
            try {
                const res = await fetch('/api/state');
                const d = await res.json();
                document.getElementById('eng-a').innerText = d.engine_a_count;
                document.getElementById('eng-b').innerText = d.engine_b_count;
                const mtmEl = document.getElementById('total-mtm');
                mtmEl.innerText = (d.total_mtm >= 0 ? '+' : '') + '₹' + d.total_mtm.toFixed(2);
                mtmEl.className = 'card-val ' + (d.total_mtm >= 0 ? 'pos' : 'neg');
                
                const currentIds = {};
                let rows = '';
                for (const p of d.positions) {
                    currentIds[p.id] = true;
                    if (!lastKnownPositions[p.id] && Object.keys(lastKnownPositions).length > 0) {
                        playExecutionChime();
                    }
                    
                    const pnlClass = p.unrealized_pnl >= 0 ? 'pos' : 'neg';
                    const sign = p.unrealized_pnl >= 0 ? '+' : '';
                    const progress = p.progress_pct !== undefined ? p.progress_pct : 50;

                    rows += `<tr>
                        <td>#${p.id}</td><td>${p.engine}</td><td>${p.sector}</td><td style="color:#38bdf8;font-weight:bold;">${p.symbol}</td>
                        <td>${p.shares}</td><td>₹${p.entry_price.toFixed(2)}</td><td style="font-weight:bold;">₹${p.current_price.toFixed(2)}</td>
                        <td style="color:#ef4444;">₹${p.stop_loss.toFixed(2)}</td><td style="color:#10b981;">₹${p.target.toFixed(2)}</td>
                        <td>
                            <div class="bar-container">
                                <div class="bar-fill" style="width: ${progress}%;"></div>
                                <div class="bar-text">${progress}%</div>
                            </div>
                        </td>
                        <td class="${pnlClass}">${sign}₹${p.unrealized_pnl.toFixed(2)} (${sign}${p.pnl_pct.toFixed(2)}%)</td>
                        <td>${p.entry_time}</td>
                    </tr>`;
                }
                
                // If a position disappeared (closed trade / target / SL), play alert chime
                if (Object.keys(lastKnownPositions).length > 0 && d.positions.length < Object.keys(lastKnownPositions).length) {
                    playExecutionChime();
                }
                
                lastKnownPositions = currentIds;
                document.getElementById('pos-tbody').innerHTML = rows;
            } catch(e){}
        }
        setInterval(update, 1500);
        update();
    </script>
</body>
</html>"""

class DCSHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/state":
            data = json.dumps(build_plant_state()).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(data)
        else:
            data = HTML_PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-type", "text/html")
            self.end_headers()
            self.wfile.write(data)

    def log_message(self, format, *args):
        pass

socketserver.TCPServer.allow_reuse_address = True
with socketserver.TCPServer(("0.0.0.0", PORT), DCSHandler) as httpd:
    httpd.serve_forever()
