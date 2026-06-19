import os
import tempfile
import unittest
from binance_ai_trader.performance_center.repository import PerformanceRepository
from binance_ai_trader.performance_center.models import (
    StrategyResult, RESULT_OPEN, RESULT_TP1, STRATEGY_HOTLIST, STRATEGY_AI_MACRO,
)


def _sr(result_id, strategy=STRATEGY_HOTLIST, result=RESULT_OPEN, source_id=None):
    return StrategyResult(
        result_id=result_id,
        strategy=strategy, symbol="BTCUSDT", direction="LONG",
        entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
        opened_at="2024-01-01T00:00:00",
        source_id=source_id or f"src_{result_id}",
        result=result,
    )


class TestPerformanceRepository(unittest.TestCase):
    def setUp(self):
        self.db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        self.repo = PerformanceRepository(self.db)

    def tearDown(self):
        os.unlink(self.db)

    def test_table_created(self):
        import sqlite3
        con = sqlite3.connect(self.db)
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        self.assertIn("strategy_results", tables)
        con.close()

    def test_upsert_and_get_all(self):
        sr = _sr("r1")
        self.repo.upsert(sr)
        results = self.repo.get_all()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].result_id, "r1")

    def test_get_open_filter(self):
        self.repo.upsert(_sr("r1", result=RESULT_OPEN))
        self.repo.upsert(_sr("r2", result=RESULT_TP1))
        open_list = self.repo.get_open()
        self.assertEqual(len(open_list), 1)
        self.assertEqual(open_list[0].result_id, "r1")

    def test_get_open_by_strategy(self):
        self.repo.upsert(_sr("r1", strategy=STRATEGY_HOTLIST))
        self.repo.upsert(_sr("r2", strategy=STRATEGY_AI_MACRO))
        open_hl = self.repo.get_open(strategy=STRATEGY_HOTLIST)
        self.assertEqual(len(open_hl), 1)
        self.assertEqual(open_hl[0].strategy, STRATEGY_HOTLIST)

    def test_source_id_exists(self):
        sr = _sr("r1", source_id="hotlist_99")
        self.repo.upsert(sr)
        self.assertTrue(self.repo.source_id_exists("hotlist_99"))
        self.assertFalse(self.repo.source_id_exists("hotlist_100"))

    def test_upsert_updates_on_conflict(self):
        sr = _sr("r1")
        self.repo.upsert(sr)
        sr.result = RESULT_TP1
        sr.pnl_pct = 4.0
        sr.rr_realized = 2.0
        sr.duration_minutes = 60
        sr.closed_at = "2024-01-02T00:00:00"
        self.repo.upsert(sr)
        results = self.repo.get_all()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].result, RESULT_TP1)
        self.assertAlmostEqual(results[0].pnl_pct, 4.0)

    def test_update_settled(self):
        sr = _sr("r1")
        self.repo.upsert(sr)
        sr.result = RESULT_TP1
        sr.pnl_pct = 3.5
        sr.rr_realized = 1.75
        sr.duration_minutes = 45
        sr.closed_at = "2024-01-01T12:00:00"
        self.repo.update_settled(sr)
        results = self.repo.get_all()
        self.assertEqual(results[0].result, RESULT_TP1)
        self.assertAlmostEqual(results[0].pnl_pct, 3.5)

    def test_bulk_upsert(self):
        srs = [_sr(f"r{i}") for i in range(5)]
        count = self.repo.bulk_upsert(srs)
        self.assertEqual(count, 5)
        self.assertEqual(len(self.repo.get_all()), 5)

    def test_get_all_by_strategy(self):
        self.repo.upsert(_sr("r1", strategy=STRATEGY_HOTLIST))
        self.repo.upsert(_sr("r2", strategy=STRATEGY_AI_MACRO))
        hl_only = self.repo.get_all(strategy=STRATEGY_HOTLIST)
        self.assertEqual(len(hl_only), 1)
        self.assertEqual(hl_only[0].strategy, STRATEGY_HOTLIST)


if __name__ == "__main__":
    unittest.main()
