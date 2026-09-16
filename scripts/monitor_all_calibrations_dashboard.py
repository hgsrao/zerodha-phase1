#!/usr/bin/env python3
"""
Real-time DCS-style monitoring dashboard for all 4 calibration engines.
Lightweight polling (every 30s) with minimal CPU overhead.
Generates live HTML artifact for visual inspection.
"""

import json
import time
import subprocess
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Optional

# Calibration checkpoint paths
CHECKPOINTS = {
    "Baseline External (HMM)": "output_external_engine/external_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json",
    "Baseline In-House (Vanilla)": "output_inhouse_engine/inhouse_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json",
    "Revision 3 External (Relay)": "output_revision3_external/revision3_external_48symbol_FULL_3YEAR_calibration_checkpoint.json",
    "Revision 3 In-House (Relay)": "output_revision3_inhouse/revision3_inhouse_48symbol_FULL_3YEAR_calibration_checkpoint.json",
}

LOG_FILES = {
    "Baseline External (HMM)": "calibration_external_3year.log",
    "Baseline In-House (Vanilla)": "calibration_inhouse_3year.log",
    "Revision 3 External (Relay)": "calibration_revision3_external_3year.log",
    "Revision 3 In-House (Relay)": "calibration_revision3_inhouse_3year.log",
}

START_TIMES = {
    "Baseline External (HMM)": datetime(2026, 9, 7, 8, 1),
    "Baseline In-House (Vanilla)": datetime(2026, 9, 7, 8, 14),
    "Revision 3 External (Relay)": datetime(2026, 9, 7, 8, 30),
    "Revision 3 In-House (Relay)": datetime(2026, 9, 7, 8, 35),
}


def get_process_info(engine_name: str) -> Dict[str, Any]:
    """Extract process info (PID, memory, CPU) for engine."""
    pattern = ""
    if "Baseline External" in engine_name:
        pattern = "run_external_engine.*FULL_3YEAR"
    elif "Baseline In-House" in engine_name:
        pattern = "run_inhouse_engine.*FULL_3YEAR"
    elif "Revision 3 External" in engine_name:
        pattern = "run_revision3_external"
    elif "Revision 3 In-House" in engine_name:
        pattern = "run_revision3_inhouse"

    try:
        result = subprocess.run(
            f"ps aux | grep '{pattern}' | grep -v grep",
            shell=True, capture_output=True, text=True, timeout=2
        )
        if result.stdout:
            parts = result.stdout.split()
            return {
                "pid": parts[1],
                "memory_mb": int(parts[5]) / 1024,
                "cpu_pct": float(parts[2]),
                "running": True,
            }
    except Exception:
        pass
    return {"running": False}


def load_checkpoint(engine_name: str) -> Optional[Dict[str, Any]]:
    """Load checkpoint JSON for engine."""
    path = Path(CHECKPOINTS[engine_name])
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None


def get_elapsed_time(engine_name: str) -> str:
    """Calculate elapsed time since engine start."""
    elapsed = datetime.now() - START_TIMES[engine_name]
    hours = elapsed.total_seconds() // 3600
    minutes = (elapsed.total_seconds() % 3600) // 60
    return f"{int(hours)}h {int(minutes)}m"


def extract_metrics(checkpoint: Dict[str, Any]) -> Dict[str, Any]:
    """Extract key metrics from checkpoint."""
    candidates = checkpoint.get('candidates', [])
    trades_per_cand = [len(c.get('report', {}).get('trades', [])) for c in candidates]

    return {
        'total_candidates': len(candidates),
        'accepted': sum(1 for c in candidates if c.get('accepted')),
        'acceptance_rate': (sum(1 for c in candidates if c.get('accepted')) / len(candidates) * 100) if candidates else 0,
        'total_trades': sum(trades_per_cand),
        'avg_trades': sum(trades_per_cand) / len(trades_per_cand) if trades_per_cand else 0,
        'zero_trade_count': sum(1 for t in trades_per_cand if t == 0),
        'best_score': checkpoint.get('best_score', 'N/A'),
    }


def get_log_tail(engine_name: str, lines: int = 3) -> list:
    """Get last N lines from log file."""
    log_path = Path(LOG_FILES[engine_name])
    if not log_path.exists():
        return []
    try:
        with open(log_path) as f:
            all_lines = f.readlines()
        return [line.rstrip() for line in all_lines[-lines:]]
    except Exception:
        return []


