import os
import sqlite3
import tempfile
import unittest
from binance_ai_trader.performance_center.repository import PerformanceRepository


class TestSQLiteMigration(unittest.TestCase):
    def test_table_schema(self):
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            PerformanceRepository(db)
            con = sqlite3.connect(db)
            cols = {r[1] for r in con.execute("PRAGMA table_info(strategy_results)").fetchall()}
            con.close()
            expected = {
                "result_id", "strategy", "symbol", "direction",
                "entry", "stop_loss", "tp1", "tp2",
                "opened_at", "closed_at", "result",
                "pnl_pct", "rr_realized", "duration_minutes", "source_id",
            }
            self.assertEqual(expected, cols)
        finally:
            os.unlink(db)

    def test_idempotent_create(self):
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            PerformanceRepository(db)
            PerformanceRepository(db)
        finally:
            os.unlink(db)

    def test_primary_key_result_id(self):
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            repo = PerformanceRepository(db)
            con = sqlite3.connect(db)
            pk_info = con.execute("PRAGMA table_info(strategy_results)").fetchall()
            pk_cols = [r[1] for r in pk_info if r[5] == 1]
            con.close()
            self.assertIn("result_id", pk_cols)
        finally:
            os.unlink(db)

    def test_default_result_open(self):
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            con = sqlite3.connect(db)
            PerformanceRepository(db)
            con2 = sqlite3.connect(db)
            con2.execute(
                "INSERT INTO strategy_results "
                "(result_id, strategy, symbol, direction, entry, stop_loss, tp1, tp2, opened_at, source_id)"
                " VALUES ('x','hotlist','BTCUSDT','LONG','50000','48000','52000','54000','2024-01-01T00:00:00','src1')"
            )
            con2.commit()
            row = con2.execute("SELECT result FROM strategy_results WHERE result_id='x'").fetchone()
            con2.close()
            self.assertEqual(row[0], "OPEN")
        finally:
            os.unlink(db)

    def test_stored_in_market_data_db(self):
        db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        try:
            repo = PerformanceRepository(db)
            self.assertEqual(repo._db, db)
        finally:
            os.unlink(db)


if __name__ == "__main__":
    unittest.main()
