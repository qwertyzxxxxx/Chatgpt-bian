"""Tests for Hotlist Ranking Audit — PR #40.

Covers:
  1. format_hotlist_alert_batch_message unified title (no Top1/Top2/Top3)
  2. #N numbering instead of N. numbering
  3. candidate_count header line
  4. rank_score (|24h%|) shown per signal
  5. Ranking algorithm sort key validation
"""
from __future__ import annotations

import unittest
from decimal import Decimal

from binance_ai_trader.hotlist.models import HotlistAlert, HotlistEntryPlan
from binance_ai_trader.hotlist.telegram import format_hotlist_alert_batch_message


def _make_plan(
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    change_24h_pct: str = "20.5",
    quote_volume: str = "500000000",
    volume_ratio_15m: str = "1.5",
) -> HotlistEntryPlan:
    return HotlistEntryPlan(
        symbol=symbol,
        direction=direction,
        current_price=Decimal("50000"),
        change_24h_pct=Decimal(change_24h_pct),
        quote_volume=Decimal(quote_volume),
        volume_ratio_15m=Decimal(volume_ratio_15m),
        ema20_15m=Decimal("49000"),
        atr14=Decimal("500"),
        swing_high=Decimal("51000"),
        swing_low=Decimal("48000"),
        suggested_limit_entry=Decimal("49500"),
        stop_loss=Decimal("48000"),
        tp1=Decimal("51000"),
        tp2=Decimal("52500"),
        rr=Decimal("2.00"),
        expires_at="2026-06-24T12:00:00+00:00",
        reason="测试原因",
        sentiment="🔥 主升浪延续",
    )


def _make_alert(plan: HotlistEntryPlan) -> HotlistAlert:
    return HotlistAlert(
        symbol=plan.symbol,
        direction=plan.direction,
        entry=plan.suggested_limit_entry,
        created_at="2026-06-24T10:00:00+00:00",
        level="HIGH",
        plan=plan,
    )


class TestHotlistBatchMessageTitle(unittest.TestCase):
    """Verify title never says Top1/Top2/Top3."""

    def _batch(self, n: int) -> str:
        plans = [
            _make_plan(symbol=f"COIN{i}USDT", change_24h_pct=str(20 - i))
            for i in range(n)
        ]
        alerts = [_make_alert(p) for p in plans]
        return format_hotlist_alert_batch_message(alerts, max_n=3)

    def test_title_no_top1(self):
        msg = self._batch(1)
        self.assertNotIn("Top1", msg)
        self.assertIn("🔥 Hotlist Alert", msg)

    def test_title_no_top2(self):
        msg = self._batch(2)
        self.assertNotIn("Top2", msg)
        self.assertIn("🔥 Hotlist Alert", msg)

    def test_title_no_top3(self):
        msg = self._batch(3)
        self.assertNotIn("Top3", msg)
        self.assertIn("🔥 Hotlist Alert", msg)

    def test_title_exact_first_line(self):
        msg = self._batch(1)
        first_line = msg.split("\n")[0]
        self.assertEqual(first_line, "🔥 Hotlist Alert")

    def test_empty_returns_empty(self):
        self.assertEqual(format_hotlist_alert_batch_message([]), "")


class TestHotlistBatchCandidateCount(unittest.TestCase):
    """candidate_count shown in header."""

    def test_one_signal_header(self):
        plan = _make_plan()
        msg = format_hotlist_alert_batch_message([_make_alert(plan)])
        self.assertIn("1 个信号", msg)

    def test_three_signals_header(self):
        plans = [_make_plan(symbol=f"X{i}USDT") for i in range(3)]
        msg = format_hotlist_alert_batch_message([_make_alert(p) for p in plans])
        self.assertIn("3 个信号", msg)

    def test_rank_basis_mentioned(self):
        plan = _make_plan()
        msg = format_hotlist_alert_batch_message([_make_alert(plan)])
        self.assertIn("排序", msg)
        self.assertIn("24h涨跌", msg)


