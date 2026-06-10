from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from binance_ai_trader.domain.models import ScoringResult
from binance_ai_trader.infrastructure.sqlite_repository import MarketDataRepository
from binance_ai_trader.scoring import InsufficientDataError, ScoringEngine


class MarketScorer:
    def __init__(self, repository: MarketDataRepository, engine: ScoringEngine | None = None) -> None:
        self._repository = repository
        self._engine = engine or ScoringEngine()

    def score_run(
        self, run_id: str, symbols: Iterable[str], excluded_symbols: Iterable[str] = (),
        snapshot_id: str | None = None,
    ) -> ScoringResult:
        snapshot = (self._repository.load_snapshot(snapshot_id) if snapshot_id
                    else self._repository.load_snapshot_for_run(run_id))
        if snapshot.collection_run_id != run_id:
            raise ValueError("scoring snapshot does not match collection run")
        excluded = set(excluded_symbols)
        scores = []
        skipped = set(excluded)
        for symbol in sorted(set(symbols) - excluded):
            klines = {
                interval: self._repository.load_klines_at(
                    symbol, interval, snapshot.data_cutoff_ms, limit=minimum
                )
                for interval, minimum in self._engine.minimum_candles.items()
            }
            try:
                scores.append(self._engine.score(symbol, klines))
            except InsufficientDataError:
                skipped.add(symbol)

        ranked = tuple(sorted(scores, key=lambda item: (-item.score, item.symbol)))
        self._repository.save_scores(run_id, ranked, _utc_now())
        return ScoringResult(run_id=run_id, ranked_scores=ranked, skipped_symbols=tuple(sorted(skipped)))


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")
