"""Freshness-aware K-line cache backed by a MarketDataRepository (SQLite).

All scan/hotlist/leaderboard/gemini modules should call this layer first
instead of hitting the Binance API directly.  Only a cache miss (stale or
absent) triggers an API call, and then only for the missing gap.

Freshness thresholds (per interval):
    15m  <= 20 minutes
    1h   <= 70 minutes
    4h   <= 5 hours
    1d   <= 30 hours
"""
from __future__ import annotations

import json
import logging
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass, field

from binance_ai_trader.domain.models import Kline
from decimal import Decimal

log = logging.getLogger(__name__)

FRESHNESS_MS: dict[str, int] = {
    "15m": 20 * 60_000,
    "1h":  70 * 60_000,
    "4h":  5 * 3_600_000,
    "1d":  30 * 3_600_000,
}

_INTERVAL_MS: dict[str, int] = {
    "15m": 900_000,
    "1h":  3_600_000,
    "4h":  14_400_000,
    "1d":  86_400_000,
}


@dataclass
class CacheStats:
    cache_hit: int = 0
    cache_miss: int = 0
    api_calls: int = 0
    symbols_updated: int = 0
    duration_seconds: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "cache_hit": self.cache_hit,
            "cache_miss": self.cache_miss,
            "api_calls": self.api_calls,
            "symbols_updated": self.symbols_updated,
            "duration_seconds": round(self.duration_seconds, 2),
        }


class MarketDataCache:
    """Freshness-aware K-line cache.

    Parameters
    ----------
    repository:
        A MarketDataRepository instance for DB reads/writes.
    base_url:
        Binance FAPI base URL used for direct fetches (when the repository
        client does not support the interval, e.g. "1d").
    client:
        Optional BinancePublicClient for 15m/1h/4h fetches.  When *None*
        the cache falls back to direct URL fetching for all intervals.
    timeout:
        HTTP timeout in seconds for direct URL fetches.
    """

    def __init__(
        self,
        repository,
        base_url: str = "https://fapi.binance.com",
        client=None,
        timeout: float = 10.0,
    ) -> None:
        self._repo = repository
        self._base_url = base_url
        self._client = client
        self._timeout = timeout
        self._stats = CacheStats()
        self._start = time.monotonic()

    def get_klines(
        self,
        symbol: str,
        interval: str,
        limit: int,
        now_ms: int | None = None,
    ) -> tuple[Kline, ...]:
        """Return up to *limit* most-recent closed klines for *symbol/interval*.

        Checks freshness against the DB first.  On a cache miss, fetches the
        missing gap from the Binance API and persists it to the DB before
        returning.
        """
        now = now_ms if now_ms is not None else (time.time_ns() // 1_000_000)
        freshness = FRESHNESS_MS.get(interval, 0)

        cached = self._repo.load_klines(symbol, interval, limit)

        if cached:
            age_ms = now - cached[-1].close_time_ms
            if age_ms <= freshness:
                self._stats.cache_hit += 1
                return cached

        self._stats.cache_miss += 1
        gap_start_ms: int | None = None
        if cached:
            gap_start_ms = cached[-1].close_time_ms + 1

        fetched = self._fetch(symbol, interval, limit, gap_start_ms, now)
        if fetched:
            self._repo.save_klines(fetched)
            self._stats.api_calls += 1
            self._stats.symbols_updated += 1
            return self._repo.load_klines(symbol, interval, limit)

        return cached

    def stats(self) -> CacheStats:
        self._stats.duration_seconds = time.monotonic() - self._start
        return self._stats

    def _fetch(
        self,
        symbol: str,
        interval: str,
        limit: int,
        start_ms: int | None,
        now_ms: int,
    ) -> tuple[Kline, ...]:
        """Fetch klines from Binance (client or direct URL)."""
        if self._client is not None and interval in {"15m", "1h", "4h"}:
            try:
                return self._client.historical_klines(
                    symbol, interval, limit=limit,
                    start_time_ms=start_ms,
                    now_ms=now_ms + 1,
                )
            except Exception as exc:
                log.debug("BinancePublicClient fetch failed %s %s: %s", symbol, interval, exc)
                return ()

        return self._fetch_direct(symbol, interval, limit, start_ms, now_ms)

    def _fetch_direct(
        self,
        symbol: str,
        interval: str,
        limit: int,
        start_ms: int | None,
        now_ms: int,
    ) -> tuple[Kline, ...]:
        """Direct HTTP fetch to Binance fapi/v1/klines (supports all intervals)."""
        params: dict[str, str] = {
            "symbol": symbol,
            "interval": interval,
            "limit": str(min(limit, 1500)),
        }
        if start_ms is not None:
            params["startTime"] = str(start_ms)
        query = urllib.parse.urlencode(params)
        url = f"{self._base_url}/fapi/v1/klines?{query}"
        try:
            with urllib.request.urlopen(url, timeout=self._timeout) as resp:
                raw = json.loads(resp.read())
            return tuple(
                Kline(
                    symbol=symbol,
                    interval=interval,
                    open_time_ms=int(row[0]),
                    open=Decimal(row[1]),
                    high=Decimal(row[2]),
                    low=Decimal(row[3]),
                    close=Decimal(row[4]),
                    volume=Decimal(row[5]),
                    close_time_ms=int(row[6]),
                    quote_volume=Decimal(row[7]),
                    trade_count=int(row[8]),
                )
                for row in raw
                if isinstance(row, list) and len(row) >= 9 and int(row[6]) < now_ms
            )
        except Exception as exc:
            log.debug("Direct klines fetch failed %s %s: %s", symbol, interval, exc)
            return ()
