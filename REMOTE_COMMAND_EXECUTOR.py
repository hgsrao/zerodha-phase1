#!/usr/bin/env python3
"""
REMOTE COMMAND EXECUTOR
Run commands on PC from Laptop (or vice versa) via web interface

On PC: python REMOTE_COMMAND_EXECUTOR.py --server
On Laptop: python REMOTE_COMMAND_EXECUTOR.py --client http://192.168.0.47:9999
"""

from flask import Flask, request, jsonify
import subprocess
import sys

app = Flask(__name__)

# Store command history
command_history = []

@app.route('/health', methods=['GET'])
def health():
    """Health check"""
    return jsonify({
        'status': 'healthy',
        'role': 'command_executor',
        'commands_executed': len(command_history)
    })

@app.route('/execute', methods=['POST'])
def execute_command():
    """Execute command"""
    try:
        data = request.json
        command = data.get('command', '')

        if not command:
            return jsonify({'error': 'No command provided'}), 400

        print(f"\n[EXECUTOR] Executing: {command}")

        # Execute command
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=60
        )

        # Store in history
        command_history.append({
            'command': command,
            'return_code': result.returncode,
            'output': result.stdout[:1000],  # First 1000 chars
            'error': result.stderr[:1000]
        })

        return jsonify({
            'status': 'success',
            'command': command,
            'return_code': result.returncode,
            'output': result.stdout,
            'error': result.stderr
        })

    except subprocess.TimeoutExpired:
        return jsonify({'error': 'Command timed out'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/history', methods=['GET'])
def history():
    """Get command history"""
    return jsonify({
        'total_commands': len(command_history),
        'history': command_history[-10:]  # Last 10 commands
    })

@app.route('/', methods=['GET'])
def index():
    """Web interface"""
    return '''
<!DOCTYPE html>
<html>
<head>
    <title>Remote Command Executor</title>
    <style>
        body { font-family: monospace; background: #1e1e1e; color: #00ff00; padding: 20px; }
        input, textarea { background: #333; color: #00ff00; border: 1px solid #00ff00; padding: 10px; width: 100%; }
        button { background: #00ff00; color: #000; padding: 10px 20px; cursor: pointer; font-weight: bold; }
        #output { background: #000; border: 1px solid #00ff00; padding: 10px; margin-top: 20px; height: 400px; overflow-y: auto; white-space: pre-wrap; }
    </style>
</head>
<body>
    <h1>🖥️ REMOTE COMMAND EXECUTOR</h1>

    <h2>Enter Command:</h2>
    <textarea id="command" rows="3" placeholder="Enter command here...">python WORKER_NODE.py</textarea>

    <br><br>

    <button onclick="executeCommand()">▶️ EXECUTE</button>
    <button onclick="clearOutput()">🗑️ CLEAR</button>
    <button onclick="getHistory()">📜 HISTORY</button>

    <div id="output">Waiting for command...</div>

    <script>
        function executeCommand() {
            const command = document.getElementById('command').value;
            const output = document.getElementById('output');

            output.textContent = '⏳ Executing: ' + command + '\\n\\n';

            fetch('/execute', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ command: command })
            })
            .then(r => r.json())
            .then(data => {
                output.textContent = 'Return Code: ' + data.return_code + '\\n\\n';
                output.textContent += '--- OUTPUT ---\\n' + data.output;
                if (data.error) {
                    output.textContent += '\\n--- ERROR ---\\n' + data.error;
                }
            })
            .catch(e => {
                output.textContent = '❌ Error: ' + e;
            });
        }

        function clearOutput() {
            document.getElementById('output').textContent = '';
        }

        function getHistory() {
            fetch('/history')
            .then(r => r.json())
            .then(data => {
                let text = 'Command History (' + data.total_commands + ' total):\\n\\n';
                data.history.forEach((cmd, i) => {
                    text += (i+1) + '. ' + cmd.command + ' [RC: ' + cmd.return_code + ']\\n';
                });
                document.getElementById('output').textContent = text;
            });
        }
    </script>
</body>
</html>
    '''

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument('--server', action='store_true', help='Run as server (on master PC)')
    parser.add_argument('--client', help='Connect to server URL (on worker laptop)')
    parser.add_argument('--port', type=int, default=9999, help='Port to run on')

    args = parser.parse_args()

    if args.server:
        print("\n[SERVER] Remote Command Executor running on PC")
        print(f"[SERVER] Open browser: http://localhost:9999")
        print(f"[SERVER] Or from Laptop: http://192.168.0.47:9999")
        print("[SERVER] Waiting for commands...\n")
        app.run(host='0.0.0.0', port=args.port, debug=False)
    else:
        print("[CLIENT] Remote Command Executor (Client mode)")
        print(f"[CLIENT] Not yet implemented - use server mode on PC")
