from __future__ import annotations

import json
import logging
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from typing import Any

from .indicator_engine import compute_indicators
from .models import Candidate, TimeframeIndicators

logger = logging.getLogger(__name__)

_HOTLIST_LOOKBACK_HOURS = 4
_D1_LIMIT = 30


def _fetch_klines_raw(
    symbol: str,
    interval: str,
    limit: int,
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
) -> list[dict[str, Any]]:
    params = urllib.parse.urlencode({"symbol": symbol, "interval": interval, "limit": str(limit)})
    url = f"{base_url}/fapi/v1/klines?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = json.loads(resp.read())
        return [
            {
                "open": float(row[1]),
                "high": float(row[2]),
                "low": float(row[3]),
                "close": float(row[4]),
                "volume": float(row[5]),
            }
            for row in raw
        ]
    except Exception as exc:
        logger.debug("klines fetch failed for %s %s: %s", symbol, interval, exc)
        return []


def _make_timeframe_indicators(klines: list[dict[str, Any]], tf: str) -> TimeframeIndicators:
    if not klines:
        return TimeframeIndicators()
    computed = compute_indicators(klines, tf)
    ti = TimeframeIndicators()
    ti.trend = computed.get("trend", "UNKNOWN")
    ti.ema10 = computed.get("ema10", "UNKNOWN")
    ti.ema20 = computed.get("ema20", "UNKNOWN")
    ti.ema60 = computed.get("ema60", "UNKNOWN")
    ti.rsi14 = computed.get("rsi14", "UNKNOWN")
    ti.atr_pct = computed.get("atr_pct", "UNKNOWN")
    ti.volume_ratio_20 = computed.get("volume_ratio_20", "UNKNOWN")
    ti.recent_swing_high = computed.get("recent_swing_high", "UNKNOWN")
    ti.recent_swing_low = computed.get("recent_swing_low", "UNKNOWN")
    ti.change_30d = computed.get("change_30d", "UNKNOWN")
    ti.recent_high_30d = computed.get("recent_high_30d", "UNKNOWN")
    ti.recent_low_30d = computed.get("recent_low_30d", "UNKNOWN")
    return ti


def _enrich_klines(candidate: Candidate, base_url: str) -> Candidate:
    symbol = candidate.symbol
    m15_klines = _fetch_klines_raw(symbol, "15m", 48, base_url)
    h1_klines = _fetch_klines_raw(symbol, "1h", 48, base_url)
    h4_klines = _fetch_klines_raw(symbol, "4h", 60, base_url)
    d1_klines = _fetch_klines_raw(symbol, "1d", _D1_LIMIT, base_url)

    candidate.m15 = _make_timeframe_indicators(m15_klines, "m15")
    candidate.h1 = _make_timeframe_indicators(h1_klines, "h1")
    candidate.h4 = _make_timeframe_indicators(h4_klines, "h4")
    candidate.d1 = _make_timeframe_indicators(d1_klines, "d1")

    if m15_klines:
        candidate.current_price = str(m15_klines[-1]["close"])
    return candidate


def _load_ticker_map(
    symbols: list[str],
    base_url: str = "https://fapi.binance.com",
    timeout: float = 10.0,
) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    try:
        url = f"{base_url}/fapi/v1/ticker/24hr"
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            tickers = json.loads(resp.read())
        sym_set = set(symbols)
        for t in tickers:
            s = t.get("symbol", "")
            if s in sym_set:
                result[s] = {
                    "change_24h": str(round(float(t.get("priceChangePercent", 0)), 4)),
                    "quote_volume": str(round(float(t.get("quoteVolume", 0)), 0)),
                }
    except Exception as exc:
        logger.debug("ticker fetch failed: %s", exc)
    return result


def _stop_pct(entry: str, stop_loss: str) -> str:
    try:
        e, s = float(entry), float(stop_loss)
        if e == 0:
            return "UNKNOWN"
        return str(round(abs(e - s) / e * 100, 4))
    except (ValueError, ZeroDivisionError):
        return "UNKNOWN"


