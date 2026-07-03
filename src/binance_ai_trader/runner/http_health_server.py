from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

log = logging.getLogger(__name__)

_started = False
_started_lock = threading.Lock()

_DB_PATH = Path("data/market_data.db")


def _export_opportunities() -> bytes:
    """Read-only dump of hotlist_opportunities for the production audit."""
    if not _DB_PATH.exists():
        return json.dumps({"error": "db not found"}).encode()
    try:
        con = sqlite3.connect(f"file:{_DB_PATH}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, symbol, direction, entry, sl, tp1, tp2, rr, "
            "confidence, created_at FROM hotlist_opportunities "
            "ORDER BY created_at ASC, id ASC"
        ).fetchall()
        counts = {
            "hotlist_opportunities": con.execute(
                "SELECT COUNT(*) FROM hotlist_opportunities"
            ).fetchone()[0],
            "hotlist_alerts": con.execute(
                "SELECT COUNT(*) FROM hotlist_alerts"
            ).fetchone()[0] if _table_exists(con, "hotlist_alerts") else -1,
            "hotlist_outcomes": con.execute(
                "SELECT COUNT(*) FROM hotlist_outcomes"
            ).fetchone()[0] if _table_exists(con, "hotlist_outcomes") else -1,
            "strategy_results_hotlist": con.execute(
                "SELECT COUNT(*) FROM strategy_results WHERE strategy='hotlist'"
            ).fetchone()[0] if _table_exists(con, "strategy_results") else -1,
        }
        con.close()
        data = {
            "counts": counts,
            "opportunities": [dict(r) for r in rows],
        }
        return json.dumps(data, separators=(",", ":")).encode()
    except Exception as exc:
        return json.dumps({"error": str(exc)}).encode()


def _table_exists(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
        (name,),
    ).fetchone()[0] > 0


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        if self.path == "/export":
            body = _export_opportunities()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
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
    The ``/export`` path returns a read-only JSON dump of
    ``hotlist_opportunities`` for the production audit script.
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