class TestHotlistBatchNumbering(unittest.TestCase):
    """Items numbered #1 #2 #3, not 1. 2. 3."""

    def test_hash_numbering_single(self):
        plan = _make_plan(symbol="BTCUSDT")
        msg = format_hotlist_alert_batch_message([_make_alert(plan)])
        self.assertIn("#1", msg)
        self.assertNotIn("1. ", msg)

    def test_hash_numbering_three(self):
        plans = [_make_plan(symbol=f"COIN{i}USDT") for i in range(3)]
        msg = format_hotlist_alert_batch_message([_make_alert(p) for p in plans])
        self.assertIn("#1", msg)
        self.assertIn("#2", msg)
        self.assertIn("#3", msg)

    def test_no_old_dot_numbering(self):
        plans = [_make_plan(symbol=f"C{i}USDT") for i in range(3)]
        msg = format_hotlist_alert_batch_message([_make_alert(p) for p in plans])
        lines = msg.split("\n")
        dot_numbered = [l for l in lines if l.startswith(("1. ", "2. ", "3. "))]
        self.assertEqual(dot_numbered, [])


class TestHotlistBatchRankScore(unittest.TestCase):
    """rank_score = |24h%| shown per signal."""

    def test_rank_score_line_present(self):
        plan = _make_plan(change_24h_pct="25.50")
        msg = format_hotlist_alert_batch_message([_make_alert(plan)])
        self.assertIn("排名分", msg)
        self.assertIn("25.50%", msg)

    def test_rank_score_short_direction(self):
        plan = _make_plan(change_24h_pct="-18.75", direction="SHORT")
        msg = format_hotlist_alert_batch_message([_make_alert(plan)])
        self.assertIn("18.75%", msg)

    def test_volume_shown_as_M(self):
        plan = _make_plan(quote_volume="350000000")
        msg = format_hotlist_alert_batch_message([_make_alert(plan)])
        self.assertIn("350M USDT", msg)


class TestHotlistRankingAlgorithm(unittest.TestCase):
    """Validate the documented ranking sort key using plain namedtuples."""

    def _ticker(self, symbol: str, pct: str, vol: str):
        from collections import namedtuple
        T = namedtuple("T", ["symbol", "price_change_percent", "quote_volume"])
        return T(symbol=symbol, price_change_percent=Decimal(pct), quote_volume=Decimal(vol))

    def test_sort_key_abs_change_primary(self):
        """Higher |change_24h_pct| ranks #1 regardless of direction."""
        tickers = [
            self._ticker("AUSDT", "30", "100000000"),
            self._ticker("BUSDT", "-40", "50000000"),
            self._ticker("CUSDT", "20", "200000000"),
        ]
        ranked = sorted(
            tickers,
            key=lambda t: (-abs(t.price_change_percent), -t.quote_volume, t.symbol),
        )
        self.assertEqual(ranked[0].symbol, "BUSDT")  # |−40| = 40 highest
        self.assertEqual(ranked[1].symbol, "AUSDT")  # |+30| = 30
        self.assertEqual(ranked[2].symbol, "CUSDT")  # |+20| = 20

    def test_sort_key_volume_tiebreaker(self):
        """Equal |change%|: higher volume wins."""
        tickers = [
            self._ticker("AUSDT", "25", "100000000"),
            self._ticker("BUSDT", "25", "300000000"),
        ]
        ranked = sorted(
            tickers,
            key=lambda t: (-abs(t.price_change_percent), -t.quote_volume, t.symbol),
        )
        self.assertEqual(ranked[0].symbol, "BUSDT")

    def test_sort_key_symbol_tiebreaker(self):
        """Equal |change%| and volume: symbol ASC."""
        tickers = [
            self._ticker("ZETAUSDT", "25", "100000000"),
            self._ticker("ALPHABETATUSDT", "25", "100000000"),
        ]
        ranked = sorted(
            tickers,
            key=lambda t: (-abs(t.price_change_percent), -t.quote_volume, t.symbol),
        )
        self.assertEqual(ranked[0].symbol, "ALPHABETATUSDT")


if __name__ == "__main__":
    unittest.main()
