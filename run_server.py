"""Wrapper that starts a health-check HTTP server before launching the run-loop.

Replit VM deployments send a startup probe (GET /) to verify the process is
ready before marking it live. The run-loop is a background worker with no HTTP
port, so without this wrapper the probe times out and the deployment fails the
promote step.
"""
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *_args):
        pass


def _start_health_server(port: int = 8080) -> None:
    server = HTTPServer(("0.0.0.0", port), _HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()


if __name__ == "__main__":
    _start_health_server()
    from binance_ai_trader.entrypoints.cli import main
    sys.exit(main(["run-loop"] + sys.argv[1:]))
