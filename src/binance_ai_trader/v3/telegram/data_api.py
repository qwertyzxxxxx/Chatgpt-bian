"""Read-only JSON HTTP API — exposes production order/signal data for offline analysis.

Runs as a daemon thread alongside the Telegram command server.
Protected by DATA_API_KEY env var (set in production env).

Endpoints:
  GET /api/orders?strategy=hotlist_v663&days=30&key=<KEY>
  GET /api/stats?strategy=hotlist_v663&key=<KEY>
  GET /api/signals?hours=48&strategy=hotlist_v663&key=<KEY>
  GET /api/health

Port: DATA_API_PORT env var (default: 8765)
Key:  DATA_API_KEY  env var (empty = no auth, dev only)

Usage from dev agent:
  import requests
  r = requests.get("https://<prod-domain>:8765/api/orders",
                   params={"strategy":"hotlist_v663","days":7,"key":"<KEY>"})
  orders = r.json()["orders"]
"""
from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

log = logging.getLogger(__name__)

_DEFAULT_PORT = 8765


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _DataApiHandler(BaseHTTPRequestHandler):
    api_key: str = ""

    def log_message(self, fmt, *args) -> None:
        log.debug("[DataAPI] %s %s", self.address_string(), fmt % args)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query, keep_blank_values=False)

        # ── auth ──
        if self.api_key:
            key = (params.get("key") or [""])[0]
            if key != self.api_key:
                self._json(401, {"error": "unauthorized"})
                return

        path = parsed.path.rstrip("/")
        try:
            if path == "/api/health":
                self._json(200, {"status": "ok", "ts": datetime.now(UTC).isoformat()})

            elif path == "/api/orders":
                strategy = (params.get("strategy") or [""])[0]
                days     = int((params.get("days") or ["30"])[0])
                self._handle_orders(strategy, days)

            elif path == "/api/stats":
                strategy = (params.get("strategy") or [""])[0]
                self._handle_stats(strategy)

            elif path == "/api/signals":
                strategy = (params.get("strategy") or [""])[0]
                hours    = int((params.get("hours") or ["48"])[0])
                self._handle_signals(strategy, hours)

            else:
                self._json(404, {
                    "error": "not found",
                    "endpoints": ["/api/health", "/api/orders", "/api/stats", "/api/signals"],
                })
        except Exception as exc:
            log.exception("[DataAPI] error handling %s", path)
            self._json(500, {"error": type(exc).__name__, "detail": str(exc)})

    # ── endpoint implementations ──────────────────────────────────────────────

    def _handle_orders(self, strategy: str, days: int) -> None:
        from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
        repo   = V3PaperOrderRepository()
        orders = repo.load_all()
        if strategy:
            orders = [o for o in orders if o.strategy_id == strategy]
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        orders = [o for o in orders if (o.created_at or "") >= cutoff]
        data   = [_order_to_dict(o) for o in orders]
        self._json(200, {"count": len(data), "strategy": strategy or "all", "days": days, "orders": data})

    def _handle_stats(self, strategy: str) -> None:
        from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
        from binance_ai_trader.v3.performance.calculator import V3PerformanceCalculator
        repo     = V3PaperOrderRepository()
        calc     = V3PerformanceCalculator(repo)
        targets  = [strategy] if strategy else [
            "hotlist_momentum_v3", "hotlist_v66", "hotlist_v662",
            "hotlist_v663", "hotlist_v664",
            "wave_long", "wave_short",
            "classic_c1", "classic_c3",
        ]
        result = {}
        for sid in targets:
            s = calc.calculate(sid, "all_time")
            s30 = calc.calculate(sid, "30d")
            s7  = calc.calculate(sid, "7d")
            result[sid] = {
                "all_time": _stats_to_dict(s),
                "30d":      _stats_to_dict(s30),
                "7d":       _stats_to_dict(s7),
            }
        self._json(200, result)

    def _handle_signals(self, strategy: str, hours: int) -> None:
        from binance_ai_trader.v3.candidates.repository import V3CandidateRepository
        repo       = V3CandidateRepository(None)
        candidates = repo.load_recent(hours=hours)
        if strategy:
            candidates = [c for c in candidates if c.strategy_id == strategy]
        data = [_candidate_to_dict(c) for c in candidates]
        self._json(200, {"count": len(data), "strategy": strategy or "all", "hours": hours, "signals": data})

    # ── helpers ───────────────────────────────────────────────────────────────

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)


