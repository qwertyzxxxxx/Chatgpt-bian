from contextlib import closing, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from binance_ai_trader.domain.models import BacktestMetrics
from binance_ai_trader.strategy_lab.models import StrategyComparison
from binance_ai_trader.entrypoints.cli import main


class ResearchPaperReportIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "research.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_auto_research_backtests_twenty_and_saves_only_top_ten_candidates(self) -> None:
        calls = []

        class FakeBacktest:
            def __init__(self, _repository, _sector_map, _policy, strategy_config) -> None:
                self.config = strategy_config

            def run(self, _start, _end, evaluation_times):
                index = int(self.config.strategy_id.rsplit("_", 1)[1])
                calls.append((self.config.strategy_id, tuple(evaluation_times)))
                metrics = BacktestMetrics(
                    total_signals=10, tp1_hit_rate=50, tp2_win_rate=20, loss_rate=30,
                    expired_rate=0, profit_factor=float(index), expectancy_r=index / 100,
                    max_drawdown_r=float(21 - index), avg_rr_tp2=2,
                )
                return SimpleNamespace(
                    run_id=self.config.strategy_id,
                    started_at="start",
                    completed_at="complete",
                    evaluation_points=len(evaluation_times),
                    metrics=metrics,
                )

        output = StringIO()
        with (
            patch("binance_ai_trader.strategy_lab.service.BacktestEngine", FakeBacktest),
            patch(
                "binance_ai_trader.strategy_lab.service._comparison_from_results",
                side_effect=lambda strategy_id, _config, *_args: StrategyComparison(
                    strategy_id,
                    BacktestMetrics(
                        total_signals=10,
                        tp1_hit_rate=50,
                        tp2_win_rate=20,
                        loss_rate=30,
                        expired_rate=0,
                        profit_factor=float(strategy_id.rsplit("_", 1)[1]),
                        expectancy_r=int(strategy_id.rsplit("_", 1)[1]) / 100,
                        max_drawdown_r=float(21 - int(strategy_id.rsplit("_", 1)[1])),
                        avg_rr_tp2=2,
                    ),
                    {},
                    {},
                ),
            ),
            patch(
                "binance_ai_trader.infrastructure.sqlite_repository.MarketDataRepository.load_backtest_evaluation_times",
                return_value=(100, 200),
            ),
            redirect_stdout(output),
        ):
            exit_code = main(["auto-research", "--database", str(self.database)])

        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, exit_code)
        self.assertEqual(20, len(calls))
        self.assertTrue(all(points == (100, 200) for _, points in calls))
        self.assertEqual(10, len(rows))
        self.assertTrue(all(row["status"] == "candidate" for row in rows))
        with closing(sqlite3.connect(self.database)) as connection:
            versions = connection.execute(
                "SELECT status, COUNT(*) FROM strategy_versions GROUP BY status"
            ).fetchall()
        self.assertEqual({"baseline": 1, "candidate": 13}, dict(versions))

        report_output = StringIO()
        with redirect_stdout(report_output):
            main(["daily-report", "--database", str(self.database), "--date", "2026-06-06"])
        report = json.loads(report_output.getvalue())
        self.assertEqual(5, len(report["top_candidates"]))
        self.assertGreaterEqual(
            report["top_candidates"][0]["metrics"]["expectancy_r"],
            report["top_candidates"][-1]["metrics"]["expectancy_r"],
        )

    def test_paper_and_daily_report_cli_contract(self) -> None:
        paper_output = StringIO()
        with redirect_stdout(paper_output):
            paper_exit = main(["paper-simulate", "--database", str(self.database)])
        paper = json.loads(paper_output.getvalue())
        self.assertEqual(0, paper_exit)
        self.assertEqual("1000", paper["ending_equity"])
        self.assertTrue(paper["aggressive_allowed"])
        self.assertIn("no profit is guaranteed", paper["disclaimer"])

        report_output = StringIO()
        with redirect_stdout(report_output):
            report_exit = main([
                "daily-report", "--database", str(self.database), "--date", "2026-06-06"
            ])
        report = json.loads(report_output.getvalue())
        self.assertEqual(0, report_exit)
        self.assertEqual("2026-06-06", report["date"])
        self.assertEqual([], report["signals"])
        self.assertEqual([], report["top3"])
        self.assertIsNone(report["regime"])
        self.assertEqual([], report["sectors"])
        self.assertEqual("1000", report["paper_account"]["equity"])
        self.assertEqual([], report["top_capital_long"])
        self.assertEqual([], report["top_capital_short"])
        self.assertEqual([], report["top_candidates"])
        self.assertTrue(report["aggressive_allowed"])


if __name__ == "__main__":
    unittest.main()
