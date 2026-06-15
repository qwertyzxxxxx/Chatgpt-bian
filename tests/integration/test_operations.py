from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from binance_ai_trader.entrypoints.cli import main


class OperationsIntegrationTest(unittest.TestCase):
    def test_status_daily_and_safety_audit_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            database = root / "market.db"
            report = root / "ops_daily.md"

            status_output = StringIO()
            with redirect_stdout(status_output):
                self.assertEqual(
                    0, main(["ops", "status", "--database", str(database)])
                )
            status = json.loads(status_output.getvalue())
            self.assertTrue(status["database_health"]["healthy"])

            daily_output = StringIO()
            with redirect_stdout(daily_output):
                self.assertEqual(
                    0,
                    main(
                        [
                            "ops",
                            "daily",
                            "--database",
                            str(database),
                            "--report",
                            str(report),
                        ]
                    ),
                )
            self.assertTrue(report.exists())
            self.assertIn("Research only", report.read_text(encoding="utf-8"))

        audit_output = StringIO()
        with redirect_stdout(audit_output):
            self.assertEqual(0, main(["ops", "safety-audit"]))
        self.assertEqual("PASS", json.loads(audit_output.getvalue())["status"])


if __name__ == "__main__":
    unittest.main()
