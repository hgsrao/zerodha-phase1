"""
Detailed Trace Dashboard - Local Web Server
Serves the interactive HTML dashboard on http://localhost:8003
"""

from http.server import HTTPServer, SimpleHTTPRequestHandler
import os
from pathlib import Path

class TraceHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/' or self.path == '':
            self.path = '/detailed_trace_all_48_equities.html'
        return super().do_GET()

    def log_message(self, format, *args):
        # Suppress verbose logging
        pass

if __name__ == '__main__':
    # Change to project directory
    project_dir = Path(__file__).parent
    os.chdir(project_dir)

    port = 8003
    server_address = ('localhost', port)
    httpd = HTTPServer(server_address, TraceHandler)

    print("\n" + "="*100)
    print("📊 DETAILED TRACE DASHBOARD - LOCAL SERVER")
    print("="*100)
    print(f"\n✅ Server started on: http://localhost:{port}")
    print(f"📁 Serving from: {project_dir}")
    print(f"📄 File: detailed_trace_dashboard.html")
    print(f"\n🎯 Open your browser to: http://localhost:{port}")
    print(f"\n💡 Features:")
    print(f"   • 25 candles from Aug 14, 2023 (INFY)")
    print(f"   • All 6 pipeline stages shown for each candle")
    print(f"   • Click candle headers to expand/collapse")
    print(f"   • Use 'Expand All' or 'Collapse All' buttons")
    print(f"\n⏹️  Press Ctrl+C to stop the server\n")
    print("="*100 + "\n")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n\n✅ Server stopped")
        httpd.shutdown()
