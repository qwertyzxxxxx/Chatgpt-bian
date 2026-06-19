from __future__ import annotations

import fcntl
import json
import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time as datetime_time, timedelta
from pathlib import Path
from typing import Union
from uuid import uuid4

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository

LOGGER = logging.getLogger(__name__)
TaskCallback = Callable[[], Union[int, None, "RunnerTaskResult"]]
TaskObserver = Callable[[str, str, str | None], None]


class RunnerLockError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RunnerTaskResult:
    status: str = "SUCCEEDED"
    details: dict[str, object] | None = None

    def __post_init__(self) -> None:
        if self.status not in {"SUCCEEDED", "SKIPPED"}:
            raise ValueError("runner task result status must be SUCCEEDED or SKIPPED")


@dataclass(frozen=True, slots=True)
class RunnerTask:
    event_type: str
    callback: TaskCallback
    interval: timedelta | None = None
    daily_at: datetime_time | None = None

    def __post_init__(self) -> None:
        if (self.interval is None) == (self.daily_at is None):
            raise ValueError("runner task requires exactly one schedule")
        if self.interval is not None and self.interval <= timedelta(0):
            raise ValueError("runner task interval must be positive")


class SingleInstanceLock:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._file = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.path.open("a+", encoding="utf-8")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise RunnerLockError(f"run-loop already holds lock: {self.path}") from error
        handle.seek(0)
        handle.truncate()
        handle.write(str(Path("/proc/self").resolve().name))
        handle.flush()
        self._file = handle

    def release(self) -> None:
        if self._file is None:
            return
        fcntl.flock(self._file.fileno(), fcntl.LOCK_UN)
        self._file.close()
        self._file = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_args: object) -> None:
        self.release()


class ProductionRunner:
    """Fault-isolated UTC scheduler for the read-only analysis pipeline."""

    def __init__(
        self,
        repository: MarketDataRepository,
        tasks: Sequence[RunnerTask],
        lock_path: Path,
        poll_seconds: float = 30.0,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        observer: TaskObserver | None = None,
    ) -> None:
        if poll_seconds <= 0:
            raise ValueError("poll_seconds must be positive")
        self._repository = repository
        self._tasks = tuple(tasks)
        self._lock = SingleInstanceLock(lock_path)
        self._poll_seconds = poll_seconds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleeper = sleeper
        self._observer = observer

    def tick(self, now: datetime | None = None, force: bool = False) -> tuple[str, ...]:
        current = _utc(now or self._clock())
        executed = []
        for task in self._tasks:
            if force or self._is_due(task, current):
                self._run_task(task, current)
                executed.append(task.event_type)
        return tuple(executed)

    def run_forever(self, once: bool = False) -> None:
        with self._lock:
            while True:
                self.tick(force=once)
                if once:
                    return
                self._sleeper(self._poll_seconds)

    def _is_due(self, task: RunnerTask, now: datetime) -> bool:
        last_started = self._repository.load_latest_runner_event_time(task.event_type)
        if task.interval is not None:
            return last_started is None or now - _parse(last_started) >= task.interval
        assert task.daily_at is not None
        scheduled = datetime.combine(now.date(), task.daily_at, UTC)
        return now >= scheduled and (last_started is None or _parse(last_started) < scheduled)

    def _run_task(self, task: RunnerTask, now: datetime) -> None:
        event_id = f"runner-{uuid4()}"
        started_at = now.isoformat(timespec="milliseconds")
        started_clock = time.monotonic_ns()
        self._repository.start_runner_event(event_id, task.event_type, started_at)
        status = "SUCCEEDED"
        error_message = None
        try:
            result = task.callback()
            if isinstance(result, RunnerTaskResult):
                status = result.status
                if result.details:
                    LOGGER.info(
                        "Runner task result: %s",
                        json.dumps(
                            {"event_type": task.event_type, **result.details},
                            separators=(",", ":"),
                            sort_keys=True,
                        ),
                    )
            elif result not in (None, 0):
                raise RuntimeError(f"task returned exit code {result}")
        except Exception as error:  # fault isolation is the runner's primary contract
            status = "FAILED"
            error_message = f"{type(error).__name__}: {error}"
            LOGGER.exception("Runner task failed: %s", task.event_type)
        completed_at = _utc(self._clock()).isoformat(timespec="milliseconds")
        duration_ms = max(0, (time.monotonic_ns() - started_clock) // 1_000_000)
        self._repository.finish_runner_event(
            event_id, status, completed_at, error_message, duration_ms
        )
        if self._observer is not None:
            try:
                self._observer(task.event_type, status, error_message)
            except Exception:
                LOGGER.exception("Runner observer failed for task: %s", task.event_type)


def default_tasks(
    scan: TaskCallback,
    evaluate: TaskCallback,
    paper_simulate: TaskCallback,
    daily_report: TaskCallback,
    auto_research: TaskCallback,
    collect_history: TaskCallback,
    history_interval: timedelta = timedelta(hours=24),
    hotlist_alert: TaskCallback | None = None,
    gemini_committee: TaskCallback | None = None,
    performance_settle: TaskCallback | None = None,
    performance_summary: TaskCallback | None = None,
    hotlist_performance: TaskCallback | None = None,
    leaderboard_update: TaskCallback | None = None,
    leaderboard_gemini: TaskCallback | None = None,
) -> tuple[RunnerTask, ...]:
    quarter_hour = timedelta(minutes=15)
    tasks = [
        RunnerTask("scan", scan, interval=quarter_hour),
        RunnerTask("evaluate", evaluate, interval=quarter_hour),
        RunnerTask("paper_simulate", paper_simulate, interval=quarter_hour),
        RunnerTask("daily_report", daily_report, daily_at=datetime_time(0, 5)),
        RunnerTask("collect_history", collect_history, interval=history_interval),
        RunnerTask("auto_research", auto_research, interval=timedelta(hours=6)),
    ]
    if hotlist_alert is not None:
        tasks.append(RunnerTask("hotlist_alert", hotlist_alert, interval=quarter_hour))
    if gemini_committee is not None:
        tasks.append(RunnerTask("gemini_committee", gemini_committee, interval=timedelta(hours=4)))
    if performance_settle is not None:
        tasks.append(RunnerTask("performance_settle", performance_settle, interval=timedelta(hours=1)))
    if performance_summary is not None:
        tasks.append(RunnerTask("performance_summary", performance_summary, daily_at=datetime_time(0, 10)))
    if hotlist_performance is not None:
        tasks.append(RunnerTask("hotlist_performance", hotlist_performance, interval=quarter_hour))
    if leaderboard_update is not None:
        tasks.append(RunnerTask("leaderboard_update", leaderboard_update, interval=timedelta(hours=1)))
    if leaderboard_gemini is not None:
        tasks.append(RunnerTask("leaderboard_gemini", leaderboard_gemini, interval=timedelta(hours=4)))
    return tuple(tasks)


def _parse(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
