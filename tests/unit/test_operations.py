from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import UTC, datetime
from decimal import Decimal
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from binance_ai_trader.entrypoints.cli import build_parser, main
from binance_ai_trader.hotlist.models import HotlistAIReview
from binance_ai_trader.hotlist.performance import HotlistPerformanceTracker
from binance_ai_trader.hotlist.performance_repository import HotlistPerformanceRepository
from binance_ai_trader.operations import build_ops_status, render_ops_daily, run_safety_audit


class OperationsTest(unittest.TestCase):
    def test_status_reports_required_operational_fields(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "market.db"
            payload = build_ops_status(database, telegram_configured=False)
        self.assertTrue(payload["database_health"]["healthy"])
        self.assertIsNone(payload["latest_regime"])
        self.assertEqual(0, payload["hotlist_watchlist_count"])
        self.assertEqual(0, payload["hotlist_alerts_count"])
        self.assertEqual(0, payload["hotlist_performance_summary"]["total_opportunities"])
        self.assertEqual([], payload["runner_last_task_statuses"])
        self.assertFalse(payload["telegram_configured"])

    def test_daily_report_contains_performance_opportunities_and_warning(self):
        with tempfile.TemporaryDirectory() as temporary:
            database = Path(temporary) / "market.db"
            repository = HotlistPerformanceRepository(database)
            try:
                HotlistPerformanceTracker(object(), repository).track(
                    (
                        HotlistAIReview(
                            "BTCUSDT",
                            "LONG",
                            Decimal("100"),
                            Decimal("95"),
                            Decimal("105"),
                            Decimal("110"),
                            Decimal("2"),
                            "STRONG",
                            "momentum",
                            "2026-06-15T01:00:00+00:00",
                        ),
                    ),
                    datetime(2026, 6, 15, tzinfo=UTC),
                )
            finally:
                repository.close()
            report = render_ops_daily(
                database,
                Path("config/strategies/baseline_v1.json"),
                datetime(2026, 6, 15, tzinfo=UTC),
            )
        self.assertIn("Hotlist alerts today", report)
        self.assertIn("BTCUSDT", report)
        self.assertIn("Performance", report)
        self.assertIn("Current Champion Strategy", report)
        self.assertIn("Research only", report)

    def test_telegram_hotlist_test_skips_without_environment(self):
        output = StringIO()
        with patch.dict(os.environ, {}, clear=True), redirect_stdout(output):
            result = main(["telegram", "hotlist-test"])
        self.assertEqual(0, result)
        self.assertEqual("SKIPPED", json.loads(output.getvalue())["status"])

    def test_safety_audit_passes_repository_contract(self):
        payload = run_safety_audit(
            Path.cwd(), Path("config/strategies/baseline_v1.json")
        )
        self.assertEqual("PASS", payload["status"])
        self.assertTrue(all(payload["checks"].values()))

    def test_parser_registers_operations(self):
        parser = build_parser()
        self.assertEqual("status", parser.parse_args(["ops", "status"]).ops_command)
        self.assertEqual("daily", parser.parse_args(["ops", "daily"]).ops_command)
        self.assertEqual(
            "safety-audit",
            parser.parse_args(["ops", "safety-audit"]).ops_command,
        )


if __name__ == "__main__":
    unittest.main()
