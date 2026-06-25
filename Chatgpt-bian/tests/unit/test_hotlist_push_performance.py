"""Tests for PR41: split hotlist push performance, telegram rank_score block,
last-7-pushed-orders settlement in hourly report."""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path

from binance_ai_trader.hotlist.models import HotlistAlert, HotlistEntryPlan
from binance_ai_trader.hotlist.telegram import format_hotlist_alert_batch_message
from binance_ai_trader.runner.hourly_strategy_report import (
    _query_hotlist_candidate_performance,
    _query_hotlist_push_performance,
    _query_last_7_pushed_orders,
    build_hourly_report,
)


def _make_plan(
    symbol: str = "BTCUSDT",
    direction: str = "LONG",
    change_24h_pct: str = "5.5",
    quote_volume: str = "500000000",
    volume_ratio_15m: str = "1.2",
    entry: str = "100",
    stop_loss: str = "95",
    tp1: str = "110",
    tp2: str = "120",
    rr: str = "2.0",
) -> HotlistEntryPlan:
    return HotlistEntryPlan(
        symbol=symbol,
        direction=direction,
        current_price=Decimal(entry),
        change_24h_pct=Decimal(change_24h_pct),
        quote_volume=Decimal(quote_volume),
        volume_ratio_15m=Decimal(volume_ratio_15m),
        ema20_15m=Decimal("99"),
        atr14=Decimal("2.5"),
        swing_high=Decimal("115"),
        swing_low=Decimal("93"),
        suggested_limit_entry=Decimal(entry),
        stop_loss=Decimal(stop_loss),
        tp1=Decimal(tp1),
        tp2=Decimal(tp2),
        rr=Decimal(rr),
        expires_at="2026-06-25T12:00:00",
        reason="Test reason",
        sentiment="",
    )


def _make_alert(symbol: str = "BTCUSDT", direction: str = "LONG") -> HotlistAlert:
    return HotlistAlert(
        symbol=symbol,
        direction=direction,
        entry=Decimal("100"),
        created_at="2026-06-24T10:00:00",
        level="HIGH",
        plan=_make_plan(symbol=symbol, direction=direction),
    )


def _setup_db_with_alerts_and_results(db_path: str) -> None:
    """Create minimal DB with hotlist_alerts + strategy_results tables."""
    con = sqlite3.connect(db_path)
    con.execute("""CREATE TABLE IF NOT EXISTS hotlist_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, direction TEXT, entry TEXT, level TEXT, created_at TEXT,
        rank_type TEXT DEFAULT 'UNKNOWN'
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS strategy_results (
        result_id TEXT PRIMARY KEY, strategy TEXT, symbol TEXT, direction TEXT,
        entry TEXT, stop_loss TEXT, tp1 TEXT, tp2 TEXT,
        opened_at TEXT, closed_at TEXT, result TEXT DEFAULT 'OPEN',
        pnl_pct REAL, rr_realized REAL, duration_minutes INTEGER, source_id TEXT
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS hotlist_outcomes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        opportunity_id INTEGER, horizon_hours INTEGER,
        status TEXT, evaluated_at TEXT, return_pct REAL
    )""")
    con.execute("INSERT INTO hotlist_alerts(id,symbol,direction,entry,level,created_at,rank_type) VALUES (1,'BTCUSDT','LONG','100','HIGH','2026-06-24T10:00:00','GAINER')")
    con.execute("INSERT INTO hotlist_alerts(id,symbol,direction,entry,level,created_at,rank_type) VALUES (2,'ETHUSDT','SHORT','3000','MED','2026-06-24T09:00:00','LOSER')")
    con.execute("INSERT INTO strategy_results VALUES ('r1','hotlist','BTCUSDT','LONG','100','95','110','120','2026-06-24T10:00:00','2026-06-24T14:00:00','TP1',3.5,2.0,240,'r1')")
    con.execute("INSERT INTO strategy_results VALUES ('r2','hotlist','ETHUSDT','SHORT','3000','3100','2900','2800','2026-06-24T09:00:00','2026-06-24T12:00:00','SL',-2.5,NULL,180,'r2')")
    con.execute("INSERT INTO hotlist_outcomes VALUES (1,1,1,'TP1_HIT','2026-06-24T11:00:00',3.5)")
    con.execute("INSERT INTO hotlist_outcomes VALUES (2,1,4,'TP2_HIT','2026-06-24T14:00:00',5.0)")
    con.execute("INSERT INTO hotlist_outcomes VALUES (3,2,1,'SL_HIT','2026-06-24T10:00:00',-2.5)")
    con.commit()
    con.close()


