from datetime import UTC, datetime, time, timedelta
from pathlib import Path
import tempfile
import unittest

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.runner import (
    ProductionRunner,
    RunnerLockError,
    RunnerTask,
    RunnerTaskResult,
    SingleInstanceLock,
    default_tasks,
)


NOW = datetime(2026, 6, 6, 0, 5, tzinfo=UTC)


class ProductionRunnerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        self.database = Path(self.tempdir.name) / "runner.db"
        self.repository = MarketDataRepository(self.database)

    def tearDown(self) -> None:
        self.repository.close()
        self.tempdir.cleanup()

    def test_interval_schedule_must_be_positive(self) -> None:
        with self.assertRaisesRegex(ValueError, "interval must be positive"):
            RunnerTask("invalid", lambda: 0, interval=timedelta(0))

    def test_single_tick_executes_due_interval_and_daily_tasks(self) -> None:
        calls = []
        tasks = (
            RunnerTask("scan", lambda: calls.append("scan") or 0, interval=timedelta(minutes=15)),
            RunnerTask("daily_report", lambda: calls.append("daily") or 0, daily_at=time(0, 5)),
        )
        runner = ProductionRunner(
            self.repository, tasks, Path(self.tempdir.name) / "runner.lock", clock=lambda: NOW
        )

        self.assertEqual(("scan", "daily_report"), runner.tick(NOW))
        self.assertEqual((), runner.tick(NOW))
        self.assertEqual(["scan", "daily"], calls)
        rows = self.repository._connection.execute(
            "SELECT event_type, status FROM runner_events ORDER BY started_at, event_type"
        ).fetchall()
        self.assertEqual([("daily_report", "SUCCEEDED"), ("scan", "SUCCEEDED")], rows)

    def test_task_failure_is_persisted_and_does_not_stop_following_task(self) -> None:
        calls = []

        def fail() -> int:
            calls.append("failed")
            raise RuntimeError("fixture failure")

        tasks = (
            RunnerTask("evaluate", fail, interval=timedelta(minutes=15)),
            RunnerTask("paper_simulate", lambda: calls.append("paper") or 0, interval=timedelta(minutes=15)),
        )
        runner = ProductionRunner(
            self.repository, tasks, Path(self.tempdir.name) / "runner.lock", clock=lambda: NOW
        )

        self.assertEqual(("evaluate", "paper_simulate"), runner.tick(NOW))
        self.assertEqual(["failed", "paper"], calls)
        rows = self.repository._connection.execute(
            "SELECT event_type, status, error_message FROM runner_events ORDER BY event_type"
        ).fetchall()
        self.assertEqual("FAILED", rows[0][1])
        self.assertIn("fixture failure", rows[0][2])
        self.assertEqual(("paper_simulate", "SUCCEEDED", None), rows[1])

    def test_observer_receives_failure_without_interrupting_runner(self) -> None:
        observations = []
        runner = ProductionRunner(
            self.repository,
            (RunnerTask("scan", lambda: 2, interval=timedelta(minutes=15)),),
            Path(self.tempdir.name) / "runner.lock",
            clock=lambda: NOW,
            observer=lambda event, status, error: observations.append((event, status, error)),
        )

        runner.tick(NOW)

        self.assertEqual("scan", observations[0][0])
        self.assertEqual("FAILED", observations[0][1])
        self.assertIn("exit code 2", observations[0][2])

    def test_default_tasks_schedule_pipeline_and_history(self) -> None:
        callback = lambda: 0
        tasks = default_tasks(callback, callback, callback, callback, callback, callback)
        schedules = {task.event_type: task for task in tasks}

        self.assertEqual(timedelta(minutes=15), schedules["scan"].interval)
        self.assertEqual(timedelta(minutes=15), schedules["evaluate"].interval)
        self.assertEqual(timedelta(minutes=15), schedules["paper_simulate"].interval)
        self.assertEqual(timedelta(hours=24), schedules["collect_history"].interval)
        self.assertEqual(time(0, 5), schedules["daily_report"].daily_at)
        self.assertNotIn("hotlist_alert", schedules)

        enabled = default_tasks(
            callback, callback, callback, callback, callback, callback,
            hotlist_alert=callback,
        )
        enabled_schedules = {task.event_type: task for task in enabled}
        self.assertEqual(
            timedelta(minutes=15), enabled_schedules["hotlist_alert"].interval
        )

    def test_skipped_task_is_persisted_without_failure(self) -> None:
        observations = []
        runner = ProductionRunner(
            self.repository,
            (
                RunnerTask(
                    "hotlist_alert",
                    lambda: RunnerTaskResult(
                        status="SKIPPED",
                        details={"skipped_reason": "telegram_not_configured"},
                    ),
                    interval=timedelta(minutes=15),
                ),
            ),
            Path(self.tempdir.name) / "runner.lock",
            clock=lambda: NOW,
            observer=lambda event, status, error: observations.append(
                (event, status, error)
            ),
        )

        runner.tick(NOW)

        row = self.repository._connection.execute(
            "SELECT status, error_message FROM runner_events"
        ).fetchone()
        self.assertEqual(("SKIPPED", None), row)
        self.assertEqual(("hotlist_alert", "SKIPPED", None), observations[0])

    def test_single_instance_lock_rejects_second_owner(self) -> None:
        path = Path(self.tempdir.name) / "runner.lock"
        first = SingleInstanceLock(path)
        second = SingleInstanceLock(path)
        first.acquire()
        try:
            with self.assertRaises(RunnerLockError):
                second.acquire()
        finally:
            first.release()
        second.acquire()
        second.release()


if __name__ == "__main__":
    unittest.main()
