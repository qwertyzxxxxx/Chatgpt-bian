from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from binance_ai_trader.domain.models import MarketRegime
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.regime import MarketRegimeEngine


class MarketRegimeAnalyzer:
    symbols = ("BTCUSDT", "ETHUSDT")

    def __init__(
        self,
        repository: MarketDataRepository,
        engine: MarketRegimeEngine | None = None,
    ) -> None:
        self._repository = repository
        self._engine = engine or MarketRegimeEngine()

    def analyze(self, snapshot_id: str | None = None) -> MarketRegime:
        if snapshot_id is not None:
            snapshot = self._repository.load_snapshot(snapshot_id)
        else:
            created_at = _utc_now()
            manual_id = self._repository.create_manual_snapshot(
                f"regime-{uuid4()}",
                int(datetime.now(UTC).timestamp() * 1000),
                created_at,
            )
            snapshot = self._repository.load_snapshot(manual_id)
        market_data = {
            symbol: {
                interval: self._repository.load_klines_at(
                    symbol, interval, snapshot.data_cutoff_ms,
                    limit=self._engine.policy.minimum_candles,
                )
                for interval in self._engine.intervals
            }
            for symbol in self.symbols
        }
        regime = self._engine.evaluate(market_data["BTCUSDT"], market_data["ETHUSDT"])
        counts = [
            len(market_data[symbol][interval])
            for symbol in self.symbols for interval in self._engine.intervals
        ]
        complete = all(count >= self._engine.policy.minimum_candles for count in counts)
        quality = (
            (self._repository.load_run_quality(snapshot.collection_run_id)
             if snapshot.collection_run_id else "COMPLETE")
            if complete else "MISSING" if not any(counts) else "PARTIAL"
        )
        regime = replace(regime, data_quality_status=quality)
        evaluated_at = _utc_now()
        self._repository.save_market_regime(regime, evaluated_at, snapshot.snapshot_id)
        if snapshot.snapshot_type == "MANUAL":
            self._repository.finalize_snapshot(snapshot.snapshot_id, evaluated_at)
        return regime


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
