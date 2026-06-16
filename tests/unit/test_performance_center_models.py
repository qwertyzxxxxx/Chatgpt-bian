import unittest
from binance_ai_trader.performance_center.models import (
    StrategyResult, StrategyStats, Leaderboard,
    RESULT_OPEN, RESULT_TP1, RESULT_SL, RESULT_TIMEOUT,
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
    WIN_RESULTS, LOSS_RESULTS,
)


class TestStrategyResultModel(unittest.TestCase):
    def _make(self, **kwargs):
        defaults = dict(
            result_id="abc123", strategy=STRATEGY_HOTLIST,
            symbol="BTCUSDT", direction="LONG",
            entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
            opened_at="2024-01-01T00:00:00", source_id="hotlist_1",
        )
        defaults.update(kwargs)
        return StrategyResult(**defaults)

    def test_defaults(self):
        sr = self._make()
        self.assertEqual(sr.result, RESULT_OPEN)
        self.assertIsNone(sr.pnl_pct)
        self.assertIsNone(sr.rr_realized)
        self.assertIsNone(sr.duration_minutes)

    def test_to_dict_keys(self):
        sr = self._make()
        d = sr.to_dict()
        for key in ["result_id", "strategy", "symbol", "direction",
                    "entry", "stop_loss", "tp1", "tp2", "opened_at",
                    "closed_at", "result", "pnl_pct", "rr_realized",
                    "duration_minutes", "source_id"]:
            self.assertIn(key, d)

    def test_from_row_roundtrip(self):
        sr = self._make(result=RESULT_TP1, pnl_pct=2.5, rr_realized=2.0, duration_minutes=120)
        d = sr.to_dict()
        cols = list(d.keys())
        row = tuple(d[c] for c in cols)
        sr2 = StrategyResult.from_row(row, cols)
        self.assertEqual(sr2.result_id, sr.result_id)
        self.assertEqual(sr2.result, RESULT_TP1)
        self.assertAlmostEqual(sr2.pnl_pct, 2.5)
        self.assertAlmostEqual(sr2.rr_realized, 2.0)
        self.assertEqual(sr2.duration_minutes, 120)

    def test_win_results(self):
        self.assertIn(RESULT_TP1, WIN_RESULTS)
        self.assertIn("TP2", WIN_RESULTS)

    def test_loss_results(self):
        self.assertIn(RESULT_SL, LOSS_RESULTS)
        self.assertIn(RESULT_TIMEOUT, LOSS_RESULTS)

    def test_strategy_constants(self):
        self.assertEqual(STRATEGY_HOTLIST, "hotlist")
        self.assertEqual(STRATEGY_AI_MACRO, "ai_macro")
        self.assertEqual(STRATEGY_GEMINI, "gemini_committee")

    def test_strategy_stats_defaults(self):
        s = StrategyStats(strategy=STRATEGY_HOTLIST)
        self.assertEqual(s.total, 0)
        self.assertEqual(s.win_rate, 0.0)
        self.assertEqual(s.avg_rr, 0.0)
        self.assertEqual(s.max_consecutive_wins, 0)

    def test_leaderboard_empty(self):
        lb = Leaderboard()
        self.assertEqual(lb.entries, [])


if __name__ == "__main__":
    unittest.main()
