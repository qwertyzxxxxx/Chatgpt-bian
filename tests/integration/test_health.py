from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.entrypoints.cli import main
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class HealthIntegrationTest(unittest.TestCase):
    def test_health_outputs_required_structure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "health.db"
            repository = MarketDataRepository(database)
            try:
                repository.start_runner_event("event-1", "scan", "2026-06-06T00:00:00.000+00:00")
                repository.finish_runner_event(
                    "event-1", "FAILED", "2026-06-06T00:00:01.000+00:00",
                    "fixture error", 1000,
                )
            finally:
                repository.close()

            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(["health", "--database", str(database)])
            payload = json.loads(output.getvalue())

        self.assertEqual(0, exit_code)
        self.assertEqual(
            {
                "last_scan_at", "last_regime", "last_signal_count", "last_runner_error",
                "paper_equity", "database_size_bytes", "database_integrity",
                "aggressive_allowed",
            },
            set(payload),
        )
        self.assertIsNone(payload["last_scan_at"])
        self.assertIsNone(payload["last_regime"])
        self.assertEqual(0, payload["last_signal_count"])
        self.assertEqual("scan", payload["last_runner_error"]["event_type"])
        self.assertEqual("1000", payload["paper_equity"])
        self.assertGreater(payload["database_size_bytes"], 0)
        self.assertEqual("ok", payload["database_integrity"])
        self.assertTrue(payload["aggressive_allowed"])


if __name__ == "__main__":
    unittest.main()
