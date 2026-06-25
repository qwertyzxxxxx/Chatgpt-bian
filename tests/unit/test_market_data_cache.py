"""Unit tests for infrastructure/market_data_cache.py."""
from __future__ import annotations

import unittest
from decimal import Decimal
from unittest.mock import MagicMock

from binance_ai_trader.domain.models import Kline
from binance_ai_trader.infrastructure.market_data_cache import (
    FRESHNESS_MS,
    CacheStats,
    MarketDataCache,
)

_NOW_MS = 1_700_000_000_000  # fixed "now" for tests


def _kline(symbol: str, interval: str, close_ms: int) -> Kline:
    return Kline(
        symbol=symbol,
        interval=interval,
        open_time_ms=close_ms - 900_000,
        open=Decimal("1"),
        high=Decimal("2"),
        low=Decimal("0.5"),
        close=Decimal("1.5"),
        volume=Decimal("1000"),
        close_time_ms=close_ms,
        quote_volume=Decimal("1500"),
        trade_count=100,
    )


class TestCacheStats(unittest.TestCase):
    def test_to_dict_fields(self):
        s = CacheStats(cache_hit=3, cache_miss=1, api_calls=1, symbols_updated=1, duration_seconds=0.5)
        d = s.to_dict()
        self.assertEqual(d["cache_hit"], 3)
        self.assertEqual(d["cache_miss"], 1)
        self.assertEqual(d["api_calls"], 1)
        self.assertEqual(d["symbols_updated"], 1)
        self.assertIsInstance(d["duration_seconds"], float)


class TestMarketDataCacheFreshness(unittest.TestCase):
    def _make_cache(self, cached_klines, fetched_klines=None):
        repo = MagicMock()
        repo.load_klines.return_value = tuple(cached_klines)
        if fetched_klines is not None:
            repo.load_klines.side_effect = [
                tuple(cached_klines),
                tuple(fetched_klines),
            ]
        client = MagicMock()
        client.historical_klines.return_value = ()
        cache = MarketDataCache(repo, client=client)
        return cache, repo

    def test_cache_hit_fresh_data(self):
        """Data younger than freshness threshold → cache hit, no API call."""
        fresh_close_ms = _NOW_MS - 60_000  # 1 minute old (15m threshold = 20min)
        kl = _kline("BTCUSDT", "15m", fresh_close_ms)
        cache, repo = self._make_cache([kl])
        result = cache.get_klines("BTCUSDT", "15m", 10, now_ms=_NOW_MS)
        self.assertEqual(len(result), 1)
        self.assertEqual(cache.stats().cache_hit, 1)
        self.assertEqual(cache.stats().cache_miss, 0)

    def test_cache_miss_stale_data(self):
        """Data older than freshness threshold → cache miss, API call."""
        stale_close_ms = _NOW_MS - (FRESHNESS_MS["15m"] + 60_000)
        kl = _kline("ETHUSDT", "15m", stale_close_ms)
        repo = MagicMock()
        repo.load_klines.side_effect = [
            (kl,),  # first call: stale cached data
            (kl,),  # second call: after save
        ]
        client = MagicMock()
        client.historical_klines.return_value = (kl,)
        cache = MarketDataCache(repo, client=client)
        cache.get_klines("ETHUSDT", "15m", 10, now_ms=_NOW_MS)
        self.assertEqual(cache.stats().cache_miss, 1)

    def test_empty_cache_triggers_fetch(self):
        """No cached data at all → cache miss."""
        repo = MagicMock()
        repo.load_klines.return_value = ()
        client = MagicMock()
        client.historical_klines.return_value = ()
        cache = MarketDataCache(repo, client=client)
        cache.get_klines("XYZUSDT", "1h", 10, now_ms=_NOW_MS)
        self.assertEqual(cache.stats().cache_miss, 1)
        self.assertEqual(cache.stats().cache_hit, 0)

    def test_stats_accumulate_across_calls(self):
        """Multiple hits accumulate correctly."""
        fresh_ms = _NOW_MS - 60_000
        kl = _kline("BTCUSDT", "1h", fresh_ms)
        repo = MagicMock()
        repo.load_klines.return_value = (kl,)
        cache = MarketDataCache(repo)
        for _ in range(3):
            cache.get_klines("BTCUSDT", "1h", 5, now_ms=_NOW_MS)
        self.assertEqual(cache.stats().cache_hit, 3)

    def test_freshness_thresholds(self):
        """Verify freshness values are > 0 for all intervals."""
        for interval in ("15m", "1h", "4h", "1d"):
            self.assertGreater(FRESHNESS_MS[interval], 0, interval)

    def test_1d_interval_uses_direct_fetch_not_client(self):
        """1d interval bypasses client and goes to _fetch_direct."""
        stale_ms = _NOW_MS - (FRESHNESS_MS["1d"] + 3_600_000)
        kl = _kline("BTCUSDT", "1d", stale_ms)
        repo = MagicMock()
        repo.load_klines.return_value = (kl,)
        client = MagicMock()
        cache = MarketDataCache(repo, client=client)
        with unittest.mock.patch.object(cache, "_fetch_direct", return_value=()) as mock_fd:
            cache.get_klines("BTCUSDT", "1d", 30, now_ms=_NOW_MS)
            mock_fd.assert_called_once()
        # client.historical_klines must NOT be called for 1d
        client.historical_klines.assert_not_called()


import unittest.mock
