from __future__ import annotations

from datetime import UTC, datetime

from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.space import SpaceEngine, SpaceSnapshot


class SpaceAnalyzer:
    def __init__(
        self, repository: MarketDataRepository, client: BinancePublicClient | None = None
    ) -> None:
        self._repository = repository
        self._client = client
        self._engine = SpaceEngine()

    def analyze_latest(self, limit: int = 20) -> tuple[SpaceSnapshot, ...]:
        ranked = self._repository.load_latest_scores(limit)
        if not ranked:
            return ()
        run_id = ranked[0].run_id
        snapshots = []
        for item in ranked:
            bars = self._repository.load_klines(item.score.symbol, "4h", 720)
            if len(bars) < self._engine.REQUIRED_4H_BARS and self._client is not None:
                bars = self._client.klines(item.score.symbol, "4h", 720)
                self._repository.save_klines(bars)
            for direction in ("LONG", "SHORT"):
                try:
                    snapshots.append(self._engine.score(run_id, item.score.symbol, direction, bars))
                except ValueError:
                    continue
        self._repository.save_space_snapshots(snapshots, _utc_now())
        return tuple(snapshots)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
