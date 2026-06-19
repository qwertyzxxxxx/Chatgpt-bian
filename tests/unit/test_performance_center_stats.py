import unittest
from binance_ai_trader.performance_center.models import (
    StrategyResult, STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
    RESULT_TP1, RESULT_TP2, RESULT_SL, RESULT_TIMEOUT, RESULT_OPEN,
)
from binance_ai_trader.performance_center.stats import (
    compute_stats, compute_all_stats, build_leaderboard,
)


def _sr(strategy, result, rr=None, pnl=None):
    import uuid
    return StrategyResult(
        result_id=str(uuid.uuid4()),
        strategy=strategy, symbol="BTCUSDT", direction="LONG",
        entry="50000", stop_loss="48000", tp1="52000", tp2="54000",
        opened_at="2024-01-01T00:00:00", source_id=str(uuid.uuid4()),
        result=result, rr_realized=rr, pnl_pct=pnl,
    )


class TestComputeStats(unittest.TestCase):
    def test_empty(self):
        s = compute_stats([], STRATEGY_HOTLIST)
        self.assertEqual(s.total, 0)
        self.assertEqual(s.win_rate, 0.0)

    def test_basic_counts(self):
        results = [
            _sr(STRATEGY_HOTLIST, RESULT_TP1, rr=2.0, pnl=4.0),
            _sr(STRATEGY_HOTLIST, RESULT_TP1, rr=2.0, pnl=4.0),
            _sr(STRATEGY_HOTLIST, RESULT_SL, rr=-1.0, pnl=-2.0),
            _sr(STRATEGY_HOTLIST, RESULT_OPEN),
        ]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.total, 4)
        self.assertEqual(s.tp1, 2)
        self.assertEqual(s.sl, 1)
        self.assertEqual(s.open_count, 1)
        self.assertAlmostEqual(s.win_rate, 66.7, places=0)

    def test_win_rate_all_wins(self):
        results = [_sr(STRATEGY_HOTLIST, RESULT_TP1, rr=2.0, pnl=4.0) for _ in range(4)]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.win_rate, 100.0)

    def test_win_rate_all_losses(self):
        results = [_sr(STRATEGY_HOTLIST, RESULT_SL, rr=-1.0, pnl=-2.0) for _ in range(3)]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.win_rate, 0.0)

    def test_avg_rr_computed(self):
        results = [
            _sr(STRATEGY_HOTLIST, RESULT_TP1, rr=3.0, pnl=6.0),
            _sr(STRATEGY_HOTLIST, RESULT_SL, rr=-1.0, pnl=-2.0),
        ]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertAlmostEqual(s.avg_rr, 1.0)

    def test_max_consecutive_wins(self):
        results = [
            _sr(STRATEGY_HOTLIST, RESULT_TP1),
            _sr(STRATEGY_HOTLIST, RESULT_TP1),
            _sr(STRATEGY_HOTLIST, RESULT_TP1),
            _sr(STRATEGY_HOTLIST, RESULT_SL),
            _sr(STRATEGY_HOTLIST, RESULT_TP1),
        ]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.max_consecutive_wins, 3)
        self.assertEqual(s.max_consecutive_losses, 1)

    def test_max_consecutive_losses(self):
        results = [
            _sr(STRATEGY_HOTLIST, RESULT_SL),
            _sr(STRATEGY_HOTLIST, RESULT_SL),
            _sr(STRATEGY_HOTLIST, RESULT_SL),
            _sr(STRATEGY_HOTLIST, RESULT_TP1),
        ]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.max_consecutive_losses, 3)

    def test_only_own_strategy_counted(self):
        results = [
            _sr(STRATEGY_HOTLIST, RESULT_TP1),
            _sr(STRATEGY_AI_MACRO, RESULT_TP1),
            _sr(STRATEGY_AI_MACRO, RESULT_TP1),
        ]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.total, 1)

    def test_timeout_counted_as_loss(self):
        results = [_sr(STRATEGY_HOTLIST, RESULT_TIMEOUT)]
        s = compute_stats(results, STRATEGY_HOTLIST)
        self.assertEqual(s.timeout, 1)
        self.assertEqual(s.win_rate, 0.0)


class TestLeaderboard(unittest.TestCase):
    def test_leaderboard_sorted_by_win_rate(self):
        results = (
            [_sr(STRATEGY_HOTLIST, RESULT_TP1) for _ in range(6)] +
            [_sr(STRATEGY_HOTLIST, RESULT_SL) for _ in range(4)] +
            [_sr(STRATEGY_AI_MACRO, RESULT_TP1) for _ in range(9)] +
            [_sr(STRATEGY_AI_MACRO, RESULT_SL) for _ in range(1)]
        )
        lb = build_leaderboard(results)
        self.assertEqual(lb.entries[0].strategy, STRATEGY_AI_MACRO)

    def test_empty_leaderboard(self):
        lb = build_leaderboard([])
        self.assertEqual(lb.entries, [])

    def test_all_three_strategies_present(self):
        results = (
            [_sr(STRATEGY_HOTLIST, RESULT_TP1)] +
            [_sr(STRATEGY_AI_MACRO, RESULT_SL)] +
            [_sr(STRATEGY_GEMINI, RESULT_TP2)]
        )
        lb = build_leaderboard(results)
        strategies = {e.strategy for e in lb.entries}
        self.assertIn(STRATEGY_HOTLIST, strategies)
        self.assertIn(STRATEGY_AI_MACRO, strategies)
        self.assertIn(STRATEGY_GEMINI, strategies)


if __name__ == "__main__":
    unittest.main()
