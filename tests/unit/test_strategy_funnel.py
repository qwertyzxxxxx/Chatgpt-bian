"""Unit tests for diagnostics/strategy_diagnostic.py — funnel diagnostics."""
from __future__ import annotations

import sqlite3
import tempfile
import os
import unittest

from binance_ai_trader.diagnostics.strategy_diagnostic import (
    FunnelLayer,
    FunnelStats,
    format_funnel_text,
    run_funnel_diagnostics,
)


class TestFunnelLayerDataclass(unittest.TestCase):
    def test_fields(self):
        layer = FunnelLayer(name="Universe", count=100, note="test note")
        self.assertEqual(layer.name, "Universe")
        self.assertEqual(layer.count, 100)
        self.assertEqual(layer.note, "test note")

    def test_default_note_empty(self):
        layer = FunnelLayer(name="Signals", count=5)
        self.assertEqual(layer.note, "")


class TestFunnelStatsDataclass(unittest.TestCase):
    def test_defaults(self):
        fs = FunnelStats(strategy_id="baseline_v1", strategy_name="Baseline V1")
        self.assertEqual(fs.new_coin_skipped, 0)
        self.assertEqual(fs.space_score_missing, 0)
        self.assertEqual(fs.space_score_ok, 0)
        self.assertIsInstance(fs.layers, list)

    def test_layers_mutable(self):
        fs = FunnelStats(strategy_id="s", strategy_name="S")
        fs.layers.append(FunnelLayer("Universe", 10))
        self.assertEqual(len(fs.layers), 1)


class TestRunFunnelDiagnosticsEmptyDB(unittest.TestCase):
    def test_empty_db_returns_list(self):
        """An empty DB should return a non-empty list without crashing."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = run_funnel_diagnostics(db_path, since_hours=24)
            self.assertIsInstance(result, list)
            # should have entries for each strategy even if counts are 0
            self.assertGreater(len(result), 0)
        finally:
            os.unlink(db_path)

    def test_empty_db_funnel_stats_have_layers(self):
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name
        try:
            result = run_funnel_diagnostics(db_path)
            for fs in result:
                self.assertIsInstance(fs, FunnelStats)
                self.assertGreater(len(fs.layers), 0)
        finally:
            os.unlink(db_path)

    def test_bad_db_path_returns_empty(self):
        result = run_funnel_diagnostics("/nonexistent/path/to.db")
        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 0)


class TestRunFunnelDiagnosticsWithData(unittest.TestCase):
    def _make_db(self) -> str:
        from datetime import datetime, UTC
        f = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        db_path = f.name
        f.close()
        con = sqlite3.connect(db_path)
        con.execute("""
            CREATE TABLE IF NOT EXISTS collection_runs (
                id TEXT PRIMARY KEY, started_at TEXT, status TEXT DEFAULT 'SUCCEEDED'
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS universe_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT, symbol TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT, symbol TEXT
            )
        """)
        con.execute("""
            CREATE TABLE IF NOT EXISTS klines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT, interval TEXT
            )
        """)
        # Use current timestamp so _latest_run_id finds the run within since_hours window
        now_iso = datetime.now(UTC).isoformat(timespec="seconds")
        con.execute("INSERT INTO collection_runs VALUES ('run1',?,'SUCCEEDED')", (now_iso,))
        for sym in ("BTCUSDT", "ETHUSDT", "NEWCOIN"):
            con.execute("INSERT INTO universe_snapshots (run_id, symbol) VALUES (?,?)", ("run1", sym))
            con.execute("INSERT INTO scores (run_id, symbol) VALUES (?,?)", ("run1", sym))
        # NEWCOIN has < 720 4h bars
        for i in range(100):
            con.execute("INSERT INTO klines (symbol, interval) VALUES (?,?)", ("NEWCOIN", "4h"))
        for i in range(800):
            con.execute("INSERT INTO klines (symbol, interval) VALUES (?,?)", ("BTCUSDT", "4h"))
        con.commit()
        con.close()
        return db_path

    def test_new_coin_count_detected(self):
        db_path = self._make_db()
        try:
            result = run_funnel_diagnostics(db_path, since_hours=24)
            baseline = next((r for r in result if r.strategy_id == "baseline_v1"), None)
            self.assertIsNotNone(baseline)
            universe_layer = baseline.layers[0]
            self.assertEqual(universe_layer.name, "Universe")
            # 3 symbols inserted
            self.assertEqual(universe_layer.count, 3)
        finally:
            os.unlink(db_path)


class TestFormatFunnelText(unittest.TestCase):
    def test_format_empty_list(self):
        text = format_funnel_text([], since_hours=24)
        self.assertIsInstance(text, str)
        self.assertIn("24", text)

    def test_format_single_funnel(self):
        fs = FunnelStats(
            strategy_id="baseline_v1",
            strategy_name="Baseline V1",
            new_coin_skipped=3,
            space_score_missing=2,
            space_score_ok=5,
        )
        fs.layers = [
            FunnelLayer("Universe", 10),
            FunnelLayer("Signals", 2, note="some note"),
        ]
        text = format_funnel_text([fs])
        self.assertIn("baseline_v1", text)
        self.assertIn("Universe: 10", text)
        self.assertIn("Signals: 2", text)
        self.assertIn("some note", text)
        self.assertIn("仅供研究", text)

    def test_new_coin_skipped_shown(self):
        fs = FunnelStats(
            strategy_id="s", strategy_name="S",
            new_coin_skipped=7, space_score_missing=2, space_score_ok=3
        )
        text = format_funnel_text([fs])
        self.assertIn("new_coin_skipped=7", text)
        self.assertIn("MISSING=2", text)

    def test_no_new_coin_skipped_not_shown(self):
        fs = FunnelStats(strategy_id="s", strategy_name="S")
        text = format_funnel_text([fs])
        self.assertNotIn("new_coin_skipped=0", text)
