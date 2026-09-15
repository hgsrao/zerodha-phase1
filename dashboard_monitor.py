#!/usr/bin/env python3
"""
Real-time Calibration Dashboard Monitor
================================================================================
Serves a live web dashboard to monitor Stage 2 calibration progress
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
    """Parse log file and extract calibration progress"""

    @staticmethod
    def read_log():
        """Read and parse log file"""
        if not Path(LOG_FILE).exists():
            return {
                'status': 'waiting',
                'message': 'Log file not created yet',
                'iterations': []
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
                'improvement': 0
            }

            # Parse log lines
            for line in content.split('\n'):
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
                'iterations': []
            }

    @staticmethod
    def read_results():
        """Read final results JSON if available"""
        results_files = list(Path('.').glob(RESULTS_FILE_PATTERN))
        if not results_files:
            return None

        try:
            with open(results_files[-1]) as f:
                return json.load(f)
        except:
            return None


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
        elif parsed.path == '/api/chart-data':
            self.serve_chart_data()
        else:
            self.send_error(404)

    def serve_dashboard(self):
        """Serve main dashboard HTML"""
        html = """<!DOCTYPE html>
<html>
<head>
    <title>Stage 2 Calibration Monitor</title>
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
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            padding: 20px;
        }

        .container {
            max-width: 1200px;
            margin: 0 auto;
        }

        .header {
            text-align: center;
            color: white;
            margin-bottom: 30px;
        }

        .header h1 {
            font-size: 2.5em;
            margin-bottom: 5px;
        }

        .header p {
            font-size: 1.1em;
            opacity: 0.9;
        }

        .metrics-grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 20px;
            margin-bottom: 30px;
        }

        .metric-card {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            transition: transform 0.3s ease;
        }

        .metric-card:hover {
            transform: translateY(-5px);
        }

        .metric-label {
            font-size: 0.9em;
            color: #666;
            text-transform: uppercase;
            letter-spacing: 1px;
            margin-bottom: 10px;
        }

        .metric-value {
            font-size: 2.2em;
            font-weight: bold;
            color: #667eea;
        }

        .metric-subtext {
            font-size: 0.85em;
            color: #999;
            margin-top: 5px;
        }

        .status-badge {
            display: inline-block;
            padding: 5px 12px;
            border-radius: 20px;
            font-size: 0.8em;
            font-weight: bold;
            margin-top: 10px;
        }

        .status-running {
            background: #4CAF50;
            color: white;
        }

        .status-complete {
            background: #2196F3;
            color: white;
        }

        .status-waiting {
            background: #FF9800;
            color: white;
        }

        .chart-container {
            background: white;
            border-radius: 10px;
            padding: 20px;
            margin-bottom: 30px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
        }

        .chart-title {
            font-size: 1.3em;
            font-weight: bold;
            color: #333;
            margin-bottom: 15px;
        }

        .iterations-log {
            background: white;
            border-radius: 10px;
            padding: 20px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            max-height: 400px;
            overflow-y: auto;
        }

        .log-entry {
            padding: 10px;
            border-bottom: 1px solid #eee;
            font-family: 'Monaco', 'Courier New', monospace;
            font-size: 0.9em;
        }

        .log-entry.best {
            background: #fff3cd;
            border-left: 4px solid #ffc107;
            padding-left: 12px;
        }

        .progress-bar {
            width: 100%;
            height: 8px;
            background: #eee;
            border-radius: 4px;
            overflow: hidden;
            margin-top: 10px;
        }

        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #667eea, #764ba2);
            transition: width 0.3s ease;
        }

        .refresh-info {
            text-align: center;
            color: #999;
            margin-top: 20px;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="header">
            <h1>Stage 2 Calibration Monitor</h1>
            <p>Real-time 24-hour backtest optimization</p>
        </div>

        <div class="metrics-grid">
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

        <div class="iterations-log">
            <div class="chart-title" style="margin-bottom: 10px;">Latest Iterations</div>
            <div id="log-entries">
                <div class="log-entry">Waiting for calibration to start...</div>
            </div>
        </div>

        <div class="refresh-info">
            Auto-refreshing every 5 seconds | Last updated: <span id="last-update">-</span>
        </div>
    </div>

    <script>
        let chart = null;

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
                            borderColor: '#667eea',
                            backgroundColor: 'rgba(102, 126, 234, 0.1)',
                            borderWidth: 2,
                            fill: true,
                            tension: 0.4,
                            pointRadius: 0,
                            pointHoverRadius: 5
                        }, {
                            label: 'Baseline (51.75%)',
                            data: Array(iterations.length).fill(51.75),
                            borderColor: '#FF6B6B',
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
                                position: 'top'
                            }
                        },
                        scales: {
                            y: {
                                min: 40,
                                max: 70,
                                title: {
                                    display: true,
                                    text: 'Win Rate %'
                                }
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

            if (data.iterations.length === 0) {
                return;
            }

            // Show last 20 iterations
            const recent = data.iterations.slice(-20);
            logContainer.innerHTML = recent.map(iter => {
                const rate = (iter.win_rate * 100).toFixed(2);
                const badge = iter.is_best ? ' <strong>[BEST]</strong>' : '';
                const className = iter.is_best ? 'log-entry best' : 'log-entry';
                return '<div class="' + className + '">[Iter ' + iter.iteration + '] ' + rate + '% [' + iter.elapsed_hours.toFixed(1) + 'h]' + badge + '</div>';
            }).join('');
        }

        // Auto-refresh every 5 seconds
        updateDashboard();
        setInterval(updateDashboard, 5000);
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

    def serve_chart_data(self):
        """Serve chart data endpoint"""
        data = CalibrationMonitor.read_log()

        chart_data = {
            'iterations': [d['iteration'] for d in data['iterations']],
            'win_rates': [d['win_rate'] for d in data['iterations']],
            'elapsed_times': [d['elapsed_hours'] for d in data['iterations']]
        }

        self.send_response(200)
        self.send_header('Content-type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps(chart_data).encode('utf-8'))

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
    print("CALIBRATION DASHBOARD STARTED")
    print("="*80)
    print("")
    print("Open in browser: http://localhost:8888")
    print("")
    print("Monitoring calibration progress...")
    print("Auto-refreshes every 5 seconds")
    print("")
    print("Press Ctrl+C to stop dashboard")
    print("="*80)
    print("")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nDashboard stopped")
        server.shutdown()


if __name__ == "__main__":
    start_server()
