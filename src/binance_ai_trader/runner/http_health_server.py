from __future__ import annotations

import json
import logging
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

log = logging.getLogger(__name__)

_started = False
_started_lock = threading.Lock()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"status": "ok"}, separators=(",", ":")).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: object) -> None:
        pass


def start_health_server(port: int | None = None) -> int:
    """Start a background HTTP server for Replit deployment healthchecks.

    Responds to every GET request with HTTP 200 ``{"status":"ok"}``.
    The server runs in a daemon thread and exits when the process does.
    Returns the port actually used.

    Calling this function more than once is a no-op; the first call wins.
    """
    global _started
    with _started_lock:
        if _started:
            return port or int(os.environ.get("PORT", 8080))
        _started = True

    effective_port = port or int(os.environ.get("PORT", 8080))

    def _serve() -> None:
        try:
            server = HTTPServer(("0.0.0.0", effective_port), _Handler)
            log.info("Health server listening on port %d", effective_port)
            server.serve_forever()
        except Exception as exc:
            log.warning("Health server failed: %s", exc)

    thread = threading.Thread(target=_serve, name="health-server", daemon=True)
    thread.start()
    return effective_port
