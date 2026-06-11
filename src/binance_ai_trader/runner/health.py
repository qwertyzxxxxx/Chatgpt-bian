from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class HealthService:
    def __init__(self, repository: MarketDataRepository, database_path: Path) -> None:
        self._repository = repository
        self._database_path = database_path

    def snapshot(self) -> dict[str, object]:
        account = self._repository.load_or_create_paper_account(1000, datetime.now(UTC).isoformat(timespec="milliseconds"))
        return {
            "last_scan_at": self._repository.load_last_scan_time(),
            "last_regime": self._repository.load_latest_regime_health(),
            "last_signal_count": self._repository.load_latest_signal_count(),
            "last_runner_error": self._repository.load_latest_runner_error(),
            "paper_equity": str(account.equity),
            "database_size_bytes": self._database_size(),
            "sqlite": self._repository.sqlite_health(),
            "aggressive_allowed": account.aggressive_allowed,
        }

    def _database_size(self) -> int:
        return sum(
            path.stat().st_size
            for path in (
                self._database_path,
                Path(f"{self._database_path}-wal"),
                Path(f"{self._database_path}-shm"),
            )
            if path.exists()
        )
