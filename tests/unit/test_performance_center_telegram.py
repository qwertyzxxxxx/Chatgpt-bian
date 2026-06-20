import unittest
from unittest.mock import patch, MagicMock
from binance_ai_trader.performance_center.models import (
    StrategyStats, Leaderboard,
    STRATEGY_HOTLIST, STRATEGY_AI_MACRO, STRATEGY_GEMINI,
)
from binance_ai_trader.performance_center.telegram_formatter import (
    format_summary, format_leaderboard, send_summary,
)


def _stats(strategy, total=10, tp1=4, tp2=2, sl=3, timeout=1, open_count=0, win_rate=60.0, avg_rr=1.5):
    s = StrategyStats(strategy=strategy)
    s.total = total
    s.tp1 = tp1
    s.tp2 = tp2
    s.sl = sl
    s.timeout = timeout
    s.open_count = open_count
    s.win_rate = win_rate
    s.avg_rr = avg_rr
    return s


class TestTelegramFormatter(unittest.TestCase):
    def setUp(self):
        self.stats = [
            _stats(STRATEGY_HOTLIST, win_rate=55.0),
            _stats(STRATEGY_AI_MACRO, win_rate=43.0),
            _stats(STRATEGY_GEMINI, win_rate=68.0),
        ]
        self.lb = Leaderboard(entries=[_stats(STRATEGY_GEMINI, win_rate=68.0)])

    def test_summary_contains_header(self):
        msg = format_summary(self.stats, self.lb)
        self.assertIn("📊 策略绩效", msg)

    def test_summary_contains_all_strategies(self):
        msg = format_summary(self.stats, self.lb)
        self.assertIn("热门榜单", msg)
        self.assertIn("AI宏观", msg)
        self.assertIn("Gemini AI委员会", msg)

    def test_summary_contains_top_winner(self):
        msg = format_summary(self.stats, self.lb)
        self.assertIn("🏆", msg)
        self.assertIn("Gemini AI委员会", msg)
        self.assertIn("68.0%", msg)

    def test_summary_contains_win_rate(self):
        msg = format_summary(self.stats, self.lb)
        self.assertIn("胜率:", msg)

    def test_summary_research_only_footer(self):
        msg = format_summary(self.stats, self.lb)
        self.assertIn("仅供研究", msg)

    def test_leaderboard_contains_header(self):
        msg = format_leaderboard(self.lb)
        self.assertIn("🏆 策略排行榜", msg)

    def test_leaderboard_empty(self):
        msg = format_leaderboard(Leaderboard())
        self.assertIn("暂无数据", msg)

    def test_leaderboard_rank_numbering(self):
        lb = Leaderboard(entries=[
            _stats(STRATEGY_GEMINI, win_rate=68.0),
            _stats(STRATEGY_HOTLIST, win_rate=55.0),
        ])
        msg = format_leaderboard(lb)
        self.assertIn("1.", msg)
        self.assertIn("2.", msg)

    def test_leaderboard_research_only_footer(self):
        msg = format_leaderboard(self.lb)
        self.assertIn("仅供研究", msg)

    def test_send_summary_success(self):
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = cm
            ok = send_summary(self.stats, self.lb, "token123", "chat456")
            self.assertTrue(ok)
            self.assertTrue(mock_open.called)

    def test_send_summary_failure_returns_false(self):
        with patch("urllib.request.urlopen", side_effect=Exception("network error")):
            ok = send_summary(self.stats, self.lb, "token123", "chat456")
            self.assertFalse(ok)

    def test_long_message_chunked(self):
        long_stats = [_stats(STRATEGY_HOTLIST)] * 50
        with patch("urllib.request.urlopen") as mock_open:
            cm = MagicMock()
            cm.__enter__ = MagicMock(return_value=cm)
            cm.__exit__ = MagicMock(return_value=False)
            mock_open.return_value = cm
            ok = send_summary(long_stats, self.lb, "token", "chat")
            self.assertTrue(ok)


if __name__ == "__main__":
    unittest.main()
