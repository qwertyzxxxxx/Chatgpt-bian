from contextlib import closing, redirect_stdout
from io import StringIO
import json
from decimal import Decimal
from pathlib import Path
import os
import sqlite3
import tempfile
import unittest

from binance_ai_trader.backtest import summarize_results
from binance_ai_trader.domain.models import BacktestResult
from binance_ai_trader.entrypoints.cli import main
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class StrategyLabIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "lab.db"

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_list_registers_immutable_baseline(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["strategies", "list", "--database", str(self.database)])
        payloads = [json.loads(line) for line in output.getvalue().splitlines()]
        payload = next(item for item in payloads if item["strategy_id"] == "baseline_v1")
        self.assertEqual(0, exit_code)
        self.assertEqual(5, len(payloads))
        self.assertEqual("baseline_v1", payload["strategy_id"])
        self.assertEqual("baseline", payload["status"])
        self.assertEqual(96, payload["config"]["evaluation_window_bars"])
        with closing(sqlite3.connect(self.database)) as connection:
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(strategy_versions)")
            }
        self.assertEqual(
            {"strategy_id", "name", "description", "config_json", "status", "created_at", "metrics_json"},
            columns,
        )

    def test_compare_outputs_required_metrics(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "strategies", "compare", "baseline_v1",
                "--database", str(self.database),
            ])
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertEqual(
            {
                "strategy_id", "trades", "win_rate", "profit_factor",
                "expectancy", "max_drawdown", "regime_breakdown",
                "direction_breakdown",
            },
            set(payload),
        )

    def test_rank_uses_latest_existing_results_without_running_backtest(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            run_id = "existing-backtest"
            started_at = "2026-06-01T00:00:00+00:00"
            completed_at = "2026-06-01T01:00:00+00:00"
            repository.start_backtest_run(run_id, started_at, None, None, 1)
            results = tuple(
                _backtest_result(
                    index,
                    regime="BEAR",
                    direction="SHORT",
                    capital_score=70,
                    space_score=85,
                    outcome="WIN_TP2" if index >= 9 else "LOSS",
                )
                for index in range(24)
            ) + tuple(
                _backtest_result(
                    24 + index,
                    regime="RANGE",
                    direction="LONG",
                    capital_score=50,
                    space_score=50,
                    outcome="LOSS",
                )
                for index in range(6)
            )
            repository.save_backtest_results(run_id, results)
            summary = summarize_results(
                run_id, started_at, completed_at, 30, results
            )
            repository.finish_backtest_run(
                run_id, completed_at, "SUCCEEDED", summary
            )
        finally:
            repository.close()

        output = StringIO()
        with redirect_stdout(output):
            exit_code = main(["strategies", "rank", "--database", str(self.database)])

        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, exit_code)
        self.assertEqual([1, 2, 3, 4], [row["rank"] for row in rows])
        self.assertEqual(
            {
                "baseline_v1",
                "range_disabled_v1",
                "bear_short_space80_v1",
                "capital_60_80_space80_v1",
            },
            {row["strategy_id"] for row in rows},
        )
        self.assertTrue(all(row["verdict"] == "PASS" for row in rows))
        self.assertTrue(all(set(row["direction_breakdown"]) == {"LONG", "SHORT"} for row in rows))
        self.assertTrue(
            all(
                set(row["regime_breakdown"]) == {"BULL", "BEAR", "RANGE", "OBSERVE"}
                for row in rows
            )
        )
        bear = next(row for row in rows if row["strategy_id"] == "bear_short_space80_v1")
        baseline = next(row for row in rows if row["strategy_id"] == "baseline_v1")
        self.assertEqual(24, bear["trades"])
        self.assertEqual(30, baseline["trades"])
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                1, connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            )

    def test_breakout_hunter_sweep_outputs_ranked_top_ten_from_existing_results(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            run_id = "sweep-backtest"
            started_at = "2026-06-01T00:00:00+00:00"
            completed_at = "2026-06-01T01:00:00+00:00"
            repository.start_backtest_run(run_id, started_at, None, None, 1)
            results = tuple(
                _backtest_result(
                    index,
                    regime="BULL" if index % 2 else "BEAR",
                    direction="LONG" if index % 2 else "SHORT",
                    capital_score=80,
                    space_score=90,
                    outcome="WIN_TP2" if index % 3 else "LOSS",
                )
                for index in range(30)
            )
            repository.save_backtest_results(run_id, results)
            summary = summarize_results(
                run_id, started_at, completed_at, 30, results
            )
            repository.finish_backtest_run(
                run_id, completed_at, "SUCCEEDED", summary
            )
        finally:
            repository.close()

        output = StringIO()
        report = Path(self.tempdir.name) / "reports" / "breakout_hunter_sweep.md"
        with redirect_stdout(output):
            exit_code = main([
                "strategies",
                "sweep",
                "breakout_hunter_v1",
                "--database",
                str(self.database),
                "--report",
                str(report),
            ])

        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, exit_code)
        self.assertEqual(list(range(1, 11)), [row["rank"] for row in rows])
        self.assertTrue(
            all(
                set(row)
                == {
                    "rank",
                    "parameters",
                    "trades",
                    "win_rate",
                    "profit_factor",
                    "expectancy",
                    "max_drawdown",
                    "verdict",
                }
                for row in rows
            )
        )
        self.assertTrue(all(row["verdict"] in {"PASS", "WATCH", "REJECT"} for row in rows))
        self.assertTrue(
            all(
                row["parameters"]["abs_move_percentile"] in {0.7, 0.8, 0.9}
                for row in rows
            )
        )
        report_text = report.read_text(encoding="utf-8")
        self.assertIn("# Strategy Sweep Report: `breakout_hunter_v1`", report_text)
        self.assertIn(f"**Database:** `{self.database}`", report_text)
        self.assertIn("**Total parameter combinations tested:** 864", report_text)
        self.assertIn("## Top 10", report_text)
        self.assertIn("## Best Parameter Set", report_text)
        self.assertIn("not live trading advice", report_text)
        with closing(sqlite3.connect(self.database)) as connection:
            self.assertEqual(
                1, connection.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
            )

    def test_champion_ranks_all_strategies_and_writes_weekly_report(self) -> None:
        repository = MarketDataRepository(self.database)
        try:
            run_id = "champion-backtest"
            started_at = "2026-06-01T00:00:00+00:00"
            completed_at = "2026-06-01T01:00:00+00:00"
            repository.start_backtest_run(run_id, started_at, None, None, 1)
            results = tuple(
                _backtest_result(
                    index,
                    regime="BEAR",
                    direction="SHORT",
                    capital_score=70,
                    space_score=85,
                    outcome="WIN_TP2" if index >= 8 else "LOSS",
                )
                for index in range(30)
            )
            repository.save_backtest_results(run_id, results)
            summary = summarize_results(
                run_id, started_at, completed_at, 30, results
            )
            repository.finish_backtest_run(
                run_id, completed_at, "SUCCEEDED", summary
            )
        finally:
            repository.close()

        output = StringIO()
        original_cwd = Path.cwd()
        baseline = original_cwd / "config/strategies/baseline_v1.json"
        try:
            os.chdir(self.tempdir.name)
            with redirect_stdout(output):
                exit_code = main([
                    "strategies",
                    "champion",
                    "--database",
                    str(self.database),
                    "--baseline-config",
                    str(baseline),
                ])
        finally:
            os.chdir(original_cwd)

        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
        self.assertIsNotNone(payload["champion"])
        self.assertEqual(5, len(payload["leaderboard"]))
        self.assertEqual(
            list(range(1, 6)),
            [item["rank"] for item in payload["leaderboard"]],
        )
        self.assertEqual(
            {
                "rank",
                "strategy_id",
                "score",
                "profit_factor",
                "expectancy",
                "max_drawdown",
                "trade_count",
                "verdict",
            },
            set(payload["champion"]),
        )
        report = (
            Path(self.tempdir.name) / "reports/champion_league.md"
        ).read_text(encoding="utf-8")
        self.assertIn("# Strategy Champion League", report)
        self.assertIn("## Champion", report)
        self.assertIn("## Leaderboard", report)
        self.assertIn("not live trading advice", report)

    def test_auto_research_saves_only_candidates_and_never_approves(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "auto_research", "--database", str(self.database), "--max-candidates", "5",
            ])
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, exit_code)
        self.assertEqual(0, len(rows))

        with closing(sqlite3.connect(self.database)) as connection:
            versions = connection.execute(
                "SELECT strategy_id, status, config_json, metrics_json FROM strategy_versions ORDER BY strategy_id"
            ).fetchall()
        baseline = [row for row in versions if row[0] == "baseline_v1"]
        candidates = [row for row in versions if row[0] != "baseline_v1"]
        self.assertEqual(1, len(baseline))
        self.assertEqual("baseline", baseline[0][1])
        self.assertEqual(
            {
                "range_disabled_v1",
                "bear_short_space80_v1",
                "capital_60_80_space80_v1",
                "breakout_hunter_v1",
            },
            {row[0] for row in candidates},
        )
        self.assertTrue(all(row[1] == "candidate" for row in candidates))
        self.assertNotIn("approved", {row[1] for row in versions})


def _backtest_result(
    index: int,
    regime: str,
    direction: str,
    capital_score: float,
    space_score: float,
    outcome: str,
) -> BacktestResult:
    realized_r = Decimal("2") if outcome == "WIN_TP2" else Decimal("-1")
    return BacktestResult(
        evaluation_time_ms=1_000 + index,
        symbol=f"S{index:02d}USDT",
        direction=direction,
        combined_regime=regime,
        sector="DEFI",
        sector_rank=1,
        score=90,
        entry=Decimal("100"),
        stop_loss=Decimal("95"),
        tp1=Decimal("105"),
        tp2=Decimal("110"),
        rr_tp1=Decimal("1"),
        rr_tp2=Decimal("2"),
        result=outcome,
        bars_to_result=1,
        realized_r=realized_r,
        capital_score=capital_score,
        space_score=space_score,
        final_signal_score=90,
    )


if __name__ == "__main__":
    unittest.main()
