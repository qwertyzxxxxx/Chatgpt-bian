"""Unit tests for domain/new_coin.py."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from binance_ai_trader.domain.new_coin import (
    REQUIRED_1D_BARS,
    REQUIRED_4H_BARS,
    NewCoinInfo,
    classify_new_coin,
)

_NOW_MS = 1_700_000_000_000


class TestNewCoinInfo(unittest.TestCase):
    def _info(self, h4: int, d1: int = 150, age: int | None = None) -> NewCoinInfo:
        is_new = h4 < REQUIRED_4H_BARS or (age is not None and age < REQUIRED_1D_BARS)
        return NewCoinInfo(
            symbol="XYZUSDT",
            history_4h_bars=h4,
            history_1d_bars=d1,
            is_new_coin=is_new,
            listing_age_days=age,
        )

    def test_not_new_coin_enough_bars(self):
        info = self._info(720, 150, 130)
        self.assertFalse(info.is_new_coin)

    def test_new_coin_insufficient_4h_bars(self):
        info = self._info(100)
        self.assertTrue(info.is_new_coin)

    def test_new_coin_too_young_by_age(self):
        info = self._info(720, 50, 30)
        self.assertTrue(info.is_new_coin)

    def test_skip_reason_contains_counts(self):
        info = self._info(100)
        reason = info.skip_reason()
        self.assertIn("100", reason)
        self.assertIn(str(REQUIRED_4H_BARS), reason)

    def test_report_contains_fields(self):
        info = self._info(500, 80, 90)
        report = info.report()
        self.assertIn("space_score=MISSING", report)
        self.assertIn("insufficient_4h_history", report)
        self.assertIn("listing_age_days=90", report)

    def test_report_no_age(self):
        info = self._info(200)
        report = info.report()
        self.assertNotIn("listing_age_days", report)


class TestClassifyNewCoin(unittest.TestCase):
    def _repo(self, h4: int, d1: int = 150, earliest_ms: int | None = None) -> MagicMock:
        repo = MagicMock()
        repo.count_klines.side_effect = lambda sym, interval: h4 if interval == "4h" else d1
        repo.load_earliest_kline_open_ms.return_value = earliest_ms
        return repo

    def test_sufficient_history_not_new(self):
        age_ms = _NOW_MS - 200 * 86_400_000  # 200 days ago
        repo = self._repo(730, 200, age_ms)
        info = classify_new_coin("BTCUSDT", repo, now_ms=_NOW_MS)
        self.assertFalse(info.is_new_coin)

    def test_insufficient_4h_bars_is_new(self):
        repo = self._repo(50, 10, None)
        info = classify_new_coin("NEWUSDT", repo, now_ms=_NOW_MS)
        self.assertTrue(info.is_new_coin)
        self.assertEqual(info.history_4h_bars, 50)

    def test_young_by_listing_age(self):
        # 720 bars but only listed 60 days ago
        recent_ms = _NOW_MS - 60 * 86_400_000
        repo = self._repo(720, 150, recent_ms)
        info = classify_new_coin("YOUNGUSDT", repo, now_ms=_NOW_MS)
        self.assertTrue(info.is_new_coin)
        self.assertEqual(info.listing_age_days, 60)

    def test_listing_age_none_when_no_earliest(self):
        repo = self._repo(720, 150, None)
        info = classify_new_coin("BTCUSDT", repo, now_ms=_NOW_MS)
        self.assertIsNone(info.listing_age_days)

    def test_repository_exception_ignored(self):
        repo = MagicMock()
        repo.count_klines.return_value = 720
        repo.load_earliest_kline_open_ms.side_effect = Exception("DB error")
        info = classify_new_coin("SAFEUSDT", repo, now_ms=_NOW_MS)
        self.assertIsNone(info.listing_age_days)


class TestClassifyNewCoinEdges(unittest.TestCase):
    def test_exactly_at_boundary_not_new(self):
        age_ms = _NOW_MS - REQUIRED_1D_BARS * 86_400_000
        repo = MagicMock()
        repo.count_klines.return_value = REQUIRED_4H_BARS
        repo.load_earliest_kline_open_ms.return_value = age_ms
        info = classify_new_coin("EDGEUSDT", repo, now_ms=_NOW_MS)
        self.assertFalse(info.is_new_coin)

    def test_one_bar_short_is_new(self):
        repo = MagicMock()
        repo.count_klines.return_value = REQUIRED_4H_BARS - 1
        repo.load_earliest_kline_open_ms.return_value = None
        info = classify_new_coin("SHORTUSDT", repo, now_ms=_NOW_MS)
        self.assertTrue(info.is_new_coin)