class TestTelegramRankScoreBlock(unittest.TestCase):
    """T001: verify expanded rank_score block in format_hotlist_alert_batch_message."""

    def setUp(self) -> None:
        self.alert = _make_alert()

    def test_rank_score_block_has_five_fields(self) -> None:
        """Rank score block must contain all 5 sub-fields."""
        msg = format_hotlist_alert_batch_message([self.alert])
        self.assertIn("|24h涨跌|:", msg)
        self.assertIn("成交额:", msg)
        self.assertIn("量比15m:", msg)
        self.assertIn("止损:", msg)
        self.assertIn("RR:", msg)

    def test_rank_score_block_label(self) -> None:
        """Block must be introduced by '排名分:' label."""
        msg = format_hotlist_alert_batch_message([self.alert])
        self.assertIn("排名分:", msg)

    def test_rank_score_values_correct(self) -> None:
        """Values in the block must match plan fields."""
        msg = format_hotlist_alert_batch_message([self.alert])
        # |24h涨跌| = abs(5.5) = 5.50%
        self.assertIn("5.50%", msg)
        # 成交额 = 500000000 / 1M = 500M
        self.assertIn("500M USDT", msg)
        # 量比15m = 1.20x
        self.assertIn("1.20x", msg)

    def test_rank_score_stop_pct_computed(self) -> None:
        """Stop pct must be computed from entry/stop_loss."""
        msg = format_hotlist_alert_batch_message([self.alert])
        # entry=100, stop=95 → stop_pct = 5.00%
        self.assertIn("5.00%", msg)

    def test_old_single_line_format_gone(self) -> None:
        """Old single-line '排名分: |24h|=X%' pattern must not appear."""
        msg = format_hotlist_alert_batch_message([self.alert])
        self.assertNotIn("|24h|=", msg)

    def test_batch_message_still_valid_structure(self) -> None:
        """Header and footer must still be present."""
        msg = format_hotlist_alert_batch_message([self.alert])
        self.assertIn("🔥 Hotlist Alert", msg)
        self.assertIn("仅供研究", msg)
        self.assertIn("#1", msg)


class TestHotlistCandidatePerformance(unittest.TestCase):
    """Tests for _query_hotlist_candidate_performance."""

    def test_returns_correct_counts_from_hotlist_outcomes(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            con = sqlite3.connect(f.name)
            # Seed evaluated_at well in the future so it falls within last 24h
            con.execute("UPDATE hotlist_outcomes SET evaluated_at='2099-01-01T00:00:00'")
            con.commit()
            result = _query_hotlist_candidate_performance(con, "2000-01-01T00:00:00")
            con.close()
        # tp2 should win over tp1 (max logic), sl = 1
        self.assertEqual(result["tp1"], 1)
        self.assertGreaterEqual(result["tp2"], 1)
        self.assertEqual(result["sl"], 1)
        self.assertIn("win_rate", result)
        self.assertIn("total", result)

    def test_empty_db_returns_zeros(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            con = sqlite3.connect(f.name)
            result = _query_hotlist_candidate_performance(con, "2000-01-01T00:00:00")
            con.close()
        self.assertEqual(result["tp1"], 0)
        self.assertEqual(result["win_rate"], 0)


class TestHotlistPushPerformance(unittest.TestCase):
    """Tests for _query_hotlist_push_performance."""

    def test_returns_push_only_stats(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            con = sqlite3.connect(f.name)
            result = _query_hotlist_push_performance(con, "2000-01-01T00:00:00")
            con.close()
        # BTC alert -> TP1, ETH alert -> SL
        self.assertEqual(result["tp1"], 1)
        self.assertEqual(result["sl"], 1)
        self.assertEqual(result["total"], 2)
        self.assertIn("win_rate", result)

    def test_empty_db_returns_zeros(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            con = sqlite3.connect(f.name)
            result = _query_hotlist_push_performance(con, "2000-01-01T00:00:00")
            con.close()
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["win_rate"], 0)


class TestLast7PushedOrders(unittest.TestCase):
    """Tests for _query_last_7_pushed_orders."""

    def test_returns_up_to_7_rows(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            con = sqlite3.connect(f.name)
            rows = _query_last_7_pushed_orders(con)
            con.close()
        self.assertLessEqual(len(rows), 7)
        self.assertGreater(len(rows), 0)

    def test_rows_have_required_keys(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            con = sqlite3.connect(f.name)
            rows = _query_last_7_pushed_orders(con)
            con.close()
        required = {"symbol", "direction", "entry", "pushed_at", "result", "pnl_pct", "rr_realized"}
        for row in rows:
            for key in required:
                self.assertIn(key, row)

    def test_empty_db_returns_empty_list(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            con = sqlite3.connect(f.name)
            rows = _query_last_7_pushed_orders(con)
            con.close()
        self.assertEqual(rows, [])

    def test_settlement_data_joined(self) -> None:
        """Most recent alert should have TP1 result joined from strategy_results."""
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            con = sqlite3.connect(f.name)
            rows = _query_last_7_pushed_orders(con)
            con.close()
        # BTC alert created_at 10:00 is newest
        btc_row = next((r for r in rows if r["symbol"] == "BTCUSDT"), None)
        self.assertIsNotNone(btc_row)
        self.assertEqual(btc_row["result"], "TP1")
        self.assertAlmostEqual(float(btc_row["pnl_pct"]), 3.5)


class TestHourlyReportIncludes(unittest.TestCase):
    """build_hourly_report must include candidate + push perf + last 7 orders."""

    def test_report_contains_candidate_section(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            report = build_hourly_report(f.name)
        self.assertIn("候选池绩效", report)

    def test_report_contains_push_section(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            report = build_hourly_report(f.name)
        self.assertIn("推送绩效", report)

    def test_report_contains_last_7_section(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as f:
            _setup_db_with_alerts_and_results(f.name)
            report = build_hourly_report(f.name)
        self.assertIn("最近7条推送订单结算", report)


if __name__ == "__main__":
    unittest.main()