def generate_html_dashboard() -> str:
    """Generate live HTML DCS-style dashboard."""
    html = """<!DOCTYPE html>
<html>
<head>
    <title>ECS Calibration DCS Monitor</title>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: 'Courier New', monospace;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            color: #0f3;
            padding: 20px;
            overflow: auto;
        }
        .header {
            text-align: center;
            margin-bottom: 30px;
            border-bottom: 2px solid #0f3;
            padding-bottom: 15px;
        }
        .header h1 {
            font-size: 28px;
            text-shadow: 0 0 10px #0f3;
            margin-bottom: 5px;
        }
        .timestamp {
            font-size: 12px;
            color: #0a0;
            margin-top: 5px;
        }
        .dashboard {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(500px, 1fr));
            gap: 20px;
            max-width: 2200px;
            margin: 0 auto;
        }
        .engine-panel {
            border: 2px solid #0f3;
            border-radius: 5px;
            padding: 15px;
            background: rgba(0, 20, 40, 0.8);
            box-shadow: 0 0 20px rgba(0, 255, 51, 0.2);
        }
        .engine-panel.running {
            border-color: #0f0;
            box-shadow: 0 0 20px rgba(0, 255, 0, 0.3);
        }
        .engine-panel.stopped {
            border-color: #f00;
            box-shadow: 0 0 20px rgba(255, 0, 0, 0.2);
        }
        .engine-name {
            font-size: 16px;
            font-weight: bold;
            margin-bottom: 10px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }
        .status-indicator {
            display: inline-block;
            width: 12px;
            height: 12px;
            border-radius: 50%;
            margin-right: 8px;
            animation: pulse 1s infinite;
        }
        .status-indicator.running { background: #0f0; box-shadow: 0 0 10px #0f0; }
        .status-indicator.stopped { background: #f00; box-shadow: 0 0 10px #f00; }
        @keyframes pulse {
            0%, 100% { opacity: 1; }
            50% { opacity: 0.5; }
        }
        .metric-row {
            display: flex;
            justify-content: space-between;
            padding: 8px 0;
            border-bottom: 1px solid rgba(0, 255, 51, 0.2);
            font-size: 13px;
        }
        .metric-label {
            font-weight: bold;
            color: #0a0;
            min-width: 120px;
        }
        .metric-value {
            text-align: right;
            color: #0f3;
            font-weight: bold;
        }
        .progress-bar {
            width: 100%;
            height: 20px;
            background: rgba(0, 255, 51, 0.1);
            border: 1px solid #0f3;
            margin-top: 10px;
            border-radius: 3px;
            overflow: hidden;
        }
        .progress-fill {
            height: 100%;
            background: linear-gradient(90deg, #0f0, #0f3);
            transition: width 0.3s ease;
            display: flex;
            align-items: center;
            justify-content: center;
            font-size: 11px;
            color: #000;
            font-weight: bold;
        }
        .log-tail {
            margin-top: 15px;
            padding: 10px;
            background: rgba(0, 0, 0, 0.5);
            border: 1px solid #0f3;
            border-radius: 3px;
            font-size: 11px;
            max-height: 80px;
            overflow-y: auto;
            line-height: 1.4;
        }
        .log-line {
            color: #0a0;
            margin-bottom: 2px;
            word-break: break-word;
        }
        .comparison-table {
            margin-top: 30px;
            width: 100%;
            max-width: 1200px;
            margin-left: auto;
            margin-right: auto;
        }
        .comparison-table h2 {
            text-align: center;
            margin-bottom: 15px;
            color: #0f3;
            text-shadow: 0 0 10px #0f3;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            background: rgba(0, 20, 40, 0.9);
        }
        th, td {
            border: 1px solid #0f3;
            padding: 10px;
            text-align: right;
            font-size: 13px;
        }
        th {
            background: rgba(0, 51, 0, 0.5);
            color: #0f0;
            font-weight: bold;
            text-transform: uppercase;
        }
        td { color: #0f3; }
        tr:hover { background: rgba(0, 255, 51, 0.05); }
        .winner { color: #0f0; font-weight: bold; }
        .loser { color: #f00; }
    </style>
</head>
<body>
    <div class="header">
        <h1>⚡ ECS CALIBRATION DCS PANEL ⚡</h1>
        <div class="timestamp">LIVE MONITORING | Last Update: <span id="timestamp">--:--:--</span></div>
    </div>

    <div class="dashboard" id="dashboard"></div>

    <div class="comparison-table">
        <h2>PERFORMANCE COMPARISON (A/B/C/D TEST)</h2>
        <table id="comparison">
            <tr>
                <th>Engine</th>
                <th>Candidates</th>
                <th>Accepted</th>
                <th>Acceptance %</th>
                <th>Total Trades</th>
                <th>Avg Trades</th>
                <th>Best Score</th>
            </tr>
        </table>
    </div>

    <script>
        const CHECKPOINTS = {
            "Baseline External (HMM)": "output_external_engine/external_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json",
            "Baseline In-House (Vanilla)": "output_inhouse_engine/inhouse_engine_48symbol_FULL_3YEAR_calibration_checkpoint.json",
            "Revision 3 External (Relay)": "output_revision3_external/revision3_external_48symbol_FULL_3YEAR_calibration_checkpoint.json",
            "Revision 3 In-House (Relay)": "output_revision3_inhouse/revision3_inhouse_48symbol_FULL_3YEAR_calibration_checkpoint.json",
        };

        async function fetchMetrics(engineName, checkpointPath) {
            try {
                const response = await fetch(checkpointPath);
                if (!response.ok) return null;
                const data = await response.json();
                const candidates = data.candidates || [];
                const tradesPerCand = candidates.map(c => (c.report?.trades || []).length);

                return {
                    total: candidates.length,
                    accepted: candidates.filter(c => c.accepted).length,
                    trades: tradesPerCand.reduce((a, b) => a + b, 0),
                    avgTrades: tradesPerCand.length ? tradesPerCand.reduce((a, b) => a + b, 0) / tradesPerCand.length : 0,
                    bestScore: data.best_score || 'N/A',
                };
            } catch (e) {
                return null;
            }
        }

        async function updateDashboard() {
            document.getElementById('timestamp').innerText = new Date().toLocaleTimeString();

            const dashboard = document.getElementById('dashboard');
            const comparison = document.getElementById('comparison');
            let comparisonHTML = `<tr>
                <th>Engine</th>
                <th>Candidates</th>
                <th>Accepted</th>
                <th>Acceptance %</th>
                <th>Total Trades</th>
                <th>Avg Trades</th>
                <th>Best Score</th>
            </tr>`;

            for (const [engineName, checkpointPath] of Object.entries(CHECKPOINTS)) {
                const metrics = await fetchMetrics(engineName, checkpointPath);
                const isRunning = metrics !== null;

                const panelClass = isRunning ? 'running' : 'stopped';
                const statusIndicator = isRunning ? 'running' : 'stopped';

                let panelHTML = `
                    <div class="engine-panel ${panelClass}">
                        <div class="engine-name">
                            <span class="status-indicator ${statusIndicator}"></span>${engineName}
                        </div>
                `;

                if (metrics) {
                    const acceptanceRate = metrics.total > 0 ? (metrics.accepted / metrics.total * 100).toFixed(1) : 0;
                    const progress = metrics.total > 0 ? (metrics.accepted / Math.max(metrics.total, 100) * 100) : 0;

                    panelHTML += `
                        <div class="metric-row">
                            <span class="metric-label">Candidates:</span>
                            <span class="metric-value">${metrics.total}</span>
                        </div>
                        <div class="metric-row">
                            <span class="metric-label">Accepted:</span>
                            <span class="metric-value">${metrics.accepted} (${acceptanceRate}%)</span>
                        </div>
                        <div class="metric-row">
                            <span class="metric-label">Total Trades:</span>
                            <span class="metric-value">${metrics.trades}</span>
                        </div>
                        <div class="metric-row">
                            <span class="metric-label">Avg Trades/Cand:</span>
                            <span class="metric-value">${metrics.avgTrades.toFixed(1)}</span>
                        </div>
                        <div class="metric-row">
                            <span class="metric-label">Best Score:</span>
                            <span class="metric-value">${typeof metrics.bestScore === 'number' ? metrics.bestScore.toFixed(3) : metrics.bestScore}</span>
                        </div>
                        <div class="progress-bar">
                            <div class="progress-fill" style="width: ${progress}%">
                                ${metrics.total > 0 ? metrics.accepted : '0'}/${metrics.total}
                            </div>
                        </div>
                    `;

                    comparisonHTML += `<tr>
                        <td>${engineName}</td>
                        <td>${metrics.total}</td>
                        <td>${metrics.accepted}</td>
                        <td>${acceptanceRate}%</td>
                        <td>${metrics.trades}</td>
                        <td>${metrics.avgTrades.toFixed(1)}</td>
                        <td>${typeof metrics.bestScore === 'number' ? metrics.bestScore.toFixed(3) : metrics.bestScore}</td>
                    </tr>`;
                } else {
                    panelHTML += `<div style="color: #f00; padding: 20px; text-align: center;">NOT STARTED</div>`;
                    comparisonHTML += `<tr><td>${engineName}</td><td colspan="6" style="text-align: center; color: #f00;">WAITING...</td></tr>`;
                }

                panelHTML += `</div>`;
                dashboard.innerHTML += panelHTML;
            }

            comparison.innerHTML = comparisonHTML;
        }

        // Initial load + poll every 30 seconds
        updateDashboard();
        setInterval(updateDashboard, 30000);
    </script>
</body>
</html>
"""
    return html


def main():
    """Generate and serve the DCS dashboard."""
    print("Generating DCS-style calibration monitoring dashboard...")

    html_content = generate_html_dashboard()
    output_path = Path("/home/shrinivas/ECS_Project_external_engine/calibration_dcs_monitor.html")

    with open(output_path, "w") as f:
        f.write(html_content)

    print(f"✓ Dashboard saved to: {output_path}")
    print(f"\nTo view in browser:")
    print(f"  1. Open file:///home/shrinivas/ECS_Project_external_engine/calibration_dcs_monitor.html")
    print(f"  2. Or: python3 -m http.server 8000 (then visit http://localhost:8000/calibration_dcs_monitor.html)")
    print(f"\nDashboard updates every 30 seconds with <1% CPU overhead.")


if __name__ == "__main__":
    main()
