"""Tests for Runner integration: Gemini Committee + Performance Center + Hotlist Performance scheduling."""
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.runner import (
    ProductionRunner,
    RunnerTask,
    RunnerTaskResult,
    default_tasks,
)

NOW = datetime(2026, 6, 6, 0, 10, tzinfo=UTC)
NOW_4H = datetime(2026, 6, 6, 4, 0, tzinfo=UTC)
NOW_1H = datetime(2026, 6, 6, 1, 0, tzinfo=UTC)


class TestDefaultTasksSchedule(unittest.TestCase):
    def _cb(self) -> int:
        return 0

    def test_gemini_committee_not_in_default(self):
        tasks = default_tasks(self._cb, self._cb, self._cb, self._cb, self._cb, self._cb)
        names = {t.event_type for t in tasks}
        self.assertNotIn("gemini_committee", names)

    def test_performance_settle_not_in_default(self):
        tasks = default_tasks(self._cb, self._cb, self._cb, self._cb, self._cb, self._cb)
        names = {t.event_type for t in tasks}
        self.assertNotIn("performance_settle", names)

    def test_performance_summary_not_in_default(self):
        tasks = default_tasks(self._cb, self._cb, self._cb, self._cb, self._cb, self._cb)
        names = {t.event_type for t in tasks}
        self.assertNotIn("performance_summary", names)

    def test_gemini_committee_enabled_with_4h_interval(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            gemini_committee=self._cb,
        )
        schedules = {t.event_type: t for t in tasks}
        self.assertIn("gemini_committee", schedules)
        self.assertEqual(timedelta(hours=4), schedules["gemini_committee"].interval)

    def test_performance_settle_enabled_with_1h_interval(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            performance_settle=self._cb,
        )
        schedules = {t.event_type: t for t in tasks}
        self.assertIn("performance_settle", schedules)
        self.assertEqual(timedelta(hours=1), schedules["performance_settle"].interval)

    def test_performance_summary_runs_every_six_hours(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            performance_summary=self._cb,
        )
        schedules = {t.event_type: t for t in tasks}
        self.assertIn("performance_summary", schedules)
        self.assertEqual(timedelta(hours=6), schedules["performance_summary"].interval)

    def test_all_three_enabled_together(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            gemini_committee=self._cb,
            performance_settle=self._cb,
            performance_summary=self._cb,
        )
        names = {t.event_type for t in tasks}
        self.assertIn("gemini_committee", names)
        self.assertIn("performance_settle", names)
        self.assertIn("performance_summary", names)

    def test_existing_tasks_unchanged(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            gemini_committee=self._cb,
            performance_settle=self._cb,
            performance_summary=self._cb,
        )
        schedules = {t.event_type: t for t in tasks}
        self.assertEqual(timedelta(minutes=15), schedules["scan"].interval)
        self.assertEqual(timedelta(minutes=15), schedules["evaluate"].interval)
        self.assertEqual(timedelta(minutes=15), schedules["paper_simulate"].interval)
        self.assertEqual(time(0, 5), schedules["daily_report"].daily_at)

    def test_hotlist_performance_not_in_default(self):
        tasks = default_tasks(self._cb, self._cb, self._cb, self._cb, self._cb, self._cb)
        names = {t.event_type for t in tasks}
        self.assertNotIn("hotlist_performance", names)

    def test_hotlist_performance_enabled_with_15min_interval(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            hotlist_performance=self._cb,
        )
        schedules = {t.event_type: t for t in tasks}
        self.assertIn("hotlist_performance", schedules)
        self.assertEqual(timedelta(minutes=15), schedules["hotlist_performance"].interval)

    def test_hotlist_performance_and_hotlist_alert_independent(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            hotlist_alert=self._cb,
        )
        names = {t.event_type for t in tasks}
        self.assertIn("hotlist_alert", names)
        self.assertNotIn("hotlist_performance", names)

    def test_hotlist_performance_without_hotlist_alert(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            hotlist_performance=self._cb,
        )
        names = {t.event_type for t in tasks}
        self.assertIn("hotlist_performance", names)
        self.assertNotIn("hotlist_alert", names)

    def test_backward_compatible_without_new_args(self):
        tasks = default_tasks(self._cb, self._cb, self._cb, self._cb, self._cb, self._cb)
        self.assertEqual(6, len(tasks))

    def test_hotlist_alert_still_optional(self):
        tasks = default_tasks(
            self._cb, self._cb, self._cb, self._cb, self._cb, self._cb,
            hotlist_alert=self._cb,
        )
        names = {t.event_type for t in tasks}
        self.assertIn("hotlist_alert", names)


