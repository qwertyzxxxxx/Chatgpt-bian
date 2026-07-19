"""Read-only JSON HTTP API — exposes production order/signal data for offline analysis.

Runs as a daemon thread alongside the Telegram command server.

Security:
  - /api/health: public, no auth required
  - All other endpoints: require DATA_API_KEY
  - Auth via X-API-Key request header (preferred) or ?key= query param
  - ?key= value is masked in all log output

Configuration (env vars):
  DATA_API_HOST  — bind host (default: 0.0.0.0)
  DATA_API_PORT  — bind port (default: 8765)
  DATA_API_KEY   — secret key; empty = no auth (dev only)

Reserved VM mapping (replit.toml):
  localPort=8765  externalPort=80
  → external URL has no port number: https://<prod-domain>/api/health

Endpoints:
  GET /api/health   — public
  GET /api/orders?strategy=hotlist_v663&days=30
  GET /api/stats?strategy=hotlist_v663
  GET /api/signals?hours=48&strategy=hotlist_v663

Usage from dev agent:
  import requests
  r = requests.get("https://<prod-domain>/api/orders",
                   headers={"X-API-Key": "<KEY>"},
                   params={"strategy": "hotlist_v663", "days": 7})
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

_DEFAULT_HOST = "0.0.0.0"
_DEFAULT_PORT = 8765


# ── HTTP handler ──────────────────────────────────────────────────────────────

class _DataApiHandler(BaseHTTPRequestHandler):
    api_key: str = ""

    def log_message(self, fmt, *args) -> None:
        log.debug("[DataAPI] %s %s", self.address_string(), fmt % args)

    # ── helpers ───────────────────────────────────────────────────────────────

    def _parse_request(self):
        """Return (path, params) with key masked in params for safe logging."""
        parsed = urlparse(self.path)
        params = parse_qs(parsed.query, keep_blank_values=False)
        return parsed.path.rstrip("/"), params

    def _extract_key(self, params: dict) -> str:
        """Extract API key from X-API-Key header (preferred) or ?key= param."""
        header_key = self.headers.get("X-API-Key", "")
        if header_key:
            return header_key
        return (params.get("key") or [""])[0]

    def _log_path(self, path: str, params: dict) -> str:
        """Return path for logging with ?key= masked."""
        safe = {k: ("***" if k == "key" else v) for k, v in params.items()}
        qs = "&".join(f"{k}={v[0] if isinstance(v, list) else v}" for k, v in safe.items())
        return f"{path}?{qs}" if qs else path

    def _json(self, code: int, data: dict) -> None:
        body = json.dumps(data, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    # ── routing ───────────────────────────────────────────────────────────────

    def do_GET(self) -> None:
        path, params = self._parse_request()
        log.info("[DataAPI] GET %s", self._log_path(path, params))

        try:
            if path == "/api/health":
                # Public — no auth required
                self._handle_health()
                return

            # All other endpoints require auth
            if self.api_key:
                provided = self._extract_key(params)
                if provided != self.api_key:
                    self._json(401, {"error": "unauthorized"})
                    return

            if path == "/api/orders":
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
                    "endpoints": [
                        "/api/health (public)",
                        "/api/orders",
                        "/api/stats",
                        "/api/signals",
                    ],
                })
        except Exception as exc:
            log.exception("[DataAPI] error handling %s", path)
            self._json(500, {"error": type(exc).__name__, "detail": str(exc)})

    # ── endpoint implementations ──────────────────────────────────────────────

    def _handle_health(self) -> None:
        env = os.environ.get("ENVIRONMENT", os.environ.get("ENV", "unknown"))
        db_ok = False
        try:
            from binance_ai_trader.v3.storage.pg import get_conn
            conn = get_conn()
            conn.close()
            db_ok = True
        except Exception:
            pass
        self._json(200, {
            "status":            "ok",
            "environment":       env,
            "server_time":       datetime.now(UTC).isoformat(timespec="seconds"),
            "database_reachable": db_ok,
        })

    def _handle_orders(self, strategy: str, days: int) -> None:
        from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
        repo   = V3PaperOrderRepository()
        orders = repo.load_all()
        if strategy:
            orders = [o for o in orders if o.strategy_id == strategy]
        cutoff = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
        orders = [o for o in orders if (o.created_at or "") >= cutoff]
        self._json(200, {
            "count":    len(orders),
            "strategy": strategy or "all",
            "days":     days,
            "orders":   [_order_to_dict(o) for o in orders],
        })

    def _handle_stats(self, strategy: str) -> None:
        from binance_ai_trader.v3.paper.repository import V3PaperOrderRepository
        from binance_ai_trader.v3.performance.calculator import V3PerformanceCalculator
        repo    = V3PaperOrderRepository()
        calc    = V3PerformanceCalculator(repo)
        targets = [strategy] if strategy else [
            "hotlist_momentum_v3", "hotlist_v66", "hotlist_v662",
            "hotlist_v663", "hotlist_v664",
            "wave_long", "wave_short",
            "classic_c1", "classic_c3",
        ]
        result = {}
        for sid in targets:
            result[sid] = {
                "all_time": _stats_to_dict(calc.calculate(sid, "all_time")),
                "30d":      _stats_to_dict(calc.calculate(sid, "30d")),
                "7d":       _stats_to_dict(calc.calculate(sid, "7d")),
            }
        self._json(200, result)

    def _handle_signals(self, strategy: str, hours: int) -> None:
        from binance_ai_trader.v3.candidates.repository import V3CandidateRepository
        repo       = V3CandidateRepository(None)
        candidates = repo.load_recent(hours=hours)
        if strategy:
            candidates = [c for c in candidates if c.strategy_id == strategy]
        self._json(200, {
            "count":    len(candidates),
            "strategy": strategy or "all",
            "hours":    hours,
            "signals":  [_candidate_to_dict(c) for c in candidates],
        })


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
        "signal_id":    c.signal_id,
        "strategy_id":  c.strategy_id,
        "symbol":       c.symbol,
        "direction":    c.direction,
        "entry":        c.entry,
        "sl":           c.sl,
        "tp1":          c.tp1,
        "status":       c.status,
        "stop_pct":     c.stop_pct,
        "change_24h":   c.change_24h,
        "quote_volume": c.quote_volume,
        "reason":       c.reason,
        "created_at":   c.created_at,
    }


# ── server class ──────────────────────────────────────────────────────────────

class DataApiServer:
    """Lightweight read-only HTTP JSON API for production order data.

    Listens on (DATA_API_HOST, DATA_API_PORT) as a daemon thread.
    /api/health is public; all other endpoints require DATA_API_KEY.
    Auth via X-API-Key header (preferred) or ?key= param (masked in logs).

    Reserved VM port mapping:  localPort=8765  externalPort=80
    → External URL has no explicit port: https://<prod-domain>/api/health
    """

    def __init__(self) -> None:
        self._host = os.environ.get("DATA_API_HOST", _DEFAULT_HOST)
        self._port = int(os.environ.get("DATA_API_PORT", _DEFAULT_PORT))
        self._key  = os.environ.get("DATA_API_KEY", "")

    def start(self) -> None:
        try:
            _DataApiHandler.api_key = self._key
            server = ThreadingHTTPServer((self._host, self._port), _DataApiHandler)
            t = threading.Thread(
                target=server.serve_forever,
                name="data-api-server",
                daemon=True,
            )
            t.start()
            auth_tag = "X-API-Key required" if self._key else "⚠️ no auth (set DATA_API_KEY)"
            log.info(
                "[DataAPI] started on %s:%d  %s",
                self._host, self._port, auth_tag,
            )
        except Exception:
            log.exception("[DataAPI] failed to start on %s:%d", self._host, self._port)


def start_data_api() -> DataApiServer | None:
    """Start the data API server if DATA_API_PORT or DATA_API_KEY is configured.

    Returns None silently when neither env var is set (production stays clean).
    Set DATA_API_PORT=8765 (+ map externalPort=80) and DATA_API_KEY in prod env.
    """
    port_set = bool(os.environ.get("DATA_API_PORT", "").strip())
    key_set  = bool(os.environ.get("DATA_API_KEY", "").strip())
    if not (port_set or key_set):
        log.debug("[DataAPI] not configured (set DATA_API_PORT or DATA_API_KEY to enable)")
        return None
    server = DataApiServer()
    server.start()
    return server
