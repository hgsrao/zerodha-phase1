#!/usr/bin/env python3
"""
================================================================================
Real-time Calibration Dashboard with 33-Parameter Display + TIME STAMPING
================================================================================
Monitors Stage 2 calibration and displays all 33 parameters in real-time
WITH COMPLETE TIME TRACKING AND LOGGING

Open in browser: http://localhost:8888
"""

import json
import re
from pathlib import Path
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs
import threading
import time

# Configuration
LOG_FILE = "STAGE2_calibration_33params.log"
PORT = 8888

# ============================================================================
# DATA PARSER WITH TIME TRACKING
# ============================================================================

class CalibrationMonitor:
    """Parse log file with complete time tracking"""

    @staticmethod
    def extract_timestamp(line):
        """Extract timestamp from log line"""
        # Format: 2026-08-30 14:23:32,276
        match = re.match(r'(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})', line)
        if match:
            try:
                return datetime.strptime(match.group(1), '%Y-%m-%d %H:%M:%S')
            except:
                return None
        return None

    @staticmethod
    def read_log():
        """Read and parse log file with time tracking"""
        if not Path(LOG_FILE).exists():
            return {
                'status': 'waiting',
                'message': 'Log file not created yet',
                'iterations': [],
                'best_params': {},
                'current_params': {},
                'start_time': None,
                'current_time': datetime.now().isoformat(),
                'elapsed_time': '0h 0m 0s',
                'time_per_iteration': '0m 0s'
            }

        try:
            with open(LOG_FILE, 'r') as f:
                content = f.read()

            data = {
                'status': 'running',
                'iterations': [],
                'best_win_rate': 0.5175,
                'best_iteration': 0,
                'start_time': None,
                'current_time': datetime.now().isoformat(),
                'elapsed_seconds': 0,
                'elapsed_time': '0h 0m 0s',
                'time_per_iteration': '0m 0s',
                'baseline': 0.5175,
                'improvement': 0,
                'best_params': {},
                'current_params': {},
                'iteration_times': []
            }

            # Parse log lines
            lines = content.split('\n')
            first_timestamp = None
            last_timestamp = None

            for i, line in enumerate(lines):
                # Extract timestamp
                ts = CalibrationMonitor.extract_timestamp(line)

                if ts and not first_timestamp:
                    first_timestamp = ts

                if ts:
                    last_timestamp = ts

                # Find iteration lines: [Iter 123] 52.34% ...
                match = re.search(r'\[Iter\s+(\d+)\]\s+([\d.]+)%\s+\[([+-][\d.]+)%\]', line)
                if match:
                    iteration = int(match.group(1))
                    win_rate = float(match.group(2)) / 100.0
                    improvement = float(match.group(3))

                    is_best = '[BEST!]' in line

                    iter_data = {
                        'iteration': iteration,
                        'win_rate': win_rate,
                        'improvement': improvement,
                        'is_best': is_best,
                        'timestamp': ts.isoformat() if ts else None,
                        'timestamp_display': ts.strftime('%H:%M:%S') if ts else 'N/A'
                    }

                    data['iterations'].append(iter_data)
                    data['iteration_times'].append(ts) if ts else None

                    if is_best:
                        data['best_win_rate'] = win_rate
                        data['best_iteration'] = iteration

                # Find PARAMS line
                if 'PARAMS: {' in line:
                    try:
                        params_str = line.split('PARAMS: ', 1)[1]
                        data['current_params'] = json.loads(params_str)
                    except:
                        pass

                # Find BEST_PARAMS line
                if 'BEST_PARAMS: {' in line:
                    try:
                        params_str = line.split('BEST_PARAMS: ', 1)[1]
                        data['best_params'] = json.loads(params_str)
                    except:
                        pass

                # Check for completion
                if 'CALIBRATION COMPLETE' in line:
                    data['status'] = 'complete'

            # Calculate elapsed time
            if first_timestamp and last_timestamp:
                data['start_time'] = first_timestamp.isoformat()
                elapsed_seconds = (last_timestamp - first_timestamp).total_seconds()
                data['elapsed_seconds'] = elapsed_seconds

                # Format elapsed time
                hours = int(elapsed_seconds // 3600)
                minutes = int((elapsed_seconds % 3600) // 60)
                seconds = int(elapsed_seconds % 60)
                data['elapsed_time'] = f"{hours}h {minutes}m {seconds}s"

                # Calculate time per iteration
                if len(data['iterations']) > 1:
                    time_per_iter = elapsed_seconds / len(data['iterations'])
                    iter_minutes = int(time_per_iter // 60)
                    iter_seconds = int(time_per_iter % 60)
                    data['time_per_iteration'] = f"{iter_minutes}m {iter_seconds}s"

            # Calculate improvement
            data['improvement'] = (data['best_win_rate'] - data['baseline']) * 100

            return data

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'iterations': [],
                'best_params': {},
                'current_params': {},
                'current_time': datetime.now().isoformat(),
                'elapsed_time': 'Error reading file'
            }


# ============================================================================
# WEB DASHBOARD WITH TIME STAMPING
# ============================================================================

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for dashboard"""

    def do_GET(self):
        """Handle GET requests"""
        parsed = urlparse(self.path)

        if parsed.path == '/':
            self.serve_dashboard()
        elif parsed.path == '/api/status':
            self.serve_status()
        else:
            self.send_error(404)

    def serve_dashboard(self):
        """Serve main dashboard HTML"""
        html = """<!DOCTYPE html>
<html>
<head>
    <title>Stage 2 Calibration Monitor - 33 Parameters (TIMESTAMPED)</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * {
            margin: 0;
            padding: 0;
            box-sizing: border-box;
        }

        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, monospace;
            background: #1a1a2e;
            color: #eee;
            padding: 20px;
        }

        .container {
            max-width: 1800px;
            margin: 0 auto;
        }

        .header {
            text-align: center;
            margin-bottom: 20px;
            border-bottom: 2px solid #0f3460;
            padding-bottom: 20px;
        }

        .header h1 {
            font-size: 2em;
            color: #00d4ff;
            margin-bottom: 5px;
        }

        .header p {
            color: #aaa;
            font-size: 0.9em;
        }

        .time-bar {
            background: #16213e;
            border: 1px solid #0f3460;
            border-radius: 5px;
            padding: 15px;
            margin-bottom: 20px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
            gap: 15px;
        }

        .time-item {
            background: #0f1a2e;
            border-left: 3px solid #00d4ff;
            padding: 12px;
            border-radius: 3px;
        }

        .time-label {
            font-size: 0.75em;
            color: #00d4ff;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 5px;
        }

        .time-value {
            font-size: 1.4em;
            font-weight: bold;
            color: #fff;
            font-family: 'Courier New', monospace;
        }

        .metrics-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 20px;
        }

        .metric-card {
            background: #16213e;
            border: 1px solid #0f3460;
            border-radius: 5px;
            padding: 15px;
        }

        .metric-label {
            font-size: 0.8em;
            color: #00d4ff;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 8px;
        }

        .metric-value {
            font-size: 1.8em;
            font-weight: bold;
            color: #fff;
        }

        .metric-subtext {
            font-size: 0.75em;
            color: #888;
            margin-top: 5px;
        }

        .status-badge {
            display: inline-block;
            padding: 4px 10px;
            border-radius: 3px;
            font-size: 0.75em;
            font-weight: bold;
            margin-top: 8px;
        }

        .status-running {
            background: #00d400;
            color: #000;
        }

        .status-complete {
            background: #0066ff;
            color: #fff;
        }

        .status-waiting {
            background: #ff9900;
            color: #000;
        }

        .section {
            background: #16213e;
            border: 1px solid #0f3460;
            border-radius: 5px;
            padding: 20px;
            margin-bottom: 20px;
        }

        .section-title {
            font-size: 1.2em;
            color: #00d4ff;
            margin-bottom: 15px;
            border-bottom: 1px solid #0f3460;
            padding-bottom: 10px;
        }

        .parameters-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
        }

        .param-card {
            background: #0f1a2e;
            border: 1px solid #1a3a52;
            border-radius: 3px;
            padding: 12px;
            font-size: 0.85em;
        }

        .param-name {
            color: #00d4ff;
            font-weight: bold;
            word-break: break-word;
            margin-bottom: 5px;
        }

        .param-value {
            color: #fff;
            font-family: 'Courier New', monospace;
            font-size: 0.9em;
            word-break: break-all;
        }

        .param-label {
            color: #888;
            font-size: 0.75em;
            margin-top: 5px;
        }

        .iterations-log {
            background: #0f1a2e;
            border: 1px solid #1a3a52;
            border-radius: 3px;
            padding: 12px;
            max-height: 400px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.8em;
        }

        .log-entry {
            padding: 8px 0;
            border-bottom: 1px solid #1a3a52;
            color: #aaa;
            display: grid;
            grid-template-columns: 90px 1fr;
            gap: 15px;
        }

        .log-timestamp {
            color: #00d4ff;
            font-weight: bold;
            min-width: 90px;
        }

        .log-entry.best {
            background: #1a3a0a;
            color: #00ff00;
            border-left: 3px solid #00ff00;
            padding-left: 8px;
        }

        .log-entry.best .log-timestamp {
            color: #00ff00;
        }

        .tier-label {
            display: inline-block;
            font-size: 0.65em;
            padding: 2px 6px;
            border-radius: 2px;
            margin-bottom: 8px;
            font-weight: bold;
        }

        .tier1 { background: #ff4444; color: white; }
        .tier2 { background: #ffaa00; color: white; }
        .tier3 { background: #4444ff; color: white; }

        .refresh-info {
            text-align: center;
            color: #666;
            margin-top: 20px;
            font-size: 0.85em;
        }

        ::-webkit-scrollbar {
            width: 8px;
        }

        ::-webkit-scrollbar-track {
            background: #0f1a2e;
        }

        ::-webkit-scrollbar-thumb {
            background: #0f3460;
            border-radius: 4px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Stage 2 Calibration Monitor - 33 Parameters</h1>
            <p>Real-time optimization with COMPLETE TIME TRACKING (24+ hours)</p>
        </div>

        <div class="time-bar">
            <div class="time-item">
                <div class="time-label">Current Time</div>
                <div class="time-value" id="current-time">--:--:--</div>
            </div>
            <div class="time-item">
                <div class="time-label">Start Time</div>
                <div class="time-value" id="start-time">Pending</div>
            </div>
            <div class="time-item">
                <div class="time-label">Elapsed Time</div>
                <div class="time-value" id="elapsed-time">0h 0m 0s</div>
            </div>
            <div class="time-item">
                <div class="time-label">Time/Iteration</div>
                <div class="time-value" id="time-per-iter">0m 0s</div>
            </div>
        </div>

        <div class="metrics-row">
            <div class="metric-card">
                <div class="metric-label">Iterations Completed</div>
                <div class="metric-value" id="iteration-count">0</div>
                <div class="metric-subtext">Of estimated 300-700 in 24h</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Best Win Rate</div>
                <div class="metric-value" id="best-win-rate">51.75%</div>
                <div class="metric-subtext" id="best-win-subtext">Baseline</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Improvement</div>
                <div class="metric-value" id="improvement">0.00%</div>
                <div class="metric-subtext">vs Stage 1</div>
            </div>
            <div class="metric-card">
                <div class="metric-label">Status</div>
                <div id="status-display">
                    <div class="status-badge status-waiting">WAITING</div>
                </div>
                <div class="metric-subtext">Initializing...</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Recent Iterations (with Timestamps)</div>
            <div class="iterations-log" id="iterations-log">
                <div class="log-entry">
                    <div class="log-timestamp">--:--:--</div>
                    <div>Waiting for calibration to start...</div>
                </div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Best Parameters Found (Tier 1: Critical - 20 params)</div>
            <div class="parameters-grid" id="best-params-tier1">
                <div class="param-card">
                    <div class="param-label"><span class="tier-label tier1">TIER 1</span></div>
                    <div class="param-name">Waiting for calibration...</div>
                </div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Best Parameters Found (Tier 2: High-Value - 8 params)</div>
            <div class="parameters-grid" id="best-params-tier2">
                <div class="param-card">
                    <div class="param-label"><span class="tier-label tier2">TIER 2</span></div>
                    <div class="param-name">Waiting for calibration...</div>
                </div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Best Parameters Found (Tier 3: Optional - 5 params)</div>
            <div class="parameters-grid" id="best-params-tier3">
                <div class="param-card">
                    <div class="param-label"><span class="tier-label tier3">TIER 3</span></div>
                    <div class="param-name">Waiting for calibration...</div>
                </div>
            </div>
        </div>

        <div class="refresh-info">
            <div>Auto-refreshing every 3 seconds | Last update: <span id="last-update">--:--:--</span></div>
        </div>
    </div>

    <script>
        // Parameter tier definitions
        const TIER_1 = [
            'base_dp_dt_multiplier', 'base_dv_dt_multiplier', 'sync_score_confidence_threshold',
            'entry_confidence_threshold', 'exit_confidence_threshold', 'min_risk_reward_ratio',
            'profit_target_margin_buffer', 'vwap_weight', 'confirmation_2bar_weight', 'momentum_weight',
            'volatility_weight', 'green_threshold', 'amber_threshold_lower', 'red_threshold',
            'slippage_guard_threshold', 'volatility_regime_multiplier', 'low_vol_regime_multiplier',
            'medium_vol_regime_multiplier', 'high_vol_regime_multiplier'
        ];

        const TIER_2 = [
            'atr_calculation_period', 'entry_signal_smoothing_window', 'exit_signal_smoothing_window',
            'slippage_cost_multiplier', 'minimum_absolute_profit_rupees', 'momentum_calculation_period',
            'vwap_calculation_period', 'signal_persistence_requirement'
        ];

        const TIER_3 = [
            'phase1_exploration_intensity', 'phase2_optimization_intensity', 'learning_rate_exploration_factor',
            'lambda_risk_trigger_level', 'lambda_reduction_factor', 'recalibration_frequency_days',
            'profit_target_atr_mult', 'stop_loss_atr_mult', 'entry_pid_kp', 'exit_pid_kp',
            'min_hold_bars', 'max_hold_bars'
        ];

        function formatValue(val) {
            if (typeof val === 'number') {
                return val.toFixed(4);
            }
            return String(val);
        }

        function renderParameters(params, tierId) {
            const tierMap = {tier1: TIER_1, tier2: TIER_2, tier3: TIER_3};
            const paramList = tierMap[tierId];
            const container = document.getElementById(`best-params-${tierId}`);

            if (!params || Object.keys(params).length === 0) {
                container.innerHTML = '<div class="param-card"><div class="param-name">Waiting...</div></div>';
                return;
            }

            const html = paramList
                .filter(p => params[p] !== undefined)
                .map(p => `
                    <div class="param-card">
                        <div class="param-label"><span class="tier-label tier${tierId.slice(-1)}">TIER ${tierId.slice(-1).toUpperCase()}</span></div>
                        <div class="param-name">${p}</div>
                        <div class="param-value">${formatValue(params[p])}</div>
                    </div>
                `).join('');

            container.innerHTML = html || '<div class="param-card"><div class="param-name">No data yet</div></div>';
        }

        function updateDashboard() {
            fetch('/api/status')
                .then(r => r.json())
                .then(data => {
                    // Update time information
                    const now = new Date();
                    document.getElementById('current-time').textContent =
                        now.toLocaleTimeString('en-US', {hour12: false});

                    if (data.start_time) {
                        const start = new Date(data.start_time);
                        document.getElementById('start-time').textContent =
                            start.toLocaleTimeString('en-US', {hour12: false});
                    }

                    document.getElementById('elapsed-time').textContent = data.elapsed_time;
                    document.getElementById('time-per-iter').textContent = data.time_per_iteration;
                    document.getElementById('last-update').textContent =
                        now.toLocaleTimeString('en-US', {hour12: false});

                    // Update metrics
                    document.getElementById('iteration-count').textContent = data.iterations.length;
                    document.getElementById('best-win-rate').textContent =
                        (data.best_win_rate * 100).toFixed(2) + '%';
                    document.getElementById('improvement').textContent =
                        data.improvement.toFixed(2) + '%';

                    if (data.improvement > 0) {
                        document.getElementById('best-win-subtext').textContent =
                            `Iteration ${data.best_iteration} [+${data.improvement.toFixed(2)}%]`;
                    }

                    // Update status
                    const statusClass = {
                        'waiting': 'status-waiting',
                        'running': 'status-running',
                        'complete': 'status-complete'
                    }[data.status] || 'status-waiting';

                    document.getElementById('status-display').innerHTML =
                        `<div class="status-badge ${statusClass}">${data.status.toUpperCase()}</div>`;

                    // Update iterations log (with timestamps)
                    const logHTML = data.iterations.slice(-10).reverse().map(iter => `
                        <div class="log-entry${iter.is_best ? ' best' : ''}">
                            <div class="log-timestamp">${iter.timestamp_display}</div>
                            <div>[Iter ${iter.iteration}] ${(iter.win_rate * 100).toFixed(2)}% [+${iter.improvement.toFixed(2)}%]${iter.is_best ? ' [BEST!]' : ''}</div>
                        </div>
                    `).join('');

                    document.getElementById('iterations-log').innerHTML = logHTML ||
                        '<div class="log-entry"><div class="log-timestamp">--:--:--</div><div>No iterations yet...</div></div>';

                    // Update parameters
                    if (Object.keys(data.best_params).length > 0) {
                        renderParameters(data.best_params, 'tier1');
                        renderParameters(data.best_params, 'tier2');
                        renderParameters(data.best_params, 'tier3');
                    }
                })
                .catch(err => console.error('Update error:', err));
        }

        // Initial update and then every 3 seconds
        updateDashboard();
        setInterval(updateDashboard, 3000);
    </script>
</body>
</html>
"""
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_status(self):
        """Serve status JSON"""
        data = CalibrationMonitor.read_log()

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


# ============================================================================
# MAIN
# ============================================================================

def run_server():
    """Start the dashboard server"""
    server = HTTPServer(('0.0.0.0', PORT), DashboardHandler)
    print("")
    print("="*80)
    print("CALIBRATION DASHBOARD - WITH TIME STAMPING")
    print("="*80)
    print("")
    print("[OK] Dashboard running at http://localhost:{}".format(PORT))
    print("[OK] Auto-refresh every 3 seconds")
    print("[OK] Displaying:")
    print("     - Current time & Start time")
    print("     - Elapsed time (hours:minutes:seconds)")
    print("     - Time per iteration")
    print("     - Iteration timestamps")
    print("     - All 33 parameters (organized by tier)")
    print("     - Win rate progression")
    print("")
    print("="*80)
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[OK] Dashboard stopped")
        server.shutdown()


if __name__ == '__main__':
    run_server()
