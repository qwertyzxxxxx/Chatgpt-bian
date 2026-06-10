from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from binance_ai_trader.capital import CapitalFlowEngine, CapitalInputs, CapitalSnapshot
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


class CapitalFlowAnalyzer:
    def __init__(self, repository: MarketDataRepository, client: BinancePublicClient) -> None:
        self._repository = repository
        self._client = client
        self._engine = CapitalFlowEngine()

    def analyze_latest(
        self, limit: int = 20, snapshot_id: str | None = None
    ) -> tuple[CapitalSnapshot, ...]:
        ranked = (self._repository.load_scores_for_snapshot(snapshot_id, limit)
                  if snapshot_id else self._repository.load_latest_scores(limit))
        if not ranked:
            return ()
        run_id = ranked[0].run_id
        volumes = self._repository.load_universe_volumes(run_id)
        snapshots = []
        for item in ranked:
            symbol = item.score.symbol
            try:
                history = self._client.open_interest_history(symbol, 30)
                if len(history) < 25:
                    continue
                current = self._client.open_interest(symbol)
                funding = self._client.current_funding_rate(symbol)
                ratio = self._client.global_long_short_ratio(symbol)
            except Exception:
                continue
            current_volume = volumes.get(symbol, Decimal("0"))
            average_volume = self._repository.load_average_daily_quote_volume(symbol, 7)
            snapshots.append(self._engine.score(run_id, CapitalInputs(
                symbol=symbol, quote_volume_24h=current_volume,
                average_quote_volume_24h=average_volume or current_volume,
                oi_current=current, oi_1h_ago=history[-2][1], oi_4h_ago=history[-5][1],
                oi_24h_ago=history[-25][1], current_funding_rate=funding,
                long_short_ratio=ratio,
            )))
        self._repository.save_capital_snapshots(snapshots, _utc_now())
        return tuple(snapshots)


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
