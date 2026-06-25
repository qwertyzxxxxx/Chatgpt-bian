"""Unit tests for application/collect_history.py — incremental collection."""
from __future__ import annotations

import time
import unittest
from unittest.mock import MagicMock, patch

from binance_ai_trader.application.collect_history import (
    IncrementalCollectionResult,
    HistoricalDataCollector,
)

_NOW_MS = 1_700_000_000_000
_15M_MS = 900_000
_1H_MS = 3_600_000
_4H_MS = 14_400_000


def _make_collector(
    symbols=("BTCUSDT", "ETHUSDT"),
    latest_close_ms: int | None = None,
    klines_per_call: int = 5,
):
    client = MagicMock()
    client.exchange_info.return_value = [
        MagicMock(symbol=s, contract_type="PERPETUAL", status="TRADING",
                  quote_asset="USDT", margin_asset="USDT")
        for s in symbols
    ]
    client.tickers_24h.return_value = [
        MagicMock(symbol=s, quote_volume="1000000000", price_change_percent="5.0")
        for s in symbols
    ]

    repository = MagicMock()
    repository.load_latest_kline_close_ms.return_value = latest_close_ms

    universe_config = MagicMock()
    universe_config.max_symbols = 200
    universe_config.min_volume_usdt = 0
    universe_config.excluded_symbols = frozenset()

    sector_config = MagicMock()

    collector = HistoricalDataCollector(
        client, repository, universe_config, sector_config, request_pause_seconds=0
    )
    # patch _configured_members to return simple items
    member_items = [MagicMock(symbol=s) for s in symbols]
    collector._configured_members = MagicMock(return_value=member_items)
    # patch _collect_klines to return a fixed count
    collector._collect_klines = MagicMock(return_value=klines_per_call)

    return collector, repository


class TestIncrementalCollectionResultDataclass(unittest.TestCase):
    def _result(self, **kwargs) -> IncrementalCollectionResult:
        defaults = dict(
            total_symbols=5, skipped_up_to_date=1, incremental_updated=2,
            full_initialized=1, downloaded_klines=100,
            failed_symbols=(), duration_seconds=1.5, timed_out=False,
        )
        defaults.update(kwargs)
        return IncrementalCollectionResult(**defaults)

    def test_to_dict_has_all_keys(self):
        r = self._result()
        d = r.to_dict()
        for key in ("total_symbols", "skipped_up_to_date", "incremental_updated",
                    "full_initialized", "downloaded_klines", "failed_symbols",
                    "duration_seconds", "timed_out"):
            self.assertIn(key, d)

    def test_failed_symbols_serialised_as_list(self):
        r = self._result(failed_symbols=("A", "B"))
        self.assertIsInstance(r.to_dict()["failed_symbols"], list)

    def test_duration_rounded(self):
        r = self._result(duration_seconds=1.23456789)
        self.assertEqual(r.to_dict()["duration_seconds"], 1.23)


class TestIncrementalCollectSkipsUpToDate(unittest.TestCase):
    def test_fresh_data_all_skipped(self):
        """If latest_close is 1 interval ago, all 3 intervals × 2 symbols → skipped."""
        fresh_close = _NOW_MS - _15M_MS  # 1 interval fresh
        collector, _ = _make_collector(latest_close_ms=fresh_close)
        result = collector.collect_incremental(end_ms=_NOW_MS)
        self.assertEqual(result.total_symbols, 2)
        self.assertEqual(result.skipped_up_to_date, 6)  # 3 intervals × 2 symbols
        self.assertEqual(result.downloaded_klines, 0)


class TestIncrementalCollectGapFill(unittest.TestCase):
    def test_stale_data_triggers_gap_fill(self):
        """latest_close = 5 intervals ago → gap fill for all intervals."""
        stale_close = _NOW_MS - _15M_MS * 5  # 5 intervals stale for 15m
        collector, _ = _make_collector(
            symbols=("BTCUSDT",), latest_close_ms=stale_close, klines_per_call=3
        )
        result = collector.collect_incremental(end_ms=_NOW_MS)
        self.assertGreater(result.downloaded_klines, 0)
        self.assertEqual(result.incremental_updated, 1)  # 1 symbol got updated
        self.assertEqual(result.full_initialized, 0)


class TestIncrementalCollectFullInit(unittest.TestCase):
    def test_no_data_triggers_full_init(self):
        """latest_close = None → full initialisation."""
        collector, _ = _make_collector(
            symbols=("NEWCOIN",), latest_close_ms=None, klines_per_call=10
        )
        result = collector.collect_incremental(end_ms=_NOW_MS, history_days=30)
        self.assertGreater(result.downloaded_klines, 0)
        self.assertEqual(result.full_initialized, 1)
        self.assertEqual(result.incremental_updated, 0)


class TestIncrementalCollectTimeout(unittest.TestCase):
    def test_timeout_stops_loop(self):
        """max_runtime_minutes=0.001 (~60ms) must timeout before all 50 symbols finish."""
        many_symbols = tuple(f"SYM{i}USDT" for i in range(50))
        collector, _ = _make_collector(symbols=many_symbols, latest_close_ms=None)

        def slow_collect(*args):
            time.sleep(0.02)  # 20ms per interval-call → ~60ms per symbol (3 intervals)
            return 5

        collector._collect_klines = slow_collect
        result = collector.collect_incremental(
            max_runtime_minutes=0.001, end_ms=_NOW_MS  # ~60ms budget
        )
        # total_symbols is always len(symbols), but the loop stops early
        self.assertEqual(result.total_symbols, 50)
        self.assertTrue(result.timed_out)
        # Fewer than 50 symbols actually processed (some were skipped due to timeout)
        processed = result.full_initialized + result.incremental_updated + len(result.failed_symbols)
        self.assertLess(processed, 50)


class TestIncrementalCollectValidation(unittest.TestCase):
    def test_negative_max_runtime_raises(self):
        collector, _ = _make_collector()
        with self.assertRaises(ValueError):
            collector.collect_incremental(max_runtime_minutes=-1.0)

    def test_zero_max_runtime_raises(self):
        collector, _ = _make_collector()
        with self.assertRaises(ValueError):
            collector.collect_incremental(max_runtime_minutes=0.0)

    def test_invalid_history_days_raises(self):
        collector, _ = _make_collector()
        with self.assertRaises(ValueError):
            collector.collect_incremental(max_runtime_minutes=1.0, history_days=0)

    def test_too_many_history_days_raises(self):
        collector, _ = _make_collector()
        with self.assertRaises(ValueError):
            collector.collect_incremental(max_runtime_minutes=1.0, history_days=9999)


class TestIncrementalCollectFailure(unittest.TestCase):
    def test_kline_exception_recorded_as_failure(self):
        """If _collect_klines raises, symbol should be in failed_symbols."""
        collector, _ = _make_collector(symbols=("BADCOIN",), latest_close_ms=None)
        collector._collect_klines = MagicMock(side_effect=Exception("network error"))
        result = collector.collect_incremental(end_ms=_NOW_MS)
        self.assertIn("BADCOIN", result.failed_symbols)
