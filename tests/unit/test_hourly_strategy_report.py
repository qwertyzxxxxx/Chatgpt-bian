"""Unit tests for hourly_strategy_report.py"""
from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path


def _make_market_db(path: str) -> None:
    """Create a minimal market_data.db schema for testing."""
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE collection_runs (
            id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
            status TEXT NOT NULL DEFAULT 'SUCCEEDED'
        );
        CREATE TABLE analysis_snapshots (
            snapshot_id TEXT PRIMARY KEY, snapshot_type TEXT NOT NULL,
            collection_run_id TEXT, source_ref TEXT NOT NULL,
            data_cutoff_ms INTEGER NOT NULL DEFAULT 0,
            strategy_id TEXT NOT NULL DEFAULT 'baseline_v1',
            created_at TEXT NOT NULL, finalized_at TEXT
        );
        CREATE TABLE signals (
            run_id TEXT NOT NULL, snapshot_id TEXT,
            rank INTEGER NOT NULL DEFAULT 1, symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            combined_regime TEXT NOT NULL DEFAULT 'BULL',
            sector TEXT NOT NULL DEFAULT 'OTHER',
            sector_rank INTEGER, score REAL NOT NULL DEFAULT 50,
            capital_score REAL NOT NULL DEFAULT 50, space_score REAL NOT NULL DEFAULT 50,
            final_signal_score REAL NOT NULL DEFAULT 50,
            entry TEXT NOT NULL DEFAULT '1.0', latest_close TEXT NOT NULL DEFAULT '1.0',
            stop_loss TEXT NOT NULL DEFAULT '0.95', stop_loss_pct TEXT NOT NULL DEFAULT '5',
            tp1 TEXT NOT NULL DEFAULT '1.05', tp2 TEXT NOT NULL DEFAULT '1.1',
            rr_tp1 TEXT NOT NULL DEFAULT '1', rr_tp2 TEXT NOT NULL DEFAULT '2',
            logic_summary TEXT NOT NULL DEFAULT 'test', generated_at TEXT NOT NULL,
            PRIMARY KEY (run_id, symbol)
        );
        CREATE TABLE paper_trades (
            signal_run_id TEXT NOT NULL, symbol TEXT NOT NULL,
            direction TEXT NOT NULL DEFAULT 'LONG',
            result TEXT NOT NULL DEFAULT 'OPEN',
            risk_pct TEXT NOT NULL DEFAULT '1',
            risk_amount TEXT NOT NULL DEFAULT '10',
            realized_r TEXT, pnl TEXT, equity_after TEXT NOT NULL DEFAULT '1000',
            action TEXT NOT NULL DEFAULT 'OPEN',
            processed_at TEXT NOT NULL
        );
        CREATE TABLE hotlist_alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE hotlist_outcomes (
            opportunity_id INTEGER, horizon_hours INTEGER,
            status TEXT NOT NULL, evaluated_at TEXT NOT NULL, return_pct TEXT
        );
        CREATE TABLE gemini_committee_reviews (
            review_id TEXT PRIMARY KEY, created_at TEXT NOT NULL,
            provider TEXT, decision TEXT, best_symbol TEXT,
            direction TEXT, entry TEXT, stop_loss TEXT, tp1 TEXT, tp2 TEXT,
            rr TEXT, rating TEXT, risk_level TEXT, should_trade INTEGER,
            data_quality TEXT, raw_prompt_hash TEXT, raw_response TEXT,
            status TEXT NOT NULL DEFAULT 'SENT'
        );
    """)
    con.commit()
    con.close()


class HotlistStatsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_hotlist_returns_zeros(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_hotlist_stats
        con = sqlite3.connect(self._db)
        stats = _query_hotlist_stats(con, "2000-01-01T00:00:00")
        con.close()
        self.assertEqual(stats["alerts"], 0)
        self.assertEqual(stats["open"], 0)
        self.assertEqual(stats["tp1"], 0)

    def test_counts_alerts_in_window(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_hotlist_stats
        con = sqlite3.connect(self._db)
        con.execute(
            "INSERT INTO hotlist_alerts (symbol, direction, entry, created_at) VALUES (?,?,?,?)",
            ("BTCUSDT", "LONG", "30000", "2030-01-01T12:00:00"),
        )
        con.commit()
        stats = _query_hotlist_stats(con, "2030-01-01T00:00:00")
        self.assertEqual(stats["alerts"], 1)
        con.close()

    def test_counts_alerts_outside_window_excluded(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_hotlist_stats
        con = sqlite3.connect(self._db)
        con.execute(
            "INSERT INTO hotlist_alerts (symbol, direction, entry, created_at) VALUES (?,?,?,?)",
            ("BTCUSDT", "LONG", "30000", "2000-01-01T12:00:00"),
        )
        con.commit()
        stats = _query_hotlist_stats(con, "2030-01-01T00:00:00")
        self.assertEqual(stats["alerts"], 0)
        con.close()


class SignalsByStrategyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_signal(self, run_id: str, symbol: str, strategy_id: str, generated_at: str) -> None:
        con = sqlite3.connect(self._db)
        snap_id = f"snap-{run_id}-{strategy_id}"
        con.execute(
            "INSERT OR IGNORE INTO analysis_snapshots"
            " (snapshot_id, snapshot_type, source_ref, strategy_id, created_at)"
            " VALUES (?,?,?,?,?)",
            (snap_id, "SCAN", run_id, strategy_id, generated_at),
        )
        con.execute(
            "INSERT OR IGNORE INTO signals (run_id, snapshot_id, symbol, generated_at)"
            " VALUES (?,?,?,?)",
            (run_id, snap_id, symbol, generated_at),
        )
        con.commit()
        con.close()

    def test_counts_by_strategy(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_signals_by_strategy
        self._insert_signal("r1", "BTCUSDT", "baseline_v1", "2030-01-01T12:00:00")
        self._insert_signal("r2", "ETHUSDT", "breakout_hunter_v1", "2030-01-01T13:00:00")
        con = sqlite3.connect(self._db)
        stats = _query_signals_by_strategy(con, "2030-01-01T00:00:00")
        con.close()
        self.assertEqual(stats.get("baseline_v1", 0), 1)
        self.assertEqual(stats.get("breakout_hunter_v1", 0), 1)

    def test_excludes_old_signals(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_signals_by_strategy
        self._insert_signal("r1", "BTCUSDT", "baseline_v1", "2000-01-01T12:00:00")
        con = sqlite3.connect(self._db)
        stats = _query_signals_by_strategy(con, "2030-01-01T00:00:00")
        con.close()
        self.assertEqual(stats.get("baseline_v1", 0), 0)


class GeminiStatsTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def _insert_review(self, review_id: str, decision: str, created_at: str,
                       risk_level: str | None = None) -> None:
        con = sqlite3.connect(self._db)
        con.execute(
            "INSERT INTO gemini_committee_reviews"
            " (review_id, created_at, decision, risk_level) VALUES (?,?,?,?)",
            (review_id, created_at, decision, risk_level),
        )
        con.commit()
        con.close()

    def test_empty_returns_zeros(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_gemini_stats
        con = sqlite3.connect(self._db)
        stats = _query_gemini_stats(con, "2000-01-01T00:00:00")
        con.close()
        self.assertEqual(stats["reviews"], 0)
        self.assertEqual(stats["TRADE"], 0)
        self.assertEqual(stats["NO_TRADE"], 0)

    def test_counts_decision_breakdown(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_gemini_stats
        self._insert_review("r1", "TRADE", "2030-01-01T12:00:00")
        self._insert_review("r2", "NO_TRADE", "2030-01-01T12:01:00", "HIGH")
        self._insert_review("r3", "NO_TRADE", "2030-01-01T12:02:00", "HIGH")
        con = sqlite3.connect(self._db)
        stats = _query_gemini_stats(con, "2030-01-01T00:00:00")
        con.close()
        self.assertEqual(stats["reviews"], 3)
        self.assertEqual(stats["TRADE"], 1)
        self.assertEqual(stats["NO_TRADE"], 2)

    def test_top_reasons_extracted(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_gemini_stats
        self._insert_review("r1", "NO_TRADE", "2030-01-01T12:00:00", "EXTREME")
        self._insert_review("r2", "NO_TRADE", "2030-01-01T12:01:00", "EXTREME")
        con = sqlite3.connect(self._db)
        stats = _query_gemini_stats(con, "2030-01-01T00:00:00")
        con.close()
        reasons = stats.get("top_reasons", [])
        self.assertTrue(any("EXTREME" in r for r in reasons))

    def test_excludes_old_reviews(self):
        from binance_ai_trader.runner.hourly_strategy_report import _query_gemini_stats
        self._insert_review("r1", "TRADE", "2000-01-01T12:00:00")
        con = sqlite3.connect(self._db)
        stats = _query_gemini_stats(con, "2030-01-01T00:00:00")
        con.close()
        self.assertEqual(stats["reviews"], 0)


class BuildHourlyReportTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmp.name) / "market.db")
        _make_market_db(self._db)

    def tearDown(self):
        self._tmp.cleanup()

    def test_report_contains_key_sections(self):
        from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report
        report = build_hourly_report(self._db)
        self.assertIn("📊 策略自检报告", report)
        self.assertIn("🔔 Hotlist", report)
        self.assertIn("Baseline V1", report)
        self.assertIn("Gemini Committee", report)
        self.assertIn("Leaderboard Watch", report)
        self.assertIn("AI Macro", report)
        self.assertIn("仅供研究", report)

    def test_dead_flag_when_no_signals(self):
        from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report
        report = build_hourly_report(self._db)
        self.assertIn("DEAD", report)

    def test_gemini_alert_when_reviews_but_no_trade(self):
        from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report
        con = sqlite3.connect(self._db)
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO gemini_committee_reviews (review_id, created_at, decision) VALUES (?,?,?)",
            ("rx1", now, "NO_TRADE"),
        )
        con.commit()
        con.close()
        report = build_hourly_report(self._db)
        self.assertIn("Gemini无有效交易", report)

    def test_gemini_no_alert_when_trade_exists(self):
        from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report
        con = sqlite3.connect(self._db)
        from datetime import UTC, datetime
        now = datetime.now(UTC).isoformat(timespec="seconds")
        con.execute(
            "INSERT INTO gemini_committee_reviews (review_id, created_at, decision) VALUES (?,?,?)",
            ("rx1", now, "TRADE"),
        )
        con.commit()
        con.close()
        report = build_hourly_report(self._db)
        self.assertNotIn("Gemini无有效交易", report)

    def test_db_connection_failure_returns_error_message(self):
        from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report
        report = build_hourly_report("/nonexistent/path/db.sqlite")
        self.assertIn("⚠️", report)

    def test_report_contains_all_five_strategies(self):
        from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report
        report = build_hourly_report(self._db)
        self.assertIn("Baseline V1", report)
        self.assertIn("Breakout Hunter", report)
        self.assertIn("Bear Short", report)
        self.assertIn("Capital 60-80", report)
        self.assertIn("Range Disabled", report)


if __name__ == "__main__":
    unittest.main()
