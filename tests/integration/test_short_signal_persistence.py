from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class ShortSignalPersistenceIntegrationTest(unittest.TestCase):
    def test_migrates_long_only_signals_constraint_to_both_directions(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "legacy.db"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    """
                    CREATE TABLE collection_runs (
                        id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
                        status TEXT NOT NULL, universe_size INTEGER NOT NULL DEFAULT 0,
                        kline_count INTEGER NOT NULL DEFAULT 0, error_summary TEXT
                    )
                    """
                )
                connection.execute(
                    """
                    CREATE TABLE signals (
                        run_id TEXT NOT NULL, rank INTEGER NOT NULL, symbol TEXT NOT NULL,
                        direction TEXT NOT NULL CHECK (direction = 'LONG'),
                        combined_regime TEXT NOT NULL DEFAULT 'OBSERVE',
                        sector TEXT NOT NULL DEFAULT 'OTHER', sector_rank INTEGER,
                        score REAL NOT NULL, entry TEXT NOT NULL, latest_close TEXT NOT NULL,
                        stop_loss TEXT NOT NULL, stop_loss_pct TEXT NOT NULL,
                        tp1 TEXT NOT NULL, tp2 TEXT NOT NULL, rr_tp1 TEXT NOT NULL,
                        rr_tp2 TEXT NOT NULL, logic_summary TEXT NOT NULL, generated_at TEXT NOT NULL,
                        PRIMARY KEY (run_id, symbol), UNIQUE (run_id, rank)
                    )
                    """
                )
                connection.commit()

            repository = MarketDataRepository(database)
            repository.close()
            with closing(sqlite3.connect(database)) as connection:
                schema = connection.execute(
                    "SELECT sql FROM sqlite_master WHERE name='signals'"
                ).fetchone()[0]
                signal_columns = {
                    row[1] for row in connection.execute("PRAGMA table_info(signals)")
                }
                snapshot_table = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='analysis_snapshots'"
                ).fetchone()

            self.assertIn("'SHORT'", schema)
            self.assertNotIn("direction = 'LONG'", schema)
            self.assertIn("snapshot_id", signal_columns)
            self.assertEqual(("analysis_snapshots",), snapshot_table)


if __name__ == "__main__":
    unittest.main()
