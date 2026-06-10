from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
import logging

from binance_ai_trader.capital import (
    CapitalFlowHistory,
    CapitalObservation,
    CapitalSnapshot,
)
from binance_ai_trader.infrastructure.binance_public import BinancePublicClient
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository


LOGGER = logging.getLogger(__name__)
_HISTORY_WINDOW_MS = 30 * 24 * 60 * 60 * 1000


class CapitalFlowAnalyzer:
    def __init__(self, repository: MarketDataRepository, client: BinancePublicClient) -> None:
        self._repository = repository
        self._client = client
        self._history = CapitalFlowHistory(repository)

    def analyze_latest(
        self, limit: int = 20, snapshot_id: str | None = None
    ) -> tuple[CapitalSnapshot, ...]:
        snapshot = (
            self._repository.load_snapshot(snapshot_id)
            if snapshot_id else self._repository.load_latest_snapshot()
        )
        ranked = self._repository.load_scores_for_snapshot(snapshot.snapshot_id, limit)
        if not ranked or snapshot.collection_run_id is None:
            return ()
        run_id = snapshot.collection_run_id
        volumes = self._repository.load_universe_volumes(run_id)
        snapshots = []
        for item in ranked:
            symbol = item.score.symbol
            try:
                observations = self._collect_observations(
                    symbol, snapshot.snapshot_id, snapshot.data_cutoff_ms,
                    volumes.get(symbol),
                )
                self._repository.save_capital_observations(observations, _utc_now())
                capital = self._history.score_at(
                    run_id, symbol, snapshot.data_cutoff_ms
                )
            except Exception as exc:
                LOGGER.warning("Capital history collection failed for %s: %s", symbol, exc)
                continue
            if capital is not None:
                snapshots.append(capital)
        self._repository.save_capital_snapshots(snapshots, _utc_now())
        return tuple(snapshots)

    def _collect_observations(
        self, symbol: str, snapshot_id: str, cutoff_ms: int,
        quote_volume: Decimal | None,
    ) -> tuple[CapitalObservation, ...]:
        start_ms = cutoff_ms - _HISTORY_WINDOW_MS
        oi_history = _paged_history(
            self._client.open_interest_history, symbol, start_ms, cutoff_ms, 500
        )
        ratio_history = _paged_history(
            self._client.global_long_short_ratio_history,
            symbol, start_ms, cutoff_ms, 500,
        )
        funding_history = _paged_history(
            self._client.funding_rate_history, symbol, start_ms, cutoff_ms, 1000
        )
        observations = [
            CapitalObservation(symbol, "OPEN_INTEREST", timestamp, value, snapshot_id)
            for timestamp, value in oi_history
        ]
        observations.extend(
            CapitalObservation(symbol, "LONG_SHORT_RATIO", timestamp, value, snapshot_id)
            for timestamp, value in ratio_history
        )
        observations.extend(
            CapitalObservation(symbol, "FUNDING_RATE", timestamp, value, snapshot_id)
            for timestamp, value in funding_history
        )
        if quote_volume is not None:
            observations.append(CapitalObservation(
                symbol, "QUOTE_VOLUME_24H", cutoff_ms, quote_volume, snapshot_id
            ))
        return tuple(observations)


def _paged_history(fetch, symbol: str, start_ms: int, end_ms: int, limit: int):
    rows = []
    cursor = start_ms
    while cursor <= end_ms:
        batch = tuple(fetch(symbol, limit, cursor, end_ms))
        batch = tuple(item for item in batch if cursor <= item[0] <= end_ms)
        if not batch:
            break
        rows.extend(batch)
        next_cursor = batch[-1][0] + 1
        if next_cursor <= cursor or len(batch) < limit:
            break
        cursor = next_cursor
    return tuple(dict.fromkeys(rows))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
