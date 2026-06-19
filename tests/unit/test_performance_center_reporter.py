import os
import tempfile
import unittest
from binance_ai_trader.performance_center.models import (
    StrategyStats, Leaderboard,
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
)
from binance_ai_trader.performance_center.reporter import (
    generate_summary_md, generate_leaderboard_md,
)


def _stats(strategy, win_rate=60.0, total=10, avg_rr=1.5):
    s = StrategyStats(strategy=strategy)
    s.total = total
    s.win_rate = win_rate
    s.avg_rr = avg_rr
    s.tp1 = 4
    s.tp2 = 2
    s.sl = 3
    s.timeout = 1
    s.open_count = 0
    return s


class TestReporter(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.summary_path = os.path.join(self.tmpdir, "performance_summary.md")
        self.leaderboard_path = os.path.join(self.tmpdir, "performance_leaderboard.md")
        self.stats = [
            _stats(STRATEGY_HOTLIST, win_rate=55.0),
            _stats(STRATEGY_AI_MACRO, win_rate=43.0),
            _stats(STRATEGY_GEMINI, win_rate=68.0),
        ]
        self.lb = Leaderboard(entries=[_stats(STRATEGY_GEMINI, win_rate=68.0)])

    def test_summary_md_created(self):
        generate_summary_md(self.stats, self.summary_path)
        self.assertTrue(os.path.exists(self.summary_path))

    def test_summary_md_contains_header(self):
        content = generate_summary_md(self.stats, self.summary_path)
        self.assertIn("# Strategy Performance Summary", content)

    def test_summary_md_contains_all_strategies(self):
        content = generate_summary_md(self.stats, self.summary_path)
        self.assertIn("Hotlist", content)
        self.assertIn("AI Macro", content)
        self.assertIn("Gemini Committee", content)

    def test_summary_md_contains_win_rate(self):
        content = generate_summary_md(self.stats, self.summary_path)
        self.assertIn("Win Rate:", content)

    def test_summary_md_contains_timestamp(self):
        content = generate_summary_md(self.stats, self.summary_path)
        self.assertIn("UTC", content)

    def test_leaderboard_md_created(self):
        generate_leaderboard_md(self.lb, self.leaderboard_path)
        self.assertTrue(os.path.exists(self.leaderboard_path))

    def test_leaderboard_md_header(self):
        content = generate_leaderboard_md(self.lb, self.leaderboard_path)
        self.assertIn("# Strategy Leaderboard", content)

    def test_leaderboard_md_table(self):
        content = generate_leaderboard_md(self.lb, self.leaderboard_path)
        self.assertIn("| Rank |", content)
        self.assertIn("Gemini Committee", content)

    def test_leaderboard_md_empty(self):
        content = generate_leaderboard_md(Leaderboard(), self.leaderboard_path)
        self.assertIn("No data", content)

    def test_creates_parent_dir(self):
        nested = os.path.join(self.tmpdir, "nested", "summary.md")
        generate_summary_md(self.stats, nested)
        self.assertTrue(os.path.exists(nested))


if __name__ == "__main__":
    unittest.main()
