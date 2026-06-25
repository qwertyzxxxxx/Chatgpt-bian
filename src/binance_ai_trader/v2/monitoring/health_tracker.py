"""V2 Health Tracker — shared mutable state for task health monitoring.

Tracks:
  - Last successful run time per task (scan / settle / report)
  - Rolling error log (last 6h window)
  - Consecutive Binance API failure counter
  - Consecutive settlement failure counter
  - DB write failure flag

All methods are NOT thread-safe — fine because ProductionRunner is single-threaded.
"""
from __future__ import annotations

from collections import deque
from datetime import UTC, datetime, timedelta


class V2HealthTracker:
    def __init__(self) -> None:
        self.last_scan_at: datetime | None = None
        self.last_settle_at: datetime | None = None
        self.last_report_at: datetime | None = None

        self._errors: deque[tuple[datetime, str, str]] = deque(maxlen=500)

        self.api_consecutive_failures: int = 0
        self.settle_consecutive_failures: int = 0
        self.db_failure: bool = False

    def record_scan_ok(self) -> None:
        self.last_scan_at = datetime.now(UTC)
        self.api_consecutive_failures = 0

    def record_settle_ok(self) -> None:
        self.last_settle_at = datetime.now(UTC)
        self.settle_consecutive_failures = 0

    def record_report_ok(self) -> None:
        self.last_report_at = datetime.now(UTC)

    def record_api_failure(self, msg: str = "") -> None:
        self.api_consecutive_failures += 1
        self._log_error("scan_api", msg or "Binance API failure")

    def record_settle_failure(self, msg: str = "") -> None:
        self.settle_consecutive_failures += 1
        self._log_error("settle", msg or "settle failure")

    def record_db_failure(self, msg: str = "") -> None:
        self.db_failure = True
        self._log_error("db", msg or "DB write failure")

    def record_error(self, task: str, msg: str) -> None:
        self._log_error(task, msg)

    def clear_db_failure(self) -> None:
        self.db_failure = False

    def errors_last_n_hours(self, hours: int = 6) -> list[tuple[datetime, str, str]]:
        cutoff = datetime.now(UTC) - timedelta(hours=hours)
        return [(t, task, msg) for t, task, msg in self._errors if t >= cutoff]

    def scan_overdue(self, hours: float = 2.0) -> bool:
        if self.last_scan_at is None:
            return False
        return (datetime.now(UTC) - self.last_scan_at).total_seconds() > hours * 3600

    def _log_error(self, task: str, msg: str) -> None:
        self._errors.append((datetime.now(UTC), task, msg))
