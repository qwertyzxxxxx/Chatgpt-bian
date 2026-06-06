from contextlib import closing, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from binance_ai_trader.entrypoints.cli import main


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
        payload = json.loads(output.getvalue())
        self.assertEqual(0, exit_code)
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
                "strategy_id", "total_signals", "tp1_hit_rate", "tp2_win_rate",
                "loss_rate", "profit_factor", "expectancy_r", "max_drawdown_r",
            },
            set(payload),
        )

    def test_auto_research_saves_only_candidates_and_never_approves(self) -> None:
        output = StringIO()
        with redirect_stdout(output):
            exit_code = main([
                "auto_research", "--database", str(self.database), "--max-candidates", "5",
            ])
        rows = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual(0, exit_code)
        self.assertEqual(5, len(rows))
        self.assertTrue(all(row["status"] == "candidate" for row in rows))

        with closing(sqlite3.connect(self.database)) as connection:
            versions = connection.execute(
                "SELECT strategy_id, status, config_json, metrics_json FROM strategy_versions ORDER BY strategy_id"
            ).fetchall()
        baseline = [row for row in versions if row[0] == "baseline_v1"]
        candidates = [row for row in versions if row[0] != "baseline_v1"]
        self.assertEqual(1, len(baseline))
        self.assertEqual("baseline", baseline[0][1])
        self.assertEqual(5, len(candidates))
        self.assertTrue(all(row[1] == "candidate" for row in candidates))
        self.assertTrue(all(row[3] is not None for row in candidates))
        self.assertNotIn("approved", {row[1] for row in versions})


if __name__ == "__main__":
    unittest.main()
