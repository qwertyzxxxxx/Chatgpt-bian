import sqlite3
import tempfile
import os
import unittest

from binance_ai_trader.performance_center.diagnostic import (
    run_strategy_diagnostic,
    _KNOWN_SIGNAL_STRATEGIES,
    _BOTTLENECK_LABELS,
)


def _make_empty_db(path: str) -> None:
    con = sqlite3.connect(path)
    con.executescript("""
        CREATE TABLE IF NOT EXISTS strategy_results (
            result_id TEXT PRIMARY KEY, strategy TEXT NOT NULL,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, stop_loss TEXT NOT NULL,
            tp1 TEXT NOT NULL, tp2 TEXT NOT NULL,
            opened_at TEXT NOT NULL, closed_at TEXT,
            result TEXT NOT NULL DEFAULT 'OPEN',
            pnl_pct REAL, rr_realized REAL, duration_minutes INTEGER,
            source_id TEXT NOT NULL
        );
    """)
    con.commit()
    con.close()


def _make_full_pipeline_db(path: str, strategy_id: str = "baseline_v1") -> None:
    con = sqlite3.connect(path)
    con.executescript(f"""
        CREATE TABLE IF NOT EXISTS analysis_snapshots (
            snapshot_id TEXT PRIMARY KEY, snapshot_type TEXT,
            collection_run_id TEXT, source_ref TEXT,
            data_cutoff_ms INTEGER, strategy_id TEXT, created_at TEXT,
            finalized_at TEXT
        );
        CREATE TABLE IF NOT EXISTS signals (
            run_id TEXT, symbol TEXT, snapshot_id TEXT,
            generated_at TEXT, direction TEXT, entry TEXT,
            stop_loss TEXT, tp1 TEXT, tp2 TEXT
        );
        CREATE TABLE IF NOT EXISTS signal_evaluations (
            signal_run_id TEXT, symbol TEXT, direction TEXT,
            result TEXT, entry TEXT, stop_loss TEXT, tp1 TEXT, tp2 TEXT
        );
        CREATE TABLE IF NOT EXISTS paper_trades (
            signal_run_id TEXT, symbol TEXT,
            realized_r REAL, risk_pct REAL
        );
        CREATE TABLE IF NOT EXISTS strategy_results (
            result_id TEXT PRIMARY KEY, strategy TEXT NOT NULL,
            symbol TEXT NOT NULL, direction TEXT NOT NULL,
            entry TEXT NOT NULL, stop_loss TEXT NOT NULL,
            tp1 TEXT NOT NULL, tp2 TEXT NOT NULL,
            opened_at TEXT NOT NULL, closed_at TEXT,
            result TEXT NOT NULL DEFAULT 'OPEN',
            pnl_pct REAL, rr_realized REAL, duration_minutes INTEGER,
            source_id TEXT NOT NULL
        );
        INSERT INTO analysis_snapshots VALUES
            ('snap-1', 'SCAN', 'run-1', 'ref', 0, '{strategy_id}', '2026-06-01T00:00:00', NULL);
        INSERT INTO signals VALUES
            ('run-1', 'BTCUSDT', 'snap-1', '2026-06-01T01:00:00', 'LONG', '50000', '48000', '52000', '54000');
        INSERT INTO signal_evaluations VALUES
            ('run-1', 'BTCUSDT', 'LONG', 'TP1_HIT', '50000', '48000', '52000', '54000');
        INSERT INTO paper_trades VALUES
            ('run-1', 'BTCUSDT', 2.0, 1.0);
        INSERT INTO strategy_results VALUES
            ('sr-1', '{strategy_id}', 'BTCUSDT', 'LONG', '50000', '48000', '52000', '54000',
             '2026-06-01T01:00:00', NULL, 'TP1', NULL, 2.0, NULL, 'paper_run-1_BTCUSDT');
    """)
    con.commit()
    con.close()


class TestRunStrategyDiagnosticEmptyDB(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        _make_empty_db(self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_all_known_strategies_diagnosed(self):
        results = run_strategy_diagnostic(self.db, days=30)
        strategy_ids = {r["strategy_id"] for r in results}
        for sid in _KNOWN_SIGNAL_STRATEGIES:
            self.assertIn(sid, strategy_ids)

    def test_no_snapshots_bottleneck_a(self):
        results = run_strategy_diagnostic(self.db, days=30)
        for r in results:
            self.assertEqual(r["bottleneck"], "A", f"{r['strategy_id']} expected A, got {r['bottleneck']}")

    def test_registered_false_when_no_snapshots(self):
        results = run_strategy_diagnostic(self.db, days=30)
        for r in results:
            self.assertFalse(r["registered"])

    def test_all_counts_zero(self):
        results = run_strategy_diagnostic(self.db, days=30)
        for r in results:
            for key in ("snapshots_30d", "signals_30d", "evaluations_30d", "paper_trades_30d", "strategy_results_30d"):
                self.assertEqual(r[key], 0, f"{r['strategy_id']}.{key} != 0")

    def test_last_seen_none(self):
        results = run_strategy_diagnostic(self.db, days=30)
        for r in results:
            self.assertIsNone(r["last_seen"])

    def test_bottleneck_description_present(self):
        results = run_strategy_diagnostic(self.db, days=30)
        for r in results:
            self.assertIn("bottleneck_description", r)
            self.assertIn(r["bottleneck"], _BOTTLENECK_LABELS)

    def test_result_fields_present(self):
        results = run_strategy_diagnostic(self.db, days=30)
        expected_keys = {
            "strategy_id", "registered", "snapshots_30d", "signals_30d",
            "evaluations_30d", "paper_trades_30d", "strategy_results_30d",
            "last_seen", "bottleneck", "bottleneck_description",
        }
        for r in results:
            self.assertEqual(set(r.keys()), expected_keys)


class TestRunStrategyDiagnosticFullPipeline(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        _make_full_pipeline_db(self.db, "baseline_v1")

    def tearDown(self):
        os.unlink(self.db)

    def test_baseline_v1_ok(self):
        results = run_strategy_diagnostic(self.db, days=365)
        baseline = next(r for r in results if r["strategy_id"] == "baseline_v1")
        self.assertEqual(baseline["bottleneck"], "OK")
        self.assertTrue(baseline["registered"])
        self.assertGreater(baseline["snapshots_30d"], 0)
        self.assertGreater(baseline["strategy_results_30d"], 0)

    def test_baseline_v1_ok_wide_window(self):
        results = run_strategy_diagnostic(self.db, days=365)
        baseline = next(r for r in results if r["strategy_id"] == "baseline_v1")
        self.assertEqual(baseline["bottleneck"], "OK")
        self.assertGreaterEqual(baseline["strategy_results_30d"], 1)

    def test_other_strategies_still_bottleneck_a(self):
        results = run_strategy_diagnostic(self.db, days=365)
        for r in results:
            if r["strategy_id"] != "baseline_v1":
                self.assertEqual(r["bottleneck"], "A")

    def test_custom_strategy_ids(self):
        results = run_strategy_diagnostic(self.db, strategy_ids=("baseline_v1",), days=365)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["strategy_id"], "baseline_v1")


class TestDiagnosticMissingTables(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        con = sqlite3.connect(self.db)
        con.close()

    def tearDown(self):
        os.unlink(self.db)

    def test_no_tables_returns_bottleneck_a(self):
        results = run_strategy_diagnostic(self.db, strategy_ids=("baseline_v1",), days=30)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["bottleneck"], "A")
        self.assertFalse(results[0]["registered"])


if __name__ == "__main__":
    unittest.main()
