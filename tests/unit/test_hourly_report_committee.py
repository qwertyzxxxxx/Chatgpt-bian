"""Unit tests for runner/hourly_strategy_report.py — gemini_committee_enabled flag."""
from __future__ import annotations

import sqlite3
import tempfile
import os
import unittest

from binance_ai_trader.runner.hourly_strategy_report import build_hourly_report


def _make_minimal_db() -> str:
    """Create a minimal SQLite DB with just enough tables to not crash build_hourly_report."""
    f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    db_path = f.name
    f.close()
    con = sqlite3.connect(db_path)
    # Minimal tables so the queries don't fail
    con.execute("""
        CREATE TABLE IF NOT EXISTS collection_runs (
            id TEXT PRIMARY KEY, started_at TEXT, status TEXT DEFAULT 'SUCCEEDED'
        )
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS hotlist_alerts (id INTEGER PRIMARY KEY, created_at TEXT)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS hotlist_outcomes (
            id INTEGER PRIMARY KEY, status TEXT, evaluated_at TEXT
        )
    """)
    con.commit()
    con.close()
    return db_path


class TestBuildHourlyReportCommitteeEnabled(unittest.TestCase):
    def setUp(self):
        self.db = _make_minimal_db()

    def tearDown(self):
        os.unlink(self.db)

    def test_committee_enabled_shows_gemini_section(self):
        text = build_hourly_report(self.db, gemini_committee_enabled=True)
        self.assertIn("Gemini Committee", text)
        self.assertNotIn("DISABLED", text)

    def test_committee_disabled_shows_disabled(self):
        text = build_hourly_report(self.db, gemini_committee_enabled=False)
        self.assertIn("DISABLED", text)

    def test_committee_disabled_no_trade_alert_suppressed(self):
        """When disabled, the 0-TRADE alert must not appear even with 0 trades."""
        text = build_hourly_report(self.db, gemini_committee_enabled=False)
        # The "Gemini无有效交易" alert should not appear when committee is disabled
        self.assertNotIn("Gemini无有效交易", text)

    def test_default_committee_enabled_true(self):
        """Default parameter = True so existing callers are unaffected."""
        text = build_hourly_report(self.db)
        self.assertNotIn("DISABLED", text)

    def test_report_always_has_disclaimer(self):
        for enabled in (True, False):
            text = build_hourly_report(self.db, gemini_committee_enabled=enabled)
            self.assertIn("仅供研究", text)

    def test_report_always_has_leaderboard_section(self):
        for enabled in (True, False):
            text = build_hourly_report(self.db, gemini_committee_enabled=enabled)
            self.assertIn("Leaderboard Watch", text)

    def test_report_returns_string(self):
        text = build_hourly_report(self.db)
        self.assertIsInstance(text, str)
        self.assertGreater(len(text), 10)

    def test_missing_db_returns_error_string(self):
        text = build_hourly_report("/nonexistent/path/to.db")
        self.assertIn("⚠️", text)


class TestBuildHourlyReportStructure(unittest.TestCase):
    def setUp(self):
        self.db = _make_minimal_db()

    def tearDown(self):
        os.unlink(self.db)

    def test_all_strategy_labels_present(self):
        text = build_hourly_report(self.db)
        for label in ("Hotlist", "AI Macro", "Leaderboard Watch"):
            self.assertIn(label, text)

    def test_committee_disabled_has_strategy_sections(self):
        """Disabling committee should not remove other strategy sections."""
        text = build_hourly_report(self.db, gemini_committee_enabled=False)
        self.assertIn("Hotlist", text)
        self.assertIn("Leaderboard Watch", text)
