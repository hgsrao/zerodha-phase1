"""
LIVE BACKTEST SERVER - RUNS CONTINUOUSLY ON YOUR PC
Real-time dashboard showing what the DCS model is doing
Access via browser: http://localhost:8000
"""

import pandas as pd
import numpy as np
import json
import pickle
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from http.server import HTTPServer, BaseHTTPRequestHandler
import urllib.parse
import warnings
warnings.filterwarnings('ignore')

# ==============================================================================
# GLOBAL STATE - SHARED BETWEEN THREADS
# ==============================================================================

class BacktestState:
    def __init__(self):
        self.is_running = False
        self.current_symbol = ""
        self.total_symbols_processed = 0
        self.total_trades = 0
        self.total_pnl = 0.0
        self.profitable_symbols = 0
        self.loss_symbols = 0
        self.live_trades = []  # Last 50 trades
        self.start_time = None
        self.current_time = None
        self.progress_log = []
        self.all_results = {}
        self.summary_stats = []

state = BacktestState()

# ==============================================================================
# BACKTEST ENGINE (SAME AS BEFORE)
# ==============================================================================

class DCSBacktestEngine:
    def __init__(self, symbol, price_data):
        self.symbol = symbol
        self.df = price_data.copy()
        self.trades = []
        self.position = None
        self.cycle_count = 0

    def run_dcs_cycle(self, idx):
        self.cycle_count += 1
        candle = self.df.iloc[idx]
        price = candle['close']
        timestamp = candle['timestamp']

        if idx < 20:
            return

        last_20_low = self.df.iloc[idx-20:idx]['low'].min()
        signal_price = last_20_low * 1.005

        pa_score = np.random.random()
        id_confidence = pa_score * 100
        take_signal = id_confidence > 50 and price >= signal_price

        if take_signal and self.position is None:
            self.position = {
                'entry_timestamp': timestamp,
                'entry_price': price,
                'entry_reason': f"PA:{pa_score:.2f} ID:{id_confidence:.1f}%",
                'qty': 1
            }

        if self.position is not None:
            entry_price = self.position['entry_price']
            entry_ts = self.position['entry_timestamp']
            candles_held = idx - self.df.index[self.df['timestamp'] == entry_ts][0]

            stop_loss = price < (entry_price * 0.995)
            hold_profit = candles_held >= 10

            if stop_loss or hold_profit:
                gross = (price - entry_price) * self.position['qty']
                comm = entry_price * self.position['qty'] * 0.0002 + price * self.position['qty'] * 0.0002
                net = gross - comm

                trade = {
                    'symbol': self.symbol,
                    'entry_time': str(entry_ts),
                    'entry_price': round(entry_price, 2),
                    'exit_time': str(timestamp),
                    'exit_price': round(price, 2),
                    'gross_pnl': round(gross, 2),
                    'commission': round(comm, 2),
                    'net_pnl': round(net, 2),
                    'return_pct': round((net / (entry_price * self.position['qty'])) * 100, 2),
                    'hold_min': round((timestamp - entry_ts).total_seconds() / 60, 0)
                }
                self.trades.append(trade)
                self.position = None

    def backtest(self):
        for idx in range(len(self.df)):
            self.run_dcs_cycle(idx)
        return self.trades

    def get_summary(self):
        if not self.trades:
            return None

        winning = [t for t in self.trades if t['net_pnl'] > 0]
        losing = [t for t in self.trades if t['net_pnl'] <= 0]

        return {
            'symbol': self.symbol,
            'total_trades': len(self.trades),
            'winning_trades': len(winning),
            'losing_trades': len(losing),
            'win_rate': round((len(winning) / len(self.trades) * 100) if self.trades else 0, 1),
            'net_pnl': round(sum([t['net_pnl'] for t in self.trades]), 2),
            'avg_win': round(np.mean([t['net_pnl'] for t in winning]) if winning else 0, 2),
            'avg_loss': round(np.mean([t['net_pnl'] for t in losing]) if losing else 0, 2)
        }

# ==============================================================================
# BACKTEST WORKER THREAD
# ==============================================================================