def load_hotlist_candidates(db_path: str, lookback_hours: int = _HOTLIST_LOOKBACK_HOURS) -> list[Candidate]:
    candidates: list[Candidate] = []
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cutoff = datetime.now(timezone.utc).isoformat()
        rows = con.execute(
            """
            SELECT o.symbol, o.direction, o.entry, o.sl AS stop_loss,
                   o.tp1, o.tp2, o.rr, o.created_at,
                   w.first_seen_at, w.last_rank, w.observation_count
            FROM hotlist_opportunities o
            LEFT JOIN hotlist_watchlist w ON w.symbol = o.symbol
            WHERE o.expiry >= ?
            ORDER BY o.created_at DESC
            """,
            (cutoff[:10],),
        ).fetchall()
        con.close()

        seen: set[str] = set()
        for row in rows:
            sym = row["symbol"]
            if sym in seen:
                continue
            seen.add(sym)

            first_seen = row["first_seen_at"] or "UNKNOWN"
            duration = "UNKNOWN"
            if first_seen != "UNKNOWN":
                try:
                    fs = datetime.fromisoformat(first_seen.replace("Z", "+00:00"))
                    duration = str(int((datetime.now(timezone.utc) - fs).total_seconds() / 60))
                except Exception:
                    pass

            obs = row["observation_count"]
            candidates.append(Candidate(
                symbol=sym,
                source="hotlist",
                direction=row["direction"],
                entry=row["entry"],
                stop_loss=row["stop_loss"],
                tp1=row["tp1"],
                tp2=row["tp2"],
                rr=row["rr"],
                stop_pct=_stop_pct(row["entry"], row["stop_loss"]),
                hotlist_rank=str(row["last_rank"]) if row["last_rank"] is not None else "UNKNOWN",
                first_seen_at=first_seen,
                active_duration_minutes=duration,
                appearance_count_24h=str(obs) if obs is not None else "UNKNOWN",
                appearance_count_7d=str(obs) if obs is not None else "UNKNOWN",
            ))
    except Exception as exc:
        logger.warning("load_hotlist_candidates failed: %s", exc)
    return candidates


def load_hotlist_alert_candidates(db_path: str, lookback_hours: int = _HOTLIST_LOOKBACK_HOURS) -> list[Candidate]:
    """Fallback: read from hotlist_alerts when hotlist_opportunities is empty.

    Alerts only carry symbol / direction / entry; missing fields are set to
    "UNKNOWN" and data_quality is marked "PARTIAL" so Gemini knows the plan
    is incomplete.
    """
    candidates: list[Candidate] = []
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=lookback_hours)).isoformat()
        rows = con.execute(
            """
            SELECT symbol, direction, entry, created_at,
                   stop_loss, tp1, tp2, rr, expires_at
            FROM hotlist_alerts
            WHERE created_at >= ?
            ORDER BY created_at DESC
            """,
            (cutoff,),
        ).fetchall()
        con.close()

        seen: set[str] = set()
        for row in rows:
            sym = row["symbol"]
            if sym in seen:
                continue
            seen.add(sym)
            sl = row["stop_loss"]
            t1 = row["tp1"]
            t2 = row["tp2"]
            rv = row["rr"]
            has_full = sl and sl != "UNKNOWN" and t1 and t2 and rv
            candidates.append(Candidate(
                symbol=sym,
                source="hotlist_alert",
                direction=row["direction"],
                entry=row["entry"],
                stop_loss=sl if has_full else "UNKNOWN",
                tp1=t1 if has_full else "UNKNOWN",
                tp2=t2 if has_full else "UNKNOWN",
                rr=rv if has_full else "UNKNOWN",
                data_quality="GOOD" if has_full else "PARTIAL",
            ))
    except Exception as exc:
        logger.warning("load_hotlist_alert_candidates failed: %s", exc)
    return candidates


def load_ai_macro_candidates(db_path: str) -> list[Candidate]:
    candidates: list[Candidate] = []
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT * FROM ai_macro_trades WHERE status='OPEN' ORDER BY score DESC LIMIT 6"
        ).fetchall()
        con.close()
        for row in rows:
            candidates.append(Candidate(
                symbol=row["symbol"],
                source="ai_macro",
                direction=row["direction"],
                entry=row["entry"],
                stop_loss=row["stop_loss"],
                tp1=row["tp1"],
                tp2=row["tp2"],
                rr="2.00",
                stop_pct=_stop_pct(row["entry"], row["stop_loss"]),
            ))
    except Exception as exc:
        logger.warning("load_ai_macro_candidates failed: %s", exc)
    return candidates


def merge_top_n(
    hotlist: list[Candidate],
    ai_macro: list[Candidate],
    max_n: int = 4,
) -> list[Candidate]:
    seen: set[str] = set()
    merged: list[Candidate] = []
    for c in hotlist:
        if c.symbol not in seen:
            seen.add(c.symbol)
            merged.append(c)
    for c in ai_macro:
        if c.symbol not in seen:
            seen.add(c.symbol)
            merged.append(c)
    return merged[:max_n]


def build_candidates(
    hotlist_db: str,
    ai_macro_db: str,
    max_candidates: int = 4,
    base_url: str = "https://fapi.binance.com",
) -> list[Candidate]:
    hotlist = load_hotlist_candidates(hotlist_db)
    if not hotlist:
        logger.info("hotlist_opportunities empty — falling back to hotlist_alerts")
        hotlist = load_hotlist_alert_candidates(hotlist_db)
    ai_macro = load_ai_macro_candidates(ai_macro_db)
    candidates = merge_top_n(hotlist, ai_macro, max_candidates)

    if not candidates:
        return candidates

    symbols = [c.symbol for c in candidates]
    ticker_map = _load_ticker_map(symbols, base_url)

    enriched = []
    for c in candidates:
        ticker = ticker_map.get(c.symbol, {})
        c.change_24h = ticker.get("change_24h", "UNKNOWN")
        c.quote_volume = ticker.get("quote_volume", "UNKNOWN")
        c = _enrich_klines(c, base_url)
        enriched.append(c)

    return enriched
