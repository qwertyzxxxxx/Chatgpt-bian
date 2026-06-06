from contextlib import closing, redirect_stdout
from decimal import Decimal
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.backtest import summarize_results
from binance_ai_trader.domain.models import BacktestResult, Kline
from binance_ai_trader.entrypoints.cli import main
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from tests.integration.test_signals import member


class BacktestIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "backtest.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_point_in_time_reads_exclude_future_data(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            repository.start_run("past", "1970-01-01T00:00:00.000+00:00")
            repository.save_universe("past", (member("BTCUSDT"),), "1970-01-01T00:00:00.000+00:00")
            repository.start_run("future", "2030-01-01T00:00:00.000+00:00")
            repository.save_universe("future", (member("FUTUREUSDT"),), "2030-01-01T00:00:00.000+00:00")
            repository.save_klines(
                Kline(
                    "BTCUSDT", "15m", index * 900_000, (index + 1) * 900_000 - 1,
                    Decimal("100"), Decimal("101"), Decimal("99"), Decimal(str(100 + index)),
                    Decimal("1"), Decimal("100"), 1,
                )
                for index in range(3)
            )
            cutoff = 2 * 900_000 - 1
            bars = repository.load_klines_at("BTCUSDT", "15m", cutoff, 10)
            universe = repository.load_backtest_universe(cutoff)
        finally:
            repository.close()

        self.assertEqual(2, len(bars))
        self.assertTrue(all(item.close_time_ms <= cutoff for item in bars))
        self.assertEqual({"BTCUSDT"}, set(universe))

    def test_evaluation_times_require_complete_96_bar_future_window(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            for symbol in ("BTCUSDT", "ETHUSDT"):
                repository.save_klines(
                    Kline(
                        symbol, "15m", index * 900_000, (index + 1) * 900_000 - 1,
                        Decimal("100"), Decimal("101"), Decimal("99"), Decimal("100"),
                        Decimal("1"), Decimal("100"), 1,
                    )
                    for index in range(100)
                )
            points = repository.load_backtest_evaluation_times()
        finally:
            repository.close()

        self.assertEqual(4, len(points))
        self.assertEqual(tuple(sorted(points)), points)

    def test_persists_backtest_result_and_summary(self) -> None:
        item = BacktestResult(
            evaluation_time_ms=1_000, symbol="BTCUSDT", direction="SHORT", combined_regime="BEAR",
            sector="LAYER1", sector_rank=1, score=91.0, entry=Decimal("100"),
            stop_loss=Decimal("98"), tp1=Decimal("102"), tp2=Decimal("104"),
            rr_tp1=Decimal("1"), rr_tp2=Decimal("2"), result="WIN_TP2",
            bars_to_result=4, realized_r=Decimal("2"),
        )
        summary = summarize_results("bt-1", "start", "end", 1, (item,))
        repository = MarketDataRepository(self.database)
        try:
            repository.start_backtest_run("bt-1", "start", None, None, 1)
            repository.save_backtest_results("bt-1", (item,))
            repository.finish_backtest_run("bt-1", "end", "SUCCEEDED", summary)
        finally:
            repository.close()

        with closing(sqlite3.connect(self.database)) as connection:
            stored = connection.execute(
                """
                SELECT symbol, direction, combined_regime, sector, sector_rank, result, realized_r
                FROM backtest_results
                """
            ).fetchone()
            run = connection.execute(
                "SELECT status, evaluation_points, total_signals FROM backtest_runs"
            ).fetchone()
        self.assertEqual(("BTCUSDT", "SHORT", "BEAR", "LAYER1", 1, "WIN_TP2", "2"), stored)
        self.assertEqual(("SUCCEEDED", 1, 1), run)

    def test_backtest_cli_persists_empty_run_and_outputs_contract(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "backtest",
                "--database", str(self.database),
                "--config", "config/sectors.json",
            ])
        payload = json.loads(output.getvalue())

        self.assertEqual(0, exit_code)
        self.assertEqual(0, payload["total_signals"])
        self.assertEqual(0.0, payload["tp1_hit_rate"])
        self.assertEqual(0.0, payload["tp2_win_rate"])
        self.assertEqual(0.0, payload["loss_rate"])
        self.assertEqual(0.0, payload["expired_rate"])
        self.assertIsNone(payload["profit_factor"])
        self.assertEqual(
            {"BULL", "BEAR", "RANGE", "OBSERVE"},
            set(payload["by_combined_regime"]),
        )
        self.assertEqual({"LONG", "SHORT"}, set(payload["by_direction"]))
        self.assertEqual({"BULL", "BEAR", "RANGE", "OBSERVE"}, set(payload["by_regime"]))
        self.assertEqual(10, len(payload["by_sector"]))
        self.assertEqual(
            {"90-100", "80-90", "70-80", "below 70"},
            set(payload["by_score_bucket"]),
        )

        with closing(sqlite3.connect(self.database)) as connection:
            run = connection.execute(
                "SELECT status, evaluation_points, total_signals, summary_json FROM backtest_runs"
            ).fetchone()
            result_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(backtest_results)")
            }
        self.assertEqual(("SUCCEEDED", 0, 0), run[:3])
        self.assertIsNotNone(run[3])
        self.assertTrue(
            {"evaluation_time_ms", "direction", "combined_regime", "sector", "score", "result", "realized_r"}
            <= result_columns
        )


if __name__ == "__main__":
    unittest.main()
