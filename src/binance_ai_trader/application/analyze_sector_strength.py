from __future__ import annotations

from datetime import UTC, datetime

from binance_ai_trader.domain.models import SectorSnapshot
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.sectors import SectorMap, SectorStrengthEngine


class SectorStrengthAnalyzer:
    def __init__(
        self,
        repository: MarketDataRepository,
        sector_map: SectorMap,
        engine: SectorStrengthEngine | None = None,
    ) -> None:
        self._repository = repository
        self._sector_map = sector_map
        self._engine = engine or SectorStrengthEngine()

    def analyze_latest(self) -> tuple[SectorSnapshot, ...]:
        run_id, members = self._repository.load_latest_sector_members()
        if run_id is None:
            return ()
        snapshots = self._engine.calculate(run_id, members, self._sector_map)
        self._repository.save_sector_snapshots(run_id, snapshots, _utc_now())
        return snapshots


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
