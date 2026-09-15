#!/usr/bin/env python3
"""
================================================================================
Real-time Calibration Dashboard with 33-Parameter Display
================================================================================
Monitors Stage 2 calibration and displays all 33 parameters in real-time
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
LOG_FILE = "REAL_calibration_with_actual_data.log"
RESULTS_FILE_PATTERN = "REAL_calibration_results_*.json"
PORT = 8888

# ============================================================================
# DATA PARSER
# ============================================================================

class CalibrationMonitor:
    """Parse log file and extract calibration progress + parameters"""

    @staticmethod
    def read_log():
        """Read and parse log file"""
        if not Path(LOG_FILE).exists():
            return {
                'status': 'waiting',
                'message': 'Log file not created yet',
                'iterations': [],
                'best_params': {},
                'current_params': {}
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
                'elapsed_hours': 0,
                'baseline': 0.5175,
                'improvement': 0,
                'best_params': {},
                'current_params': {}
            }

            # Parse log lines
            lines = content.split('\n')

            for i, line in enumerate(lines):
                # Find iteration lines: [Iter 123] 52.34% ...
                match = re.search(r'\[Iter\s+(\d+)\]\s+([\d.]+)%', line)
                if match:
                    iteration = int(match.group(1))
                    win_rate = float(match.group(2)) / 100.0

                    # Extract elapsed time
                    time_match = re.search(r'\[([\d.]+)h elapsed\]', line)
                    elapsed = float(time_match.group(1)) if time_match else 0

                    is_best = '[BEST!]' in line

                    data['iterations'].append({
                        'iteration': iteration,
                        'win_rate': win_rate,
                        'elapsed_hours': elapsed,
                        'is_best': is_best
                    })

                    if is_best:
                        data['best_win_rate'] = win_rate
                        data['best_iteration'] = iteration
                        data['elapsed_hours'] = elapsed

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

            # Calculate improvement
            data['improvement'] = (data['best_win_rate'] - data['baseline']) * 100

            return data

        except Exception as e:
            return {
                'status': 'error',
                'message': str(e),
                'iterations': [],
                'best_params': {},
                'current_params': {}
            }


# ============================================================================
# WEB DASHBOARD
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
    <title>Stage 2 Calibration Monitor - 33 Parameters</title>
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
            max-width: 1600px;
            margin: 0 auto;
        }

        .header {
            text-align: center;
            margin-bottom: 30px;
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

        .metrics-row {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin-bottom: 30px;
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

        .chart-container {
            background: #16213e;
            border: 1px solid #0f3460;
            border-radius: 5px;
            padding: 20px;
            margin-bottom: 20px;
        }

        .chart-title {
            color: #00d4ff;
            margin-bottom: 15px;
            font-weight: bold;
        }

        .progress-bar {
            width: 100%;
            height: 10px;
            background: #0f1a2e;
            border-radius: 5px;
            overflow: hidden;
            margin-top: 10px;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #00d4ff, #0066ff);
            transition: width 0.3s ease;
        }

        .iterations-log {
            background: #0f1a2e;
            border: 1px solid #1a3a52;
            border-radius: 3px;
            padding: 12px;
            max-height: 300px;
            overflow-y: auto;
            font-family: 'Courier New', monospace;
            font-size: 0.8em;
        }

        .log-entry {
            padding: 5px 0;
            border-bottom: 1px solid #1a3a52;
            color: #aaa;
        }

        .log-entry.best {
            background: #1a3a0a;
            color: #00ff00;
            border-left: 3px solid #00ff00;
            padding-left: 8px;
        }

        .refresh-info {
            text-align: center;
            color: #666;
            margin-top: 20px;
            font-size: 0.85em;
        }

        .tier-label {
            display: inline-block;
            font-size: 0.65em;
            padding: 2px 6px;
            border-radius: 2px;
            margin-bottom: 8px;
            font-weight: bold;
        }

        .tier1 {
            background: #ff4444;
            color: white;
        }

        .tier2 {
            background: #ffaa00;
            color: white;
        }

        .tier3 {
            background: #4444ff;
            color: white;
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
            <h1>Stage 2 Calibration Monitor</h1>
            <p>Real-time 33-parameter optimization (24-48+ hours)</p>
        </div>

        <div class="metrics-row">
            <div class="metric-card">
                <div class="metric-label">Current Iteration</div>
                <div class="metric-value" id="iteration">-</div>
                <div class="metric-subtext">of 500 total</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Best Win Rate</div>
                <div class="metric-value" id="best-rate">-</div>
                <div class="metric-subtext" id="best-iter">-</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Improvement</div>
                <div class="metric-value" id="improvement">-</div>
                <div class="metric-subtext">vs baseline 51.75%</div>
            </div>

            <div class="metric-card">
                <div class="metric-label">Elapsed Time</div>
                <div class="metric-value" id="elapsed">-</div>
                <div class="metric-subtext" id="status-badge">
                    <span class="status-badge status-waiting" id="status">Waiting</span>
                </div>
            </div>
        </div>

        <div class="progress-bar">
            <div class="progress-fill" id="progress-fill" style="width: 0%"></div>
        </div>

        <div class="chart-container">
            <div class="chart-title">Win Rate Progression</div>
            <canvas id="chart"></canvas>
        </div>

        <div class="section">
            <div class="section-title">BEST PARAMETERS FOUND</div>
            <div class="parameters-grid" id="best-params-grid">
                <div style="color: #888;">Loading parameters...</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">CURRENT ITERATION PARAMETERS</div>
            <div class="parameters-grid" id="current-params-grid">
                <div style="color: #888;">Loading parameters...</div>
            </div>
        </div>

        <div class="section">
            <div class="section-title">Recent Iterations</div>
            <div class="iterations-log" id="log-entries">
                <div class="log-entry">Waiting for calibration to start...</div>
            </div>
        </div>

        <div class="refresh-info">
            Auto-refreshing every 3 seconds | Last updated: <span id="last-update">-</span>
        </div>
    </div>

    <script>
        // Tier mapping
        const TIER_1 = [
            'base_dp_dt_multiplier', 'base_dv_dt_multiplier',
            'sync_score_confidence_threshold', 'entry_confidence_threshold', 'exit_confidence_threshold',
            'min_risk_reward_ratio', 'profit_target_margin_buffer',
            'vwap_weight', 'confirmation_2bar_weight', 'momentum_weight', 'volatility_weight',
            'green_threshold', 'amber_threshold_lower', 'red_threshold', 'slippage_guard_threshold',
            'volatility_regime_multiplier', 'low_vol_regime_multiplier', 'medium_vol_regime_multiplier', 'high_vol_regime_multiplier'
        ];

        const TIER_2 = [
            'atr_calculation_period', 'entry_signal_smoothing_window', 'exit_signal_smoothing_window',
            'slippage_cost_multiplier', 'minimum_absolute_profit_rupees',
            'momentum_calculation_period', 'vwap_calculation_period', 'signal_persistence_requirement'
        ];

        const TIER_3 = [
            'phase1_exploration_intensity', 'phase2_optimization_intensity', 'learning_rate_exploration_factor',
            'lambda_risk_trigger_level', 'lambda_reduction_factor', 'recalibration_frequency_days'
        ];

        let chart = null;

        function getTierClass(paramName) {
            if (TIER_1.includes(paramName)) return 'tier1';
            if (TIER_2.includes(paramName)) return 'tier2';
            if (TIER_3.includes(paramName)) return 'tier3';
            return '';
        }

        function getTierLabel(paramName) {
            if (TIER_1.includes(paramName)) return 'TIER 1';
            if (TIER_2.includes(paramName)) return 'TIER 2';
            if (TIER_3.includes(paramName)) return 'TIER 3';
            return '';
        }

        function displayParameters(params, containerId) {
            const container = document.getElementById(containerId);
            if (!params || Object.keys(params).length === 0) {
                container.innerHTML = '<div style="color: #888;">No parameters yet...</div>';
                return;
            }

            container.innerHTML = Object.entries(params)
                .sort((a, b) => a[0].localeCompare(b[0]))
                .map(([name, value]) => {
                    const tier = getTierLabel(name);
                    const tierClass = getTierClass(name);
                    const displayValue = typeof value === 'number'
                        ? (value % 1 === 0 ? value : value.toFixed(6))
                        : value;

                    return `<div class="param-card">
                        <div class="tier-label ${tierClass}">${tier}</div>
                        <div class="param-name">${name}</div>
                        <div class="param-value">${displayValue}</div>
                    </div>`;
                })
                .join('');
        }

        async function updateDashboard() {
            try {
                const response = await fetch('/api/status');
                const data = await response.json();

                // Update metrics
                if (data.iterations.length > 0) {
                    const latest = data.iterations[data.iterations.length - 1];
                    document.getElementById('iteration').textContent = latest.iteration;
                    document.getElementById('best-rate').textContent = (data.best_win_rate * 100).toFixed(2) + '%';
                    document.getElementById('best-iter').textContent = 'at iteration ' + data.best_iteration;
                    document.getElementById('improvement').textContent = '+' + data.improvement.toFixed(2) + '%';
                    document.getElementById('elapsed').textContent = data.elapsed_hours.toFixed(1) + 'h';

                    // Progress bar
                    const progress = (latest.iteration / 500) * 100;
                    document.getElementById('progress-fill').style.width = progress + '%';

                    // Status badge
                    const statusEl = document.getElementById('status');
                    statusEl.textContent = data.status.toUpperCase();
                    statusEl.className = 'status-badge status-' + data.status;
                }

                // Update chart
                updateChart(data);

                // Display parameters
                displayParameters(data.best_params, 'best-params-grid');
                displayParameters(data.current_params, 'current-params-grid');

                // Update log entries
                updateLog(data);

                // Update timestamp
                const now = new Date();
                document.getElementById('last-update').textContent = now.toLocaleTimeString();

            } catch (error) {
                console.error('Error updating dashboard:', error);
            }
        }

        function updateChart(data) {
            const iterations = data.iterations.map(i => i.iteration);
            const rates = data.iterations.map(i => (i.win_rate * 100).toFixed(2));

            if (!chart) {
                const ctx = document.getElementById('chart').getContext('2d');
                chart = new Chart(ctx, {
                    type: 'line',
                    data: {
                        labels: iterations,
                        datasets: [{
                            label: 'Win Rate %',
                            data: rates,
                            borderColor: '#00d4ff',
                            backgroundColor: 'rgba(0, 212, 255, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 2,
                            pointHoverRadius: 5,
                            pointBackgroundColor: '#00d4ff'
                        }, {
                            label: 'Baseline (51.75%)',
                            data: Array(iterations.length).fill(51.75),
                            borderColor: '#ff4444',
                            borderDash: [5, 5],
                            borderWidth: 2,
                            fill: false,
                            pointRadius: 0
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: true,
                        plugins: {
                            legend: {
                                display: true,
                                labels: { color: '#aaa' }
                            }
                        },
                        scales: {
                            y: {
                                min: 40,
                                max: 70,
                                ticks: { color: '#aaa' },
                                grid: { color: '#1a3a52' }
                            },
                            x: {
                                ticks: { color: '#aaa' },
                                grid: { color: '#1a3a52' }
                            }
                        }
                    }
                });
            } else {
                chart.data.labels = iterations;
                chart.data.datasets[0].data = rates;
                chart.data.datasets[1].data = Array(iterations.length).fill(51.75);
                chart.update('none');
            }
        }

        function updateLog(data) {
            const logContainer = document.getElementById('log-entries');
            if (data.iterations.length === 0) return;

            const recent = data.iterations.slice(-15);
            logContainer.innerHTML = recent.map(iter => {
                const rate = (iter.win_rate * 100).toFixed(2);
                const badge = iter.is_best ? ' [BEST]' : '';
                const className = iter.is_best ? 'log-entry best' : 'log-entry';
                return '<div class="' + className + '">[Iter ' + iter.iteration + '] ' + rate + '% [' + iter.elapsed_hours.toFixed(1) + 'h]' + badge + '</div>';
            }).join('');
        }

        // Auto-refresh every 3 seconds
        updateDashboard();
        setInterval(updateDashboard, 3000);
    </script>
</body>
</html>"""

        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(html.encode('utf-8'))

    def serve_status(self):
        """Serve status API endpoint"""
        data = CalibrationMonitor.read_log()

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(data).encode('utf-8'))

    def log_message(self, format, *args):
        """Suppress default logging"""
        pass


# ============================================================================
# SERVER
# ============================================================================

def start_server():
    """Start dashboard server"""
    server = HTTPServer(('localhost', PORT), DashboardHandler)
    print("")
    print("="*80)
    print("CALIBRATION DASHBOARD WITH 33-PARAMETER DISPLAY")
    print("="*80)
    print("")
    print("Open in browser: http://localhost:8888")
    print("")
    print("Features:")
    print("  - Real-time iteration progress")
    print("  - Best parameters found (all 33)")
    print("  - Current iteration parameters")
    print("  - Win rate progression chart")
    print("  - Auto-refresh every 3 seconds")
    print("")
    print("Parameter Tiers:")
    print("  TIER 1 (RED):   20 critical parameters")
    print("  TIER 2 (ORANGE): 8 high-value parameters")
    print("  TIER 3 (BLUE):   5 optional parameters")
    print("")
    print("Press Ctrl+C to stop")
    print("="*80)
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped")
        server.shutdown()


if __name__ == "__main__":
    start_server()