class TestRunnerExecution(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tmpdir.name) / "runner.db"
        self.repository = MarketDataRepository(self.database)
        self.lock = Path(self.tmpdir.name) / "runner.lock"

    def tearDown(self):
        self.repository.close()
        self.tmpdir.cleanup()

    def _runner(self, tasks):
        return ProductionRunner(
            self.repository, tasks, self.lock, clock=lambda: NOW
        )

    def test_gemini_committee_executes_on_schedule(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=lambda: calls.append("gc") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("gc", calls)

    def test_performance_settle_executes_on_schedule(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            performance_settle=lambda: calls.append("settle") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("settle", calls)

    def test_performance_summary_executes_at_daily_time(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            performance_summary=lambda: calls.append("summary") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("summary", calls)

    def test_gemini_committee_not_re_executed_within_4h(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=lambda: calls.append("gc") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        early = datetime(2026, 6, 6, 1, 0, tzinfo=UTC)
        runner.tick(early)
        self.assertEqual(1, calls.count("gc"))

    def test_gemini_committee_re_executed_after_4h(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=lambda: calls.append("gc") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        later = datetime(2026, 6, 6, 4, 10, tzinfo=UTC)
        runner2 = ProductionRunner(
            self.repository, tasks, self.lock, clock=lambda: later
        )
        runner2.tick(later)
        self.assertEqual(2, calls.count("gc"))

    def test_performance_settle_re_executed_after_1h(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            performance_settle=lambda: calls.append("settle") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        later = datetime(2026, 6, 6, 1, 10, tzinfo=UTC)
        runner2 = ProductionRunner(
            self.repository, tasks, self.lock, clock=lambda: later
        )
        runner2.tick(later)
        self.assertEqual(2, calls.count("settle"))

    def test_hotlist_performance_executes_on_schedule(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            hotlist_performance=lambda: calls.append("hp") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("hp", calls)

    def test_hotlist_performance_not_re_executed_within_15min(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            hotlist_performance=lambda: calls.append("hp") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        early = datetime(2026, 6, 6, 0, 20, tzinfo=UTC)
        runner.tick(early)
        self.assertEqual(1, calls.count("hp"))

    def test_hotlist_performance_re_executed_after_15min(self):
        calls = []
        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            hotlist_performance=lambda: calls.append("hp") or 0,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        later = datetime(2026, 6, 6, 0, 25, tzinfo=UTC)
        runner2 = ProductionRunner(
            self.repository, tasks, self.lock, clock=lambda: later
        )
        runner2.tick(later)
        self.assertEqual(2, calls.count("hp"))


class TestFaultIsolation(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tmpdir.name) / "runner.db"
        self.repository = MarketDataRepository(self.database)
        self.lock = Path(self.tmpdir.name) / "runner.lock"

    def tearDown(self):
        self.repository.close()
        self.tmpdir.cleanup()

    def _runner(self, tasks):
        return ProductionRunner(
            self.repository, tasks, self.lock, clock=lambda: NOW
        )

    def test_gemini_failure_does_not_stop_scan(self):
        calls = []

        def failing_gc():
            raise RuntimeError("Gemini API error")

        tasks = default_tasks(
            lambda: calls.append("scan") or 0,
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=failing_gc,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("scan", calls)

    def test_gemini_failure_does_not_stop_evaluate(self):
        calls = []

        def failing_gc():
            raise RuntimeError("Gemini API error")

        tasks = default_tasks(
            lambda: 0,
            lambda: calls.append("evaluate") or 0,
            lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=failing_gc,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("evaluate", calls)

    def test_performance_settle_failure_does_not_stop_scan(self):
        calls = []

        def failing_settle():
            raise RuntimeError("DB error")

        tasks = default_tasks(
            lambda: calls.append("scan") or 0,
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            performance_settle=failing_settle,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("scan", calls)

    def test_performance_summary_failure_does_not_stop_paper_simulate(self):
        calls = []

        def failing_summary():
            raise RuntimeError("Telegram error")

        tasks = default_tasks(
            lambda: 0, lambda: 0,
            lambda: calls.append("paper") or 0,
            lambda: 0, lambda: 0, lambda: 0,
            performance_summary=failing_summary,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("paper", calls)

    def test_gemini_failure_persisted_as_failed(self):
        def failing_gc():
            raise RuntimeError("fixture error")

        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=failing_gc,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        row = self.repository._connection.execute(
            "SELECT status, error_message FROM runner_events WHERE event_type='gemini_committee'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("FAILED", row[0])
        self.assertIn("fixture error", row[1])

    def test_gemini_skipped_result_recorded_correctly(self):
        def skipped_gc():
            return RunnerTaskResult(status="SKIPPED", details={"reason": "gemini_api_key_missing"})

        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            gemini_committee=skipped_gc,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        row = self.repository._connection.execute(
            "SELECT status FROM runner_events WHERE event_type='gemini_committee'"
        ).fetchone()
        self.assertEqual("SKIPPED", row[0])

    def test_all_tasks_still_execute_when_gemini_and_perf_both_fail(self):
        calls = []

        def fail():
            raise RuntimeError("test failure")

        tasks = default_tasks(
            lambda: calls.append("scan") or 0,
            lambda: calls.append("evaluate") or 0,
            lambda: calls.append("paper") or 0,
            lambda: calls.append("daily") or 0,
            lambda: 0, lambda: 0,
            gemini_committee=fail,
            performance_settle=fail,
            performance_summary=fail,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("scan", calls)
        self.assertIn("evaluate", calls)
        self.assertIn("paper", calls)

    def test_hotlist_performance_failure_does_not_stop_scan(self):
        calls = []

        def failing_hp():
            raise RuntimeError("public API timeout")

        tasks = default_tasks(
            lambda: calls.append("scan") or 0,
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            hotlist_performance=failing_hp,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("scan", calls)

    def test_hotlist_performance_failure_does_not_stop_evaluate(self):
        calls = []

        def failing_hp():
            raise RuntimeError("public API timeout")

        tasks = default_tasks(
            lambda: 0,
            lambda: calls.append("evaluate") or 0,
            lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            hotlist_performance=failing_hp,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("evaluate", calls)

    def test_hotlist_performance_failure_persisted_as_failed(self):
        def failing_hp():
            raise RuntimeError("binance_hp_error")

        tasks = default_tasks(
            lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0, lambda: 0,
            hotlist_performance=failing_hp,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        row = self.repository._connection.execute(
            "SELECT status, error_message FROM runner_events WHERE event_type='hotlist_performance'"
        ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual("FAILED", row[0])
        self.assertIn("binance_hp_error", row[1])

    def test_all_pipeline_tasks_execute_despite_hotlist_performance_failure(self):
        calls = []

        def fail():
            raise RuntimeError("hp failure")

        tasks = default_tasks(
            lambda: calls.append("scan") or 0,
            lambda: calls.append("evaluate") or 0,
            lambda: calls.append("paper") or 0,
            lambda: calls.append("daily") or 0,
            lambda: 0, lambda: 0,
            hotlist_alert=lambda: calls.append("alert") or 0,
            hotlist_performance=fail,
        )
        runner = self._runner(tasks)
        runner.tick(NOW)
        self.assertIn("scan", calls)
        self.assertIn("evaluate", calls)
        self.assertIn("paper", calls)
        self.assertIn("alert", calls)


if __name__ == "__main__":
    unittest.main()
