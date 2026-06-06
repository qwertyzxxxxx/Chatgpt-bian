from __future__ import annotations

from datetime import UTC, datetime

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

    def analyze(self) -> MarketRegime:
        market_data = {
            symbol: {
                interval: self._repository.load_klines(
                    symbol,
                    interval,
                    limit=self._engine.policy.minimum_candles,
                )
                for interval in self._engine.intervals
            }
            for symbol in self.symbols
        }
        regime = self._engine.evaluate(market_data["BTCUSDT"], market_data["ETHUSDT"])
        self._repository.save_market_regime(regime, _utc_now())
        return regime


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