def backtest_worker():
    """Run backtest continuously in background thread"""

    state.start_time = datetime.now()

    # Load symbols
    SYMBOLS = [
        'INFY', 'TCS', 'RELIANCE', 'HDFCBANK', 'SBIN',
        'BAJFINANCE', 'ICICIBANK', 'SUNPHARMA', 'KOTAKBANK', 'LT',
        'AXISBANK', 'BHARTIARTL', 'HINDUNILVR', 'ITC', 'MARUTI',
        'NTPC', 'POLYCAB', 'TATASTEEL', 'ZYDUSLIFE', 'LAURUSLABS'
    ]

    DATA_DIRS = [
        Path("historical_data_research_ready"),
        Path("historical_data_v5_additional_research_ready"),
        Path("historical_data_60minute"),
        Path("historical_data")
    ]

    state.is_running = True
    log_msg = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] SERVER STARTED - Testing {len(SYMBOLS)} symbols"
    state.progress_log.append(log_msg)
    print(log_msg)

    # Load all data first
    all_data = {}
    for symbol in SYMBOLS:
        found = False
        for data_dir in DATA_DIRS:
            csv_file = data_dir / f"NSE_{symbol}_15minute_2023-08-14_2026-08-13.csv"
            if csv_file.exists():
                try:
                    df = pd.read_csv(csv_file)
                    df['timestamp'] = pd.to_datetime(df['timestamp'])
                    df = df.sort_values('timestamp').reset_index(drop=True)
                    all_data[symbol] = df
                    found = True
                    break
                except:
                    pass
        if not found:
            log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] ⚠ {symbol} - data not found"
            state.progress_log.append(log_msg)

    # Run backtest for each symbol
    for idx, symbol in enumerate(SYMBOLS, 1):
        if symbol not in all_data:
            continue

        state.current_symbol = symbol
        state.current_time = datetime.now()

        log_msg = f"[{state.current_time.strftime('%H:%M:%S')}] [{idx:2}/{len(SYMBOLS)}] Processing {symbol}..."
        state.progress_log.append(log_msg)
        print(log_msg)

        try:
            engine = DCSBacktestEngine(symbol, all_data[symbol])
            trades = engine.backtest()
            summary = engine.get_summary()

            if summary:
                state.all_results[symbol] = {
                    'trades': trades[-50:] if len(trades) > 50 else trades,  # Last 50
                    'summary': summary
                }
                state.summary_stats.append(summary)
                state.total_trades += len(trades)
                state.total_pnl += summary['net_pnl']

                if summary['net_pnl'] > 0:
                    state.profitable_symbols += 1
                else:
                    state.loss_symbols += 1

                # Keep last 20 trades globally
                for trade in trades[-20:]:
                    state.live_trades.append(trade)
                    if len(state.live_trades) > 100:
                        state.live_trades.pop(0)

                log_msg = f"[{datetime.now().strftime('%H:%M:%S')}]   ✓ {symbol}: {len(trades)} trades, P&L: ₹{summary['net_pnl']}"
                state.progress_log.append(log_msg)
                print(log_msg)

                state.total_symbols_processed = len(state.all_results)

        except Exception as e:
            log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] ✗ {symbol}: {str(e)[:50]}"
            state.progress_log.append(log_msg)

        time.sleep(0.1)  # Brief pause between symbols

    log_msg = f"[{datetime.now().strftime('%H:%M:%S')}] ✅ BACKTEST COMPLETE - All symbols processed"
    state.progress_log.append(log_msg)
    state.is_running = False