# ── serializers ───────────────────────────────────────────────────────────────

def _order_to_dict(o) -> dict:
    return {
        "order_id":    o.order_id,
        "signal_id":   o.signal_id,
        "strategy_id": o.strategy_id,
        "symbol":      o.symbol,
        "direction":   o.direction,
        "entry":       str(o.entry),
        "stop_loss":   str(o.stop_loss),
        "tp1":         str(o.tp1),
        "tp2":         str(o.tp2),
        "rr":          str(o.rr),
        "status":      o.status,
        "result":      o.result,
        "created_at":  o.created_at,
        "filled_at":   o.filled_at,
        "closed_at":   o.closed_at,
        "pnl_pct":     str(o.pnl_pct)     if o.pnl_pct     else None,
        "rr_realized": str(o.rr_realized) if o.rr_realized else None,
    }


def _stats_to_dict(s) -> dict:
    return {
        "pushed":   s.pushed,
        "filled":   s.filled,
        "settled":  s.settled,
        "tp1":      s.tp1,
        "tp2":      s.tp2,
        "sl":       s.sl,
        "timeout":  s.timeout,
        "win_rate": str(s.win_rate),
        "avg_rr":   str(s.avg_rr),
        "avg_pnl":  str(s.avg_pnl),
    }


def _candidate_to_dict(c) -> dict:
    return {
        "signal_id":   c.signal_id,
        "strategy_id": c.strategy_id,
        "symbol":      c.symbol,
        "direction":   c.direction,
        "entry":       c.entry,
        "sl":          c.sl,
        "tp1":         c.tp1,
        "status":      c.status,
        "stop_pct":    c.stop_pct,
        "change_24h":  c.change_24h,
        "quote_volume":c.quote_volume,
        "reason":      c.reason,
        "created_at":  c.created_at,
    }


# ── server class ──────────────────────────────────────────────────────────────

class DataApiServer:
    """Lightweight read-only HTTP JSON API for production order data.

    Starts on DATA_API_PORT (default 8765) as a daemon thread.
    Protected by DATA_API_KEY env var; empty key disables auth (dev only).

    Quick usage from any HTTP client / agent dev session:
        curl "https://<prod-domain>:8765/api/orders?strategy=hotlist_v663&days=7&key=<KEY>"
        curl "https://<prod-domain>:8765/api/stats?key=<KEY>"
        curl "https://<prod-domain>:8765/api/signals?hours=24&key=<KEY>"
    """

    def __init__(self) -> None:
        self._port = int(os.environ.get("DATA_API_PORT", _DEFAULT_PORT))
        self._key  = os.environ.get("DATA_API_KEY", "")

    def start(self) -> None:
        try:
            _DataApiHandler.api_key = self._key
            server = ThreadingHTTPServer(("0.0.0.0", self._port), _DataApiHandler)
            t = threading.Thread(target=server.serve_forever, name="data-api-server", daemon=True)
            t.start()
            auth_info = "key=DATA_API_KEY" if self._key else "⚠️ no auth (set DATA_API_KEY)"
            log.info("[DataAPI] started on port %d  %s", self._port, auth_info)
        except Exception:
            log.exception("[DataAPI] failed to start on port %d", self._port)


def start_data_api() -> DataApiServer | None:
    """Start the data API server if DATA_API_PORT or DATA_API_KEY is configured.

    Returns None silently if not configured (so production stays clean by default).
    Set DATA_API_PORT=8765 or DATA_API_KEY=anything in production env to enable.
    """
    port_set = bool(os.environ.get("DATA_API_PORT", "").strip())
    key_set  = bool(os.environ.get("DATA_API_KEY", "").strip())
    if not (port_set or key_set):
        log.debug("[DataAPI] not configured (set DATA_API_PORT or DATA_API_KEY to enable)")
        return None
    server = DataApiServer()
    server.start()
    return server