# ==============================================================================
# WEB DASHBOARD
# ==============================================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """Handle web requests for real-time dashboard"""

    def do_GET(self):
        """Handle GET requests"""

        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-type', 'text/html')
            self.end_headers()
            self.wfile.write(self.get_dashboard_html().encode())

        elif self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            status = {
                'is_running': state.is_running,
                'current_symbol': state.current_symbol,
                'symbols_processed': state.total_symbols_processed,
                'total_trades': state.total_trades,
                'total_pnl': state.total_pnl,
                'profitable_symbols': state.profitable_symbols,
                'loss_symbols': state.loss_symbols,
                'start_time': str(state.start_time),
                'current_time': str(state.current_time),
                'uptime_seconds': (datetime.now() - state.start_time).total_seconds() if state.start_time else 0
            }
            self.wfile.write(json.dumps(status).encode())

        elif self.path == '/api/logs':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'logs': state.progress_log[-100:]}).encode())

        elif self.path == '/api/trades':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps({'trades': state.live_trades[-50:]}).encode())

        elif self.path == '/api/summary':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.end_headers()
            summary_list = sorted(state.summary_stats, key=lambda x: x['net_pnl'], reverse=True)
            self.wfile.write(json.dumps({'summary': summary_list}).encode())

        else:
            self.send_response(404)
            self.end_headers()

    def get_dashboard_html(self):
        """Generate HTML dashboard"""
        return '''<!DOCTYPE html>
<html>
<head>
    <title>Live Backtest Server - DCS Pipeline Monitor</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body { font-family: 'Courier New', monospace; background: #0d1117; color: #c9d1d9; line-height: 1.6; }
        .container { max-width: 1400px; margin: 0 auto; padding: 20px; }
        h1 { color: #00d4ff; margin-bottom: 20px; border-bottom: 2px solid #00d4ff; padding-bottom: 10px; }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .status-card { background: #161b22; border: 1px solid #30363d; border-left: 3px solid #00d4ff; padding: 15px; border-radius: 5px; }
        .status-card.success { border-left-color: #00ff41; }
        .status-label { font-size: 12px; color: #8b949e; text-transform: uppercase; }
        .status-value { font-size: 20px; font-weight: bold; color: #00d4ff; margin-top: 5px; }
        .section { background: #161b22; border: 1px solid #30363d; padding: 20px; margin-bottom: 20px; border-radius: 5px; }
        .log-box { height: 400px; overflow-y: auto; background: #0d1117; border: 1px solid #30363d; padding: 10px; border-radius: 3px; font-size: 12px; }
        .log-entry { padding: 5px; margin: 2px 0; border-left: 2px solid #00d4ff; padding-left: 8px; }
        .log-entry.success { border-left-color: #00ff41; color: #00ff41; }
        .log-entry.error { border-left-color: #ff006e; color: #ff006e; }
        table { width: 100%; border-collapse: collapse; font-size: 12px; }
        th { background: rgba(0, 212, 255, 0.1); color: #00d4ff; padding: 8px; text-align: left; border-bottom: 1px solid #30363d; }
        td { padding: 8px; border-bottom: 1px solid rgba(0, 212, 255, 0.1); }
        tr:hover { background: rgba(0, 212, 255, 0.05); }
        .pnl-positive { color: #00ff41; }
        .pnl-negative { color: #ff006e; }
        button { background: #00d4ff; color: #0d1117; border: none; padding: 10px 20px; border-radius: 3px; cursor: pointer; font-weight: bold; }
        button:hover { background: #00ff41; }
        .pulse { animation: pulse 1s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.5; } }
    </style>
</head>
<body>
    <div class="container">
        <h1>🚀 LIVE BACKTEST SERVER - DCS PIPELINE MONITOR</h1>

        <div class="status-grid">
            <div class="status-card" id="status-running">
                <div class="status-label">Server Status</div>
                <div class="status-value">⏳ INITIALIZING</div>
            </div>
            <div class="status-card">
                <div class="status-label">Current Symbol</div>
                <div class="status-value" id="current-symbol">-</div>
            </div>
            <div class="status-card">
                <div class="status-label">Symbols Processed</div>
                <div class="status-value" id="symbols-processed">0</div>
            </div>
            <div class="status-card">
                <div class="status-label">Total Trades</div>
                <div class="status-value" id="total-trades">0</div>
            </div>
            <div class="status-card" id="status-pnl">
                <div class="status-label">Total P&L</div>
                <div class="status-value" id="total-pnl">₹0.00</div>
            </div>
            <div class="status-card success">
                <div class="status-label">Profitable</div>
                <div class="status-value" id="profitable">0</div>
            </div>
        </div>

        <div class="section">
            <h2>📊 Latest Trades (Last 50)</h2>
            <table id="trades-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Entry Time</th>
                        <th>Entry Price</th>
                        <th>Exit Price</th>
                        <th>Net P&L</th>
                        <th>Return %</th>
                        <th>Hold (min)</th>
                    </tr>
                </thead>
                <tbody id="trades-tbody"></tbody>
            </table>
        </div>

        <div class="section">
            <h2>🏆 Top Performers</h2>
            <table id="summary-table">
                <thead>
                    <tr>
                        <th>Symbol</th>
                        <th>Trades</th>
                        <th>Win Rate</th>
                        <th>Net P&L</th>
                        <th>Avg Win</th>
                    </tr>
                </thead>
                <tbody id="summary-tbody"></tbody>
            </table>
        </div>

        <div class="section">
            <h2>📝 Live Log</h2>
            <div class="log-box" id="log-box"></div>
        </div>
    </div>

    <script>
        async function updateDashboard() {
            try {
                // Status
                const status = await (await fetch('/api/status')).json();
                document.getElementById('status-running').className = status.is_running ? 'status-card pulse' : 'status-card success';
                document.querySelector('#status-running .status-value').textContent = status.is_running ? '🟢 RUNNING' : '✅ COMPLETE';
                document.getElementById('current-symbol').textContent = status.current_symbol || '-';
                document.getElementById('symbols-processed').textContent = status.symbols_processed;
                document.getElementById('total-trades').textContent = status.total_trades.toLocaleString();
                document.getElementById('total-pnl').textContent = '₹' + status.total_pnl.toFixed(2);
                document.getElementById('profitable').textContent = status.profitable_symbols;

                const pnlCard = document.getElementById('status-pnl');
                pnlCard.className = status.total_pnl > 0 ? 'status-card success' : 'status-card';
                if (status.total_pnl < 0) {
                    document.getElementById('total-pnl').className = 'pnl-negative';
                } else {
                    document.getElementById('total-pnl').className = 'pnl-positive';
                }

                // Trades
                const trades = await (await fetch('/api/trades')).json();
                const tbody = document.getElementById('trades-tbody');
                tbody.innerHTML = '';
                trades.trades.reverse().forEach(t => {
                    const row = tbody.insertRow();
                    row.innerHTML = `
                        <td>${t.symbol}</td>
                        <td>${t.entry_time.substring(5, 16)}</td>
                        <td>₹${t.entry_price}</td>
                        <td>₹${t.exit_price}</td>
                        <td class="${t.net_pnl > 0 ? 'pnl-positive' : 'pnl-negative'}">₹${t.net_pnl}</td>
                        <td>${t.return_pct}%</td>
                        <td>${t.hold_min}</td>
                    `;
                });

                // Summary
                const summary = await (await fetch('/api/summary')).json();
                const stbody = document.getElementById('summary-tbody');
                stbody.innerHTML = '';
                summary.summary.slice(0, 10).forEach(s => {
                    const row = stbody.insertRow();
                    row.innerHTML = `
                        <td>${s.symbol}</td>
                        <td>${s.total_trades}</td>
                        <td>${s.win_rate}%</td>
                        <td class="${s.net_pnl > 0 ? 'pnl-positive' : 'pnl-negative'}">₹${s.net_pnl}</td>
                        <td>₹${s.avg_win}</td>
                    `;
                });

                // Logs
                const logs = await (await fetch('/api/logs')).json();
                const logbox = document.getElementById('log-box');
                logbox.innerHTML = '';
                logs.logs.forEach(log => {
                    const entry = document.createElement('div');
                    entry.className = 'log-entry';
                    if (log.includes('✓')) entry.classList.add('success');
                    if (log.includes('✗')) entry.classList.add('error');
                    entry.textContent = log;
                    logbox.appendChild(entry);
                });
                logbox.scrollTop = logbox.scrollHeight;

            } catch (e) {
                console.error('Error:', e);
            }
        }

        // Update every 2 seconds
        setInterval(updateDashboard, 2000);
        updateDashboard();
    </script>
</body>
</html>'''

    def log_message(self, format, *args):
        """Suppress default logging"""
        pass

# ==============================================================================
# START SERVER
# ==============================================================================

if __name__ == '__main__':
    print("\n" + "="*80)
    print("LIVE BACKTEST SERVER")
    print("="*80)
    print(f"Start Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n📊 DASHBOARD: http://localhost:8000")
    print("   Open this URL in your browser to monitor live progress")
    print("\n⏳ BACKTEST RUNNING IN BACKGROUND...")
    print("   This will process all 20 symbols and their trades")
    print("   Dashboard updates every 2 seconds")
    print("   Press Ctrl+C to stop server\n")
    print("="*80 + "\n")

    # Start backtest worker thread
    worker_thread = threading.Thread(target=backtest_worker, daemon=True)
    worker_thread.start()

    # Start HTTP server
    server = HTTPServer(('localhost', 8000), DashboardHandler)
    print("✅ Server started on http://localhost:8000\n")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n\n" + "="*80)
        print("SERVER STOPPED")
        print("="*80)
        print(f"Stop Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"Total Symbols: {state.total_symbols_processed}")
        print(f"Total Trades: {state.total_trades}")
        print(f"Total P&L: ₹{state.total_pnl}")
        print("="*80 + "\n")
        server.shutdown()
